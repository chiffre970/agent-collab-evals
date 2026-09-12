"""Operator command acceptance checks; paid endpoints are never installed."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_collab_evals.cli import main
from agent_collab_evals.canonical import digest_bytes
from agent_collab_evals.pilot_evidence import retain_document
from agent_collab_evals.solo_pilot_command import PilotAborted, _SyntheticCandidateHarness, run_solo_pilot
from agent_collab_evals.adapters.synthetic_pilot_compute import SyntheticPilotTransport


REPOSITORY = Path(__file__).resolve().parents[1]
CONFIG = REPOSITORY / "config/pilots/solo-no-spend-v1.json"


class SoloPilotCommandTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def config(self, **changes):
        document = json.loads(CONFIG.read_bytes())
        document.update(runtime="fake")
        document.update(changes)
        path = self.root / "config.json"
        retain_document(path, document)
        return path

    def test_command_runs_complete_pipeline_and_retains_final_evidence(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["solo-pilot", "--config", str(self.config()), "--state-root", str(self.root), "--run-id", "pilot"]), 0)
        result = json.loads(output.getvalue())
        audit_path = Path(result["audit_path"])
        self.assertEqual(digest_bytes(audit_path.read_bytes()), result["audit_digest"])
        audit = json.loads(audit_path.read_bytes())
        self.assertEqual(audit["status"], "complete")
        self.assertFalse(audit["scoreable"])
        self.assertFalse(audit["used_default"])
        self.assertEqual(audit["synthetic_compute_executions"], 12)
        self.assertEqual(audit["synthetic_compute_seconds"], 84)
        self.assertTrue(audit["budget_reconciliation"]["valid"])
        self.assertEqual(audit["actual_spend_usd_nanos"], 0)
        self.assertEqual(audit["external_compute_executions"], 0)
        for name, digest in audit["evidence_digests"].items():
            self.assertEqual(digest_bytes((audit_path.parent / name).read_bytes()), digest)
        self.assertEqual(digest_bytes(Path(result["selected_candidate_path"]).read_bytes()), audit["selected_artifact_digest"])
        self.assertEqual(json.loads((audit_path.parent / "compute-inventory-seal.json").read_bytes())["inventory_digest"], audit["inventory_digest"])
        with self.assertRaises(FileExistsError):
            run_solo_pilot(self.root / "config.json", self.root, "pilot")
        self.assertEqual(digest_bytes(audit_path.read_bytes()), result["audit_digest"])

    def test_reference_winner_is_hidden_evaluated(self):
        result = run_solo_pilot(self.config(synthetic_candidate_public_ppm=900000), self.root, "reference")
        audit = json.loads(Path(result["audit_path"]).read_bytes())
        self.assertTrue(audit["used_default"])
        self.assertEqual(audit["synthetic_compute_executions"], 12)
        self.assertTrue(audit["hidden_result"]["eligible"])

    def test_live_mode_is_rejected_before_creating_a_run(self):
        with self.assertRaisesRegex(ValueError, "live pilot execution is disabled"):
            run_solo_pilot(self.config(execution_mode="live"), self.root, "live")
        self.assertFalse((self.root / "live").exists())

    def test_agent_failure_aborts_retains_evidence_and_stops_runtime(self):
        original_stop = _SyntheticCandidateHarness.stop
        with (patch.object(_SyntheticCandidateHarness, "deliver", side_effect=RuntimeError("agent failed")),
              patch.object(_SyntheticCandidateHarness, "stop", autospec=True, side_effect=original_stop) as stopped):
            with self.assertRaises(PilotAborted):
                run_solo_pilot(self.config(), self.root, "aborted")
            self.assertEqual(stopped.call_count, 1)
        audit = json.loads((self.root / "aborted/audit.json").read_bytes())
        self.assertEqual(audit["status"], "aborted")
        self.assertEqual(audit["failure"]["type"], "RuntimeError")
        self.assertNotIn("cleanup_failure", audit)
        self.assertFalse(audit["scoreable"])
        self.assertEqual(audit["failure"]["stage"], "agent_job")

    def test_hidden_transport_failure_preserves_partial_accounting(self):
        original_poll = SyntheticPilotTransport.poll

        def poll(transport, *args):
            if transport.recipe["phase"] == "quality":
                raise RuntimeError("synthetic quality transport unavailable")
            return original_poll(transport, *args)

        with patch.object(SyntheticPilotTransport, "poll", autospec=True, side_effect=poll):
            with self.assertRaises(PilotAborted):
                run_solo_pilot(self.config(), self.root, "hidden-abort")
        audit = json.loads((self.root / "hidden-abort/audit.json").read_bytes())
        self.assertEqual(audit["failure"]["stage"], "hidden_evaluation")
        self.assertEqual(audit["status"], "aborted")
        self.assertIn("partial_compute_snapshot", audit)
        self.assertIn("budget_reconciliation", audit)


@unittest.skipUnless(os.environ.get("RUN_MODEL_GATEWAY_INTEGRATION") == "1", "enable local OpenCode integration")
class SoloPilotOpenCodeTests(unittest.TestCase):
    def test_real_opencode_command_completes_all_evaluator_phases_without_spend(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_solo_pilot(CONFIG, Path(directory), "opencode-pilot")
            audit = json.loads(Path(result["audit_path"]).read_bytes())
            self.assertGreater(audit["synthetic_model_calls"], 0)
            self.assertEqual(audit["synthetic_compute_executions"], 12)
            self.assertTrue(audit["budget_reconciliation"]["valid"])
            self.assertEqual(audit["external_model_calls"], 0)
            self.assertEqual(audit["actual_spend_usd_nanos"], 0)


if __name__ == "__main__":
    unittest.main()
