from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
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


def _free_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
    finally:
        probe.close()


def _request(socket_path: Path, token: str, payload: dict[str, object]) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = (
        b"POST /v1/call HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        + f"Authorization: Bearer {token}\r\n".encode("ascii")
        + b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        + body
    )
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(str(socket_path))
        client.sendall(request)
        response = bytearray()
        while part := client.recv(65_536):
            response.extend(part)
        return bytes(response)
    finally:
        client.close()


@unittest.skipUnless(
    os.environ.get("RUN_PEER_TOOL_INTEGRATION") == "1",
    "set RUN_PEER_TOOL_INTEGRATION=1 to create a Unix peer socket",
)
class UnixPeerToolIntegrationTests(unittest.TestCase):
    def test_dedicated_socket_and_relay_preserve_session_authority(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp", prefix="acp-") as directory:
            root = Path(directory)
            model_port = _free_port()
            peer_port = _free_port()
            while peer_port == model_port:
                peer_port = _free_port()
            model_endpoint = f"http://127.0.0.1:{model_port}/v1"
            peer_endpoint = f"http://127.0.0.1:{peer_port}/v1/call"
            identities = SessionIdentityRegistry()
            backend = SqliteCollaborationBackend(
                root / "collaboration.sqlite3", identities
            )
            gateway = PeerToolGateway(
                backend,
                identities,
                serve_http=False,
                unix_socket_root=root / "sockets",
                advertised_endpoint=peer_endpoint,
            )
            campaign_run_id = "unix-peer-gateway"
            gateway.provision(
                campaign_run_id, CollaborationVisibility.ORGANISATION_SHARED
            )
            first = gateway.issue(AgentIdentity(campaign_run_id, 0))
            second = gateway.issue(AgentIdentity(campaign_run_id, 1))
            try:
                assert first.broker_socket is not None
                assert second.broker_socket is not None
                gateway.activate(first, SessionHandle("unix-peer-first"))
                gateway.activate(second, SessionHandle("unix-peer-second"))
                published = _request(
                    first.broker_socket,
                    first.token,
                    {
                        "operation": "publish",
                        "arguments": {
                            "idempotency_key": "direct",
                            "body": "direct socket publication",
                            "reply_to": None,
                        },
                    },
                )
                self.assertIn(b"HTTP/1.0 200 OK", published)

                cross_socket = _request(
                    first.broker_socket,
                    second.token,
                    {
                        "operation": "list_recent",
                        "arguments": {"cursor": None, "limit": 50},
                    },
                )
                self.assertIn(b"HTTP/1.0 403 Forbidden", cross_socket)

                relay_output = root / "relay-response.json"
                relay_payload = json.dumps(
                    {
                        "operation": "publish",
                        "arguments": {
                            "idempotency_key": "relayed",
                            "body": "relayed socket publication",
                            "reply_to": None,
                        },
                    },
                    separators=(",", ":"),
                )
                relay_code = (
                    "import pathlib,sys,urllib.request;"
                    "request=urllib.request.Request(sys.argv[1],"
                    "data=sys.argv[3].encode(),headers={"
                    "'Authorization':'Bearer '+sys.argv[2],"
                    "'Content-Type':'application/json'});"
                    "pathlib.Path(sys.argv[4]).write_bytes("
                    "urllib.request.urlopen(request).read())"
                )
                launched = subprocess.run(
                    (
                        sys.executable,
                        str(
                            REPOSITORY_ROOT
                            / "scripts/runtime/session_launcher.py"
                        ),
                        "--timeout-seconds",
                        "10",
                        "--broker-socket",
                        str(first.broker_socket),
                        "--model-endpoint",
                        model_endpoint,
                        "--peer-broker-socket",
                        str(first.broker_socket),
                        "--peer-endpoint",
                        gateway.endpoint,
                        "--",
                        sys.executable,
                        "-c",
                        relay_code,
                        gateway.endpoint,
                        first.token,
                        relay_payload,
                        str(relay_output),
                    ),
                    check=False,
                    capture_output=True,
                    timeout=15,
                )
                self.assertEqual(
                    launched.returncode, 0, launched.stderr.decode("utf-8")
                )
                self.assertIn(b"relayed socket publication", relay_output.read_bytes())
                export = backend.export(
                    backend.provision(
                        campaign_run_id,
                        CollaborationVisibility.ORGANISATION_SHARED,
                    )
                )
                self.assertEqual(len(export.entries), 2)

                gateway.revoke(first)
                self.assertFalse(first.broker_socket.exists())
            finally:
                gateway.close()


if __name__ == "__main__":
    unittest.main()
