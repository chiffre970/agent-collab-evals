#!/usr/bin/env node

import { createHash } from "node:crypto";
import { dirname } from "node:path";
import { createInterface } from "node:readline";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { createOpencode } from "@opencode-ai/sdk";

let runtime;
let directory;
let eventAbort;
let eventTask;
const events = [];
const MAX_BUFFERED_EVENTS = 100_000;
let eventCursor = 0;
let eventLossReason;
let eventStreamError;

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function digest(value) {
  return `sha256:${createHash("sha256").update(canonical(value)).digest("hex")}`;
}

function sortedRecords(values) {
  return [...values].sort((left, right) =>
    canonical(left).localeCompare(canonical(right)),
  );
}

function normalizeActorLocal(value) {
  if (typeof value === "string") {
    const runtimeRoot = dirname(process.env.HOME);
    const replacements = [
      [directory, "<actor-workspace>"],
      [runtimeRoot, "<actor-runtime>"],
    ];
    let normalized = value;
    for (const [source, replacement] of replacements) {
      normalized = normalized.split(source).join(replacement);
      if (source.startsWith("/")) {
        normalized = normalized.split(source.slice(1)).join(replacement);
      }
    }
    return normalized;
  }
  if (Array.isArray(value)) return value.map(normalizeActorLocal);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, normalizeActorLocal(item)]),
    );
  }
  return value;
}

async function awaitMcpReady(config, timeoutMilliseconds) {
  const expected = Object.keys(config.mcp ?? {}).sort();
  if (expected.length === 0) return;
  const deadline = Date.now() + timeoutMilliseconds;
  while (Date.now() < deadline) {
    const result = await runtime.client.mcp.status({ query: query() });
    const statuses = checked(result, "MCP status");
    const failure = expected.find((name) => statuses[name]?.status === "failed");
    if (failure !== undefined) {
      throw new Error(
        `MCP server ${failure} failed: ${statuses[failure].error ?? "unknown error"}`,
      );
    }
    if (expected.every((name) => statuses[name]?.status === "connected")) return;
    await sleep(25);
  }
  throw new Error(`MCP servers did not become ready: ${expected.join(", ")}`);
}

