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
from agent_collab_evals.adapters.sqlite_collaboration import (
    SqliteCollaborationBackend,
)
from agent_collab_evals.adapters.opencode_harness import (
    GatewayAccessToken,
    OpenCodeHarnessRuntime,
    OpenCodeRuntimeProfile,
)
from agent_collab_evals.controller import CampaignController
from agent_collab_evals.domain import CoordinationCondition, Job, OrganisationSpec
from agent_collab_evals.collaboration import CollaborationVisibility
from agent_collab_evals.peer_tool import (
    PeerToolGateway,
    PeerToolIntegrationProfile,
)
from agent_collab_evals.session_identity import SessionIdentityRegistry


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = (
    REPOSITORY_ROOT
    / "config/runtime_profiles/opencode-deepseek-v4-flash-development.json"
)
PEER_PROFILE_PATH = (
    REPOSITORY_ROOT / "config/peer_tool_profiles/peer-tool-v0.json"
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


class _PeerCallingGatewayHandler(BaseHTTPRequestHandler):
    server: "_GatewayServer"

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length))
        self.server.requests.append(body)
        messages = body["messages"]
        last_user = max(
            index
            for index, message in enumerate(messages)
            if message.get("role") == "user"
        )
        tool_count = sum(
            message.get("role") == "tool" for message in messages[last_user + 1 :]
        )
        token_label = self.headers.get("authorization", "missing").removeprefix(
            "Bearer "
        )
        request_id = f"chatcmpl-peer-{len(self.server.requests)}"
        if tool_count >= 2:
            chunks = [
                {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "content": "PEER_TOOL_OK",
                            },
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
        else:
            if tool_count == 0:
                function = {
                    "name": "peer_publish",
                    "arguments": json.dumps(
                        {
                            "idempotency_key": f"peer-{token_label}",
                            "body": f"finding from {token_label}",
                            "reply_to": None,
                        }
                    ),
                }
            else:
                function = {
                    "name": "peer_list_recent",
                    "arguments": json.dumps({"cursor": None, "limit": 50}),
                }
            chunks = [
                {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": f"peer-call-{tool_count + 1}",
                                        "type": "function",
                                        "function": function,
                                    }
                                ],
                            },
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
                            "delta": {},
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 4,
                        "total_tokens": 14,
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

    def test_stock_runtime_executes_session_bound_peer_tool(self) -> None:
        model_gateway = _GatewayServer(
            ("127.0.0.1", 0), _PeerCallingGatewayHandler
        )
        model_gateway.requests = []
        model_thread = threading.Thread(
            target=model_gateway.serve_forever, daemon=True
        )
        model_thread.start()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identities = SessionIdentityRegistry()
            collaboration = SqliteCollaborationBackend(
                root / "collaboration.sqlite3", identities
            )
            peer_gateway = PeerToolGateway(collaboration, identities)
            tokens = _TokenIssuer()
            try:
                runtime_profile = OpenCodeRuntimeProfile.load(PROFILE_PATH)
                peer_profile = PeerToolIntegrationProfile.load(
                    PEER_PROFILE_PATH, repository_root=REPOSITORY_ROOT
                )
                runtime = OpenCodeHarnessRuntime(
                    runtime_profile,
                    root / "runtime-state",
                    tokens,
                    peer_profile=peer_profile,
                    peer_gateway=peer_gateway,
                    timeout_seconds=30,
                )
                spec = OrganisationSpec(
                    campaign_run_id="opencode-peer-integration",
                    condition=CoordinationCondition.PEER_ISOLATED,
                    organisation_size=1,
                    workspace_root=root / "workspaces",
                    model_endpoint=(
                        f"http://127.0.0.1:{model_gateway.server_port}/v1"
                    ),
                )
                controller = CampaignController(
                    runtime, LocalEventSink(root / "events")
                )
                handle = controller.start(spec)
                controller.deliver(
                    handle,
                    Job(
                        "peer-job",
                        "Publish one finding through the peer tool.",
                        "sha256:peer-job",
                        {},
                    ),
                )
                session_state = next(
                    iter(runtime._organisation(handle.organisation).sessions.values())
                )
                peer_access = session_state.peer_access
                self.assertIsNotNone(peer_access)
                assert peer_access is not None
                snapshot = runtime.snapshot(handle.organisation)
                controller.close(handle, "complete")

                serialized_snapshot = json.dumps(snapshot.payload, sort_keys=True)
                self.assertNotIn(peer_access.token, serialized_snapshot)
                self.assertTrue(
                    all(
                        peer_access.token not in json.dumps(request, sort_keys=True)
                        for request in model_gateway.requests
                    )
                )
                with self.assertRaisesRegex(PermissionError, "access denied"):
                    peer_gateway.invoke(
                        peer_access,
                        "list_recent",
                        {"cursor": None, "limit": 50},
                    )

                scope = collaboration.provision(
                    "opencode-peer-integration",
                    CollaborationVisibility.ACTOR_PRIVATE,
                )
                export = collaboration.export(scope)
                self.assertEqual(len(export.entries), 1)
                self.assertTrue(export.entries[0].body.startswith("finding from "))
                offered_tools = {
                    tool["function"]["name"]
                    for tool in model_gateway.requests[0]["tools"]
                }
                self.assertIn("peer_publish", offered_tools)
                self.assertNotIn("task", offered_tools)
                surface = snapshot.payload["sessions"][0]["surface"]
                self.assertFalse(surface["task_enabled"])
            finally:
                peer_gateway.close()
                model_gateway.shutdown()
                model_gateway.server_close()
                model_thread.join(timeout=5)

    def test_four_actor_peer_arms_have_matched_surfaces_and_visibility(self) -> None:
        model_gateway = _GatewayServer(
            ("127.0.0.1", 0), _PeerCallingGatewayHandler
        )
        model_gateway.requests = []
        model_thread = threading.Thread(
            target=model_gateway.serve_forever, daemon=True
        )
        model_thread.start()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identities = SessionIdentityRegistry()
            collaboration = SqliteCollaborationBackend(
                root / "collaboration.sqlite3", identities
            )
            peer_gateway = PeerToolGateway(collaboration, identities)
            tokens = _TokenIssuer()
            runtime_profile = OpenCodeRuntimeProfile.load(PROFILE_PATH)
            peer_profile = PeerToolIntegrationProfile.load(
                PEER_PROFILE_PATH, repository_root=REPOSITORY_ROOT
            )
            endpoint = f"http://127.0.0.1:{model_gateway.server_port}/v1"
            try:
                first_runtime = OpenCodeHarnessRuntime(
                    runtime_profile,
                    root / "runtime-state",
                    tokens,
                    peer_profile=peer_profile,
                    peer_gateway=peer_gateway,
                    timeout_seconds=30,
                )
                controller = CampaignController(
                    first_runtime, LocalEventSink(root / "events")
                )
                private_spec = OrganisationSpec(
                    campaign_run_id="matched-peer-private",
                    condition=CoordinationCondition.PEER_ISOLATED,
                    organisation_size=4,
                    workspace_root=root / "private-workspaces",
                    model_endpoint=endpoint,
                )
                private_handle = controller.start(private_spec)
                controller.deliver(
                    private_handle,
                    Job(
                        "private-job",
                        "Publish and inspect one peer finding.",
                        "sha256:private-job",
                        {},
                    ),
                )
                private_snapshot = first_runtime.snapshot(
                    private_handle.organisation
                )
                controller.close(private_handle, "private-complete")

                shared_spec = OrganisationSpec(
                    campaign_run_id="matched-peer-shared",
                    condition=CoordinationCondition.PEER_COLLAB,
                    organisation_size=4,
                    workspace_root=root / "shared-workspaces",
                    model_endpoint=endpoint,
                )
                shared_handle = controller.start(shared_spec)
                controller.deliver(
                    shared_handle,
                    Job(
                        "shared-job-1",
                        "Publish and inspect one peer finding.",
                        "sha256:shared-job-1",
                        {},
                    ),
                )
                shared_campaign_snapshot = controller.snapshot(shared_handle)
                first_runtime.suspend(shared_handle.organisation)

                second_runtime = OpenCodeHarnessRuntime(
                    runtime_profile,
                    root / "runtime-state",
                    tokens,
                    peer_profile=peer_profile,
                    peer_gateway=peer_gateway,
                    timeout_seconds=30,
                )
                resumed_controller = CampaignController(
                    second_runtime, LocalEventSink(root / "events-resumed")
                )
                resumed = resumed_controller.resume(shared_campaign_snapshot)
                resumed_controller.deliver(
                    resumed,
                    Job(
                        "shared-job-2",
                        "Publish and inspect a second peer finding.",
                        "sha256:shared-job-2",
                        {},
                    ),
                )
                resumed_controller.close(resumed, "shared-complete")

                private_surface_items = [
                    session["surface"]
                    for session in private_snapshot.payload["sessions"]
                ]
                shared_surface_items = [
                    session["surface"]
                    for session in shared_campaign_snapshot.harness.payload[
                        "sessions"
                    ]
                ]
                for field in private_surface_items[0]:
                    with self.subTest(surface_field=field):
                        private_values = {
                            json.dumps(surface[field], sort_keys=True)
                            for surface in private_surface_items
                        }
                        shared_values = {
                            json.dumps(surface[field], sort_keys=True)
                            for surface in shared_surface_items
                        }
                        self.assertEqual(len(private_values), 1)
                        self.assertEqual(private_values, shared_values)

                private_scope = collaboration.provision(
                    "matched-peer-private",
                    CollaborationVisibility.ACTOR_PRIVATE,
                )
                private_export = collaboration.export(private_scope)
                private_owner = {
                    entry.entry_id: entry.actor_id
                    for entry in private_export.entries
                }
                private_reads = [
                    event
                    for event in private_export.audit_events
                    if event["kind"] == "recent.read"
                ]
                self.assertEqual(len(private_export.entries), 4)
                self.assertTrue(private_reads)
                self.assertTrue(
                    all(
                        all(
                            private_owner[entry_id] == event["actor_id"]
                            for entry_id in event["details"]["entry_ids"]
                        )
                        for event in private_reads
                    )
                )

                shared_scope = collaboration.provision(
                    "matched-peer-shared",
                    CollaborationVisibility.ORGANISATION_SHARED,
                )
                shared_export = collaboration.export(shared_scope)
                shared_owner = {
                    entry.entry_id: entry.actor_id for entry in shared_export.entries
                }
                shared_reads = [
                    event
                    for event in shared_export.audit_events
                    if event["kind"] == "recent.read"
                ]
                self.assertEqual(len(shared_export.entries), 8)
                self.assertTrue(
                    any(
                        any(
                            shared_owner[entry_id] != event["actor_id"]
                            for entry_id in event["details"]["entry_ids"]
                        )
                        for event in shared_reads
                    )
                )
            finally:
                peer_gateway.close()
                model_gateway.shutdown()
                model_gateway.server_close()
                model_thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
