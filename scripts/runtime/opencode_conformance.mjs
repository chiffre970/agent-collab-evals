#!/usr/bin/env node

import { createHash } from "node:crypto";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import http from "node:http";
import net from "node:net";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { createOpencode } from "@opencode-ai/sdk";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPOSITORY_ROOT = path.resolve(SCRIPT_DIR, "../..");
const DEFAULT_ROOT = path.join(REPOSITORY_ROOT, "tmp/opencode-conformance");
const PROVIDER_ID = "conformance_gateway";
const MODEL_ID = "deterministic-v1";
const MODEL = `${PROVIDER_ID}/${MODEL_ID}`;

function canonical(value) {
  if (Array.isArray(value)) {
    return `[${value.map(canonical).join(",")}]`;
  }
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

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert(address && typeof address === "object", "failed to reserve a loopback port");
  const port = address.port;
  await new Promise((resolve, reject) => server.close((error) => (error ? reject(error) : resolve())));
  return port;
}

function textFromMessages(messages) {
  return messages
    .filter((message) => message.role === "user")
    .map((message) => {
      if (typeof message.content === "string") return message.content;
      if (!Array.isArray(message.content)) return "";
      return message.content
        .filter((part) => part.type === "text")
        .map((part) => part.text)
        .join("\n");
    })
    .join("\n");
}

function toolArguments(tool) {
  const schema = tool.function?.parameters ?? {};
  const properties = schema.properties ?? {};
  const values = {
    agent: "general",
    subagent_type: "general",
    description: "Conformance child",
    prompt: "Return exactly CHILD_OK.",
  };
  const result = {};
  for (const name of schema.required ?? Object.keys(properties)) {
    if (name in values) {
      result[name] = values[name];
    } else if (properties[name]?.type === "boolean") {
      result[name] = false;
    } else if (properties[name]?.type === "number" || properties[name]?.type === "integer") {
      result[name] = 1;
    } else if (properties[name]?.type === "array") {
      result[name] = [];
    } else if (properties[name]?.type === "object") {
      result[name] = {};
    } else {
      result[name] = "conformance";
    }
  }
  return result;
}

function completionChunk(id, model, delta, finishReason = null, usage = undefined) {
  const result = {
    id,
    object: "chat.completion.chunk",
    created: Math.floor(Date.now() / 1000),
    model,
    choices: [{ index: 0, delta, finish_reason: finishReason }],
  };
  if (usage) result.usage = usage;
  return result;
}

function completion(id, model, message, finishReason = "stop") {
  return {
    id,
    object: "chat.completion",
    created: Math.floor(Date.now() / 1000),
    model,
    choices: [{ index: 0, message, finish_reason: finishReason }],
    usage: { prompt_tokens: 10, completion_tokens: 2, total_tokens: 12 },
  };
}

function chooseGatewayReply(body) {
  const prompt = textFromMessages(body.messages ?? []);
  const afterTool = (body.messages ?? []).some((message) => message.role === "tool");
  const taskTool = (body.tools ?? []).find(
    (tool) => tool.type === "function" && tool.function?.name === "task",
  );

  if (afterTool) {
    return { kind: "text", text: "PARENT_OK" };
  }
  if (prompt.includes("CHILD_OK")) {
    return { kind: "text", text: "CHILD_OK" };
  }
  if (prompt.includes("NATIVE_HANDOFF")) {
    if (!taskTool) return { kind: "text", text: "SOLO_TASK_DENIED" };
    return {
      kind: "tool",
      name: "task",
      arguments: toolArguments(taskTool),
    };
  }
  return { kind: "text", text: "ROUTED_OK" };
}

