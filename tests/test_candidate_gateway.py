from __future__ import annotations

import http.client
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from agent_collab_evals.candidate_gateway import SessionToolGateway
from agent_collab_evals.candidate_rehearsal import create_synthetic_candidate_services
from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.domain import AgentIdentity, SessionHandle
from agent_collab_evals.native_admission import NativeAdmissionTools
from agent_collab_evals.session_identity import SessionIdentityRegistry


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: Path):
        super().__init__("localhost", timeout=5)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(self.path))


def _call(access, operation, arguments):
    connection = _UnixConnection(access.broker_socket)
    try:
        connection.request(
            "POST", "/v1/call", json.dumps({"operation": operation, "arguments": arguments}),
            {"Authorization": f"Bearer {access.token}", "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"capability request failed: {response.status}")
        return json.loads(response.read())["result"]
    finally:
        connection.close()


class SessionToolConfigurationTests(unittest.TestCase):
    def test_transport_requires_exactly_one_listener_mode(self):
        with self.assertRaisesRegex(ValueError, "select either"):
            SessionToolGateway(Mock(), SessionIdentityRegistry(), serve_http=False)
        with self.assertRaisesRegex(ValueError, "select either"):
            SessionToolGateway(
                Mock(), SessionIdentityRegistry(), unix_socket_root=Path("/tmp/unused"),
                advertised_endpoint="http://127.0.0.1:4319/v1/call",
            )


@unittest.skipUnless(os.environ.get("RUN_MODEL_GATEWAY_INTEGRATION") == "1", "enable local socket integration")
class UnixSessionToolTests(unittest.TestCase):
    def test_candidate_lifecycle_over_unix_socket(self):
        repository = Path(__file__).resolve().parents[1]
        campaign = ModelServingCampaign.load(repository / "campaigns/model_serving_v0/campaign.toml")
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            services = create_synthetic_candidate_services(root, campaign, "unix-candidate")
            gateway = SessionToolGateway(
                services.tools, services.sessions, serve_http=False,
                unix_socket_root=root / "s", advertised_endpoint="http://127.0.0.1:4319/v1/call",
            )
            try:
                actor = AgentIdentity("unix-candidate", 0)
                access = gateway.issue(actor)
                self.assertIsNone(gateway._server)
                self.assertTrue(access.broker_socket.is_socket())
                gateway.activate(access, SessionHandle("unix-candidate-session"))
                candidate = json.loads((campaign.root / "candidates/vllm-stream-interval-10.json").read_bytes())
                args = {"candidate": candidate, "idempotency_key": "unix-first"}
                receipt = _call(access, "submit", args)
                self.assertEqual(_call(access, "submit", args), receipt)
                request = {"receipt": receipt["receipt"]}
                self.assertEqual(_call(access, "evaluate", request)["status"], "pending")
                services.compute.release_visible_results(actor.campaign_run_id, actor.actor_id)
                self.assertEqual(_call(access, "result", request)["result"]["criterion_units"], 1100000)
                gateway.revoke(access)
                self.assertFalse(access.broker_socket.parent.exists())
                # A new capability can bind the same session after normal revocation.
                replacement = gateway.issue(actor)
                gateway.activate(replacement, SessionHandle("unix-candidate-session"))
                self.assertNotEqual(replacement.token_id, access.token_id)
                self.assertEqual(_call(replacement, "submit", args), receipt)
            finally:
                gateway.close()
            self.assertFalse(replacement.broker_socket.parent.exists())
            with self.assertRaisesRegex(RuntimeError, "closed"):
                gateway.issue(actor)

    def test_native_admission_uses_same_unix_transport(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            sessions = SessionIdentityRegistry()
            tools = NativeAdmissionTools(root / "ledger", sessions, "unix-native", 2)
            gateway = SessionToolGateway(
                tools, sessions, serve_http=False, unix_socket_root=root / "s",
                advertised_endpoint="http://127.0.0.1:4320/v1/call",
            )
            try:
                access = gateway.issue(AgentIdentity("unix-native", 0))
                gateway.activate(access, SessionHandle("primary"))
                permit = _call(access, "reserve", {
                    "session_id": "primary", "call_id": "first", "task_id": None, "subagent_type": "general",
                })
                _call(access, "complete", {"permit": permit["permit"], "child_session_id": "child"})
                self.assertTrue(tools.reconcile("primary", ("child",))["valid"])
            finally:
                gateway.close()


if __name__ == "__main__":
    unittest.main()
