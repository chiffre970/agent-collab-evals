from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from dataclasses import replace

from agent_collab_evals.campaigns.serving_correctness import (
    CorrectnessValidationError,
    CorrectnessCase,
    CorrectnessWorkload,
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

    def test_correct_sum_with_wrong_format_does_not_become_a_passing_answer(self) -> None:
        # Public synthetic inputs reproduce the stock control's formatting
        # failure without revealing a held-out prompt or weakening its check.
        result = score_correctness_responses(
            self.workload,
            {
                "exact-echo": self._response("CALIBRATION_OK"),
                "small-arithmetic": self._response("20 + 22 = 42"),
                "basic-fact": self._response("Paris"),
            },
            served_model_name=SERVED_MODEL,
        )

        self.assertFalse(result.eligible)
        self.assertEqual(result.passed_cases, 2)
        self.assertEqual(result.failures, ("small-arithmetic:check_failed",))

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

    def test_v3_arithmetic_is_typed_and_formatting_is_separate(self) -> None:
        case = CorrectnessCase("sum", ({"role": "user", "content": "What is 20 + 22?"},),
            16, "integer_sum", "42", (20, 22))
        workload = CorrectnessWorkload(digest_bytes(b"synthetic-sum-v3"), (case,))
        for answer, eligible, representation in (
            ("42", True, "bare_integer"),
            ("20 + 22 = 42", True, "addition_equation"),
            ("22 + 20 = 42", True, "addition_equation"),
            ("20 + 22 = 43", False, "addition_equation"),
            ("21 + 21 = 42", False, "addition_equation"),
            ("43", False, "bare_integer"),
            ("The answer is 42", False, "other"),
            ("42 or 43", False, "other"),
            ("42\n43", False, "other"),
        ):
            with self.subTest(answer=answer):
                result = score_correctness_responses(workload, {"sum": self._response(answer)}, served_model_name=SERVED_MODEL)
                self.assertEqual(result.eligible, eligible)
                self.assertEqual(result.to_document()["formatting"], {"sum": representation})
        truncated = score_correctness_responses(workload,
            {"sum": self._response("42", finish_reason="length")}, served_model_name=SERVED_MODEL)
        self.assertFalse(truncated.eligible)
        self.assertEqual(truncated.failures, ("sum:api_schema",))
        legacy = CorrectnessWorkload(workload.digest, (replace(case, check_kind="regex", expected="^42$", operands=None),))
        result = score_correctness_responses(legacy, {"sum": self._response("20 + 22 = 42")}, served_model_name=SERVED_MODEL)
        self.assertFalse(result.eligible)
        self.assertNotIn("formatting", result.to_document())

    def test_v3_arithmetic_rejects_inconsistent_or_untyped_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sum.jsonl"
            for operands, expected in (([20, 22], "43"), ([True, 22], "23"), ([20.0, 22], "42"), ([20], "20")):
                document = {"id": "sum", "messages": [{"role": "user", "content": "Synthetic arithmetic"}],
                    "max_tokens": 16, "check": {"kind": "integer_sum", "value": expected, "operands": operands}}
                path.write_text(json.dumps(document) + "\n")
                with self.assertRaisesRegex(CorrectnessValidationError, "integer-sum"):
                    load_correctness_workload(path)

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
