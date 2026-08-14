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


if __name__ == "__main__":
    unittest.main()
