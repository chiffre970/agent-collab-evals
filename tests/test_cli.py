from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agent_collab_evals.cli import main


class CliTests(unittest.TestCase):
    def test_fake_solo_runs_through_persisted_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(
                    [
                        "fake-solo",
                        "--state-root",
                        directory,
                        "--run-id",
                        "cli-test-run",
                    ]
                )

            report = json.loads(output.getvalue())
            self.assertEqual(result, 0)
            self.assertTrue(report["ok"])
            self.assertTrue(report["durable_resume_exercised"])
            self.assertEqual(report["delivered_job_ids"], ["optimize-serving"])
            self.assertTrue(
                (Path(directory) / "snapshots/cli-test-run/snapshot.json").is_file()
            )

    def test_fake_candidate_lifecycle_selects_and_seals_without_gpu(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(
                    [
                        "fake-candidate-lifecycle",
                        "--state-root",
                        directory,
                        "--run-id",
                        "cli-candidate-test",
                    ]
                )

            report = json.loads(output.getvalue())
            self.assertEqual(result, 0)
            self.assertTrue(report["ok"])
            self.assertFalse(report["gpu_spend"])
            self.assertEqual(report["visible_criterion_units"], 1_001_872)
            self.assertEqual(report["hidden_criterion_units"], 999_202)
            self.assertTrue(report["selection_digest"].startswith("sha256:"))
            self.assertTrue(report["selection_receipt"].startswith("selection-"))
            self.assertTrue(report["storage_seal_digest"].startswith("sha256:"))

    def test_modal_compute_dispatch_requires_explicit_spend_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RuntimeError, "allow-gpu-spend"):
                main(
                    [
                        "modal-compute-development",
                        "--dispatch",
                        "--state-root",
                        temporary,
                    ]
                )

    def test_rehearse_study_runs_complete_four_condition_block_without_spend(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(
                    [
                        "rehearse-study",
                        "--state-root",
                        directory,
                        "--rehearsal-id",
                        "cli-rehearsal",
                    ]
                )

            report = json.loads(output.getvalue())
            self.assertEqual(result, 0)
            self.assertTrue(report["ok"])
            self.assertEqual(report["execution_class"], "no_spend")
            self.assertFalse(report["scoreable"])
            self.assertFalse(report["treatment_surfaces_exercised"])
            self.assertEqual(report["block_count"], 1)
            self.assertEqual(report["run_count"], 4)
            self.assertEqual(report["model_calls"], 0)
            self.assertEqual(report["compute_executions"], 0)
            self.assertTrue(Path(report["audit_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
