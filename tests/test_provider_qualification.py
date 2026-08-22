from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from agent_collab_evals.canonical import digest_file
from agent_collab_evals.provider_qualification import (
    ProviderQualificationPlan,
    QualifiedProviderRoute,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    REPOSITORY_ROOT
    / "config/provider_qualification/deepseek-v4-flash-development-policy.json"
)
RECORD_PATH = (
    REPOSITORY_ROOT
    / "config/provider_qualification/deepseek-v4-flash-deepinfra-development-selection.json"
)


class ProviderQualificationTests(unittest.TestCase):
    def test_live_development_record_binds_selection_gateway_and_receipts(self) -> None:
        qualified = QualifiedProviderRoute.load(
            RECORD_PATH, repository_root=REPOSITORY_ROOT
        )

        self.assertEqual(qualified.selection.selected_provider, "DeepInfra")
        self.assertEqual(
            qualified.gateway_profile_id,
            "openrouter-deepinfra-development-v0",
        )
        self.assertTrue(qualified.record_digest.startswith("sha256:"))
        self.assertTrue(qualified.resolved_digest.startswith("sha256:"))

    def test_frozen_policy_selects_lowest_cost_eligible_route(self) -> None:
        plan = ProviderQualificationPlan.load(
            POLICY_PATH, repository_root=REPOSITORY_ROOT
        )

        selection = plan.select()

        self.assertEqual(selection.selected_provider, "DeepInfra")
        self.assertEqual(selection.selected_tag, "deepinfra/fp8")
        self.assertEqual(selection.selected_quantization, "fp8")
        self.assertEqual(selection.projected_cost_usd_nanos, 9_800_000)
        self.assertEqual(
            selection.eligible_providers,
            ("DeepInfra", "CoreWeave", "Novita"),
        )
        self.assertEqual(selection.rejected_providers, {})
        self.assertTrue(selection.resolved_digest.startswith("sha256:"))

    def test_policy_rejects_unknown_fields(self) -> None:
        payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        payload["unexpected"] = True
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "policy.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown=\\['unexpected'\\]"):
                ProviderQualificationPlan.load(
                    source, repository_root=REPOSITORY_ROOT
                )

    def test_route_with_implicit_caching_fails_closed(self) -> None:
        plan = ProviderQualificationPlan.load(
            POLICY_PATH, repository_root=REPOSITORY_ROOT
        )
        candidate = replace(
            plan.candidates[0], supports_implicit_caching=True
        )

        reasons = plan._rejection_reasons(candidate)

        self.assertIn("implicit_caching_enabled", reasons)

    def test_plan_rejects_tampered_raw_source_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(
                REPOSITORY_ROOT / "config/provider_qualification",
                root / "config/provider_qualification",
            )
            shutil.copytree(
                REPOSITORY_ROOT / "evidence/provider_qualification/sources",
                root / "evidence/provider_qualification/sources",
            )
            source = next(
                (root / "evidence/provider_qualification/sources").glob(
                    "openrouter-endpoints-*.json.gz"
                )
            )
            source.write_bytes(source.read_bytes() + b"tampered")

            with self.assertRaisesRegex(ValueError, "compressed source digest"):
                ProviderQualificationPlan.load(
                    root
                    / "config/provider_qualification/"
                    "deepseek-v4-flash-development-policy.json",
                    repository_root=root,
                )

    def test_plan_rejects_normalized_candidates_not_derived_from_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(
                REPOSITORY_ROOT / "config/provider_qualification",
                root / "config/provider_qualification",
            )
            shutil.copytree(
                REPOSITORY_ROOT / "evidence/provider_qualification/sources",
                root / "evidence/provider_qualification/sources",
            )
            snapshot = (
                root
                / "config/provider_qualification/"
                "openrouter-deepseek-v4-flash-zdr-20260822.json"
            )
            payload = json.loads(snapshot.read_text(encoding="utf-8"))
            payload["candidates"][0]["pricing"]["prompt_usd_per_token"] = "0"
            snapshot.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "deterministic extraction"):
                ProviderQualificationPlan.load(
                    root
                    / "config/provider_qualification/"
                    "deepseek-v4-flash-development-policy.json",
                    repository_root=root,
                )

    def test_qualified_route_rejects_tampered_retained_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(REPOSITORY_ROOT / "config", root / "config")
            shutil.copytree(
                REPOSITORY_ROOT / "evidence/provider_qualification",
                root / "evidence/provider_qualification",
            )
            receipt = next(
                (root / "evidence/provider_qualification/receipts").glob(
                    "*.stream.sse"
                )
            )
            receipt.write_bytes(receipt.read_bytes() + b"tampered")

            with self.assertRaisesRegex(ValueError, "retained evidence"):
                QualifiedProviderRoute.load(
                    root
                    / "config/provider_qualification/"
                    "deepseek-v4-flash-deepinfra-development-selection.json",
                    repository_root=root,
                )

    def test_qualified_route_rejects_coherent_record_charge_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(REPOSITORY_ROOT / "config", root / "config")
            shutil.copytree(
                REPOSITORY_ROOT / "evidence/provider_qualification",
                root / "evidence/provider_qualification",
            )
            selection_path = (
                root
                / "config/provider_qualification/"
                "deepseek-v4-flash-deepinfra-development-selection.json"
            )
            selection = json.loads(selection_path.read_text(encoding="utf-8"))
            record_path = root / selection["qualification"][
                "qualification_record_file"
            ]
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["charges"][0]["charged_usd_nanos"] += 1
            record["total_charged_usd_nanos"] += 1
            record_path.write_text(json.dumps(record), encoding="utf-8")
            selection["qualification"]["total_charged_usd_nanos"] += 1
            selection["qualification"]["qualification_record_digest"] = (
                digest_file(record_path)
            )
            selection_path.write_text(json.dumps(selection), encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError, "charge differs from raw receipts"
            ):
                QualifiedProviderRoute.load(
                    selection_path,
                    repository_root=root,
                )

    def test_attempt_index_preserves_all_route_qualification_spend(self) -> None:
        index = (
            REPOSITORY_ROOT
            / "evidence/provider_qualification/development-attempts.jsonl"
        )
        attempts = [
            json.loads(line)
            for line in index.read_text(encoding="utf-8").splitlines()
        ]

        self.assertEqual(len(attempts), 4)
        self.assertEqual(
            sum(attempt["charged_usd_nanos"] for attempt in attempts[-3:]),
            152_760,
        )
        self.assertEqual(
            sum(attempt["charged_usd_nanos"] for attempt in attempts),
            207_640,
        )
        self.assertEqual(
            [attempt["disposition"] for attempt in attempts[-3:]],
            [
                "diagnostic_local_evidence",
                "retained_qualification",
                "diagnostic_local_evidence",
            ],
        )

    def test_qualified_route_rejects_tampered_attempt_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(REPOSITORY_ROOT / "config", root / "config")
            shutil.copytree(
                REPOSITORY_ROOT / "evidence/provider_qualification",
                root / "evidence/provider_qualification",
            )
            index = (
                root
                / "evidence/provider_qualification/development-attempts.jsonl"
            )
            index.write_text(
                index.read_text(encoding="utf-8").replace(
                    '"charged_usd_nanos":50740',
                    '"charged_usd_nanos":1',
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "attempt index digest"):
                QualifiedProviderRoute.load(
                    root
                    / "config/provider_qualification/"
                    "deepseek-v4-flash-deepinfra-development-selection.json",
                    repository_root=root,
                )


if __name__ == "__main__":
    unittest.main()
