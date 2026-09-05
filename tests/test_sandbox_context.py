from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_collab_evals.sandbox import SandboxLaunchContext, SandboxedProcess


class SandboxLaunchContextTests(unittest.TestCase):
    def test_accepts_existing_disjoint_server_derived_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace = root / "workspace"
            state = root / "state"
            assets = root / "assets"
            for path in (workspace, state, assets):
                path.mkdir()

            context = SandboxLaunchContext(
                workspace,
                state,
                assets,
                "http://127.0.0.1:9000/v1",
            )

            self.assertEqual(context.workspace_root, workspace)

    def test_rejects_nested_workspace_and_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace = root / "workspace"
            state = workspace / "state"
            assets = root / "assets"
            state.mkdir(parents=True)
            assets.mkdir()

            with self.assertRaisesRegex(ValueError, "must be disjoint"):
                SandboxLaunchContext(
                    workspace,
                    state,
                    assets,
                    "http://127.0.0.1:9000/v1",
                )

    def test_sandboxed_process_copies_its_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = {"SAFE": "value"}
            process = SandboxedProcess(
                ("/usr/bin/true",),
                Path(directory).resolve(),
                environment,
            )
            environment["SAFE"] = "changed"

            self.assertEqual(process.environment, {"SAFE": "value"})

    def test_assets_cannot_contain_workspace_or_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace, state = root / "workspace", root / "state"
            workspace.mkdir()
            state.mkdir()
            with self.assertRaisesRegex(ValueError, "must be disjoint"):
                SandboxLaunchContext(workspace, state, root, "http://localhost/v1")


if __name__ == "__main__":
    unittest.main()
