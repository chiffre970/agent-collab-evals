from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent_collab_evals.campaigns.model_serving import (
    ManifestValidationError,
    ModelServingCampaign,
)
from agent_collab_evals.campaigns.serving_benchmark import (
    build_vllm_benchmark_invocations,
)


CAMPAIGN_PATH = Path("campaigns/model_serving_v0/campaign.toml")
MODEL_REVISION = "1cfa9a7208912126459214e8b04321603b3df60c"


class ModelServingCampaignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.campaign = ModelServingCampaign.load(CAMPAIGN_PATH)
        candidate_path = self.campaign.root / "reference/candidate.json"
        self.candidate = json.loads(candidate_path.read_text(encoding="utf-8"))

    def test_campaign_and_reference_are_pinned_and_valid(self) -> None:
        descriptor = self.campaign.validate_candidate_document(self.candidate)

        self.assertEqual(self.campaign.target_model_revision, MODEL_REVISION)
        self.assertEqual(descriptor.candidate_id, "stock-vllm-0.21.0")
        self.assertTrue(self.campaign.manifest_digest.startswith("sha256:"))
        self.assertEqual(len(self.campaign.benchmark_buckets()), 3)

    def test_benchmark_plan_expands_to_nine_argv_only_points(self) -> None:
        invocations = build_vllm_benchmark_invocations(
            self.campaign.benchmark_plan(),
            base_url="http://127.0.0.1:8000",
            model_source="/models/pinned-qwen",
            served_model_name="target-model",
            result_directory=Path("/results"),
        )

        self.assertEqual(len(invocations), 9)
        first = invocations[0]
        self.assertEqual(first.bucket_id, "short")
        self.assertEqual(first.request_rate, 1)
        self.assertEqual(first.argv[:3], ("vllm", "bench", "serve"))
        self.assertIn("/models/pinned-qwen", first.argv)
        self.assertIn("--save-detailed", first.argv)
        self.assertEqual(first.result_file, Path("/results/short-1rps.json"))

    def test_benchmark_goodput_slos_are_explicit(self) -> None:
        invocation = build_vllm_benchmark_invocations(
            self.campaign.benchmark_plan(),
            base_url="https://evaluator.invalid",
            model_source="/models/pinned-qwen",
            served_model_name="target-model",
            result_directory=Path("results"),
            goodput_slos_ms={"ttft": 500, "tpot": 50},
        )[0]

        index = invocation.argv.index("--goodput")
        self.assertEqual(invocation.argv[index + 1 : index + 3], ("tpot:50", "ttft:500"))

    def test_materialization_is_seeded_and_deterministic(self) -> None:
        first = self.campaign.materialize(1729)
        repeated = self.campaign.materialize(1729)
        changed = self.campaign.materialize(1730)

        self.assertEqual(first, repeated)
        self.assertNotEqual(first.material_digest, changed.material_digest)
        self.assertEqual(first.jobs[0].materials_digest, first.material_digest)

    def test_campaign_manifest_rejects_unknown_fields_and_float_money(self) -> None:
        unknown = copy.deepcopy(self.campaign.raw)
        unknown["unregistered"] = True
        with self.assertRaisesRegex(ManifestValidationError, "keys differ"):
            ModelServingCampaign._validate_manifest(unknown)

        float_money = copy.deepcopy(self.campaign.raw)
        float_money["development_limits"]["modal_usd_cap"] = 5.0
        with self.assertRaisesRegex(ManifestValidationError, "currency string"):
            ModelServingCampaign._validate_manifest(float_money)

    def test_candidate_cannot_change_fixed_model_or_hardware(self) -> None:
        changed_model = copy.deepcopy(self.candidate)
        changed_model["model"]["id"] = "another/model"
        with self.assertRaisesRegex(
            ManifestValidationError, "changes the target model"
        ):
            self.campaign.validate_candidate_document(changed_model)

        changed_gpu = copy.deepcopy(self.candidate)
        changed_gpu["resource"]["gpu_count"] = 2
        with self.assertRaisesRegex(ManifestValidationError, "GPU count"):
            self.campaign.validate_candidate_document(changed_gpu)

    def test_candidate_artifact_may_be_outside_campaign_pack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(json.dumps(self.candidate), encoding="utf-8")

            descriptor = self.campaign.validate_candidate(path)

        self.assertEqual(descriptor.candidate_id, "stock-vllm-0.21.0")

    def test_unknown_candidate_fields_fail_closed(self) -> None:
        candidate = copy.deepcopy(self.candidate)
        candidate["unregistered"] = True
        with self.assertRaisesRegex(ManifestValidationError, "keys differ"):
            self.campaign.validate_candidate_document(candidate)

    def test_duplicate_candidate_keys_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(
                '{"schema_version":"first","schema_version":"second"}',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ManifestValidationError, "unambiguous"):
                self.campaign.validate_candidate(path)


if __name__ == "__main__":
    unittest.main()
