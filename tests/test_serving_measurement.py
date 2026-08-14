from __future__ import annotations

import json
import tempfile
import unittest
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
    MeasurementValidationError,
    parse_vllm_benchmark_result,
)


CAMPAIGN_PATH = Path("campaigns/model_serving_v0/campaign.toml")
MODEL_SOURCE = "/models/pinned-qwen"


def _result_document(
    invocation: BenchmarkInvocation, *, failed: int = 0
) -> dict[str, object]:
    completed = invocation.num_prompts - failed
    duration = 2.0
    errors = [""] * completed + ["backend failure"] * failed
    input_lens = [invocation.input_tokens] * invocation.num_prompts
    output_lens = [invocation.output_tokens] * completed + [0] * failed
    total_input = invocation.input_tokens * completed
    total_output = invocation.output_tokens * completed
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
        "request_goodput": None,
        "output_throughput": total_output / duration,
        "total_token_throughput": (total_input + total_output) / duration,
        "input_lens": input_lens,
        "output_lens": output_lens,
        "ttfts": [0.1] * invocation.num_prompts,
        "itls": [[] for _ in range(invocation.num_prompts)],
        "start_times": [1.0] * invocation.num_prompts,
        "generated_texts": ["x"] * completed + [""] * failed,
        "errors": errors,
        "max_output_tokens_per_s": 128.0,
        "max_concurrent_requests": 2,
        "rtfx": 0.0,
    }
    for metric in ("ttft", "tpot", "itl", "e2el"):
        result[f"mean_{metric}_ms"] = 10.0
        result[f"median_{metric}_ms"] = 9.0
        result[f"std_{metric}_ms"] = 1.0
        result[f"p50_{metric}_ms"] = 9.0
        result[f"p95_{metric}_ms"] = 12.0
        result[f"p99_{metric}_ms"] = 14.0
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

    def _raw(self, *, failed: int = 0) -> bytes:
        return json.dumps(
            _result_document(self.invocation, failed=failed),
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
        self.assertEqual(result.latency_us["p99_e2el"], 14_000)
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