function recordEvent(event) {
  eventCursor += 1;
  if (events.length >= MAX_BUFFERED_EVENTS) {
    eventLossReason ??= `event buffer exceeded ${MAX_BUFFERED_EVENTS} records`;
    return;
  }
  events.push({ cursor: eventCursor, event });
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function checked(result, label) {
  if (result.error !== undefined) {
    throw new Error(`${label} failed: ${JSON.stringify(result.error)}`);
  }
  return result.data;
}

function query(targetDirectory = directory) {
  return { directory: targetDirectory };
}

function redactConfig(config) {
  const copy = structuredClone(config);
  for (const plugin of copy.plugin ?? []) {
    if (Array.isArray(plugin) && plugin[0].endsWith("/native_admission_plugin.mjs")) {
      plugin[1] = { endpoint: "<native-admission>", token: "<redacted>" };
    }
  }
  for (const provider of Object.values(copy.provider ?? {})) {
    if (provider?.options && "apiKey" in provider.options) {
      provider.options.apiKey = "<redacted>";
    }
  }
  for (const server of Object.values(copy.mcp ?? {})) {
    if (server?.environment) {
      if ("AGENT_COLLAB_PEER_ENDPOINT" in server.environment) {
        server.environment.AGENT_COLLAB_PEER_ENDPOINT = "<peer-gateway>";
      }
      if ("AGENT_COLLAB_PEER_TOKEN" in server.environment) {
        server.environment.AGENT_COLLAB_PEER_TOKEN = "<redacted>";
      }
      if ("AGENT_COLLAB_CANDIDATE_ENDPOINT" in server.environment) {
        server.environment.AGENT_COLLAB_CANDIDATE_ENDPOINT = "<candidate-gateway>";
      }
      if ("AGENT_COLLAB_CANDIDATE_TOKEN" in server.environment) {
        server.environment.AGENT_COLLAB_CANDIDATE_TOKEN = "<redacted>";
      }
    }
  }
  return copy;
}

async function inspectSurface() {
  const [configResult, idsResult, toolsResult, agentsResult] = await Promise.all([
    runtime.client.config.get({ query: query() }),
    runtime.client.tool.ids({ query: query() }),
    runtime.client.tool.list({
      query: {
        ...query(),
        provider: process.env.AGENT_COLLAB_PROVIDER_ID,
        model: process.env.AGENT_COLLAB_MODEL_ID,
      },
    }),
    runtime.client.app.agents({ query: query() }),
  ]);
  const config = redactConfig(checked(configResult, "effective config"));
  const toolIDs = [...checked(idsResult, "tool identifiers")].sort();
  const tools = sortedRecords(
    checked(toolsResult, "tool list").map(normalizeActorLocal),
  );
  const agents = sortedRecords(
    checked(agentsResult, "agent list").map(normalizeActorLocal),
  );
  const permissions = {
    global: config.permission,
    agents: Object.fromEntries(
      Object.entries(config.agent ?? {}).map(([name, agent]) => [name, agent?.permission]),
    ),
  };
  const surface = {
    config_digest: digest(config),
    model_digest: digest({ model: config.model, small_model: config.small_model, provider: config.provider }),
    tool_digest: digest({ ids: toolIDs, tools }),
    permission_digest: digest(permissions),
    agent_digest: digest(agents),
    task_enabled: config.agent?.build?.tools?.task === true,
  };
  return surface;
}

async function startObservation() {
  eventAbort = new AbortController();
  const subscription = await runtime.client.event.subscribe({
    query: query(),
    signal: eventAbort.signal,
    sseMaxRetryAttempts: 0,
  });
  eventTask = (async () => {
    try {
      for await (const event of subscription.stream) recordEvent(event);
    } catch (error) {
      if (!eventAbort.signal.aborted) {
        eventStreamError = error.stack ?? error.message ?? String(error);
      }
    }
  })();
}

async function reconcileSessionTree(rootSessionIDs, targetDirectory) {
  const pending = [...rootSessionIDs];
  const visited = new Set();
  const sessions = [];
  while (pending.length > 0) {
    const sessionID = pending.shift();
    if (visited.has(sessionID)) continue;
    visited.add(sessionID);
    const [sessionResult, childrenResult, messagesResult] = await Promise.all([
      runtime.client.session.get({
        path: { id: sessionID },
        query: query(targetDirectory),
      }),
      runtime.client.session.children({
        path: { id: sessionID },
        query: query(targetDirectory),
      }),
      runtime.client.session.messages({
        path: { id: sessionID },
        query: query(targetDirectory),
      }),
    ]);
    const session = checked(sessionResult, "reconcile session");
    const children = checked(childrenResult, "reconcile children");
    const messages = checked(messagesResult, "reconcile messages");
    for (const child of children) pending.push(child.id);
    sessions.push({
      session_id: sessionID,
      parent_id: session.parentID ?? null,
      child_ids: children.map((child) => child.id).sort(),
      message_ids: messages.map((message) => message.info.id),
      message_count: messages.length,
      messages_digest: digest(messages),
      messages_json: canonical(messages),
    });
  }
  const statuses = checked(
    await runtime.client.session.status({ query: query(targetDirectory) }),
    "reconcile session status",
  );
  sessions.sort((left, right) => left.session_id.localeCompare(right.session_id));
  return {
    sessions,
    statuses: Object.fromEntries(
      sessions.map((session) => [
        session.session_id,
        statuses[session.session_id] ?? { type: "idle_unlisted" },
      ]),
    ),
  };
}

async function checkpoint(rootSessionIDs, targetDirectory) {
  let stableCursor = -1;
  let stableSamples = 0;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    await sleep(25);
    if (eventCursor === stableCursor) {
      stableSamples += 1;
      if (stableSamples >= 3) break;
    } else {
      stableCursor = eventCursor;
      stableSamples = 0;
    }
  }
  const reconciliation = await reconcileSessionTree(
    rootSessionIDs,
    targetDirectory,
  );
  const terminal = Object.values(reconciliation.statuses).every(
    (status) => status.type !== "busy" && status.type !== "retry",
  );
  const quiet = stableSamples >= 3;
  const records = events.splice(0, events.length);
  return {
    source_cursor: eventCursor,
    records,
    event_loss_reason: eventLossReason ?? null,
    event_stream_error: eventStreamError ?? null,
    quiet,
    terminal,
    complete:
      quiet && terminal && eventLossReason === undefined && eventStreamError === undefined,
    reconciliation,
  };
}

