from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
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
        })
        reference = json.loads(job.public_materials["reference_candidate"])
        campaign.validate_candidate_document(reference)
        self.assertEqual(reference["model"]["revision"], campaign.target_model_revision)
        self.assertEqual(json.loads(job.public_materials["submission_schema"])["title"], "Model serving candidate")
        self.assertIn("declarative vLLM", job.mission)
        self.assertIn("candidate_submit", job.mission)
        self.assertIn("pending response", job.mission)


if __name__ == "__main__":
    unittest.main()
