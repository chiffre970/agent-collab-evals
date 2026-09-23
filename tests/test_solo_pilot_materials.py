from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path

from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.evaluation import EvaluationResult
from agent_collab_evals.solo_pilot_materials import materialize_solo_pilot


class SoloPilotMaterialTests(unittest.TestCase):
    def test_public_brief_is_self_contained_and_reproducible(self):
        campaign = ModelServingCampaign.load(Path(__file__).resolve().parents[1] / "campaigns/model_serving_v0/campaign.toml")
        material = materialize_solo_pilot(campaign, 1729)
        self.assertEqual(material, materialize_solo_pilot(campaign, 1729))
        self.assertNotEqual(material.material_digest, materialize_solo_pilot(campaign, 1730).material_digest)
        job = material.jobs[0]
        self.assertEqual(set(job.public_materials), {
            "submission_schema", "correctness_workload", "benchmark_profile",
            "reference_candidate", "measurement_profile", "scoring_profile",
            "pilot_context",
        })
        reference = json.loads(job.public_materials["reference_candidate"])
        campaign.validate_candidate_document(reference)
        self.assertEqual(reference["model"]["revision"], campaign.target_model_revision)
        self.assertEqual(json.loads(job.public_materials["submission_schema"])["title"], "Model serving candidate")
        self.assertIn("declarative vLLM", job.mission)
        self.assertIn("candidate_submit", job.mission)
        self.assertIn("pending response", " ".join(job.mission.split()))
        self.assertIn("one admitted candidate", job.mission)
        self.assertIn("cannot revise", job.mission)
        self.assertNotIn("You may change the inference engine", job.mission)
        self.assertNotIn("Quantization is allowed", job.mission)
        self.assertIsNone(json.loads(job.public_materials["pilot_context"])["reference_public_result"])

    def test_current_reference_and_limits_are_public_and_digest_bound(self):
        campaign = ModelServingCampaign.load(Path(__file__).resolve().parents[1] / "campaigns/model_serving_v0/campaign.toml")
        result = EvaluationResult(True, 1_002_000, (), "sha256:" + "a" * 64,
                                  {"private_diagnostic": "must not enter prompt"})
        arguments = dict(reference_result=result, model_limit_usd_nanos=2_900_000_000,
                         public_compute_seconds=1800)
        material = materialize_solo_pilot(campaign, 1729, **arguments)
        self.assertEqual(material, materialize_solo_pilot(campaign, 1729, **arguments))
        context = json.loads(material.jobs[0].public_materials["pilot_context"])
        self.assertEqual(context["reference_public_result"]["criterion_units"], 1_002_000)
        self.assertEqual(context["model_budget_usd_nanos"], 2_900_000_000)
        self.assertEqual(context["public_candidate_compute_allowance_seconds"], 1800)
        self.assertEqual(context["candidate_limit"], 1)
        self.assertFalse(context["revision_after_feedback"])
        self.assertNotIn("private_diagnostic", str(material))
        for changed in (
            {"reference_result": replace(result, criterion_units=1_003_000)},
            {"model_limit_usd_nanos": 1_000_000_000},
            {"public_compute_seconds": 900},
        ):
            with self.subTest(changed=changed):
                self.assertNotEqual(material.material_digest,
                    materialize_solo_pilot(campaign, 1729, **(arguments | changed)).material_digest)
        for invalid in (0, -1, True, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                materialize_solo_pilot(campaign, 1729, model_limit_usd_nanos=invalid)


if __name__ == "__main__":
    unittest.main()
