from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from agent_collab_evals.adapters.composite_hidden_evaluator import (
    CompositeHiddenEvaluationProfile,
    CompositeHiddenServingEvaluator,
    HiddenEvaluationPhaseProfile,
)
from agent_collab_evals.adapters.fake_serving_evaluator import (
    FakeModelServingEvaluator,
)
from agent_collab_evals.artifacts import ArtifactRef
from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.canonical import digest_value
from agent_collab_evals.evaluation import (
    EvaluationReservation,
    EvaluationReservationStatus,
    EvaluationScope,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_ID = "stock-vllm-0.21.0"


class CompositeHiddenServingEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.campaign = ModelServingCampaign.load(
            REPOSITORY_ROOT / "campaigns/model_serving_v0/campaign.toml"
        )
        self.correctness = self._fake("correctness", 1, 2)
        self.quality = self._fake("quality", 990_000, 3)
        self.performance = self._fake("performance", 930_000, 5)
        self.profile = CompositeHiddenEvaluationProfile(
            profile_id="model-serving-hidden-v0",
            campaign_manifest_digest=self.campaign.manifest_digest,
            hidden_workload_manifest_digest=digest_value(
                {"hidden_workload": "v0"}
            ),
            correctness=self._phase("correctness", self.correctness, 7),
            quality=self._phase("quality", self.quality, 11),
            performance=self._phase("performance", self.performance, 13),
        )
        self.evaluator = self._evaluator()
        self.candidate = self.campaign.reference_candidate_path.read_bytes()
        self.reservation = self._reservation(self.profile.reserved_seconds)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_combines_three_gates_into_one_durable_hidden_result(self) -> None:
        receipt = self.evaluator.hidden_evaluate(
            self.candidate, self.reservation, "hidden:selection-1"
        )
        result = self.evaluator.resolve(
            receipt,
            self.candidate,
            self.reservation,
            EvaluationScope.HIDDEN,
        )

        self.assertTrue(result.eligible)
        self.assertEqual(result.criterion_units, 930_000)
        self.assertEqual(result.failures, ())
        self.assertEqual(self.evaluator.used_seconds(receipt), 10)
        self.assertEqual(
            set(result.diagnostics["phase_evidence_digests"]),
            {"correctness", "quality", "performance"},
        )

        restarted = self._evaluator()
        self.assertEqual(
            restarted.hidden_evaluate(
                self.candidate, self.reservation, "hidden:selection-1"
            ),
            receipt,
        )
        self.assertEqual(
            restarted.resolve(
                receipt,
                self.candidate,
                self.reservation,
                EvaluationScope.HIDDEN,
            ),
            result,
        )
        self.assertEqual(restarted.used_seconds(receipt), 10)

    def test_any_failed_gate_makes_the_result_ineligible(self) -> None:
        failed_quality = FakeModelServingEvaluator(
            self.root / "failed-quality.sqlite3",
            self.campaign,
            {},
            {},
            hidden_used_seconds=3,
        )
        profile = CompositeHiddenEvaluationProfile(
            profile_id="model-serving-hidden-failed-quality",
            campaign_manifest_digest=self.campaign.manifest_digest,
            hidden_workload_manifest_digest=digest_value(
                {"hidden_workload": "failed-quality"}
            ),
            correctness=self._phase("correctness", self.correctness, 7),
            quality=self._phase("quality", failed_quality, 11),
            performance=self._phase("performance", self.performance, 13),
        )
        evaluator = CompositeHiddenServingEvaluator(
            self.root / "failed-composite.sqlite3",
            profile,
            {
                "correctness": self.correctness,
                "quality": failed_quality,
                "performance": self.performance,
            },
        )
        reservation = self._reservation(profile.reserved_seconds)

        receipt = evaluator.hidden_evaluate(
            self.candidate, reservation, "hidden:failed-quality"
        )
        result = evaluator.resolve(
            receipt, self.candidate, reservation, EvaluationScope.HIDDEN
        )

        self.assertFalse(result.eligible)
        self.assertEqual(result.criterion_units, 0)
        self.assertEqual(
            result.failures,
            ("quality:candidate_not_in_fake_score_fixture",),
        )
        self.assertEqual(
            result.diagnostics["performance_criterion_units"], 930_000
        )

    def test_phase_allowances_exactly_partition_the_outer_reservation(self) -> None:
        with self.assertRaisesRegex(ValueError, "differs from phase allowances"):
            self.evaluator.hidden_evaluate(
                self.candidate,
                self._reservation(self.profile.reserved_seconds + 1),
                "hidden:wrong-budget",
            )

        with self.assertRaisesRegex(RuntimeError, "cannot serve visible"):
            self.evaluator.visible_evaluate(
                self.candidate, None, "visible:wrong-scope"
            )

    def test_composite_and_phase_ledger_tampering_fail_closed(self) -> None:
        receipt = self.evaluator.hidden_evaluate(
            self.candidate, self.reservation, "hidden:tamper"
        )
        with closing(sqlite3.connect(self.root / "composite.sqlite3")) as connection:
            connection.execute(
                "UPDATE composite_hidden_receipts SET result_digest = ?",
                ("sha256:" + "0" * 64,),
            )
            connection.commit()
        with self.assertRaisesRegex(RuntimeError, "binding differs"):
            self.evaluator.resolve(
                receipt,
                self.candidate,
                self.reservation,
                EvaluationScope.HIDDEN,
            )

        clean = self._evaluator(self.root / "phase-tamper-composite.sqlite3")
        phase_receipt = clean.hidden_evaluate(
            self.candidate, self.reservation, "hidden:phase-tamper"
        )
        with closing(sqlite3.connect(self.root / "performance.sqlite3")) as connection:
            connection.execute(
                "UPDATE evaluation_receipts SET result_digest = ?",
                ("sha256:" + "1" * 64,),
            )
            connection.commit()
        with self.assertRaisesRegex(RuntimeError, "evidence differs"):
            clean.resolve(
                phase_receipt,
                self.candidate,
                self.reservation,
                EvaluationScope.HIDDEN,
            )

    def test_profile_rejects_missing_or_mismatched_phase_authority(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly three phases"):
            CompositeHiddenServingEvaluator(
                self.root / "missing.sqlite3",
                self.profile,
                {
                    "correctness": self.correctness,
                    "quality": self.quality,
                },
            )
        with self.assertRaisesRegex(ValueError, "differs from its registered"):
            CompositeHiddenServingEvaluator(
                self.root / "mismatch.sqlite3",
                self.profile,
                {
                    "correctness": self.quality,
                    "quality": self.correctness,
                    "performance": self.performance,
                },
            )

    def _fake(
        self, name: str, hidden_score: int, hidden_used_seconds: int
    ) -> FakeModelServingEvaluator:
        return FakeModelServingEvaluator(
            self.root / f"{name}.sqlite3",
            self.campaign,
            {},
            {CANDIDATE_ID: hidden_score},
            hidden_used_seconds=hidden_used_seconds,
        )

    @staticmethod
    def _phase(
        name: str,
        evaluator: FakeModelServingEvaluator,
        reserved_seconds: int,
    ) -> HiddenEvaluationPhaseProfile:
        return HiddenEvaluationPhaseProfile(
            name=name,
            evaluator_profile_digest=evaluator.profile_digest,
            workload_digest=digest_value({"hidden_phase": name}),
            reserved_seconds=reserved_seconds,
        )

    def _evaluator(
        self, database: Path | None = None
    ) -> CompositeHiddenServingEvaluator:
        return CompositeHiddenServingEvaluator(
            database or self.root / "composite.sqlite3",
            self.profile,
            {
                "correctness": self.correctness,
                "quality": self.quality,
                "performance": self.performance,
            },
        )

    @staticmethod
    def _reservation(seconds: int) -> EvaluationReservation:
        return EvaluationReservation(
            reservation_id="evaluation-" + "9" * 32,
            reservation_key="hidden:selection",
            campaign_run_id="campaign-run",
            actor_id=None,
            artifact_ref=ArtifactRef("artifact-" + "8" * 32),
            scope=EvaluationScope.HIDDEN,
            reserved_seconds=seconds,
            status=EvaluationReservationStatus.RESERVED,
        )


if __name__ == "__main__":
    unittest.main()
