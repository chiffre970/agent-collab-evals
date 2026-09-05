from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_collab_evals.adapters.oci_sandbox import (
    OciSandboxExec,
    OciSandboxProfile,
)
from agent_collab_evals.canonical import digest_value
from agent_collab_evals.sandbox import SandboxLaunchContext


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = (
    REPOSITORY_ROOT
    / "config/enforcement_profiles/oci-opencode-v0-candidate.json"
)


class OciSandboxTests(unittest.TestCase):
    def test_committed_candidate_is_semantic_and_fails_closed(self) -> None:
        profile = OciSandboxProfile.load(PROFILE_PATH)

        self.assertFalse(profile.execution_authorized)
        self.assertIn("pinned_runtime_image", profile.unresolved_gates)
        with self.assertRaisesRegex(PermissionError, "not execution-authorized"):
            OciSandboxExec(
                profile,
                Path("/usr/bin/true"),
                digest_value({"engine": "test"}),
            )

    def test_authorized_profile_builds_an_exact_restricted_invocation(self) -> None:
        profile = self._registered_profile()
        sandbox = OciSandboxExec(
            profile,
            Path("/usr/bin/true"),
            digest_value({"engine": "test"}),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace = root / "workspace"
            state = root / "state"
            broker = root / "broker"
            peer_broker = root / "peer-broker"
            for path in (workspace, state, broker, peer_broker):
                path.mkdir()
            socket_path = broker / "model.sock"
            peer_socket_path = peer_broker / "peer.sock"
            socket_path.touch()
            peer_socket_path.touch()
            with patch("agent_collab_evals.sandbox.stat.S_ISSOCK", return_value=True):
                context = SandboxLaunchContext(
                    workspace_root=workspace,
                    runtime_state_root=state,
                    runtime_assets_root=REPOSITORY_ROOT / "scripts/runtime",
                    model_endpoint=profile.container_model_endpoint,
                    broker_socket=socket_path,
                    peer_endpoint=profile.container_peer_endpoint,
                    peer_broker_socket=peer_socket_path,
                )
                environment = {
                    "PATH": "/host/path",
                    "LANG": "C.UTF-8",
                    "HOME": str(state / "home"),
                    "TMPDIR": str(state / "tmp"),
                    "XDG_DATA_HOME": str(state / "xdg/data"),
                    "XDG_CONFIG_HOME": str(state / "xdg/config"),
                    "XDG_CACHE_HOME": str(state / "xdg/cache"),
                    "XDG_STATE_HOME": str(state / "xdg/state"),
                    "AGENT_COLLAB_PROVIDER_ID": "gateway",
                    "AGENT_COLLAB_MODEL_ID": "model",
                }
                process = sandbox.prepare(
                    (
                        "/host/node",
                        str(
                            REPOSITORY_ROOT
                            / "scripts/runtime/opencode_bridge.mjs"
                        ),
                    ),
                    context,
                    environment,
                )

        command = process.command
        self.assertEqual(command[:2], ("/usr/bin/true", "run"))
        for required in (
            "--read-only",
            "--interactive",
            "none",
            "ALL",
            "no-new-privileges:true",
            "--pids-limit",
            "256",
            "--memory",
            "4g",
        ):
            self.assertIn(required, command)
        self.assertNotIn("/host/path", command)
        self.assertIn(
            "PATH=/opt/agent-collab/runtime/node_modules/.bin:/usr/local/bin:/usr/bin:/bin",
            command,
        )
        self.assertIn("--peer-broker-socket", command)
        self.assertIn(str(peer_socket_path), command)
        self.assertIn("--peer-endpoint", command)
        self.assertIn(profile.container_peer_endpoint, command)
        self.assertIn(
            "agent-collab/opencode-runtime@" + "sha256:" + "1" * 64,
            command,
        )
        self.assertEqual(process.environment["PATH"], "/usr/bin:/bin")

    def test_registered_profile_cannot_retain_an_unresolved_gate(self) -> None:
        with PROFILE_PATH.open("r", encoding="utf-8") as source:
            value = json.load(source)
        value["status"] = "registered"
        value["execution_authorized"] = True
        value["image"]["reference"] = "agent-collab/opencode-runtime"
        value["image"]["digest"] = "sha256:" + "1" * 64
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fully registered"):
                OciSandboxProfile.load(path, repository_root=REPOSITORY_ROOT)

    def test_runtime_image_recipe_matches_profile_identity_and_sources(self) -> None:
        profile = OciSandboxProfile.load(
            PROFILE_PATH, repository_root=REPOSITORY_ROOT
        )
        dockerfile = (
            REPOSITORY_ROOT / "containers/opencode-runtime/Dockerfile"
        ).read_text(encoding="utf-8")

        self.assertIn(f"USER {profile.uid}:{profile.gid}", dockerfile)
        self.assertIn(
            "COPY scripts/runtime/opencode_bridge.mjs ./opencode_bridge.mjs",
            dockerfile,
        )
        self.assertIn(
            "COPY scripts/runtime/session_launcher.py "
            "/opt/agent-collab/bin/session-launcher",
            dockerfile,
        )
        self.assertIn(profile.bridge_executable, dockerfile)
        self.assertIn(profile.launcher_executable, dockerfile)
        self.assertIn(
            "COPY scripts/runtime/peer_tool_server.mjs ./peer_tool_server.mjs",
            dockerfile,
        )

    @staticmethod
    def _registered_profile() -> OciSandboxProfile:
        with PROFILE_PATH.open("r", encoding="utf-8") as source:
            value = json.load(source)
        value["status"] = "registered"
        value["execution_authorized"] = True
        value["image"]["reference"] = "agent-collab/opencode-runtime"
        value["image"]["digest"] = "sha256:" + "1" * 64
        value["unresolved_gates"] = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            return OciSandboxProfile.load(path, repository_root=REPOSITORY_ROOT)


if __name__ == "__main__":
    unittest.main()
