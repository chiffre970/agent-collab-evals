#!/usr/bin/env node

import process from "node:process";

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import * as z from "zod/v4";

const endpoint = process.env.AGENT_COLLAB_PEER_ENDPOINT;
const token = process.env.AGENT_COLLAB_PEER_TOKEN;
if (!endpoint || !token) {
  throw new Error("peer-tool endpoint and token are required");
}

async function callGateway(operation, args) {
  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ operation, arguments: args }),
    signal: AbortSignal.timeout(30_000),
  });
  const payload = await response.json();
  if (!response.ok || !payload || typeof payload.result !== "object") {
    throw new Error(payload?.error ?? `peer-tool gateway returned ${response.status}`);
  }
  return payload.result;
}

function result(value) {
  return {
    content: [{ type: "text", text: JSON.stringify(value, null, 2) }],
    structuredContent: value,
  };
}

const cursor = z.string().min(1).nullable().optional();
const limit = z.number().int().min(1).max(100).optional();
const server = new McpServer({ name: "agent-collab-peer", version: "1.0.0" });

server.registerTool(
  "publish",
  {
    description:
      "Publish a durable finding or reply in the current peer workspace. " +
      "Use a stable idempotency key when retrying the same publication.",
    inputSchema: {
      idempotency_key: z.string().min(1).max(256),
      body: z.string().min(1).max(32_768),
      reply_to: z.string().min(1).nullable().optional(),
    },
  },
  async ({ idempotency_key, body, reply_to }) =>
    result(
      await callGateway("publish", {
        idempotency_key,
        body,
        reply_to: reply_to ?? null,
      }),
    ),
);

server.registerTool(
  "list_recent",
  {
    description: "List peer-workspace entries visible to this session.",
    inputSchema: { cursor, limit },
    annotations: { readOnlyHint: true },
  },
  async ({ cursor: pageCursor, limit: pageLimit }) =>
    result(
      await callGateway("list_recent", {
        cursor: pageCursor ?? null,
        limit: pageLimit ?? 50,
      }),
    ),
);

server.registerTool(
  "get_thread",
  {
    description: "Read the visible thread containing a peer-workspace entry.",
    inputSchema: { entry_id: z.string().min(1) },
    annotations: { readOnlyHint: true },
  },
  async ({ entry_id }) =>
    result(await callGateway("get_thread", { entry_id })),
);

server.registerTool(
  "search",
  {
    description: "Search visible peer-workspace entry text.",
    inputSchema: {
      query: z.string().min(1).max(512),
      cursor,
      limit,
    },
    annotations: { readOnlyHint: true },
  },
  async ({ query, cursor: pageCursor, limit: pageLimit }) =>
    result(
      await callGateway("search", {
        query,
        cursor: pageCursor ?? null,
        limit: pageLimit ?? 50,
      }),
    ),
);

server.registerTool(
  "notifications",
  {
    description:
      "Poll peer-workspace notifications from a durable cursor watermark.",
    inputSchema: { cursor, limit },
    annotations: { readOnlyHint: true },
  },
  async ({ cursor: pageCursor, limit: pageLimit }) =>
    result(
      await callGateway("notifications", {
        cursor: pageCursor ?? null,
        limit: pageLimit ?? 50,
      }),
    ),
);

await server.connect(new StdioServerTransport());
