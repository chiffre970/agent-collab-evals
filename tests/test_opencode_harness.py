from __future__ import annotations

import io
import json
import os
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_collab_evals.adapters.opencode_harness import (
    GatewayAccessToken,
    OpenCodeHarnessRuntime,
    OpenCodeRuntimeProfile,
    _Bridge,
    _bridge_environment,
    _runtime_config,
)
from agent_collab_evals.domain import (
    CoordinationCondition,
    HarnessSnapshot,
    OrganisationSpec,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = (
    REPOSITORY_ROOT
    / "config/runtime_profiles/opencode-deepseek-v4-flash-development.json"
)


class _TokenIssuer:
    def issue(self, **_: str) -> GatewayAccessToken:
        return GatewayAccessToken("test-token", "opaque-test-token")

    def revoke(self, token_id: str, reason: str) -> None:
        return


class OpenCodeRuntimeProfileTests(unittest.TestCase):
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
            runtime = OpenCodeHarnessRuntime(profile, root / "state", _TokenIssuer())
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

        with self.assertRaisesRegex(TimeoutError, "bridge terminated"):
            bridge.request("never_returns")
        self.assertTrue(bridge._unusable)
        self.assertEqual(bridge._process.return_code, -15)
        with self.assertRaisesRegex(RuntimeError, "not running"):
            bridge.request("next")

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
            runtime = OpenCodeHarnessRuntime(profile, root / "state", issuer)
            campaign_id = "resume-rollback"
            state_root = runtime._organisation_state_root(campaign_id)
            session_items = [
                {
                    "session_id": f"session-{ordinal}",
                    "actor_ordinal": ordinal,
                    "directory": str(root / "workspaces" / f"actor-{ordinal:04d}"),
                    "state_root": str(state_root / f"actor-{ordinal:04d}"),
                    "gateway_token_id": f"old-{ordinal}",
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
                    "schema": "opencode-harness-snapshot/v2",
                    "runtime_profile_id": profile.profile_id,
                    "runtime_profile_digest": profile.resolved_digest,
                    "spec": {
                        "campaign_run_id": campaign_id,
                        "condition": "peer_isolated",
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
