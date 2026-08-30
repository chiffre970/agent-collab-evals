from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from agent_collab_evals.adapters.quality_series_evaluator import (
    PairedQualitySeriesEvaluator,
    QualityRepetitionReceipt,
    QualitySeriesProfile,
    quality_policy_authority_digest,
)
from agent_collab_evals.artifacts import ArtifactRef
from agent_collab_evals.canonical import digest_bytes, digest_value
from agent_collab_evals.campaigns.serving_quality import (
    QUALITY_RUN_SCHEMA,
    QualityPolicy,
)
from agent_collab_evals.evaluation import (
    EvaluationReservation,
    EvaluationReservationStatus,
    EvaluationScope,
)


POLICY_PATH = Path("campaigns/model_serving_v0/evaluator/quality_policy.toml")


class _FakeQualityBackend:
    def __init__(self, policy: QualityPolicy, *, candidate_failures: int = 0) -> None:
        self.policy = policy
        self.candidate_failures = candidate_failures
        self.profile_digest = digest_value(
            {
                "fake_quality_backend": "v1",
                "policy": digest_value(policy),
                "candidate_failures": candidate_failures,
            }
        )
        self.calls: list[tuple[str, int, str]] = []

    def evaluate(
        self,
        candidate: bytes,
        reservation: EvaluationReservation,
        execution_key: str,
        *,
        role: str,
        repetition: int,
    ) -> QualityRepetitionReceipt:
        self.calls.append((role, repetition, reservation.artifact_ref.value))
        return self._receipt(candidate, reservation, execution_key, role, repetition)

    def resolve(
        self,
        receipt: QualityRepetitionReceipt,
        candidate: bytes,
        reservation: EvaluationReservation,
        *,
        role: str,
        repetition: int,
    ) -> Mapping[str, object]:
        suffix = f":quality:{repetition}:{role}"
        execution_key = next(
            (
                key
                for key in ("hidden:selection" + suffix, "hidden:failed" + suffix)
                if self._receipt(
                    candidate, reservation, key, role, repetition
                )
                == receipt
            ),
            None,
        )
        if execution_key is None:
            raise RuntimeError("fake quality receipt binding differs")
        return self._run(role, repetition)

    def used_seconds(self, receipt: QualityRepetitionReceipt) -> int:
        return 7

    def _receipt(
        self,
        candidate: bytes,
        reservation: EvaluationReservation,
        execution_key: str,
        role: str,
        repetition: int,
    ) -> QualityRepetitionReceipt:
        return QualityRepetitionReceipt(
            "qualityreceipt-"
            + digest_value(
                {
                    "profile": self.profile_digest,
                    "candidate": digest_bytes(candidate),
                    "reservation": reservation.reservation_id,
                    "key": execution_key,
                    "role": role,
                    "repetition": repetition,
                }
            )[7:39]
        )

    def _run(self, role: str, repetition: int) -> Mapping[str, object]:
        failures = self.candidate_failures if role == "candidate" else 0
        cases: list[dict[str, object]] = []
        family_scores: dict[str, dict[str, int]] = {}
        remaining = failures
        pass_count = 0
        per_family = self.policy.case_count // len(self.policy.families)
        for family in self.policy.families:
            family_passes = 0
            for index in range(per_family):
                passed = remaining == 0
                if remaining:
                    remaining -= 1
                family_passes += int(passed)
                pass_count += int(passed)
                cases.append(
                    {
                        "case_id": f"{family}-{index:02d}",
                        "family_id": family,
                        "passed": passed,
                        "extracted": "answer" if passed else "wrong",
                        "content_digest": digest_value(
                            {
                                "role": role,
                                "repetition": repetition,
                                "family": family,
                                "case": index,
                                "passed": passed,
                            }
                        ),
                    }
                )
            family_scores[family] = {
                "case_count": per_family,
                "pass_count": family_passes,
                "score_ppm": family_passes * 1_000_000 // per_family,
            }
        return {
            "schema_version": QUALITY_RUN_SCHEMA,
            "profile_digest": self.policy.quality_profile_digest,
            "workload_digest": self.policy.quality_workload_digest,
            "role": role,
            "repetition": repetition,
            "case_count": self.policy.case_count,
            "pass_count": pass_count,
            "score_ppm": pass_count * 1_000_000 // self.policy.case_count,
            "family_scores": family_scores,
            "cases": cases,
        }


class PairedQualitySeriesEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.policy = replace(
            QualityPolicy.load(POLICY_PATH), bootstrap_resamples=100
        )
        self.reference = b'{"candidate":"reference"}'
        self.candidate = b'{"candidate":"selected"}'
        self.backend = _FakeQualityBackend(self.policy)
        self.profile = self._profile(self.backend)
        self.evaluator = self._evaluator(self.backend)
        self.reservation = self._reservation(self.profile.reserved_seconds)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_runs_fresh_paired_series_in_registered_order(self) -> None:
        receipt = self.evaluator.hidden_evaluate(
            self.candidate, self.reservation, "hidden:selection"
        )
        result = self.evaluator.resolve(
            receipt,
            self.candidate,
            self.reservation,
            EvaluationScope.HIDDEN,
        )

        self.assertTrue(result.eligible)
        self.assertEqual(result.criterion_units, 0)
        self.assertEqual(self.evaluator.used_seconds(receipt), 42)
        self.assertEqual(
            self.backend.calls,
            [
                ("reference", 1, "artifact-" + "6" * 32),
                ("candidate", 1, "artifact-" + "8" * 32),
                ("candidate", 2, "artifact-" + "8" * 32),
                ("reference", 2, "artifact-" + "6" * 32),
                ("reference", 3, "artifact-" + "6" * 32),
                ("candidate", 3, "artifact-" + "8" * 32),
            ],
        )

        restarted = self._evaluator(self.backend)
        self.assertEqual(
            restarted.resolve(
                receipt,
                self.candidate,
                self.reservation,
                EvaluationScope.HIDDEN,
            ),
            result,
        )

    def test_quality_failure_is_an_ineligible_gate_not_an_execution_error(self) -> None:
        backend = _FakeQualityBackend(self.policy, candidate_failures=16)
        profile = self._profile(backend)
        evaluator = self._evaluator(backend, profile, self.root / "failed.sqlite3")
        reservation = self._reservation(profile.reserved_seconds)

        receipt = evaluator.hidden_evaluate(
            self.candidate, reservation, "hidden:failed"
        )
        result = evaluator.resolve(
            receipt, self.candidate, reservation, EvaluationScope.HIDDEN
        )

        self.assertFalse(result.eligible)
        self.assertLess(result.criterion_units, 0)
        self.assertTrue(result.failures)

    def test_schedule_budget_scope_and_ledger_tampering_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "paired schedule"):
            self.evaluator.hidden_evaluate(
                self.candidate,
                self._reservation(self.profile.reserved_seconds + 1),
                "hidden:selection",
            )
        with self.assertRaisesRegex(RuntimeError, "cannot serve visible"):
            self.evaluator.visible_evaluate(
                self.candidate, None, "visible:selection"
            )

        receipt = self.evaluator.hidden_evaluate(
            self.candidate, self.reservation, "hidden:selection"
        )
        with closing(sqlite3.connect(self.root / "quality.sqlite3")) as connection:
            connection.execute(
                "UPDATE quality_series_receipts SET decision_digest = ?",
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

    def test_parsed_policy_and_backend_authority_are_registered(self) -> None:
        changed_policy = replace(
            self.policy,
            aggregate_margin_ppm=self.policy.aggregate_margin_ppm + 1,
        )
        with self.assertRaisesRegex(ValueError, "policy authority differs"):
            PairedQualitySeriesEvaluator(
                self.root / "changed-policy.sqlite3",
                self.profile,
                changed_policy,
                self.reference,
                self.backend,
            )

        changed_backend = _FakeQualityBackend(self.policy, candidate_failures=1)
        with self.assertRaisesRegex(ValueError, "backend differs"):
            PairedQualitySeriesEvaluator(
                self.root / "changed-backend.sqlite3",
                self.profile,
                self.policy,
                self.reference,
                changed_backend,
            )

    def _profile(self, backend: _FakeQualityBackend) -> QualitySeriesProfile:
        return QualitySeriesProfile(
            profile_id="paired-hidden-quality-v0",
            campaign_manifest_digest=digest_value({"campaign": "v0"}),
            hidden_workload_manifest_digest=digest_value({"hidden": "v0"}),
            quality_profile_digest=self.policy.quality_profile_digest,
            quality_policy_digest=self.policy.digest,
            quality_policy_authority_digest=quality_policy_authority_digest(
                self.policy
            ),
            quality_workload_digest=self.policy.quality_workload_digest,
            reference_artifact_ref="artifact-" + "6" * 32,
            reference_candidate_digest=digest_bytes(self.reference),
            repetition_backend_profile_digest=backend.profile_digest,
            repetitions=3,
            repetition_reserved_seconds=60,
            role_order_by_repetition=(
                ("reference", "candidate"),
                ("candidate", "reference"),
                ("reference", "candidate"),
            ),
        )

    def _evaluator(
        self,
        backend: _FakeQualityBackend,
        profile: QualitySeriesProfile | None = None,
        database: Path | None = None,
    ) -> PairedQualitySeriesEvaluator:
        return PairedQualitySeriesEvaluator(
            database or self.root / "quality.sqlite3",
            profile or self.profile,
            self.policy,
            self.reference,
            backend,
        )

    @staticmethod
    def _reservation(seconds: int) -> EvaluationReservation:
        return EvaluationReservation(
            reservation_id="evaluation-" + "7" * 32,
            reservation_key="hidden:quality",
            campaign_run_id="campaign-run",
            actor_id=None,
            artifact_ref=ArtifactRef("artifact-" + "8" * 32),
            scope=EvaluationScope.HIDDEN,
            reserved_seconds=seconds,
            status=EvaluationReservationStatus.RESERVED,
        )


if __name__ == "__main__":
    unittest.main()
