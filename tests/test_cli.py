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


if __name__ == "__main__":
    unittest.main()
