from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_collab_evals.candidate_rehearsal import create_synthetic_candidate_services, run_candidate_rehearsal
from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.domain import AgentIdentity, SessionHandle


REPOSITORY = Path(__file__).resolve().parents[1]
CAMPAIGN = REPOSITORY / "campaigns/model_serving_v0/campaign.toml"


class CandidateToolsTests(unittest.TestCase):
    def test_candidate_identity_survives_service_reconstruction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            campaign = ModelServingCampaign.load(CAMPAIGN)
            candidate = json.loads((campaign.root / "candidates/vllm-stream-interval-10.json").read_bytes())
            actor = AgentIdentity("candidate-restart", 0)
            args = {"candidate": candidate, "idempotency_key": "first"}
            first = create_synthetic_candidate_services(root, campaign, actor.campaign_run_id)
            session = first.sessions.bind(actor, SessionHandle("original"))
            receipt = first.tools.call(session, "submit", args)
            second = create_synthetic_candidate_services(root, campaign, actor.campaign_run_id)
            restored = second.sessions.bind(actor, SessionHandle("restored"))
            self.assertEqual(second.tools.call(restored, "submit", args), receipt)
            self.assertEqual(len(second.compute.snapshot(actor.campaign_run_id).reservations), 1)

    def test_submit_retry_evaluate_and_controller_owned_release(self):
        with tempfile.TemporaryDirectory() as directory:
            campaign = ModelServingCampaign.load(CAMPAIGN)
            services = create_synthetic_candidate_services(Path(directory), campaign, "candidate-test")
            actor = AgentIdentity("candidate-test", 0)
            session = services.sessions.bind(actor, SessionHandle("actor-session"))
            candidate = json.loads((campaign.root / "candidates/vllm-stream-interval-10.json").read_bytes())
            args = {"candidate": candidate, "idempotency_key": "first"}
            receipt = services.tools.call(session, "submit", args)
            self.assertEqual(services.tools.call(session, "submit", args), receipt)
            request = {"receipt": receipt["receipt"]}
            self.assertEqual(services.tools.call(session, "evaluate", request), {"status": "pending", "result": None})
            services.compute.release_visible_results(actor.campaign_run_id, actor.actor_id)
            result = services.tools.call(session, "result", request)
            self.assertEqual(result["status"], "released")
            self.assertEqual(result["result"]["criterion_units"], 1100000)
            self.assertEqual(len(services.compute.snapshot(actor.campaign_run_id).reservations), 1)


@unittest.skipUnless(os.environ.get("RUN_MODEL_GATEWAY_INTEGRATION") == "1", "enable local OpenCode integration")
class CandidateRehearsalIntegrationTests(unittest.TestCase):
    def test_real_opencode_candidate_session_survives_service_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = run_candidate_rehearsal(
                CAMPAIGN, Path(directory), "solo-candidate-restart", restart_runtime=True,
            )
            self.assertFalse(audit["scoreable"])
            self.assertFalse(audit["live_evaluation_authorized"])
            self.assertEqual(audit["external_model_calls"], 0)
            self.assertEqual(audit["external_compute_executions"], 0)
            self.assertEqual(audit["tools_called"], ["candidate_submit", "candidate_evaluate", "candidate_result"])
            self.assertTrue(audit["budget_reconciliation"]["valid"])
            restart = audit["restart_evidence"]
            self.assertTrue(restart["same_session"])
            self.assertTrue(restart["capability_rotated"])
            self.assertTrue(restart["compute_unchanged"])
            self.assertEqual(restart["replayed_model_calls"], 0)
            root = Path(directory) / "solo-candidate-restart"
            compute = json.loads((root / "compute-snapshot.json").read_bytes())
            self.assertEqual(len(compute["reservations"]), 2)  # One public and one hidden.
            snapshot = json.loads((root / "runtime-snapshot.json").read_bytes())
            session = snapshot["payload"]["sessions"][0]
            messages = json.loads(session["checkpoint"]["reconciliation"]["sessions"][0]["messages_json"])
            results = [
                part for message in messages for part in message["parts"]
                if part["type"] == "tool" and part["tool"] == "candidate_result"
            ]
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["state"]["status"], "completed")
            self.assertIn("1100000", results[0]["state"]["output"])

    def test_real_opencode_submits_evaluates_reads_and_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = run_candidate_rehearsal(CAMPAIGN, Path(directory), "solo-candidate-test")
            self.assertFalse(audit["scoreable"])
            self.assertFalse(audit["used_default"])
            self.assertEqual(audit["external_model_calls"], 0)
            self.assertEqual(audit["external_compute_executions"], 0)
            self.assertEqual(audit["tools_called"], ["candidate_submit", "candidate_evaluate", "candidate_result"])
            self.assertTrue(audit["budget_reconciliation"]["valid"])
            snapshot_path = Path(directory) / "solo-candidate-test/runtime-snapshot.json"
            snapshot = json.loads(snapshot_path.read_bytes())
            messages = json.loads(snapshot["payload"]["sessions"][0]["checkpoint"]["reconciliation"]["sessions"][0]["messages_json"])
            results = [
                part for message in messages for part in message["parts"]
                if part["type"] == "tool" and part["tool"] == "candidate_result"
            ]
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["state"]["status"], "completed")
            self.assertIn("1100000", results[0]["state"]["output"])


if __name__ == "__main__":
    unittest.main()
