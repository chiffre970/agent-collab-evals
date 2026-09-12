from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
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


def _unused_ports(count):
    listeners = [socket.socket() for _ in range(count)]
    try:
        for listener in listeners:
            listener.bind(("127.0.0.1", 0))
        return [listener.getsockname()[1] for listener in listeners]
    finally:
        for listener in listeners:
            listener.close()


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
    def test_candidate_and_native_requests_cross_session_launcher(self):
        repository = Path(__file__).resolve().parents[1]
        campaign = ModelServingCampaign.load(repository / "campaigns/model_serving_v0/campaign.toml")
        ports = _unused_ports(3)
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            services = create_synthetic_candidate_services(root, campaign, "relay-candidate")
            sessions = SessionIdentityRegistry()
            native = NativeAdmissionTools(root / "native", sessions, "relay-native", 2)
            candidate_gateway = SessionToolGateway(
                services.tools, services.sessions, serve_http=False, unix_socket_root=root / "c",
                advertised_endpoint=f"http://127.0.0.1:{ports[1]}/v1/call",
            )
            native_gateway = SessionToolGateway(
                native, sessions, serve_http=False, unix_socket_root=root / "n",
                advertised_endpoint=f"http://127.0.0.1:{ports[2]}/v1/call",
            )
            try:
                actor = AgentIdentity("relay-candidate", 0)
                candidate_access = candidate_gateway.issue(actor)
                native_access = native_gateway.issue(AgentIdentity("relay-native", 0))
                candidate_gateway.activate(candidate_access, SessionHandle("candidate-primary"))
                native_gateway.activate(native_access, SessionHandle("primary"))
                command = [
                    sys.executable, str(repository / "scripts/runtime/session_launcher.py"),
                    "--timeout-seconds", "15",
                    # Model forwarding is tested separately; this required relay is idle here.
                    "--broker-socket", str(native_access.broker_socket),
                    "--model-endpoint", f"http://127.0.0.1:{ports[0]}/v1",
                    "--candidate-broker-socket", str(candidate_access.broker_socket),
                    "--candidate-endpoint", candidate_access.endpoint,
                    "--native-broker-socket", str(native_access.broker_socket),
                    "--native-endpoint", native_access.endpoint,
                    "--", sys.executable, str(repository / "tests/fixtures/capability_relay_client.py"),
                ]
                request = {
                    "candidate_access": {"endpoint": candidate_access.endpoint, "token": candidate_access.token},
                    "native_access": {"endpoint": native_access.endpoint, "token": native_access.token},
                    "candidate": json.loads((campaign.root / "candidates/vllm-stream-interval-10.json").read_bytes()),
                }
                def run_client():
                    result = subprocess.run(command, input=json.dumps(request), capture_output=True, text=True, timeout=25)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    return json.loads(result.stdout)

                result = run_client()
                self.assertEqual(result["evaluation"]["status"], "pending")
                self.assertTrue(result["native"]["complete"])
                self.assertTrue(native.reconcile("primary", ("child",))["valid"])
                services.compute.release_visible_results(actor.campaign_run_id, actor.actor_id)
                request["receipt"] = result["receipt"]
                # Local tests share a network namespace, unlike separate OCI runs.
                # Use fresh host ports instead of reusing sockets in TIME_WAIT.
                for label, port in zip(("model", "candidate", "native"), _unused_ports(3)):
                    endpoint = f"http://127.0.0.1:{port}" + ("/v1" if label == "model" else "/v1/call")
                    command[command.index(f"--{label}-endpoint") + 1] = endpoint
                    if label != "model":
                        request[f"{label}_access"]["endpoint"] = endpoint
                result = run_client()
                self.assertEqual(result["status"], "released")
                self.assertEqual(result["result"]["criterion_units"], 1100000)
                self.assertEqual(len(services.compute.snapshot(actor.campaign_run_id).reservations), 1)
            finally:
                candidate_gateway.close()
                native_gateway.close()

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
