from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_collab_evals.campaigns.serving_correctness import (
    CorrectnessValidationError,
    load_correctness_workload,
    score_correctness_responses,
)
from agent_collab_evals.canonical import canonical_json_bytes, digest_bytes


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_WORKLOAD = (
    REPOSITORY_ROOT
    / "campaigns/model_serving_v0/workloads/public/correctness.jsonl"
)
SERVED_MODEL = "target-model"


class ServingCorrectnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workload = load_correctness_workload(PUBLIC_WORKLOAD)

    def test_loads_workload_and_builds_frozen_non_thinking_requests(self) -> None:
        self.assertEqual(self.workload.digest, digest_bytes(PUBLIC_WORKLOAD.read_bytes()))
        self.assertEqual(len(self.workload.cases), 3)
        requests = self.workload.requests(SERVED_MODEL)
        self.assertEqual(requests[0]["model"], SERVED_MODEL)
        self.assertEqual(requests[0]["temperature"], 0)
        self.assertEqual(requests[0]["seed"], 1729)
        self.assertEqual(
            requests[0]["chat_template_kwargs"], {"enable_thinking": False}
        )

    def test_scores_exact_regex_and_casefold_checks(self) -> None:
        responses = {
            "exact-echo": self._response("CALIBRATION_OK"),
            "small-arithmetic": self._response("42"),
            "basic-fact": self._response("PARIS"),
        }

        result = score_correctness_responses(
            self.workload, responses, served_model_name=SERVED_MODEL
        )

        self.assertTrue(result.eligible)
        self.assertEqual(result.passed_cases, 3)
        self.assertEqual(result.total_cases, 3)
        self.assertEqual(result.failures, ())
        self.assertEqual(
            result.response_digests["small-arithmetic"],
            digest_bytes(responses["small-arithmetic"]),
        )

    def test_content_failure_and_api_failure_are_distinct_and_nonrevealing(self) -> None:
        wrong_model = self._response("Paris", model="different-model")
        result = score_correctness_responses(
            self.workload,
            {
                "exact-echo": self._response("WRONG"),
                "small-arithmetic": wrong_model,
                "basic-fact": self._response("paris"),
            },
            served_model_name=SERVED_MODEL,
        )

        self.assertFalse(result.eligible)
        self.assertEqual(result.passed_cases, 1)
        self.assertEqual(
            result.failures,
            ("exact-echo:check_failed", "small-arithmetic:api_schema"),
        )
        self.assertNotIn("CALIBRATION_OK", str(result.to_document()))

    def test_truncation_duplicate_json_and_case_set_changes_fail_closed(self) -> None:
        truncated = self._response("42", finish_reason="length")
        duplicate = (
            b'{"model":"target-model","model":"target-model",'
            b'"choices":[]}'
        )
        result = score_correctness_responses(
            self.workload,
            {
                "exact-echo": duplicate,
                "small-arithmetic": truncated,
                "basic-fact": self._response("Paris"),
            },
            served_model_name=SERVED_MODEL,
        )
        self.assertEqual(
            result.failures,
            ("exact-echo:api_schema", "small-arithmetic:api_schema"),
        )

        with self.assertRaisesRegex(CorrectnessValidationError, "case set differs"):
            score_correctness_responses(
                self.workload,
                {"exact-echo": self._response("CALIBRATION_OK")},
                served_model_name=SERVED_MODEL,
            )

    def test_rejects_ambiguous_or_invalid_workloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            duplicate = root / "duplicate.jsonl"
            duplicate.write_text(
                '{"id":"one","id":"two","messages":[],"max_tokens":1,'
                '"check":{"kind":"exact","value":"x"}}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CorrectnessValidationError, "ambiguous"):
                load_correctness_workload(duplicate)

            invalid_regex = root / "invalid-regex.jsonl"
            invalid_regex.write_text(
                '{"id":"one","messages":[{"role":"user","content":"x"}],'
                '"max_tokens":1,"check":{"kind":"regex","value":"["}}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CorrectnessValidationError, "regex"):
                load_correctness_workload(invalid_regex)

    @staticmethod
    def _response(
        content: str,
        *,
        model: str = SERVED_MODEL,
        finish_reason: str = "stop",
    ) -> bytes:
        return canonical_json_bytes(
            {
                "id": "chatcmpl-test",
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": finish_reason,
                        "message": {"role": "assistant", "content": content},
                    }
                ],
            }
        )


if __name__ == "__main__":
    unittest.main()
