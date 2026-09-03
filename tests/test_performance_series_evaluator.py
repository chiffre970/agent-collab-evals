from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_collab_evals.adapters.performance_series_evaluator import (
    PerformanceSeriesEvaluator,
    PerformanceSeriesProfile,
)
from agent_collab_evals.artifacts import ArtifactRef
from agent_collab_evals.campaigns.serving_scoring import ScoringProfile
from agent_collab_evals.canonical import digest_bytes, digest_value
from agent_collab_evals.evaluation import (
    EvaluationReceipt,
    EvaluationReservation,
    EvaluationReservationStatus,
    EvaluationResult,
    EvaluationScope,
)
from tests.quality_fixture import REPOSITORY_ROOT


class _RepetitionEvaluator:
    def __init__(
        self,
        profile_digest: str,
        scalar: int,
        used_seconds: int,
        *,
        eligible: bool = True,
        failures: tuple[str, ...] = (),
    ) -> None:
        self.profile_digest = profile_digest
        self.scalar = scalar
        self._used_seconds = used_seconds
        self.eligible = eligible
        self.failures = failures
        self.calls = 0

    def hidden_evaluate(self, candidate, reservation, evaluation_key):
        self.calls += 1
        return EvaluationReceipt(
            "evalreceipt-"
            + digest_value(
                {
                    "profile": self.profile_digest,
                    "candidate": digest_bytes(candidate),
                    "reservation": reservation.reservation_id,
                    "key": evaluation_key,
                }
            )[7:39]
        )

    def visible_evaluate(self, candidate, reservation, evaluation_key):
        raise AssertionError("visible evaluation is not expected")

    def resolve(self, receipt, candidate, reservation, scope):
        return EvaluationResult(
            eligible=self.eligible,
            criterion_units=self.scalar,
            failures=self.failures,
            evidence_digest=digest_value({"receipt": receipt.value}),
            diagnostics={},
        )

    def used_seconds(self, receipt):
        return self._used_seconds


class PerformanceSeriesEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.scoring = ScoringProfile.load(
            REPOSITORY_ROOT
            / "campaigns/model_serving_v0/evaluator/scoring_hidden_v1.toml"
        )
        digests = tuple(
            digest_value({"evaluator": repetition}) for repetition in (1, 2, 3)
        )
        self.profile = PerformanceSeriesProfile(
            profile_id="hidden-performance-series-v1",
            campaign_manifest_digest=digest_value({"campaign": 1}),
            hidden_workload_manifest_digest=digest_value({"hidden": 1}),
            workload_digest=digest_value({"workload": 1}),
            scoring_profile_digest=self.scoring.digest,
            repetition_evaluator_profile_digests=digests,
            repetition_reserved_seconds=1_800,
        )
        self.evaluators = {
            repetition: _RepetitionEvaluator(
                digests[repetition - 1], scalar, repetition * 10
            )
            for repetition, scalar in enumerate(
                (1_010_000, 1_020_000, 1_030_000), start=1
            )
        }
        self.evaluator = PerformanceSeriesEvaluator(
            self.root / "performance.sqlite3",
            self.profile,
            self.scoring,
            self.evaluators,
        )
        self.candidate = b"candidate"
        self.reservation = EvaluationReservation(
            reservation_id="evaluation-" + "1" * 32,
            reservation_key="hidden:performance-series",
            campaign_run_id="campaign-run",
            actor_id=None,
            artifact_ref=ArtifactRef("artifact-" + "2" * 32),
            scope=EvaluationScope.HIDDEN,
            reserved_seconds=self.profile.reserved_seconds,
            status=EvaluationReservationStatus.RESERVED,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_series_runs_three_repetitions_and_reconciles_usage(self) -> None:
        receipt = self.evaluator.hidden_evaluate(
            self.candidate, self.reservation, "hidden:performance"
        )
        result = self.evaluator.resolve(
            receipt,
            self.candidate,
            self.reservation,
            EvaluationScope.HIDDEN,
        )

        self.assertTrue(result.eligible)
        self.assertEqual(result.criterion_units, 1_020_000)
        self.assertEqual(result.diagnostics["repetition_scalar_ppm"], (
            1_010_000,
            1_020_000,
            1_030_000,
        ))
        self.assertEqual(self.evaluator.used_seconds(receipt), 60)

    def test_retry_is_durable_and_receipt_stable(self) -> None:
        first = self.evaluator.hidden_evaluate(
            self.candidate, self.reservation, "hidden:performance"
        )
        second = self.evaluator.hidden_evaluate(
            self.candidate, self.reservation, "hidden:performance"
        )

        self.assertEqual(first, second)

    def test_wrong_outer_allowance_fails_before_repetition(self) -> None:
        changed = EvaluationReservation(
            reservation_id=self.reservation.reservation_id,
            reservation_key=self.reservation.reservation_key,
            campaign_run_id=self.reservation.campaign_run_id,
            actor_id=None,
            artifact_ref=self.reservation.artifact_ref,
            scope=EvaluationScope.HIDDEN,
            reserved_seconds=self.profile.reserved_seconds - 1,
            status=self.reservation.status,
        )

        with self.assertRaisesRegex(ValueError, "differs from its schedule"):
            self.evaluator.hidden_evaluate(
                self.candidate, changed, "hidden:performance"
            )
        self.assertEqual(
            [evaluator.calls for evaluator in self.evaluators.values()],
            [0, 0, 0],
        )

    def test_ineligible_repetition_without_failures_fails_closed(self) -> None:
        self.evaluators[2].eligible = False

        receipt = self.evaluator.hidden_evaluate(
            self.candidate, self.reservation, "hidden:performance"
        )
        result = self.evaluator.resolve(
            receipt,
            self.candidate,
            self.reservation,
            EvaluationScope.HIDDEN,
        )

        self.assertFalse(result.eligible)
        self.assertEqual(result.criterion_units, 0)
        self.assertIn("repetition 2: repetition is ineligible", result.failures)


if __name__ == "__main__":
    unittest.main()
