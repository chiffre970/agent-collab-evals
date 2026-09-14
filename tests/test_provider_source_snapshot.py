"""Source refreshes must preserve historical evidence and route selection."""

from contextlib import redirect_stdout
from datetime import UTC, datetime
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.preflight import provider_source_snapshot as snapshot


class ProviderSourceSnapshotTests(unittest.TestCase):
    def test_refresh_writes_versioned_sources_without_changing_policy(self):
        policy_before = snapshot.POLICY_PATH.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            historical = root / "config/provider_qualification/openrouter-deepseek-v4-flash-zdr-20260822.json"
            snapshot._write(historical, b"historical evidence")
            with (patch.object(snapshot, "REPOSITORY_ROOT", root),
                  patch.object(snapshot, "SOURCE_DIRECTORY", root / "evidence/provider_qualification/sources"),
                  patch.object(snapshot, "datetime") as clock,
                  patch.object(snapshot, "_fetch", return_value=b'{"data":[]}'),
                  patch.object(snapshot, "extract_candidate_snapshot", return_value=("fixed-model", [])),
                  patch.dict("os.environ", {"OPENROUTER_API_KEY": "synthetic-no-network"}),
                  patch("sys.argv", ["snapshot", "--execute"]),
                  redirect_stdout(io.StringIO()) as output):
                clock.now.return_value = datetime(2026, 9, 14, 1, 2, 3, tzinfo=UTC)
                self.assertEqual(snapshot.main(), 0)
            result = json.loads(output.getvalue())
            self.assertTrue(result["candidate_snapshot"].endswith("20260914T010203Z.json"))
            self.assertTrue((root / result["candidate_snapshot"]).exists())
            self.assertEqual(historical.read_bytes(), b"historical evidence")
        self.assertEqual(snapshot.POLICY_PATH.read_bytes(), policy_before)

    def test_conflicting_writes_cannot_replace_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            snapshot._write(path, b"first")
            snapshot._write(path, b"first")
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                snapshot._write(path, b"second")
            self.assertEqual(path.read_bytes(), b"first")


if __name__ == "__main__":
    unittest.main()
