from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_collab_evals.adapters.deterministic_model import (
    DeterministicModelUpstream,
)
from agent_collab_evals.adapters.provider_receipts import OpenRouterReceiptVerifier
from agent_collab_evals.adapters.sqlite_budget import SqliteBudgetAccount
from agent_collab_evals.budget import ActorBudgetAllocation, BudgetPlan
from agent_collab_evals.canonical import digest_value
from agent_collab_evals.domain import SessionHandle
from agent_collab_evals.model_gateway import ModelBudgetGateway, ModelGatewayProfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = (
    REPOSITORY_ROOT
    / "config/gateway_profiles/openrouter-deepinfra-local-conformance-v0.json"
)


@unittest.skipUnless(
    os.environ.get("RUN_MODEL_GATEWAY_INTEGRATION") == "1",
    "set RUN_MODEL_GATEWAY_INTEGRATION=1 to create a Unix gateway socket",
)
class UnixModelGatewayIntegrationTests(unittest.TestCase):
    def test_dedicated_socket_uses_the_existing_gateway_authorities(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp", prefix="acg-") as directory:
            root = Path(directory)
            profile = ModelGatewayProfile.load(
                PROFILE_PATH, repository_root=REPOSITORY_ROOT
            )
            campaign_run_id = "unix-gateway"
            actor_id = f"{campaign_run_id}:actor:0"
            allocation = ActorBudgetAllocation(
                campaign_run_id, actor_id, 250_000_000
            )
            plan = BudgetPlan.create(
                plan_id="unix-gateway-plan",
                status="conformance_only",
                campaign_run_id=campaign_run_id,
                organisation_limit_usd_nanos=allocation.limit_usd_nanos,
                allocations=(allocation,),
                rate_card_digest=digest_value(profile.rate_card),
            )
            account = SqliteBudgetAccount(
                root / "budget.sqlite3",
                profile.rate_card,
                require_metadata_receipts=False,
                budget_plan=plan,
                receipt_verifier=OpenRouterReceiptVerifier(
                    profile, require_metadata_receipt=False
                ),
            )
            account.open_campaign(
                campaign_run_id, allocation.limit_usd_nanos, (allocation,)
            )
            upstream = DeterministicModelUpstream(
                model=profile.expected_returned_model,
                provider=profile.expected_provider,
            )
            gateway = ModelBudgetGateway(
                profile,
                account,
                upstream,
                serve_http=False,
                unix_socket_root=root / "sockets",
                advertised_endpoint="http://127.0.0.1:4317/v1",
            )
            try:
                access = gateway.issue(
                    campaign_run_id=campaign_run_id,
                    actor_id=actor_id,
                    model_endpoint=gateway.endpoint,
                )
                self.assertIsNotNone(access.broker_socket)
                assert access.broker_socket is not None
                gateway.activate(access.token_id, SessionHandle("unix-session"))
                body = json.dumps(
                    {
                        "model": profile.requested_model,
                        "stream": True,
                        "messages": [{"role": "user", "content": "test"}],
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
                request = (
                    b"POST /v1/chat/completions HTTP/1.1\r\n"
                    b"Host: 127.0.0.1\r\n"
                    + f"Authorization: Bearer {access.value}\r\n".encode("ascii")
                    + b"Content-Type: application/json\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
                    + body
                )
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    client.connect(str(access.broker_socket))
                    client.sendall(request)
                    response = bytearray()
                    while part := client.recv(65_536):
                        response.extend(part)
                finally:
                    client.close()

                self.assertIn(b"HTTP/1.0 200 OK", response)
                self.assertIn(b"LOCAL_ADAPTER_REHEARSAL_OK", response)
                other = gateway.issue(
                    campaign_run_id=campaign_run_id,
                    actor_id=actor_id,
                    model_endpoint=gateway.endpoint,
                )
                gateway.activate(other.token_id, SessionHandle("other-session"))
                wrong_socket_request = request.replace(
                    access.value.encode("ascii"), other.value.encode("ascii")
                )
                wrong_socket_client = socket.socket(
                    socket.AF_UNIX, socket.SOCK_STREAM
                )
                try:
                    wrong_socket_client.connect(str(access.broker_socket))
                    wrong_socket_client.sendall(wrong_socket_request)
                    denied = bytearray()
                    while part := wrong_socket_client.recv(65_536):
                        denied.extend(part)
                finally:
                    wrong_socket_client.close()
                self.assertIn(b"HTTP/1.0 403 Forbidden", denied)
                self.assertEqual(len(upstream.requests), 1)
                gateway.revoke(other.token_id, "cross-socket probe complete")
                relay_output = root / "relay-response.bin"
                relay_code = (
                    "import pathlib,sys,urllib.request;"
                    "body=sys.argv[3].encode();"
                    "request=urllib.request.Request(sys.argv[1]+'/chat/completions',"
                    "data=body,headers={'Authorization':'Bearer '+sys.argv[2],"
                    "'Content-Type':'application/json'});"
                    "pathlib.Path(sys.argv[4]).write_bytes(urllib.request.urlopen(request).read())"
                )
                launched = subprocess.run(
                    (
                        sys.executable,
                        str(REPOSITORY_ROOT / "scripts/runtime/session_launcher.py"),
                        "--timeout-seconds",
                        "10",
                        "--broker-socket",
                        str(access.broker_socket),
                        "--model-endpoint",
                        gateway.endpoint,
                        "--",
                        sys.executable,
                        "-c",
                        relay_code,
                        gateway.endpoint,
                        access.value,
                        body.decode("utf-8"),
                        str(relay_output),
                    ),
                    check=False,
                    capture_output=True,
                    timeout=15,
                )
                self.assertEqual(
                    launched.returncode, 0, launched.stderr.decode("utf-8")
                )
                self.assertIn(
                    b"LOCAL_ADAPTER_REHEARSAL_OK", relay_output.read_bytes()
                )
                self.assertEqual(len(upstream.requests), 2)
                self.assertTrue(account.reconcile(campaign_run_id).valid)
                gateway.revoke(access.token_id, "test complete")
                self.assertFalse(access.broker_socket.exists())
            finally:
                gateway.close()


if __name__ == "__main__":
    unittest.main()
