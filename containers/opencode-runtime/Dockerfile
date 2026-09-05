ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
RUN apt-get update \
    && apt-get install --yes --no-install-recommends python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/agent-collab/runtime
COPY package.json package-lock.json ./
RUN npm ci --omit=dev \
    && npm cache clean --force

COPY scripts/runtime/opencode_bridge.mjs ./opencode_bridge.mjs
COPY scripts/runtime/peer_tool_server.mjs ./peer_tool_server.mjs
COPY scripts/runtime/candidate_tool_server.mjs ./candidate_tool_server.mjs
COPY scripts/runtime/native_admission_plugin.mjs ./native_admission_plugin.mjs
COPY scripts/runtime/session_launcher.py /opt/agent-collab/bin/session-launcher
RUN chmod 0555 \
      /opt/agent-collab/runtime/opencode_bridge.mjs \
      /opt/agent-collab/runtime/peer_tool_server.mjs \
      /opt/agent-collab/runtime/candidate_tool_server.mjs \
      /opt/agent-collab/runtime/native_admission_plugin.mjs \
      /opt/agent-collab/bin/session-launcher

USER 1000:1000
WORKDIR /workspace
