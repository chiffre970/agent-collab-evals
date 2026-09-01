from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from agent_collab_evals.adapters.local_measurements import (
    LocalMeasurementBundleStore,
)
from agent_collab_evals.campaigns.model_serving import (
    ModelServingCampaign,
    load_benchmark_plan,
)
from agent_collab_evals.campaigns.serving_benchmark import (
    BenchmarkInvocation,
    build_vllm_benchmark_invocations,
)
from agent_collab_evals.campaigns.serving_performance_calibration import (
    CalibrationBundle,
    PerformanceCalibrationError,
    PerformanceCalibrationPlan,
    derive_performance_calibration,
    load_calibration_bundle,
)
from agent_collab_evals.canonical import digest_bytes, digest_file


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_PATH = REPOSITORY_ROOT / "campaigns/model_serving_v0/campaign.toml"
PLAN_PATH = (
    REPOSITORY_ROOT
    / "config/calibration/model-serving-hidden-performance-v1.json"
)
PROFILE_PATH = (
    REPOSITORY_ROOT / "campaigns/model_serving_v0/workloads/public/profile.toml"
)


class ServingPerformanceCalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.campaign = ModelServingCampaign.load(CAMPAIGN_PATH)
        self.plan = PerformanceCalibrationPlan.load(PLAN_PATH)
        benchmark = load_benchmark_plan(PROFILE_PATH)
        self.invocations = build_vllm_benchmark_invocations(
            benchmark,
            base_url="http://127.0.0.1:8000",
            model_source=self.campaign.target_model_id,
            served_model_name="target-model",
            result_directory=Path("."),
            warmup_requests=1,
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_derivation_requires_three_repetitions_and_is_deterministic(self) -> None:
        bundles = tuple(self._bundle(repetition) for repetition in (1, 2, 3))

        proposal = derive_performance_calibration(
            self.plan,
            PROFILE_PATH,
            bundles,
            model_source=self.campaign.target_model_id,
            reference_candidate_manifest_digest=(
                self.campaign.validate_reference_candidate().manifest_digest
            ),
            prior_slos_ms=self.campaign.scoring_profile().goodput_slos_ms_by_bucket,
        )

        self.assertEqual(
            proposal["status"], "calibration_proposal_not_registered"
        )
        self.assertEqual(
            proposal["study_hidden_bundle_policy"],
            "fresh_seed_after_policy_freeze",
        )
        self.assertEqual(
            proposal["reference_repetition_scalar_ppm"],
            [1_000_000, 1_000_000, 1_000_000],
        )
        rules = {value["id"]: value for value in proposal["buckets"]}
        self.assertEqual(rules["short"]["ttft_slo_ms"], 150)
        self.assertEqual(rules["short"]["tpot_slo_ms"], 15)
        self.assertEqual(rules["long"]["selected_request_rate"], 2)

        changed_environment = (
            bundles[0],
            bundles[1],
            CalibrationBundle(
                repetition=3,
                receipt_digest=bundles[2].receipt_digest,
                provenance_digest="sha256:" + "8" * 64,
                candidate_manifest_digest=bundles[2].candidate_manifest_digest,
                measurement_id=bundles[2].measurement_id,
                raw_documents=bundles[2].raw_documents,
            ),
        )
        with self.assertRaisesRegex(
            PerformanceCalibrationError, "environments differ"
        ):
            derive_performance_calibration(
                self.plan,
                PROFILE_PATH,
                changed_environment,
                model_source=self.campaign.target_model_id,
                reference_candidate_manifest_digest=(
                    self.campaign.validate_reference_candidate().manifest_digest
                ),
                prior_slos_ms=(
                    self.campaign.scoring_profile().goodput_slos_ms_by_bucket
                ),
            )

        with self.assertRaisesRegex(
            PerformanceCalibrationError, "repetitions 1 through 3"
        ):
            derive_performance_calibration(
                self.plan,
                PROFILE_PATH,
                bundles[:2],
                model_source=self.campaign.target_model_id,
                reference_candidate_manifest_digest=(
                    self.campaign.validate_reference_candidate().manifest_digest
                ),
                prior_slos_ms=(
                    self.campaign.scoring_profile().goodput_slos_ms_by_bucket
                ),
            )

    def test_bundle_loader_rechecks_local_raw_digests(self) -> None:
        measurement_id = "calibration-reference"
        normalized = {
            "repetition": 1,
            "campaign_manifest_digest": "sha256:" + "a" * 64,
            "candidate_manifest_digest": "sha256:" + "b" * 64,
            "performance_profile_digest": digest_file(PROFILE_PATH),
            "failure": None,
            "parse_errors": [],
            "environment_errors": [],
            "performance_score": {"eligible": False},
            "durable_evidence": {},
            "platform_build": {"git_commit": "d" * 40},
            "remote_receipt": {
                "model_id": "Qwen/Qwen3-4B",
                "model_revision": "e" * 40,
                "vllm_version": "0.21.0",
                "environment": {"package_set_digest": "sha256:" + "f" * 64},
                "gpu_before": {
                    "name": "NVIDIA L4",
                    "memory_mib": "23034",
                    "driver_version": "580.95.05",
                    "power_limit_watts": "72.00",
                },
                "gpu_after": {
                    "name": "NVIDIA L4",
                    "memory_mib": "23034",
                    "driver_version": "580.95.05",
                    "power_limit_watts": "72.00",
                },
            },
        }
        raw = {"short-1rps.json": b"{}"}
        normalized["durable_evidence"]["raw_digests"] = {
            name: digest_bytes(content) for name, content in raw.items()
        }
        destination = LocalMeasurementBundleStore(self.root).save(
            measurement_id, 1, normalized, raw
        )

        loaded = load_calibration_bundle(
            destination / "receipt.json",
            performance_profile_digest=digest_file(PROFILE_PATH),
        )

        self.assertEqual(loaded.repetition, 1)
        self.assertEqual(loaded.raw_documents, raw)

    def _bundle(self, repetition: int) -> CalibrationBundle:
        documents = {
            invocation.result_file.name: json.dumps(
                _result_document(invocation, self.campaign.target_model_id),
                separators=(",", ":"),
            ).encode("utf-8")
            for invocation in self.invocations
        }
        return CalibrationBundle(
            repetition=repetition,
            receipt_digest=digest_bytes(f"receipt-{repetition}".encode()),
            provenance_digest="sha256:" + "9" * 64,
            candidate_manifest_digest=(
                self.campaign.validate_reference_candidate().manifest_digest
            ),
            measurement_id=f"reference-{repetition}",
            raw_documents=documents,
        )


def _result_document(
    invocation: BenchmarkInvocation, model_source: str
) -> dict[str, object]:
    duration = Decimal(invocation.num_prompts) / Decimal(invocation.request_rate)
    ttft = Decimal("0.1")
    tpot = Decimal("0.01")
    intervals = [float(tpot)] * (invocation.output_tokens - 1)
    e2e_ms = float((ttft + tpot * (invocation.output_tokens - 1)) * 1000)
    total_input = invocation.input_tokens * invocation.num_prompts
    total_output = invocation.output_tokens * invocation.num_prompts
    result: dict[str, object] = {
        "date": "20260901-000000",
        "endpoint_type": "openai-chat",
        "backend": "openai-chat",
        "label": "openai-chat",
        "model_id": model_source,
        "tokenizer_id": model_source,
        "num_prompts": invocation.num_prompts,
        "request_rate": invocation.request_rate,
        "burstiness": 1.0,
        "max_concurrency": None,
        "duration": float(duration),
        "completed": invocation.num_prompts,
        "failed": 0,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "request_throughput": float(Decimal(invocation.num_prompts) / duration),
        "request_goodput": float(Decimal(invocation.num_prompts) / duration),
        "output_throughput": float(Decimal(total_output) / duration),
        "total_token_throughput": float(
            Decimal(total_input + total_output) / duration
        ),
        "input_lens": [invocation.input_tokens] * invocation.num_prompts,
        "output_lens": [invocation.output_tokens] * invocation.num_prompts,
        "ttfts": [float(ttft)] * invocation.num_prompts,
        "itls": [intervals] * invocation.num_prompts,
        "start_times": [1.0] * invocation.num_prompts,
        "generated_texts": ["x"] * invocation.num_prompts,
        "errors": [""] * invocation.num_prompts,
        "max_output_tokens_per_s": 128.0,
        "max_concurrent_requests": 2,
        "rtfx": 0.0,
    }
    metrics = {"ttft": 100.0, "tpot": 10.0, "itl": 10.0, "e2el": e2e_ms}
    for metric, value in metrics.items():
        result[f"mean_{metric}_ms"] = value
        result[f"median_{metric}_ms"] = value
        result[f"std_{metric}_ms"] = 0.0
        result[f"p50_{metric}_ms"] = value
        result[f"p95_{metric}_ms"] = value
        result[f"p99_{metric}_ms"] = value
    return result


if __name__ == "__main__":
    unittest.main()
