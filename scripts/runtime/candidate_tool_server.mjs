#!/usr/bin/env node
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import * as z from "zod/v4";

const endpoint = process.env.AGENT_COLLAB_CANDIDATE_ENDPOINT;
const token = process.env.AGENT_COLLAB_CANDIDATE_TOKEN;
if (!endpoint || !token) throw new Error("candidate endpoint and token are required");

async function call(operation, args) {
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ operation, arguments: args }),
    signal: AbortSignal.timeout(60_000),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error ?? "candidate service unavailable");
  return {
    content: [{ type: "text", text: JSON.stringify(payload.result) }],
    structuredContent: payload.result,
  };
}

const server = new McpServer({ name: "agent-collab-candidate", version: "1.0.0" });
server.registerTool("submit", {
  description: "Submit a declarative serving candidate for this mission. Retry identical content with the same key.",
  inputSchema: {
    candidate: z.record(z.string(), z.unknown()),
    idempotency_key: z.string().min(1).max(256),
  },
}, async (args) => call("submit", args));
server.registerTool("evaluate", {
  description: "Request public evaluation of your admitted candidate. Results remain pending until the controller releases them.",
  inputSchema: { receipt: z.string().min(1) },
}, async (args) => call("evaluate", args));
server.registerTool("result", {
  description: "Read your released public evaluation. Hidden evaluation is never exposed.",
  inputSchema: { receipt: z.string().min(1) },
  annotations: { readOnlyHint: true },
}, async (args) => call("result", args));
await server.connect(new StdioServerTransport());
