from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from agent_collab_evals.canonical import digest_file
from agent_collab_evals.campaigns.serving_quality import (
    QualityProfile,
    QualityValidationError,
    build_quality_requests,
    compare_quality_runs,
    load_quality_workload,
    materialize_quality_workload,
    score_quality_outputs,
    write_private_workload,
)


PROFILE_PATH = Path(
    "campaigns/model_serving_v0/evaluator/quality_calibration.toml"
)


class ServingQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = QualityProfile.load(PROFILE_PATH)

    def _sources(self, root: Path) -> Path:
        mmlu = root / "mmlu.csv"
        with mmlu.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=("", "Question", "A", "B", "C", "D", "Answer", "Subject"),
            )
            writer.writeheader()
            for index in range(16):
                writer.writerow(
                    {
                        "": index,
                        "Question": f"Which value is {index}?",
                        "A": str(index),
                        "B": str(index + 1),
                        "C": str(index + 2),
                        "D": str(index + 3),
                        "Answer": "A",
                        "Subject": f"subject-{index}",
                    }
                )
        gsm8k = root / "gsm8k-test.jsonl"
        gsm8k.write_text(
            "".join(
                json.dumps(
                    {
                        "question": f"What is {index} plus one?",
                        "answer": f"Calculation.\n#### {index + 1}",
                    }
                )
                + "\n"
                for index in range(16)
            ),
            encoding="utf-8",
        )
        bbh_date = root / "bbh-date-understanding.json"
        bbh_logic = root / "bbh-logical-deduction.json"
        for path, prefix in ((bbh_date, "date"), (bbh_logic, "logic")):
            path.write_text(
                json.dumps(
                    {
                        "canary": "private",
                        "examples": [
                            {
                                "input": f"{prefix} question {index}\n(A) yes\n(B) no",
                                "target": "(A)",
                            }
                            for index in range(8)
                        ],
                    }
                ),
                encoding="utf-8",
            )
        source_rows = (
            ("mmlu", "openai_mmlu_csv", mmlu, "mmlu-revision"),
            ("gsm8k", "gsm8k_jsonl", gsm8k, "gsm-revision"),
            ("bbh_date_understanding", "bbh_json", bbh_date, "bbh-revision"),
            ("bbh_logical_deduction", "bbh_json", bbh_logic, "bbh-revision"),
        )
        source_profile = root / "sources.toml"
        lines = ['schema_version = "model-serving-quality-sources/v0alpha1"', ""]
        for source_id, source_format, path, revision in source_rows:
            lines.extend(
                [
                    "[[sources]]",
                    f'id = "{source_id}"',
                    f'format = "{source_format}"',
                    f'filename = "{path.name}"',
                    f'url = "https://example.invalid/{path.name}"',
                    f'revision = "{revision}"',
                    f'sha256 = "{digest_file(path).removeprefix("sha256:")}"',
                    "",
                ]
            )
        source_profile.write_text("\n".join(lines), encoding="utf-8")
        return source_profile

    def test_materialization_is_private_deterministic_and_digest_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = self._sources(root)
            seed = bytes(range(32))
            first = materialize_quality_workload(self.profile, sources, root, seed)
            second = materialize_quality_workload(self.profile, sources, root, seed)
            different = materialize_quality_workload(
                self.profile, sources, root, bytes(reversed(range(32)))
            )

            self.assertEqual(first, second)
            self.assertNotEqual(first["cases"], different["cases"])
            self.assertEqual(first["case_count"], 64)
            self.assertNotIn(seed.hex(), json.dumps(first))

            path = write_private_workload(root / "workload.json", first)
            loaded = load_quality_workload(path, self.profile)
            self.assertEqual(len(loaded.cases), 64)

            (root / "mmlu.csv").write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(QualityValidationError, "digest differs"):
                materialize_quality_workload(self.profile, sources, root, seed)

    def test_served_outputs_are_scored_and_compared_by_paired_case(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = self._sources(root)
            document = materialize_quality_workload(
                self.profile, sources, root, bytes(range(32))
            )
            workload = load_quality_workload(
                write_private_workload(root / "workload.json", document),
                self.profile,
            )
            requests = build_quality_requests(
                self.profile, workload, served_model_name="target-model"
            )
            self.assertEqual(len(requests), 64)
            thinking = {
                request["body"]["chat_template_kwargs"]["enable_thinking"]
                for request in requests
            }
            self.assertEqual(thinking, {False, True})

            outputs = {
                case.case_id: f"work\n<answer>{case.expected}</answer>"
                for case in workload.cases
            }
            reference = score_quality_outputs(
                self.profile, workload, outputs, repetition=1, role="reference"
            )
            changed = dict(outputs)
            changed[workload.cases[0].case_id] = "<answer>WRONG</answer>"
            candidate = score_quality_outputs(
                self.profile, workload, changed, repetition=1, role="candidate"
            )
            comparison = compare_quality_runs(reference, candidate)

            self.assertEqual(reference["score_ppm"], 1_000_000)
            self.assertEqual(candidate["score_ppm"], 984_375)
            self.assertEqual(comparison["delta_ppm"], -15_625)
            self.assertEqual(comparison["paired_transitions"]["pass_fail"], 1)

            missing = dict(outputs)
            missing.pop(next(iter(missing)))
            with self.assertRaisesRegex(QualityValidationError, "case set differs"):
                score_quality_outputs(
                    self.profile,
                    workload,
                    missing,
                    repetition=1,
                    role="candidate",
                )


if __name__ == "__main__":
    unittest.main()
