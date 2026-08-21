#!/usr/bin/env node

import process from "node:process";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const endpoint = process.env.AGENT_COLLAB_PEER_ENDPOINT;
const token = process.env.AGENT_COLLAB_PEER_TOKEN;
if (!endpoint || !token) {
  throw new Error("peer-tool endpoint and token are required");
}

const serverScript = new URL("./peer_tool_server.mjs", import.meta.url).pathname;
const transport = new StdioClientTransport({
  command: process.execPath,
  args: [serverScript],
  env: {
    PATH: process.env.PATH ?? "",
    AGENT_COLLAB_PEER_ENDPOINT: endpoint,
    AGENT_COLLAB_PEER_TOKEN: token,
  },
  stderr: "pipe",
});
const client = new Client({ name: "peer-tool-conformance", version: "1.0.0" });

try {
  await client.connect(transport);
  const tools = await client.listTools();
  const result = await client.callTool({
    name: "list_recent",
    arguments: { cursor: null, limit: 50 },
  });
  process.stdout.write(
    `${JSON.stringify({
      tool_names: tools.tools.map((tool) => tool.name).sort(),
      list_recent: result.structuredContent,
    })}\n`,
  );
} finally {
  await transport.close();
}
