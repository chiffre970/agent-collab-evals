"""Host staging and feedback release without model or GPU calls."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_collab_evals.candidate_rehearsal import create_synthetic_candidate_services
from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.domain import AgentIdentity, SessionHandle
from agent_collab_evals.evaluation import EvaluationInProgress
from agent_collab_evals.solo_evaluation_handoff import SoloEvaluationHandoff


class SoloEvaluationHandoffTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.campaign = ModelServingCampaign.load(Path(__file__).resolve().parents[1] / "campaigns/model_serving_v0/campaign.toml")
        self.actor = AgentIdentity("staged-solo", 0)
        self.services = self.reopen()
        self.session = self.services.sessions.bind(self.actor, SessionHandle("solo-session"))
        self.handoff = SoloEvaluationHandoff(self.services.submissions, self.services.compute, self.actor.campaign_run_id)

    def reopen(self):
        return create_synthetic_candidate_services(self.root, self.campaign, self.actor.campaign_run_id, host_evaluation=True)

    def admit(self):
        candidate = json.loads((self.campaign.root / "candidates/vllm-stream-interval-10.json").read_bytes())
        return self.services.tools.call(self.session, "submit", {"candidate": candidate, "idempotency_key": "first"})

    def test_deferred_tools_do_not_execute_and_feedback_survives_restart(self):
        admitted = self.admit()
        arguments = {"receipt": admitted["receipt"]}
        with patch.object(self.services.submissions, "evaluate_visible", side_effect=AssertionError("agent dispatched evaluation")):
            for _ in range(3):
                self.assertEqual(self.services.tools.call(self.session, "evaluate", arguments), {"status": "pending", "result": None})
        prepared = self.handoff.prepare()
        first_job = self.handoff.evaluate()
        services = self.reopen()
        handoff = SoloEvaluationHandoff(services.submissions, services.compute, self.actor.campaign_run_id)
        self.assertEqual(handoff.prepare().candidate, prepared.candidate)
        self.assertEqual(handoff.evaluate(), first_job)
        session = services.sessions.bind(self.actor, SessionHandle("restored"))
        self.assertEqual(services.tools.call(session, "result", arguments)["result"]["criterion_units"], 1100000)
        self.assertEqual(len(services.compute.snapshot(self.actor.campaign_run_id).reservations), 1)

    def test_nonterminal_work_does_not_release_or_close_admissions(self):
        self.admit()
        with patch.object(self.services.evaluator, "visible_evaluate", side_effect=EvaluationInProgress("pending")):
            with self.assertRaises(EvaluationInProgress):
                self.handoff.evaluate()
        self.assertFalse(self.services.compute.is_visible_result_released(self.actor.campaign_run_id, self.actor.actor_id))
        self.admit()  # Idempotent admission still succeeds while evaluation waits.
        self.handoff.evaluate()

    def test_terminal_failure_never_releases_feedback(self):
        self.admit()
        with patch.object(self.services.evaluator, "visible_evaluate", side_effect=RuntimeError("evaluator failed")):
            with self.assertRaisesRegex(RuntimeError, "did not complete"):
                self.handoff.evaluate()
        self.assertFalse(self.services.compute.is_visible_result_released(self.actor.campaign_run_id, self.actor.actor_id))

    def test_missing_candidate_has_no_dispatch(self):
        with patch.object(self.services.evaluator, "visible_evaluate", side_effect=AssertionError("unexpected dispatch")):
            with self.assertRaisesRegex(RuntimeError, "exactly one admitted"):
                self.handoff.evaluate()


if __name__ == "__main__":
    unittest.main()
