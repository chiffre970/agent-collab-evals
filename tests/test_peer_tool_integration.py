from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_collab_evals.adapters.sqlite_collaboration import (
    SqliteCollaborationBackend,
)
from agent_collab_evals.collaboration import CollaborationVisibility
from agent_collab_evals.domain import AgentIdentity, SessionHandle
from agent_collab_evals.peer_tool import PeerToolGateway
from agent_collab_evals.session_identity import SessionIdentityRegistry


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.environ.get("RUN_PEER_TOOL_INTEGRATION") == "1",
    "set RUN_PEER_TOOL_INTEGRATION=1 to run loopback MCP integration",
)
class PeerToolIntegrationTests(unittest.TestCase):
    def test_pinned_stdio_server_reaches_session_bound_gateway(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            identities = SessionIdentityRegistry()
            backend = SqliteCollaborationBackend(
                Path(directory) / "collaboration.sqlite3", identities
            )
            gateway = PeerToolGateway(backend, identities)
            gateway.provision(
                "mcp-integration", CollaborationVisibility.ACTOR_PRIVATE
            )
            access = gateway.issue(AgentIdentity("mcp-integration", 0))
            gateway.activate(access, SessionHandle("mcp-session"))
            try:
                environment = {
                    "PATH": os.environ["PATH"],
                    "AGENT_COLLAB_PEER_ENDPOINT": access.endpoint,
                    "AGENT_COLLAB_PEER_TOKEN": access.token,
                }
                completed = subprocess.run(
                    [
                        "node",
                        "scripts/runtime/peer_tool_conformance.mjs",
                    ],
                    cwd=REPOSITORY_ROOT,
                    env=environment,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                payload = json.loads(completed.stdout)
                self.assertEqual(
                    payload["tool_names"],
                    [
                        "get_thread",
                        "list_recent",
                        "notifications",
                        "publish",
                        "search",
                    ],
                )
                self.assertEqual(payload["list_recent"]["items"], [])
            finally:
                gateway.close()


if __name__ == "__main__":
    unittest.main()
