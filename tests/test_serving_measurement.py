from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from agent_collab_evals.adapters.local_measurements import (
    LocalMeasurementBundleStore,
    MeasurementBundleError,
)
from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.campaigns.serving_benchmark import (
    BenchmarkInvocation,
    build_vllm_benchmark_invocations,
)
from agent_collab_evals.campaigns.serving_measurement import (
    GoodputReplay,
    MeasurementValidationError,
    parse_vllm_benchmark_result,
    replay_vllm_goodput,
)
from agent_collab_evals.campaigns.serving_scoring import (
    score_repetition,
    summarize_candidate,
)


CAMPAIGN_PATH = Path("campaigns/model_serving_v0/campaign.toml")
MODEL_SOURCE = "/models/pinned-qwen"


def _result_document(
    invocation: BenchmarkInvocation, *, failed: int = 0, direct_goodput: bool = False
) -> dict[str, object]:
    completed = invocation.num_prompts - failed
    duration = 2.0
    errors = [""] * completed + ["backend failure"] * failed
    input_lens = [invocation.input_tokens] * invocation.num_prompts
    output_lens = [invocation.output_tokens] * completed + [0] * failed
    total_input = invocation.input_tokens * completed
    total_output = invocation.output_tokens * completed
    ttft_seconds = Decimal("0.1")
    tpot_seconds = Decimal("0.01")
    decode_intervals = [float(tpot_seconds)] * (invocation.output_tokens - 1)
    itls = [decode_intervals] * completed + [[] for _ in range(failed)]
    ttfts = [float(ttft_seconds)] * completed + [0.0] * failed
    e2el_ms = float(
        (ttft_seconds + tpot_seconds * (invocation.output_tokens - 1)) * 1000
    )
    result: dict[str, object] = {
        "date": "20260814-120000",
        "endpoint_type": "openai-chat",
        "backend": "openai-chat",
        "label": "openai-chat",
        "model_id": MODEL_SOURCE,
        "tokenizer_id": MODEL_SOURCE,
        "num_prompts": invocation.num_prompts,
        "request_rate": invocation.request_rate,
        "burstiness": 1.0,
        "max_concurrency": None,
        "duration": duration,
        "completed": completed,
        "failed": failed,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "request_throughput": completed / duration,
        "request_goodput": completed / duration if direct_goodput else None,
        "output_throughput": total_output / duration,
        "total_token_throughput": (total_input + total_output) / duration,
        "input_lens": input_lens,
        "output_lens": output_lens,
        "ttfts": ttfts,
        "itls": itls,
        "start_times": [1.0] * invocation.num_prompts,
        "generated_texts": ["x"] * completed + [""] * failed,
        "errors": errors,
        "max_output_tokens_per_s": 128.0,
        "max_concurrent_requests": 2,
        "rtfx": 0.0,
    }
    metric_values = {"ttft": 100.0, "tpot": 10.0, "itl": 10.0, "e2el": e2el_ms}
    for metric, value in metric_values.items():
        result[f"mean_{metric}_ms"] = value
        result[f"median_{metric}_ms"] = value
        result[f"std_{metric}_ms"] = 0.0
        result[f"p50_{metric}_ms"] = value
        result[f"p95_{metric}_ms"] = value
        result[f"p99_{metric}_ms"] = value
    return result


class ServingMeasurementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.campaign = ModelServingCampaign.load(CAMPAIGN_PATH)
        self.invocation = build_vllm_benchmark_invocations(
            self.campaign.benchmark_plan(),
            base_url="http://127.0.0.1:8000",
            model_source=MODEL_SOURCE,
            served_model_name="target-model",
            result_directory=Path("/results"),
            warmup_requests=self.campaign.measurement_profile().point_warmups,
        )[0]

    def _raw(self, *, failed: int = 0, direct_goodput: bool = False) -> bytes:
        return json.dumps(
            _result_document(
                self.invocation,
                failed=failed,
                direct_goodput=direct_goodput,
            ),
            separators=(",", ":"),
        ).encode("utf-8")

    def test_vllm_result_is_validated_and_normalized_to_integer_units(self) -> None:
        raw = self._raw()
        result = parse_vllm_benchmark_result(
            raw,
            invocation=self.invocation,
            model_source=MODEL_SOURCE,
            metric_percentiles=self.campaign.benchmark_plan().metric_percentiles,
        )

        self.assertTrue(result.valid)
        self.assertEqual(result.duration_us, 2_000_000)
        self.assertEqual(
            result.request_throughput_micro_rps,
            self.invocation.num_prompts * 500_000,
        )
        self.assertEqual(result.latency_us["p99_e2el"], 1_370_000)
        self.assertTrue(result.raw_digest.startswith("sha256:"))

    def test_failed_requests_are_preserved_as_an_invalid_point(self) -> None:
        result = parse_vllm_benchmark_result(
            self._raw(failed=1),
            invocation=self.invocation,
            model_source=MODEL_SOURCE,
            metric_percentiles=self.campaign.benchmark_plan().metric_percentiles,
        )

        self.assertFalse(result.valid)
        self.assertEqual(result.failed, 1)

    def test_identity_mismatch_and_nonfinite_numbers_fail_closed(self) -> None:
        wrong_rate = _result_document(self.invocation)
        wrong_rate["request_rate"] = self.invocation.request_rate + 1
        with self.assertRaisesRegex(MeasurementValidationError, "request_rate"):
            parse_vllm_benchmark_result(
                json.dumps(wrong_rate).encode(),
                invocation=self.invocation,
                model_source=MODEL_SOURCE,
                metric_percentiles=(50, 95, 99),
            )

        raw = self._raw().replace(b'"duration":2.0', b'"duration":NaN')
        with self.assertRaisesRegex(MeasurementValidationError, "non-finite"):
            parse_vllm_benchmark_result(
                raw,
                invocation=self.invocation,
                model_source=MODEL_SOURCE,
                metric_percentiles=(50, 95, 99),
            )

    def test_goodput_replay_distinguishes_legacy_and_direct_evidence(self) -> None:
        arguments = {
            "invocation": self.invocation,
            "model_source": MODEL_SOURCE,
            "goodput_slos_ms": {"ttft": 150, "tpot": 45},
            "legacy_classification_guard_us": 100,
            "aggregate_tolerance_us": 100,
        }
        legacy = replay_vllm_goodput(self._raw(), **arguments)
        direct = replay_vllm_goodput(
            self._raw(direct_goodput=True), **arguments
        )

        self.assertEqual(legacy.source, "guarded_saved_detail_replay")
        self.assertEqual(direct.source, "vllm_direct")
        self.assertEqual(direct.good_completed, self.invocation.num_prompts)
        self.assertEqual(direct.joint_attainment_ppm, 1_000_000)
        self.assertEqual(direct.goodput_micro_rps, legacy.goodput_micro_rps)

        with self.assertRaisesRegex(MeasurementValidationError, "ambiguous"):
            replay_vllm_goodput(
                self._raw(),
                **{
                    **arguments,
                    "goodput_slos_ms": {"ttft": 100, "tpot": 45},
                },
            )

    def test_cross_bucket_score_is_balanced_and_uses_conservative_bound(self) -> None:
        profile = self.campaign.scoring_profile()
        plan = self.campaign.benchmark_plan()

        def make_replays(multiplier_ppm: int) -> list[GoodputReplay]:
            replays: list[GoodputReplay] = []
            for bucket in plan.buckets:
                rule = profile.bucket_rules[bucket.bucket_id]
                for rate in bucket.request_rates:
                    goodput = (
                        rule.reference_goodput_micro_rps * multiplier_ppm
                    ) // 1_000_000
                    replays.append(
                        GoodputReplay(
                            bucket_id=bucket.bucket_id,
                            request_rate=rate,
                            expected_prompts=bucket.num_prompts,
                            completed=bucket.num_prompts,
                            failed=0,
                            good_completed=bucket.num_prompts,
                            joint_attainment_ppm=1_000_000,
                            goodput_micro_rps=goodput,
                            source="vllm_direct",
                            minimum_slo_margin_us=1_000,
                            aggregate_reconstruction_error_us=0,
                            raw_digest="sha256:" + "0" * 64,
                        )
                    )
            return replays

        reference_level = tuple(
            score_repetition(
                profile,
                plan,
                make_replays(1_000_000),
                repetition=repetition,
                role="candidate",
            )
            for repetition in range(1, 4)
        )
        reference_summary = summarize_candidate(profile, reference_level)
        self.assertEqual(reference_summary.primary_scalar_ppm, 1_000_000)
        self.assertFalse(reference_summary.clears_improvement_bound)

        improved = tuple(
            score_repetition(
                profile,
                plan,
                make_replays(1_020_000),
                repetition=repetition,
                role="candidate",
            )
            for repetition in range(1, 4)
        )
        improved_summary = summarize_candidate(profile, improved)
        self.assertTrue(improved_summary.clears_improvement_bound)
        self.assertGreater(
            improved_summary.conservative_improvement_lower_bound_ppm, 0
        )

    def test_atomic_bundle_store_preserves_verbatim_raw_results(self) -> None:
        raw = self._raw()
        point = parse_vllm_benchmark_result(
            raw,
            invocation=self.invocation,
            model_source=MODEL_SOURCE,
            metric_percentiles=(50, 95, 99),
        )
        with tempfile.TemporaryDirectory() as directory:
            store = LocalMeasurementBundleStore(Path(directory))
            first = store.save(
                "baseline-stock-vllm",
                1,
                {"points": [point.to_document()]},
                {"short-1rps.json": raw},
            )
            repeated = store.save(
                "baseline-stock-vllm",
                1,
                {"points": [point.to_document()]},
                {"short-1rps.json": raw},
            )
            loaded = store.load("baseline-stock-vllm", 1)

            self.assertEqual(first, repeated)
            self.assertEqual(loaded.raw_documents["short-1rps.json"], raw)
            self.assertEqual(
                loaded.receipt["normalized"]["points"][0]["duration_us"],
                2_000_000,
            )
            self.assertEqual(loaded.receipt["attempt"], 1)

            with self.assertRaisesRegex(MeasurementBundleError, "different content"):
                store.save(
                    "baseline-stock-vllm",
                    1,
                    {"points": []},
                    {"short-1rps.json": raw},
                )

            (first / "raw" / "unexpected.json").write_bytes(b"{}")
            with self.assertRaisesRegex(MeasurementBundleError, "set differs"):
                store.load("baseline-stock-vllm", 1)


if __name__ == "__main__":
    unittest.main()