async function stopRuntime() {
  eventAbort?.abort();
  runtime?.server.close();
  if (eventTask) await eventTask.catch(() => {});
  runtime = undefined;
}

async function dispatch(message) {
  switch (message.command) {
    case "init": {
      if (runtime) throw new Error("bridge is already initialized");
      directory = message.directory;
      for (const plugin of message.config.plugin ?? []) {
        if (Array.isArray(plugin) && plugin[0].endsWith("/native_admission_plugin.mjs")) {
          plugin[0] = new URL("./native_admission_plugin.mjs", import.meta.url).href;
        }
      }
      for (const name of ["peer", "candidate"]) {
        if (!message.config.mcp?.[name]) continue;
        // Resolve the packaged sidecar in either the host or container image.
        message.config.mcp[name].command = [
          process.execPath,
          fileURLToPath(new URL(`./${name}_tool_server.mjs`, import.meta.url)),
        ];
      }
      runtime = await createOpencode({
        port: message.port,
        timeout: message.timeout_ms ?? 20_000,
        config: message.config,
      });
      await startObservation();
      await awaitMcpReady(message.config, message.timeout_ms ?? 20_000);
      return { url: runtime.server.url, surface: await inspectSurface() };
    }
    case "create_session":
      return checked(
        await runtime.client.session.create({
          query: query(message.directory),
          body: { title: message.title },
        }),
        "create session",
      );
    case "get_session":
      return checked(
        await runtime.client.session.get({
          path: { id: message.session_id },
          query: query(message.directory),
        }),
        "get session",
      );
    case "find_prompt": {
      const messages = checked(
        await runtime.client.session.messages({
          path: { id: message.session_id },
          query: query(message.directory),
        }),
        "find prompt",
      );
      const matches = messages.filter(
        (item) =>
          item?.info?.role === "user" &&
          item?.parts?.some(
            (part) => part?.type === "text" && part?.text === message.text,
          ),
      );
      return {
        match_count: matches.length,
        message_id: matches.length === 1 ? matches[0]?.info?.id ?? null : null,
        response_digest: matches.length === 1 ? digest(matches[0]) : null,
      };
    }
    case "prompt": {
      const response = checked(
        await runtime.client.session.prompt({
          path: { id: message.session_id },
          query: query(message.directory),
          body: {
            agent: "build",
            model: {
              providerID: process.env.AGENT_COLLAB_PROVIDER_ID,
              modelID: process.env.AGENT_COLLAB_MODEL_ID,
            },
            parts: [{ type: "text", text: message.text }],
          },
        }),
        "prompt session",
      );
      return {
        message_id: response?.info?.id ?? null,
        response_digest: digest(response),
      };
    }
    case "children":
      return checked(
        await runtime.client.session.children({
          path: { id: message.session_id },
          query: query(message.directory),
        }),
        "list children",
      );
    case "messages":
      return checked(
        await runtime.client.session.messages({
          path: { id: message.session_id },
          query: query(message.directory),
        }),
        "list messages",
      );
    case "surface":
      return inspectSurface();
    case "checkpoint":
      return checkpoint(message.session_ids, message.directory);
    case "shutdown":
      await stopRuntime();
      return { stopped: true };
    default:
      throw new Error(`unknown bridge command: ${message.command}`);
  }
}

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of lines) {
  let request;
  try {
    request = JSON.parse(line);
    const result = await dispatch(request);
    process.stdout.write(`${JSON.stringify({ id: request.id, ok: true, result })}\n`);
    if (request.command === "shutdown") break;
  } catch (error) {
    process.stdout.write(
      `${JSON.stringify({ id: request?.id, ok: false, error: error.stack ?? error.message })}\n`,
    );
  }
}

await stopRuntime();