async function startGateway() {
  const requests = [];
  const server = http.createServer(async (request, response) => {
    try {
      if (request.method === "GET" && request.url === "/v1/models") {
        response.writeHead(200, { "content-type": "application/json" });
        response.end(JSON.stringify({ object: "list", data: [{ id: MODEL_ID, object: "model" }] }));
        return;
      }
      if (request.method !== "POST" || request.url !== "/v1/chat/completions") {
        response.writeHead(404, { "content-type": "application/json" });
        response.end(JSON.stringify({ error: { message: "not found" } }));
        return;
      }

      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      requests.push(body);
      const selected = chooseGatewayReply(body);
      const id = `chatcmpl-${requests.length}`;

      if (!body.stream) {
        const message = selected.kind === "tool"
          ? {
              role: "assistant",
              content: null,
              tool_calls: [{
                id: "call-conformance",
                type: "function",
                function: { name: selected.name, arguments: JSON.stringify(selected.arguments) },
              }],
            }
          : { role: "assistant", content: selected.text };
        response.writeHead(200, { "content-type": "application/json" });
        response.end(JSON.stringify(completion(id, body.model, message, selected.kind === "tool" ? "tool_calls" : "stop")));
        return;
      }

      response.writeHead(200, {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
        connection: "keep-alive",
      });
      const send = (value) => response.write(`data: ${JSON.stringify(value)}\n\n`);
      send(completionChunk(id, body.model, { role: "assistant" }));
      if (selected.kind === "tool") {
        send(completionChunk(id, body.model, {
          tool_calls: [{
            index: 0,
            id: "call-conformance",
            type: "function",
            function: { name: selected.name, arguments: JSON.stringify(selected.arguments) },
          }],
        }));
        send(completionChunk(id, body.model, {}, "tool_calls", {
          prompt_tokens: 10,
          completion_tokens: 2,
          total_tokens: 12,
        }));
      } else {
        send(completionChunk(id, body.model, { content: selected.text }));
        send(completionChunk(id, body.model, {}, "stop", {
          prompt_tokens: 10,
          completion_tokens: 2,
          total_tokens: 12,
        }));
      }
      response.end("data: [DONE]\n\n");
    } catch (error) {
      response.writeHead(500, { "content-type": "application/json" });
      response.end(JSON.stringify({ error: { message: error.message } }));
    }
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert(address && typeof address === "object", "gateway did not bind a port");
  return {
    baseURL: `http://127.0.0.1:${address.port}/v1`,
    requests,
    close: () => new Promise((resolve, reject) => server.close((error) => (error ? reject(error) : resolve()))),
  };
}

function runtimeConfig(baseURL, nativeHandoffs) {
  const deniedTools = {
    bash: false,
    edit: false,
    webfetch: false,
    write: false,
  };
  return {
    logLevel: "ERROR",
    autoupdate: false,
    share: "disabled",
    snapshot: false,
    plugin: [],
    formatter: false,
    lsp: false,
    enabled_providers: [PROVIDER_ID],
    model: MODEL,
    small_model: MODEL,
    provider: {
      [PROVIDER_ID]: {
        name: "ADR 0001 deterministic gateway",
        npm: "@ai-sdk/openai-compatible",
        options: { apiKey: "local-conformance-only", baseURL, timeout: 10_000 },
        models: {
          [MODEL_ID]: {
            id: MODEL_ID,
            name: "Deterministic conformance model",
            tool_call: true,
            temperature: false,
            reasoning: false,
            attachment: false,
            limit: { context: 16_384, output: 2_048 },
            modalities: { input: ["text"], output: ["text"] },
            status: "active",
          },
        },
      },
    },
    permission: {
      edit: "deny",
      bash: "deny",
      webfetch: "deny",
      doom_loop: "deny",
      external_directory: "deny",
    },
    tools: { ...deniedTools, task: nativeHandoffs },
    agent: {
      build: {
        mode: "primary",
        model: MODEL,
        tools: { ...deniedTools, task: nativeHandoffs },
        permission: {
          edit: "deny",
          bash: "deny",
          webfetch: "deny",
          doom_loop: "deny",
          external_directory: "deny",
        },
      },
      general: {
        mode: "subagent",
        model: MODEL,
        tools: { ...deniedTools, task: false },
        permission: {
          edit: "deny",
          bash: "deny",
          webfetch: "deny",
          doom_loop: "deny",
          external_directory: "deny",
        },
      },
    },
  };
}

async function startRuntime(config, directory) {
  const port = await freePort();
  return createOpencode({ port, timeout: 20_000, config }).then((runtime) => ({
    ...runtime,
    directory,
  }));
}

async function checked(result, label) {
  if (result.error !== undefined) {
    throw new Error(`${label} failed: ${JSON.stringify(result.error)}`);
  }
  return result.data;
}

function messageText(message) {
  return (message?.parts ?? [])
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("");
}

async function prompt(runtime, sessionID, text) {
  return checked(
    await runtime.client.session.prompt({
      path: { id: sessionID },
      query: { directory: runtime.directory },
      body: {
        agent: "build",
        model: { providerID: PROVIDER_ID, modelID: MODEL_ID },
        parts: [{ type: "text", text }],
      },
    }),
    "session prompt",
  );
}

async function observe(runtime, events, abortController) {
  const subscription = await runtime.client.event.subscribe({
    query: { directory: runtime.directory },
    signal: abortController.signal,
    sseMaxRetryAttempts: 0,
  });
  try {
    for await (const event of subscription.stream) events.push(event);
  } catch (error) {
    if (!abortController.signal.aborted) throw error;
  }
}

async function inspectSurface(runtime) {
  const query = { directory: runtime.directory };
  const [config, toolIDs, tools, agents] = await Promise.all([
    checked(await runtime.client.config.get({ query }), "effective config"),
    checked(await runtime.client.tool.ids({ query }), "tool identifiers"),
    checked(
      await runtime.client.tool.list({ query: { ...query, provider: PROVIDER_ID, model: MODEL_ID } }),
      "tool list",
    ),
    checked(await runtime.client.app.agents({ query }), "agent list"),
  ]);
  const permissionProjection = {
    global: config.permission,
    agents: Object.fromEntries(
      Object.entries(config.agent ?? {}).map(([name, agent]) => [name, agent?.permission]),
    ),
  };
  return {
    configDigest: digest(config),
    modelDigest: digest({ model: config.model, small_model: config.small_model, provider: config.provider }),
    toolDigest: digest({ ids: toolIDs, tools }),
    permissionDigest: digest(permissionProjection),
    agentDigest: digest(agents),
    toolIDs,
    tools,
    agents,
  };
}

async function main() {
  const keep = process.argv.includes("--keep");
  const root = path.resolve(process.env.OPENCODE_CONFORMANCE_ROOT ?? DEFAULT_ROOT);
  if (!keep) await rm(root, { recursive: true, force: true });
  const xdg = {
    XDG_DATA_HOME: path.join(root, "xdg/data"),
    XDG_CONFIG_HOME: path.join(root, "xdg/config"),
    XDG_CACHE_HOME: path.join(root, "xdg/cache"),
    XDG_STATE_HOME: path.join(root, "xdg/state"),
  };
  const project = path.join(root, "project");
  await Promise.all([...Object.values(xdg), project].map((directory) => mkdir(directory, { recursive: true })));
  Object.assign(process.env, xdg, {
    PATH: `${path.join(REPOSITORY_ROOT, "node_modules/.bin")}:${process.env.PATH}`,
  });

  const gateway = await startGateway();
  const nativeConfig = runtimeConfig(gateway.baseURL, true);
  const soloConfig = runtimeConfig(gateway.baseURL, false);
  let runtime;
  let observation;
  let resumedObservation;
  const events = [];
  const abortController = new AbortController();
  const resumedAbortController = new AbortController();

  try {
    runtime = await startRuntime(nativeConfig, project);
    observation = observe(runtime, events, abortController);
    const observedSurface = await inspectSurface(runtime);
    assert(observedSurface.toolIDs.includes("task"), "native profile lacks stock task tool");

    const created = await checked(
      await runtime.client.session.create({
        query: { directory: project },
        body: { title: "ADR 0001 conformance" },
      }),
      "session create",
    );
    const routed = await prompt(runtime, created.id, "Return exactly ROUTED_OK.");
    assert(messageText(routed).includes("ROUTED_OK"), "prompt did not route through the pinned gateway");
    const beforeRestart = await checked(
      await runtime.client.session.messages({ path: { id: created.id }, query: { directory: project } }),
      "messages before restart",
    );
    assert(beforeRestart.length >= 2, "session did not persist its prompt and response");

    abortController.abort();
    runtime.server.close();
    await observation;
    runtime = undefined;

    runtime = await startRuntime(nativeConfig, project);
    resumedObservation = observe(runtime, events, resumedAbortController);
    const resumed = await checked(
      await runtime.client.session.get({ path: { id: created.id }, query: { directory: project } }),
      "session resume",
    );
    assert(resumed.id === created.id, "session identifier changed across server restart");
    const afterRestart = await checked(
      await runtime.client.session.messages({ path: { id: created.id }, query: { directory: project } }),
      "messages after restart",
    );
    assert(afterRestart.length === beforeRestart.length, "session messages changed across restart");

    const handedOff = await prompt(
      runtime,
      created.id,
      "NATIVE_HANDOFF: use the native task tool once, then report its result.",
    );
    assert(messageText(handedOff).includes("PARENT_OK"), "parent did not finish after native handoff");
    const children = await checked(
      await runtime.client.session.children({ path: { id: created.id }, query: { directory: project } }),
      "child sessions",
    );
    assert(children.length === 1, `expected one child session, received ${children.length}`);
    const childMessages = await checked(
      await runtime.client.session.messages({ path: { id: children[0].id }, query: { directory: project } }),
      "child messages",
    );
    assert(
      childMessages.some((message) => messageText(message).includes("CHILD_OK")),
      "child session did not execute through the gateway",
    );
    resumedAbortController.abort();
    await resumedObservation;
    assert(
      JSON.stringify(events).includes(children[0].id),
      "event stream did not capture the native child session",
    );

    const observedRequest = gateway.requests[0];
    const unobservedSession = await checked(
      await runtime.client.session.create({
        query: { directory: project },
        body: { title: "ADR 0001 unobserved comparison" },
      }),
      "unobserved session create",
    );
    await prompt(runtime, unobservedSession.id, "Return exactly ROUTED_OK.");
    const unobservedRequest = gateway.requests.at(-1);
    assert(
      canonical(observedRequest) === canonical(unobservedRequest),
      "out-of-process observation changed the outbound model request",
    );
    const promptDigest = digest(observedRequest.messages);

    const unobservedSurface = await inspectSurface(runtime);
    for (const field of ["configDigest", "modelDigest", "toolDigest", "permissionDigest", "agentDigest"]) {
      assert(
        unobservedSurface[field] === observedSurface[field],
        `out-of-process observation changed ${field}`,
      );
    }
    runtime.server.close();
    runtime = undefined;

    runtime = await startRuntime(soloConfig, project);
    const soloSurface = await inspectSurface(runtime);
    assert(soloSurface.toolIDs.includes("task"), "stock task tool disappeared instead of remaining profile-controlled");
    const effectiveSoloConfig = await checked(
      await runtime.client.config.get({ query: { directory: project } }),
      "solo effective config",
    );
    assert(effectiveSoloConfig.tools?.task === false, "solo profile did not deny native handoffs");
    assert(effectiveSoloConfig.agent?.build?.tools?.task === false, "solo primary can still invoke native handoffs");
    const soloSession = await checked(
      await runtime.client.session.create({
        query: { directory: project },
        body: { title: "ADR 0001 solo denial" },
      }),
      "solo session create",
    );
    const deniedHandoff = await prompt(
      runtime,
      soloSession.id,
      "NATIVE_HANDOFF: attempt to use the native task tool.",
    );
    assert(messageText(deniedHandoff).includes("SOLO_TASK_DENIED"), "solo denial probe failed");
    const soloRequest = gateway.requests.at(-1);
    assert(
      !(soloRequest.tools ?? []).some((tool) => tool.function?.name === "task"),
      "solo model request still offered the task tool",
    );

    const packageMetadata = JSON.parse(
      await readFile(path.join(REPOSITORY_ROOT, "node_modules/opencode-ai/package.json"), "utf8"),
    );
    const sdkMetadata = JSON.parse(
      await readFile(path.join(REPOSITORY_ROOT, "node_modules/@opencode-ai/sdk/package.json"), "utf8"),
    );
    const report = {
      schema: "opencode-conformance/v1",
      passed: true,
      versions: { opencode: packageMetadata.version, sdk: sdkMetadata.version },
      stateRoot: root,
      gateway: {
        requestCount: gateway.requests.length,
        requestDigest: digest(gateway.requests),
        requestedModels: [...new Set(gateway.requests.map((request) => request.model))],
      },
      nativeProfile: {
        ...Object.fromEntries(
          ["configDigest", "modelDigest", "toolDigest", "permissionDigest", "agentDigest"].map((field) => [
            field,
            observedSurface[field],
          ]),
        ),
        taskEnabled: true,
      },
      soloProfile: {
        configDigest: soloSurface.configDigest,
        modelDigest: soloSurface.modelDigest,
        permissionDigest: soloSurface.permissionDigest,
        taskEnabled: false,
        taskOfferedToModel: false,
      },
      durableSession: {
        sessionID: created.id,
        messagesBeforeRestart: beforeRestart.length,
        messagesAfterRestart: afterRestart.length,
      },
      nativeHandoff: {
        childSessionID: children[0].id,
        childMessageCount: childMessages.length,
      },
      observation: {
        mode: "stock-sdk-out-of-process-sse",
        eventCount: events.length,
        eventTypes: [...new Set(events.map((event) => event.type))].sort(),
        promptDigest,
        outboundRequestUnchanged: true,
        surfaceUnchanged: true,
      },
    };
    const reportPath = path.join(root, "report.json");
    await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
    console.log(JSON.stringify({ ...report, reportPath }, null, 2));
  } finally {
    abortController.abort();
    resumedAbortController.abort();
    runtime?.server.close();
    if (observation) await observation.catch(() => {});
    if (resumedObservation) await resumedObservation.catch(() => {});
    await gateway.close();
  }
}

main().catch((error) => {
  console.error(`OpenCode conformance failed: ${error.stack ?? error.message}`);
  process.exitCode = 1;
});
