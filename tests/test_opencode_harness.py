from __future__ import annotations

import io
import json
import os
import queue
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from agent_collab_evals.adapters.darwin_sandbox import DarwinSandboxExec
from agent_collab_evals.adapters.opencode_harness import (
    OpenCodeHarnessRuntime,
    OpenCodeRuntimeProfile,
    _Bridge,
    _bridge_environment,
    _runtime_config,
)
from agent_collab_evals.sandbox import SandboxProfile
from agent_collab_evals.budget import GatewayAccessToken
from agent_collab_evals.candidate_gateway import CandidateToolAccess
from agent_collab_evals.canonical import digest_value
from agent_collab_evals.domain import (
    AgentIdentity,
    CoordinationCondition,
    HarnessSnapshot,
    Job,
    OrganisationSpec,
    SessionHandle,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = (
    REPOSITORY_ROOT
    / "config/runtime_profiles/opencode-deepseek-v4-flash-development.json"
)
SANDBOX_PROFILE_PATH = (
    REPOSITORY_ROOT
    / "config/sandbox_profiles/darwin-loopback-network-v0.json"
)


def _sandbox() -> DarwinSandboxExec:
    return DarwinSandboxExec(SandboxProfile.load(SANDBOX_PROFILE_PATH))


class _TokenIssuer:
    def issue(self, **_: str) -> GatewayAccessToken:
        return GatewayAccessToken("test-token", "opaque-test-token")

    def revoke(self, token_id: str, reason: str) -> None:
        return

    def activate(self, token_id: str, session: SessionHandle) -> None:
        return


class OpenCodeRuntimeProfileTests(unittest.TestCase):
    def test_runtime_rejects_unwired_capability_relays_before_launch(self):
        with self.assertRaisesRegex(RuntimeError, "Unix relays are not wired"):
            _Bridge(
                state_root=Path("/unused/state"), directory=Path("/unused/workspace"),
                profile=Mock(), endpoint="http://127.0.0.1:4317/v1", gateway_token="opaque",
                broker_socket=None, process_sandbox=Mock(), native_handoffs=False,
                peer_access=None, timeout_seconds=1,
                candidate_access=CandidateToolAccess(
                    "capability", "http://127.0.0.1:4319/v1/call", "opaque", Path("/unused/socket"),
                ),
            )

    def test_candidate_resume_rolls_back_after_receipt_load_and_cleanup_failure(self):
        profile = OpenCodeRuntimeProfile.load(PROFILE_PATH)
        issuer = Mock(wraps=_TokenIssuer())
        gateway = Mock(profile_digest=digest_value({"service": "candidate-test"}))
        access = CandidateToolAccess("candidate-test", "http://127.0.0.1:4319/v1/call", "opaque-test")
        gateway.issue.return_value = access
        bridge = Mock()
        bridge.request.return_value = {"surface": "same"}
        bridge.close.side_effect = RuntimeError("simulated close failure")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = OpenCodeHarnessRuntime(
                profile, root / "state", issuer, process_sandbox=_sandbox(), candidate_gateway=gateway,
            )
            run_id = "candidate-resume-cleanup"
            snapshot = HarnessSnapshot("opencode:" + run_id, {
                "schema": "opencode-harness-snapshot/v4",
                "candidate_tool_profile_digest": runtime._candidate_profile_digest(),
                "runtime_profile_digest": profile.resolved_digest,
                "sandbox_profile_digest": runtime._process_sandbox.profile_digest,
                "stopped": False,
                "spec": {
                    "campaign_run_id": run_id, "condition": "solo", "organisation_size": 1,
                    "workspace_root": str(root / "workspace"), "model_endpoint": "http://127.0.0.1:4317/v1",
                },
                "sessions": [{
                    "session_id": "primary", "actor_ordinal": 0, "peer_tool_enabled": False,
                    "directory": str(root / "workspace/actor-0000"),
                    "state_root": str(runtime._organisation_state_root(run_id) / "actor-0000"),
                    "surface": {"surface": "same"}, "delivered_jobs": [{}],
                    "events": [], "event_cursor": 0, "checkpoint": {},
                }],
            })
            with (
                patch.object(runtime, "_start_bridge", return_value=bridge),
                patch("agent_collab_evals.adapters.opencode_harness.HarnessDeliveryReceipt.from_document", side_effect=RuntimeError("simulated receipt load failure")),
                self.assertRaisesRegex(RuntimeError, "simulated close failure"),
            ):
                runtime.resume(snapshot)
            gateway.activate.assert_called_once_with(access, SessionHandle("primary"))
            gateway.revoke.assert_called_once_with(access)
            issuer.revoke.assert_called_once()
            bridge.close.assert_called_once()
            self.assertEqual(runtime._session_to_organisation, {})
            self.assertEqual(runtime._organisations, {})

    def test_native_runtime_cannot_be_promoted_without_identity_admission(self) -> None:
        profile = replace(OpenCodeRuntimeProfile.load(PROFILE_PATH), status="registered")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = OpenCodeHarnessRuntime(
                profile, root / "state", _TokenIssuer(), process_sandbox=_sandbox()
            )
            self.assertFalse(runtime.capabilities()["native_identity_limit_enforced"])
            with self.assertRaisesRegex(RuntimeError, "identity admission"):
                runtime.start_organisation(OrganisationSpec(
                    "native-unqualified", CoordinationCondition.NATIVE_MULTIAGENT,
                    4, root / "workspace", "http://127.0.0.1:4317/v1",
                ))

    def test_snapshot_failure_still_closes_all_bridges_and_revokes_tokens(self) -> None:
        from types import SimpleNamespace

        runtime = object.__new__(OpenCodeHarnessRuntime)
        sessions = {
            str(index): SimpleNamespace(
                bridge=Mock(), gateway_token_id=f"token-{index}", peer_access=None,
                candidate_access=None, native_access=None,
            )
            for index in range(2)
        }
        sessions["0"].bridge.close.side_effect = RuntimeError("bridge close failed")
        state = SimpleNamespace(sessions=sessions, stopped=False)
        runtime._gateway_tokens = Mock()
        with (
            patch.object(runtime, "_organisation", return_value=state),
            patch.object(runtime, "snapshot", side_effect=RuntimeError("checkpoint failed")),
            patch.object(runtime, "_revoke_peer_access") as revoke_peer,
            self.assertRaises(ExceptionGroup) as caught,
        ):
            runtime.stop(Mock(), "failure")
        self.assertEqual(len(caught.exception.exceptions), 2)
        self.assertTrue(state.stopped)
        self.assertEqual(runtime._gateway_tokens.revoke.call_count, 2)
        self.assertEqual(revoke_peer.call_count, 2)
        for session in sessions.values():
            session.bridge.close.assert_called_once()

    def test_committed_profile_is_strict_and_transitively_digested(self) -> None:
        profile = OpenCodeRuntimeProfile.load(PROFILE_PATH)

        self.assertEqual(profile.opencode_version, "1.18.19")
        self.assertEqual(profile.sdk_version, "1.18.19")
        self.assertEqual(profile.model_id, "deepseek/deepseek-v4-flash-0731")
        self.assertTrue(profile.resolved_digest.startswith("sha256:"))
        self.assertNotEqual(profile.source_digest, profile.resolved_digest)
        self.assertTrue(profile.package_lock_digest.startswith("sha256:"))

    def test_profile_rejects_unknown_fields(self) -> None:
        payload = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        payload["unexpected"] = True
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "profile.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown=\\['unexpected'\\]"):
                OpenCodeRuntimeProfile.load(source)

    def test_condition_controls_only_native_task_surface(self) -> None:
        profile = OpenCodeRuntimeProfile.load(PROFILE_PATH)
        solo = _runtime_config(profile, "https://gateway.invalid/v1", "secret", False)
        native = _runtime_config(profile, "https://gateway.invalid/v1", "secret", True)

        self.assertFalse(solo["tools"]["task"])
        self.assertFalse(solo["agent"]["build"]["tools"]["task"])
        self.assertTrue(native["tools"]["task"])
        self.assertTrue(native["agent"]["build"]["tools"]["task"])
        solo_provider = solo["provider"][profile.provider_id]
        native_provider = native["provider"][profile.provider_id]
        self.assertEqual(solo_provider, native_provider)
        self.assertEqual(solo["model"], native["model"])

    def test_bridge_environment_does_not_inherit_host_credentials(self) -> None:
        profile = OpenCodeRuntimeProfile.load(PROFILE_PATH)
        sensitive = {
            "OPENROUTER_API_KEY": "openrouter-secret",
            "HF_TOKEN": "hugging-face-secret",
            "MODAL_TOKEN_SECRET": "modal-secret",
            "GITHUB_TOKEN": "github-secret",
        }
        previous = {name: os.environ.get(name) for name in sensitive}
        os.environ.update(sensitive)
        try:
            with tempfile.TemporaryDirectory() as directory:
                environment = _bridge_environment(Path(directory), profile)
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        self.assertEqual(
            set(environment),
            {
                "PATH",
                "HOME",
                "TMPDIR",
                "LANG",
                "XDG_DATA_HOME",
                "XDG_CONFIG_HOME",
                "XDG_CACHE_HOME",
                "XDG_STATE_HOME",
                "AGENT_COLLAB_PROVIDER_ID",
                "AGENT_COLLAB_MODEL_ID",
            },
        )
        self.assertFalse(set(sensitive) & set(environment))

    def test_peer_conditions_fail_without_matched_peer_tool(self) -> None:
        profile = OpenCodeRuntimeProfile.load(PROFILE_PATH)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = OpenCodeHarnessRuntime(
                profile,
                root / "state",
                _TokenIssuer(),
                process_sandbox=_sandbox(),
            )
            for condition in (
                CoordinationCondition.PEER_ISOLATED,
                CoordinationCondition.PEER_COLLAB,
            ):
                with self.subTest(condition=condition):
                    spec = OrganisationSpec(
                        campaign_run_id=f"run-{condition.value}",
                        condition=condition,
                        organisation_size=4,
                        workspace_root=root / condition.value,
                        model_endpoint="https://gateway.invalid/v1",
                    )
                    with self.assertRaisesRegex(RuntimeError, "matched peer-tool"):
                        runtime.start_organisation(spec)

    def test_bridge_timeout_is_terminal(self) -> None:
        class Process:
            pid = 12345
            return_code: int | None = None
            stdin = io.StringIO()
            stdout = io.StringIO()
            stderr = io.StringIO()

            def poll(self) -> int | None:
                return self.return_code

            def terminate(self) -> None:
                self.return_code = -15

            def kill(self) -> None:
                self.return_code = -9

            def wait(self, timeout: int) -> int:
                assert self.return_code is not None
                return self.return_code

        class Thread:
            def join(self, timeout: int) -> None:
                return

        bridge = object.__new__(_Bridge)
        bridge._process = Process()
        bridge._timeout_seconds = 0.001
        bridge._sequence = 0
        bridge._unusable = False
        bridge._responses = queue.Queue()
        bridge._stderr = []
        bridge._lock = threading.Lock()
        bridge._stdout_thread = Thread()
        bridge._stderr_thread = Thread()

        with (
            patch("os.killpg", side_effect=lambda *_: bridge._process.terminate()),
            self.assertRaisesRegex(TimeoutError, "bridge terminated"),
        ):
            bridge.request("never_returns")
        self.assertTrue(bridge._unusable)
        self.assertEqual(bridge._process.return_code, -15)
        with self.assertRaisesRegex(RuntimeError, "not running"):
            bridge.request("next")

    def test_delivery_returns_stable_runtime_acknowledgement_receipt(self) -> None:
        class Bridge:
            def __init__(self, existing: bool = False) -> None:
                self.existing = existing
                self.prompt_count = 0

            def request(self, command: str, **_: object) -> object:
                if command == "create_session":
                    return {"id": "session-1"}
                if command == "find_prompt":
                    return {
                        "match_count": 1 if self.existing else 0,
                        "message_id": "existing-message" if self.existing else None,
                        "response_digest": (
                            digest_value({"message": "existing"})
                            if self.existing
                            else None
                        ),
                    }
                if command == "prompt":
                    self.prompt_count += 1
                    return {
                        "message_id": "message-1",
                        "response_digest": digest_value(
                            {"response": "complete"}
                        ),
                    }
                raise AssertionError(f"unexpected command: {command}")

            def close(self) -> None:
                return

        profile = OpenCodeRuntimeProfile.load(PROFILE_PATH)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(OpenCodeHarnessRuntime, "_verify_installation"):
                runtime = OpenCodeHarnessRuntime(
                    profile,
                    root / "state",
                    _TokenIssuer(),
                    process_sandbox=_sandbox(),
                )
            bridge = Bridge()
            runtime._start_bridge = lambda *args: bridge  # type: ignore[method-assign]
            organisation = runtime.start_organisation(
                OrganisationSpec(
                    "delivery-receipt-run",
                    CoordinationCondition.SOLO,
                    1,
                    root / "workspace",
                    "http://127.0.0.1:12345/v1",
                )
            )
            session = runtime.create_primary(
                organisation,
                AgentIdentity("delivery-receipt-run", 0),
            )
            job = Job(
                "job-1",
                "Complete the task.",
                digest_value({"materials": "job-1"}),
                {},
            )

            first = runtime.deliver(session, job)
            repeated = runtime.deliver(session, job)

            with patch.object(OpenCodeHarnessRuntime, "_verify_installation"):
                recovered_runtime = OpenCodeHarnessRuntime(
                    profile,
                    root / "recovered-state",
                    _TokenIssuer(),
                    process_sandbox=_sandbox(),
                )
            recovered_bridge = Bridge(existing=True)
            recovered_runtime._start_bridge = (  # type: ignore[method-assign]
                lambda *args: recovered_bridge
            )
            recovered_organisation = recovered_runtime.start_organisation(
                OrganisationSpec(
                    "recovered-delivery-run",
                    CoordinationCondition.SOLO,
                    1,
                    root / "recovered-workspace",
                    "http://127.0.0.1:12345/v1",
                )
            )
            recovered_session = recovered_runtime.create_primary(
                recovered_organisation,
                AgentIdentity("recovered-delivery-run", 0),
            )
            recovered = recovered_runtime.deliver(recovered_session, job)

        self.assertEqual(first, repeated)
        self.assertEqual(bridge.prompt_count, 1)
        self.assertEqual(first.acknowledgement["message_id"], "message-1")
        self.assertEqual(first.acknowledgement["source"], "prompt_response")
        self.assertEqual(first.session_id, session.value)
        self.assertEqual(recovered_bridge.prompt_count, 0)
        self.assertEqual(
            recovered.acknowledgement["source"],
            "session_message_reconciliation",
        )

    def test_failed_multi_actor_resume_removes_provisional_mappings(self) -> None:
        class Issuer:
            def __init__(self) -> None:
                self.count = 0
                self.revoked: list[str] = []

            def issue(self, **_: str) -> GatewayAccessToken:
                self.count += 1
                return GatewayAccessToken(
                    f"token-{self.count}", f"opaque-{self.count}"
                )

            def revoke(self, token_id: str, reason: str) -> None:
                self.revoked.append(token_id)

            def activate(self, token_id: str, session: SessionHandle) -> None:
                return

        class Bridge:
            def __init__(self, fail: bool) -> None:
                self.fail = fail
                self.closed = False

            def request(self, command: str, **_: object) -> object:
                if self.fail and command == "get_session":
                    raise RuntimeError("injected restore failure")
                if command == "surface":
                    return {"digest": "same"}
                return {}

            def close(self) -> None:
                self.closed = True

        profile = OpenCodeRuntimeProfile.load(PROFILE_PATH)
        issuer = Issuer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = OpenCodeHarnessRuntime(
                profile,
                root / "state",
                issuer,
                process_sandbox=_sandbox(),
            )
            campaign_id = "resume-rollback"
            state_root = runtime._organisation_state_root(campaign_id)
            session_items = [
                {
                    "session_id": f"session-{ordinal}",
                    "actor_ordinal": ordinal,
                    "directory": str(root / "workspaces" / f"actor-{ordinal:04d}"),
                    "state_root": str(state_root / f"actor-{ordinal:04d}"),
                    "gateway_token_id": f"old-{ordinal}",
                    "peer_tool_enabled": False,
                    "delivered_jobs": [],
                    "surface": {"digest": "same"},
                    "events": [],
                    "event_cursor": 0,
                    "checkpoint": {},
                }
                for ordinal in range(2)
            ]
            snapshot = HarnessSnapshot(
                f"opencode:{campaign_id}",
                {
                    "schema": "opencode-harness-snapshot/v4",
                    "runtime_profile_id": profile.profile_id,
                    "runtime_profile_digest": profile.resolved_digest,
                    "sandbox_profile_id": runtime._process_sandbox.profile_id,
                    "sandbox_profile_digest": (
                        runtime._process_sandbox.profile_digest
                    ),
                    "spec": {
                        "campaign_run_id": campaign_id,
                        "condition": "native_multiagent",
                        "organisation_size": 2,
                        "workspace_root": str(root / "workspaces"),
                        "model_endpoint": "https://gateway.invalid/v1",
                    },
                    "sessions": session_items,
                    "stopped": False,
                },
            )
            bridges = [Bridge(False), Bridge(True)]
            runtime._start_bridge = lambda *args: bridges.pop(0)  # type: ignore[method-assign]
            with (
                patch.object(runtime, "_validate_spec"),
                patch(
                    "agent_collab_evals.adapters.opencode_harness.top_level_actor_count",
                    return_value=2,
                ),
                self.assertRaisesRegex(RuntimeError, "injected restore failure"),
            ):
                runtime.resume(snapshot)

        self.assertEqual(runtime._session_to_organisation, {})
        self.assertEqual(issuer.revoked, ["token-2", "token-1"])


if __name__ == "__main__":
    unittest.main()
