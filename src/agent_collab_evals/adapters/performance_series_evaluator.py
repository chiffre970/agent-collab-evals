"""Durable three-repetition aggregation for hidden serving performance."""

from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ..canonical import canonical_json_bytes, digest_bytes, digest_value, parse_json
from ..evaluation import (
    EvaluationReceipt,
    EvaluationReservation,
    EvaluationResult,
    EvaluationScope,
)
from ..ports import CandidateEvaluator
from ..campaigns.serving_scoring import (
    RepetitionScore,
    ScoringProfile,
    summarize_candidate,
)


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}")


@dataclass(frozen=True, slots=True)
class PerformanceSeriesProfile:
    """Registered authority for independent hidden performance repetitions."""

    profile_id: str
    campaign_manifest_digest: str
    hidden_workload_manifest_digest: str
    workload_digest: str
    scoring_profile_digest: str
    repetition_evaluator_profile_digests: tuple[str, ...]
    repetition_reserved_seconds: int

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.profile_id):
            raise ValueError("performance series profile ID is invalid")
        for value in (
            self.campaign_manifest_digest,
            self.hidden_workload_manifest_digest,
            self.workload_digest,
            self.scoring_profile_digest,
            *self.repetition_evaluator_profile_digests,
        ):
            if not _DIGEST.fullmatch(value):
                raise ValueError("performance series digest is invalid")
        if len(self.repetition_evaluator_profile_digests) != 3:
            raise ValueError("performance series requires three repetitions")
        if len(set(self.repetition_evaluator_profile_digests)) != 3:
            raise ValueError("performance repetition profiles must differ")
        if (
            type(self.repetition_reserved_seconds) is not int
            or self.repetition_reserved_seconds < 1
        ):
            raise ValueError("performance repetition allowance must be positive")

    @property
    def repetitions(self) -> int:
        return len(self.repetition_evaluator_profile_digests)

    @property
    def reserved_seconds(self) -> int:
        return self.repetitions * self.repetition_reserved_seconds

    @property
    def digest(self) -> str:
        return digest_value(
            {
                "adapter": "performance-series-evaluator/v0alpha1",
                "profile_id": self.profile_id,
                "campaign_manifest_digest": self.campaign_manifest_digest,
                "hidden_workload_manifest_digest": (
                    self.hidden_workload_manifest_digest
                ),
                "workload_digest": self.workload_digest,
                "scoring_profile_digest": self.scoring_profile_digest,
                "repetition_evaluator_profile_digests": (
                    self.repetition_evaluator_profile_digests
                ),
                "repetition_reserved_seconds": self.repetition_reserved_seconds,
            }
        )


