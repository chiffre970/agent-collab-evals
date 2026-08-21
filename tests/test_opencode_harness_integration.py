from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent_collab_evals.adapters.local_events import LocalEventSink
from agent_collab_evals.adapters.local_snapshots import LocalCampaignSnapshotStore
from agent_collab_evals.adapters.opencode_harness import (
    GatewayAccessToken,
    OpenCodeHarnessRuntime,
    OpenCodeRuntimeProfile,
)
from agent_collab_evals.controller import CampaignController
from agent_collab_evals.domain import CoordinationCondition, Job, OrganisationSpec


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = (
    REPOSITORY_ROOT
    / "config/runtime_profiles/opencode-deepseek-v4-flash-development.json"
)


class _GatewayHandler(BaseHTTPRequestHandler):
    server: "_GatewayServer"

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length))
        self.server.requests.append(body)
        request_id = f"chatcmpl-{len(self.server.requests)}"
        chunks = [
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": 1,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant"},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": 1,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "ROUTED_OK"},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": 1,
                "model": body["model"],
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            },
        ]
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, format: str, *args: object) -> None:
        return


class _GatewayServer(ThreadingHTTPServer):
    requests: list[dict[str, object]]


class _TokenIssuer:
    def __init__(self) -> None:
        self.issued: list[str] = []
        self.revoked: list[tuple[str, str]] = []

    def issue(self, **_: str) -> GatewayAccessToken:
        token_id = f"test-token-{len(self.issued) + 1}"
        self.issued.append(token_id)
        return GatewayAccessToken(token_id, f"opaque-{token_id}")

    def revoke(self, token_id: str, reason: str) -> None:
        self.revoked.append((token_id, reason))


@unittest.skipUnless(
    os.environ.get("RUN_OPENCODE_INTEGRATION") == "1",
    "set RUN_OPENCODE_INTEGRATION=1 to run loopback OpenCode integration",
)
class OpenCodeHarnessIntegrationTests(unittest.TestCase):
    def test_two_jobs_cross_a_real_server_restart(self) -> None:
        gateway = _GatewayServer(("127.0.0.1", 0), _GatewayHandler)
        gateway.requests = []
        thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        thread.start()
        tokens = _TokenIssuer()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                profile = OpenCodeRuntimeProfile.load(PROFILE_PATH)
                spec = OrganisationSpec(
                    campaign_run_id="opencode-integration",
                    condition=CoordinationCondition.SOLO,
                    organisation_size=1,
                    workspace_root=root / "workspaces",
                    model_endpoint=f"http://127.0.0.1:{gateway.server_port}/v1",
                )
                event_sink = LocalEventSink(root / "events")
                store = LocalCampaignSnapshotStore(root / "snapshots")

                first_runtime = OpenCodeHarnessRuntime(
                    profile,
                    root / "runtime-state",
                    tokens,
                    timeout_seconds=30,
                )
                first_controller = CampaignController(first_runtime, event_sink)
                handle = first_controller.start(spec)
                first_controller.deliver(
                    handle,
                    Job("first", "Complete the first mission.", "sha256:first", {}),
                )
                campaign_snapshot = first_controller.snapshot(handle)
                store.save(campaign_snapshot)
                first_runtime.suspend(handle.organisation)

                serialized = (
                    root / "snapshots/opencode-integration/snapshot.json"
                ).read_text(encoding="utf-8")
                self.assertNotIn("opaque-test-token", serialized)

                second_runtime = OpenCodeHarnessRuntime(
                    profile,
                    root / "runtime-state",
                    tokens,
                    timeout_seconds=30,
                )
                second_controller = CampaignController(second_runtime, event_sink)
                resumed = second_controller.resume(store.load("opencode-integration"))
                second_controller.deliver(
                    resumed,
                    Job("second", "Complete the second mission.", "sha256:second", {}),
                )
                result = second_controller.close(resumed, "complete")

                self.assertEqual(result.delivered_job_ids, ("first", "second"))
                self.assertEqual(len(gateway.requests), 2)
                self.assertTrue(
                    all(
                        request["model"] == "deepseek/deepseek-v4-flash-0731"
                        for request in gateway.requests
                    )
                )
                session_snapshot = result.final_harness_snapshot.payload["sessions"][0]
                self.assertGreater(len(session_snapshot["events"]), 0)
                self.assertTrue(session_snapshot["checkpoint"]["complete"])
                self.assertEqual(
                    [event["cursor"] for event in session_snapshot["events"]],
                    list(range(1, len(session_snapshot["events"]) + 1)),
                )
                self.assertTrue(
                    session_snapshot["surface"]["config_digest"].startswith("sha256:")
                )
                self.assertEqual(tokens.issued, ["test-token-1", "test-token-2"])
                self.assertEqual(
                    [token_id for token_id, _ in tokens.revoked],
                    ["test-token-1", "test-token-2"],
                )
        finally:
            gateway.shutdown()
            gateway.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
