"""Fail-closed composition of hidden serving evaluation phases."""

from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ..canonical import canonical_json_bytes, digest_bytes, digest_value
from ..evaluation import (
    EvaluationReceipt,
    EvaluationReservation,
    EvaluationResult,
    EvaluationScope,
)
from ..ports import CandidateEvaluator


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}")
_PHASE_NAMES = ("correctness", "quality", "performance")


@dataclass(frozen=True, slots=True)
class HiddenEvaluationPhaseProfile:
    """Registered identity and compute allowance for one hidden phase."""

    name: str
    evaluator_profile_digest: str
    workload_digest: str
    reserved_seconds: int

    def __post_init__(self) -> None:
        if self.name not in _PHASE_NAMES:
            raise ValueError("hidden evaluation phase name is invalid")
        if not _DIGEST.fullmatch(self.evaluator_profile_digest):
            raise ValueError("hidden phase evaluator profile must be SHA-256")
        if not _DIGEST.fullmatch(self.workload_digest):
            raise ValueError("hidden phase workload must be SHA-256")
        if type(self.reserved_seconds) is not int or self.reserved_seconds < 1:
            raise ValueError("hidden phase allowance must be a positive integer")

    @property
    def digest(self) -> str:
        return digest_value(
            {
                "name": self.name,
                "evaluator_profile_digest": self.evaluator_profile_digest,
                "workload_digest": self.workload_digest,
                "reserved_seconds": self.reserved_seconds,
            }
        )


@dataclass(frozen=True, slots=True)
class CompositeHiddenEvaluationProfile:
    """Frozen authority for the complete hidden serving outcome."""

    profile_id: str
    campaign_manifest_digest: str
    hidden_workload_manifest_digest: str
    correctness: HiddenEvaluationPhaseProfile
    quality: HiddenEvaluationPhaseProfile
    performance: HiddenEvaluationPhaseProfile

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.profile_id):
            raise ValueError("composite hidden evaluator profile ID is invalid")
        for value in (
            self.campaign_manifest_digest,
            self.hidden_workload_manifest_digest,
        ):
            if not _DIGEST.fullmatch(value):
                raise ValueError("composite hidden manifest digest must be SHA-256")
        phases = self.phases
        if tuple(phase.name for phase in phases) != _PHASE_NAMES:
            raise ValueError("hidden evaluation phases must use the fixed order")
        if len({phase.workload_digest for phase in phases}) != len(phases):
            raise ValueError("hidden phase workloads must differ")

    @property
    def phases(self) -> tuple[HiddenEvaluationPhaseProfile, ...]:
        return (self.correctness, self.quality, self.performance)

    @property
    def reserved_seconds(self) -> int:
        return sum(phase.reserved_seconds for phase in self.phases)

    @property
    def digest(self) -> str:
        return digest_value(
            {
                "adapter": "composite-hidden-serving-evaluator/v0alpha1",
                "profile_id": self.profile_id,
                "campaign_manifest_digest": self.campaign_manifest_digest,
                "hidden_workload_manifest_digest": (
                    self.hidden_workload_manifest_digest
                ),
                "phase_digests": [phase.digest for phase in self.phases],
            }
        )


class CompositeHiddenServingEvaluator:
    """Combine correctness, quality, and performance into one hidden result."""

    def __init__(
        self,
        database: Path,
        profile: CompositeHiddenEvaluationProfile,
        phase_evaluators: Mapping[str, CandidateEvaluator],
    ) -> None:
        if set(phase_evaluators) != set(_PHASE_NAMES):
            raise ValueError("composite evaluator requires exactly three phases")
        for phase in profile.phases:
            evaluator = phase_evaluators[phase.name]
            if evaluator.profile_digest != phase.evaluator_profile_digest:
                raise ValueError(
                    f"{phase.name} evaluator differs from its registered profile"
                )
        self._database = database
        self._profile = profile
        self._evaluators = dict(phase_evaluators)
        self._lock = threading.RLock()
        self._used_authorities: dict[str, int] = {}
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS composite_hidden_receipts(
                    receipt_id TEXT PRIMARY KEY,
                    evaluation_key TEXT NOT NULL UNIQUE,
                    profile_digest TEXT NOT NULL,
                    candidate_digest TEXT NOT NULL,
                    reservation_id TEXT NOT NULL,
                    correctness_receipt_id TEXT NOT NULL,
                    quality_receipt_id TEXT NOT NULL,
                    performance_receipt_id TEXT NOT NULL,
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
        raise RuntimeError("composite hidden evaluator cannot serve visible work")

    def hidden_evaluate(
        self,
        candidate: bytes,
        reservation: EvaluationReservation,
        evaluation_key: str,
    ) -> EvaluationReceipt:
        self._validate_request(reservation, evaluation_key)
        phase_receipts: dict[str, EvaluationReceipt] = {}
        phase_results: dict[str, EvaluationResult] = {}
        for phase in self._profile.phases:
            phase_reservation = self._phase_reservation(reservation, phase)
            evaluator = self._evaluators[phase.name]
            phase_key = f"{evaluation_key}:{phase.name}"
            phase_receipt = evaluator.hidden_evaluate(
                candidate, phase_reservation, phase_key
            )
            phase_receipts[phase.name] = phase_receipt
            phase_results[phase.name] = evaluator.resolve(
                phase_receipt,
                candidate,
                phase_reservation,
                EvaluationScope.HIDDEN,
            )
        result = self._combine(
            candidate, reservation, evaluation_key, phase_results
        )
        receipt = self._receipt(
            candidate, reservation, evaluation_key, phase_receipts
        )
        expected = self._row_values(
            receipt,
            candidate,
            reservation,
            evaluation_key,
            phase_receipts,
            result,
        )
        with self._lock:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM composite_hidden_receipts "
                    "WHERE evaluation_key = ?",
                    (evaluation_key,),
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO composite_hidden_receipts("
                        "receipt_id, evaluation_key, profile_digest, "
                        "candidate_digest, reservation_id, "
                        "correctness_receipt_id, quality_receipt_id, "
                        "performance_receipt_id, result_json, result_digest) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        expected,
                    )
                elif self._stored_values(row) != expected:
                    connection.rollback()
                    raise RuntimeError(
                        "composite hidden evaluation key changed across retry"
                    )
                connection.commit()
        self._used_authorities[receipt.value] = self._phase_used_seconds(
            phase_receipts
        )
        return receipt

    def resolve(
        self,
        receipt: EvaluationReceipt,
        candidate: bytes,
        reservation: EvaluationReservation | None,
        scope: EvaluationScope,
    ) -> EvaluationResult:
        if reservation is None:
            raise ValueError("hidden composite resolution requires a reservation")
        if scope is not EvaluationScope.HIDDEN:
            raise ValueError("hidden composite receipt has the wrong scope")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM composite_hidden_receipts WHERE receipt_id = ?",
                (receipt.value,),
            ).fetchone()
        if row is None:
            raise RuntimeError("composite hidden receipt is unavailable")
        evaluation_key = str(row["evaluation_key"])
        self._validate_request(reservation, evaluation_key)
        phase_receipts = {
            name: EvaluationReceipt(str(row[f"{name}_receipt_id"]))
            for name in _PHASE_NAMES
        }
        phase_results: dict[str, EvaluationResult] = {}
        for phase in self._profile.phases:
            phase_results[phase.name] = self._evaluators[phase.name].resolve(
                phase_receipts[phase.name],
                candidate,
                self._phase_reservation(reservation, phase),
                EvaluationScope.HIDDEN,
            )
        result = self._combine(
            candidate, reservation, evaluation_key, phase_results
        )
        expected_receipt = self._receipt(
            candidate, reservation, evaluation_key, phase_receipts
        )
        expected = self._row_values(
            expected_receipt,
            candidate,
            reservation,
            evaluation_key,
            phase_receipts,
            result,
        )
        if receipt != expected_receipt or self._stored_values(row) != expected:
            raise RuntimeError("composite hidden receipt binding differs")
        self._used_authorities[receipt.value] = self._phase_used_seconds(
            phase_receipts
        )
        return result

    def used_seconds(self, receipt: EvaluationReceipt) -> int:
        try:
            return self._used_authorities[receipt.value]
        except KeyError as error:
            raise RuntimeError(
                "composite hidden receipt must be resolved before accounting"
            ) from error

    def _validate_request(
        self, reservation: EvaluationReservation, evaluation_key: str
    ) -> None:
        if reservation.scope is not EvaluationScope.HIDDEN:
            raise ValueError("composite hidden evaluator requires hidden scope")
        if reservation.actor_id is not None:
            raise ValueError("hidden composite reservation cannot name an actor")
        if reservation.reserved_seconds != self._profile.reserved_seconds:
            raise ValueError("hidden reservation differs from phase allowances")
        if not evaluation_key.startswith("hidden:") or not _SAFE_ID.fullmatch(
            evaluation_key
        ):
            raise ValueError("composite hidden evaluation key is invalid")

    def _phase_reservation(
        self,
        reservation: EvaluationReservation,
        phase: HiddenEvaluationPhaseProfile,
    ) -> EvaluationReservation:
        phase_id = (
            "evaluation-"
            + digest_value(
                {
                    "profile_digest": self.profile_digest,
                    "outer_reservation_id": reservation.reservation_id,
                    "phase_digest": phase.digest,
                }
            )[7:39]
        )
        return EvaluationReservation(
            reservation_id=phase_id,
            reservation_key=f"{reservation.reservation_key}:{phase.name}",
            campaign_run_id=reservation.campaign_run_id,
            actor_id=None,
            artifact_ref=reservation.artifact_ref,
            scope=EvaluationScope.HIDDEN,
            reserved_seconds=phase.reserved_seconds,
            status=reservation.status,
        )

    def _combine(
        self,
        candidate: bytes,
        reservation: EvaluationReservation,
        evaluation_key: str,
        results: Mapping[str, EvaluationResult],
    ) -> EvaluationResult:
        failures: list[str] = []
        for name in _PHASE_NAMES:
            result = results[name]
            if not result.eligible and not result.failures:
                failures.append(f"{name}:ineligible")
            failures.extend(f"{name}:{failure}" for failure in result.failures)
        eligible = all(results[name].eligible for name in _PHASE_NAMES)
        criterion_units = (
            results["performance"].criterion_units if eligible else 0
        )
        phase_evidence = {
            name: {
                "eligible": results[name].eligible,
                "criterion_units": results[name].criterion_units,
                "failures": list(results[name].failures),
                "evidence_digest": results[name].evidence_digest,
            }
            for name in _PHASE_NAMES
        }
        evidence_digest = digest_value(
            {
                "profile_digest": self.profile_digest,
                "hidden_workload_manifest_digest": (
                    self._profile.hidden_workload_manifest_digest
                ),
                "evaluation_key": evaluation_key,
                "candidate_digest": digest_bytes(candidate),
                "reservation_id": reservation.reservation_id,
                "phases": phase_evidence,
                "eligible": eligible,
                "criterion_units": criterion_units,
                "failures": failures,
            }
        )
        return EvaluationResult(
            eligible=eligible,
            criterion_units=criterion_units,
            failures=tuple(failures),
            evidence_digest=evidence_digest,
            diagnostics={
                "hidden_workload_manifest_digest": (
                    self._profile.hidden_workload_manifest_digest
                ),
                "phase_evidence_digests": {
                    name: results[name].evidence_digest for name in _PHASE_NAMES
                },
                "performance_criterion_units": results[
                    "performance"
                ].criterion_units,
            },
        )

    def _phase_used_seconds(
        self, receipts: Mapping[str, EvaluationReceipt]
    ) -> int:
        return sum(
            self._evaluators[name].used_seconds(receipts[name])
            for name in _PHASE_NAMES
        )

    def _receipt(
        self,
        candidate: bytes,
        reservation: EvaluationReservation,
        evaluation_key: str,
        receipts: Mapping[str, EvaluationReceipt],
    ) -> EvaluationReceipt:
        return EvaluationReceipt(
            "evalreceipt-"
            + digest_value(
                {
                    "profile_digest": self.profile_digest,
                    "evaluation_key": evaluation_key,
                    "candidate_digest": digest_bytes(candidate),
                    "reservation_id": reservation.reservation_id,
                    "phase_receipts": {
                        name: receipts[name].value for name in _PHASE_NAMES
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
        phase_receipts: Mapping[str, EvaluationReceipt],
        result: EvaluationResult,
    ) -> tuple[object, ...]:
        result_document = _result_document(result)
        return (
            receipt.value,
            evaluation_key,
            self.profile_digest,
            digest_bytes(candidate),
            reservation.reservation_id,
            *(phase_receipts[name].value for name in _PHASE_NAMES),
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
                "correctness_receipt_id",
                "quality_receipt_id",
                "performance_receipt_id",
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


def _result_document(result: EvaluationResult) -> dict[str, object]:
    return {
        "eligible": result.eligible,
        "criterion_units": result.criterion_units,
        "failures": list(result.failures),
        "evidence_digest": result.evidence_digest,
        "diagnostics": dict(result.diagnostics),
    }