class PerformanceSeriesEvaluator:
    """Aggregate three independently receipted performance evaluations."""

    def __init__(
        self,
        database: Path,
        profile: PerformanceSeriesProfile,
        scoring: ScoringProfile,
        evaluators: Mapping[int, CandidateEvaluator],
    ) -> None:
        if scoring.digest != profile.scoring_profile_digest:
            raise ValueError("performance scoring differs from the series profile")
        if scoring.candidate_repetitions != profile.repetitions:
            raise ValueError("performance repetition count differs from scoring")
        if set(evaluators) != set(range(1, profile.repetitions + 1)):
            raise ValueError("performance repetition evaluator set differs")
        for repetition, expected in enumerate(
            profile.repetition_evaluator_profile_digests, start=1
        ):
            if evaluators[repetition].profile_digest != expected:
                raise ValueError("performance repetition evaluator differs")
        self._database = database
        self._profile = profile
        self._scoring = scoring
        self._evaluators = dict(evaluators)
        self._lock = threading.RLock()
        self._used_authorities: dict[str, int] = {}
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS performance_series_receipts(
                    receipt_id TEXT PRIMARY KEY,
                    evaluation_key TEXT NOT NULL UNIQUE,
                    profile_digest TEXT NOT NULL,
                    candidate_digest TEXT NOT NULL,
                    reservation_id TEXT NOT NULL,
                    repetition_receipts_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    result_digest TEXT NOT NULL
                )
                """
            )
            connection.commit()

    @property
    def profile_digest(self) -> str:
        return self._profile.digest

    def visible_evaluate(
        self,
        candidate: bytes,
        reservation: EvaluationReservation | None,
        evaluation_key: str,
    ) -> EvaluationReceipt:
        raise RuntimeError("hidden performance series cannot serve visible work")

    def hidden_evaluate(
        self,
        candidate: bytes,
        reservation: EvaluationReservation,
        evaluation_key: str,
    ) -> EvaluationReceipt:
        self._validate_request(reservation, evaluation_key)
        receipts: dict[int, EvaluationReceipt] = {}
        results: dict[int, EvaluationResult] = {}
        for repetition in range(1, self._profile.repetitions + 1):
            inner_reservation = self._repetition_reservation(
                reservation, repetition
            )
            evaluator = self._evaluators[repetition]
            inner_key = f"{evaluation_key}:repetition:{repetition}:performance"
            inner_receipt = evaluator.hidden_evaluate(
                candidate, inner_reservation, inner_key
            )
            receipts[repetition] = inner_receipt
            results[repetition] = evaluator.resolve(
                inner_receipt,
                candidate,
                inner_reservation,
                EvaluationScope.HIDDEN,
            )
        result = self._result(
            candidate, reservation, evaluation_key, receipts, results
        )
        receipt = self._receipt(candidate, reservation, evaluation_key, receipts)
        expected = self._row_values(
            receipt, candidate, reservation, evaluation_key, receipts, result
        )
        with self._lock:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM performance_series_receipts "
                    "WHERE evaluation_key = ?",
                    (evaluation_key,),
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO performance_series_receipts("
                        "receipt_id, evaluation_key, profile_digest, "
                        "candidate_digest, reservation_id, "
                        "repetition_receipts_json, result_json, result_digest) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        expected,
                    )
                elif self._stored_values(row) != expected:
                    connection.rollback()
                    raise RuntimeError("performance series changed across retry")
                connection.commit()
        self._used_authorities[receipt.value] = self._used_seconds(receipts)
        return receipt

    def resolve(
        self,
        receipt: EvaluationReceipt,
        candidate: bytes,
        reservation: EvaluationReservation | None,
        scope: EvaluationScope,
    ) -> EvaluationResult:
        if reservation is None:
            raise ValueError("performance series resolution requires a reservation")
        if scope is not EvaluationScope.HIDDEN:
            raise ValueError("performance series receipt has the wrong scope")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM performance_series_receipts WHERE receipt_id = ?",
                (receipt.value,),
            ).fetchone()
        if row is None:
            raise RuntimeError("performance series receipt is unavailable")
        evaluation_key = str(row["evaluation_key"])
        self._validate_request(reservation, evaluation_key)
        raw_receipts = parse_json(str(row["repetition_receipts_json"]))
        expected_keys = {
            str(value) for value in range(1, self._profile.repetitions + 1)
        }
        if not isinstance(raw_receipts, dict) or set(raw_receipts) != expected_keys:
            raise RuntimeError("performance repetition receipt set differs")
        receipts = {
            int(key): EvaluationReceipt(str(value))
            for key, value in raw_receipts.items()
        }
        results: dict[int, EvaluationResult] = {}
        for repetition in range(1, self._profile.repetitions + 1):
            results[repetition] = self._evaluators[repetition].resolve(
                receipts[repetition],
                candidate,
                self._repetition_reservation(reservation, repetition),
                EvaluationScope.HIDDEN,
            )
        result = self._result(
            candidate, reservation, evaluation_key, receipts, results
        )
        expected_receipt = self._receipt(
            candidate, reservation, evaluation_key, receipts
        )
        expected = self._row_values(
            expected_receipt,
            candidate,
            reservation,
            evaluation_key,
            receipts,
            result,
        )
        if receipt != expected_receipt or self._stored_values(row) != expected:
            raise RuntimeError("performance series receipt binding differs")
        self._used_authorities[receipt.value] = self._used_seconds(receipts)
        return result

    def used_seconds(self, receipt: EvaluationReceipt) -> int:
        try:
            return self._used_authorities[receipt.value]
        except KeyError as error:
            raise RuntimeError(
                "performance series receipt must be resolved before accounting"
            ) from error

    def _validate_request(
        self, reservation: EvaluationReservation, evaluation_key: str
    ) -> None:
        if reservation.scope is not EvaluationScope.HIDDEN:
            raise ValueError("performance series requires hidden scope")
        if reservation.actor_id is not None:
            raise ValueError("performance series reservation cannot name an actor")
        if reservation.reserved_seconds != self._profile.reserved_seconds:
            raise ValueError("performance reservation differs from its schedule")
        if not evaluation_key.startswith("hidden:") or not _SAFE_ID.fullmatch(
            evaluation_key
        ):
            raise ValueError("performance series evaluation key is invalid")

    def _repetition_reservation(
        self, outer: EvaluationReservation, repetition: int
    ) -> EvaluationReservation:
        return EvaluationReservation(
            reservation_id=(
                "evaluation-"
                + digest_value(
                    {
                        "profile_digest": self.profile_digest,
                        "outer_reservation_id": outer.reservation_id,
                        "repetition": repetition,
                    }
                )[7:39]
            ),
            reservation_key=f"{outer.reservation_key}:performance:{repetition}",
            campaign_run_id=outer.campaign_run_id,
            actor_id=None,
            artifact_ref=outer.artifact_ref,
            scope=EvaluationScope.HIDDEN,
            reserved_seconds=self._profile.repetition_reserved_seconds,
            status=outer.status,
        )

    def _result(
        self,
        candidate: bytes,
        reservation: EvaluationReservation,
        evaluation_key: str,
        receipts: Mapping[int, EvaluationReceipt],
        results: Mapping[int, EvaluationResult],
    ) -> EvaluationResult:
        repetitions = tuple(
            RepetitionScore(
                repetition=repetition,
                eligible=results[repetition].eligible,
                scalar_ppm=results[repetition].criterion_units,
                bucket_ratio_ppm={},
                selected_goodput_micro_rps={},
                failures=results[repetition].failures,
            )
            for repetition in range(1, self._profile.repetitions + 1)
        )
        summary = summarize_candidate(self._scoring, repetitions)
        evidence_digest = digest_value(
            {
                "profile_digest": self.profile_digest,
                "evaluation_key": evaluation_key,
                "candidate_digest": digest_bytes(candidate),
                "reservation_id": reservation.reservation_id,
                "repetition_receipts": {
                    str(key): receipts[key].value for key in sorted(receipts)
                },
                "summary": summary.to_document(),
            }
        )
        return EvaluationResult(
            eligible=summary.eligible,
            criterion_units=summary.primary_scalar_ppm,
            failures=summary.failures,
            evidence_digest=evidence_digest,
            diagnostics={
                "scoring_profile_digest": self._scoring.digest,
                "repetitions": self._profile.repetitions,
                "repetition_scalar_ppm": summary.repetition_scalar_ppm,
                "reference_max_scalar_ppm": summary.reference_max_scalar_ppm,
                "conservative_improvement_lower_bound_ppm": (
                    summary.conservative_improvement_lower_bound_ppm
                ),
                "clears_improvement_bound": summary.clears_improvement_bound,
            },
        )

    def _used_seconds(self, receipts: Mapping[int, EvaluationReceipt]) -> int:
        return sum(
            self._evaluators[repetition].used_seconds(receipts[repetition])
            for repetition in sorted(receipts)
        )

    def _receipt(
        self,
        candidate: bytes,
        reservation: EvaluationReservation,
        evaluation_key: str,
        receipts: Mapping[int, EvaluationReceipt],
    ) -> EvaluationReceipt:
        return EvaluationReceipt(
            "evalreceipt-"
            + digest_value(
                {
                    "profile_digest": self.profile_digest,
                    "candidate_digest": digest_bytes(candidate),
                    "reservation_id": reservation.reservation_id,
                    "evaluation_key": evaluation_key,
                    "repetition_receipts": {
                        str(key): receipts[key].value for key in sorted(receipts)
                    },
                }
            )[7:39]
        )

    def _row_values(
        self,
        receipt: EvaluationReceipt,
        candidate: bytes,
        reservation: EvaluationReservation,
        evaluation_key: str,
        receipts: Mapping[int, EvaluationReceipt],
        result: EvaluationResult,
    ) -> tuple[object, ...]:
        receipt_document = {
            str(key): receipts[key].value for key in sorted(receipts)
        }
        result_document = {
            "eligible": result.eligible,
            "criterion_units": result.criterion_units,
            "failures": result.failures,
            "evidence_digest": result.evidence_digest,
            "diagnostics": result.diagnostics,
        }
        return (
            receipt.value,
            evaluation_key,
            self.profile_digest,
            digest_bytes(candidate),
            reservation.reservation_id,
            canonical_json_bytes(receipt_document).decode(),
            canonical_json_bytes(result_document).decode(),
            digest_value(result_document),
        )

    @staticmethod
    def _stored_values(row: sqlite3.Row) -> tuple[object, ...]:
        return tuple(
            row[name]
            for name in (
                "receipt_id",
                "evaluation_key",
                "profile_digest",
                "candidate_digest",
                "reservation_id",
                "repetition_receipts_json",
                "result_json",
                "result_digest",
            )
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection
