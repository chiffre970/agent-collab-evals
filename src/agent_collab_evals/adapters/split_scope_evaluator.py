"""Registered evaluator composition with isolated visible and hidden lanes."""

from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from ..canonical import digest_bytes, digest_value
from ..evaluation import (
    EvaluationReceipt,
    EvaluationReservation,
    EvaluationResult,
    EvaluationScope,
)
from ..ports import CandidateEvaluator


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}")


@dataclass(frozen=True, slots=True)
class EvaluationLaneProfile:
    """Frozen authority and workload identity for one evaluation scope."""

    scope: EvaluationScope
    evaluator_profile_digest: str
    compute_backend_profile_digest: str
    workload_digest: str
    compute_account_id: str
    schedule_digest: str
    evidence_namespace: str

    def __post_init__(self) -> None:
        for name in (
            "evaluator_profile_digest",
            "compute_backend_profile_digest",
            "workload_digest",
            "schedule_digest",
        ):
            if not _DIGEST.fullmatch(getattr(self, name)):
                raise ValueError(f"evaluation lane {name} must be SHA-256")
        for name in ("compute_account_id", "evidence_namespace"):
            if not _SAFE_ID.fullmatch(getattr(self, name)):
                raise ValueError(f"evaluation lane {name} is invalid")

    @property
    def digest(self) -> str:
        return digest_value(
            {
                "scope": self.scope.value,
                "evaluator_profile_digest": self.evaluator_profile_digest,
                "compute_backend_profile_digest": self.compute_backend_profile_digest,
                "workload_digest": self.workload_digest,
                "compute_account_id": self.compute_account_id,
                "schedule_digest": self.schedule_digest,
                "evidence_namespace": self.evidence_namespace,
            }
        )


@dataclass(frozen=True, slots=True)
class RegisteredEvaluationProfile:
    """Complete registered identity for public and hidden evaluation."""

    profile_id: str
    campaign_manifest_digest: str
    registration_manifest_digest: str
    visible: EvaluationLaneProfile
    hidden: EvaluationLaneProfile

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.profile_id):
            raise ValueError("registered evaluation profile ID is invalid")
        for value in (
            self.campaign_manifest_digest,
            self.registration_manifest_digest,
        ):
            if not _DIGEST.fullmatch(value):
                raise ValueError("registered evaluation manifest digest is invalid")
        if self.visible.scope is not EvaluationScope.VISIBLE:
            raise ValueError("visible lane has the wrong evaluation scope")
        if self.hidden.scope is not EvaluationScope.HIDDEN:
            raise ValueError("hidden lane has the wrong evaluation scope")
        if self.visible.compute_account_id == self.hidden.compute_account_id:
            raise ValueError("visible and hidden compute accounts must differ")
        if (
            self.visible.evaluator_profile_digest
            == self.hidden.evaluator_profile_digest
        ):
            raise ValueError("visible and hidden evaluator profiles must differ")
        if self.visible.workload_digest == self.hidden.workload_digest:
            raise ValueError("visible and hidden workloads must differ")
        if self.visible.schedule_digest == self.hidden.schedule_digest:
            raise ValueError("visible and hidden schedules must differ")
        if self.visible.evidence_namespace == self.hidden.evidence_namespace:
            raise ValueError("visible and hidden evidence namespaces must differ")

    @property
    def digest(self) -> str:
        return digest_value(
            {
                "adapter": "split-scope-serving-evaluator/v0alpha1",
                "profile_id": self.profile_id,
                "campaign_manifest_digest": self.campaign_manifest_digest,
                "registration_manifest_digest": self.registration_manifest_digest,
                "visible_lane_digest": self.visible.digest,
                "hidden_lane_digest": self.hidden.digest,
            }
        )


class SplitScopeServingEvaluator:
    """Expose isolated scope-specific evaluators as one registered evaluator."""

    def __init__(
        self,
        database: Path,
        profile: RegisteredEvaluationProfile,
        visible_evaluator: CandidateEvaluator,
        hidden_evaluator: CandidateEvaluator,
    ) -> None:
        if visible_evaluator.profile_digest != profile.visible.evaluator_profile_digest:
            raise ValueError("visible evaluator differs from its registered lane")
        if hidden_evaluator.profile_digest != profile.hidden.evaluator_profile_digest:
            raise ValueError("hidden evaluator differs from its registered lane")
        self._database = database
        self._profile = profile
        self._visible_evaluator = visible_evaluator
        self._hidden_evaluator = hidden_evaluator
        self._lock = threading.RLock()
        self._used_authorities: dict[str, int] = {}
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS split_evaluation_receipts(
                    receipt_id TEXT PRIMARY KEY,
                    evaluation_key TEXT NOT NULL UNIQUE,
                    profile_digest TEXT NOT NULL,
                    lane_digest TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    candidate_digest TEXT NOT NULL,
                    reservation_id TEXT,
                    inner_receipt_id TEXT NOT NULL
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
        _validate_evaluation_key(evaluation_key, EvaluationScope.VISIBLE)
        if reservation is not None and reservation.scope is not EvaluationScope.VISIBLE:
            raise ValueError("visible evaluator received a hidden reservation")
        inner = self._visible_evaluator.visible_evaluate(
            candidate, reservation, evaluation_key
        )
        return self._bind(
            candidate, reservation, evaluation_key, EvaluationScope.VISIBLE, inner
        )

    def hidden_evaluate(
        self,
        candidate: bytes,
        reservation: EvaluationReservation,
        evaluation_key: str,
    ) -> EvaluationReceipt:
        _validate_evaluation_key(evaluation_key, EvaluationScope.HIDDEN)
        if reservation.scope is not EvaluationScope.HIDDEN:
            raise ValueError("hidden evaluator received a visible reservation")
        inner = self._hidden_evaluator.hidden_evaluate(
            candidate, reservation, evaluation_key
        )
        return self._bind(
            candidate, reservation, evaluation_key, EvaluationScope.HIDDEN, inner
        )

    def resolve(
        self,
        receipt: EvaluationReceipt,
        candidate: bytes,
        reservation: EvaluationReservation | None,
        scope: EvaluationScope,
    ) -> EvaluationResult:
        row = self._row(receipt)
        lane = self._lane(scope)
        evaluator = self._evaluator(scope)
        reservation_id = reservation.reservation_id if reservation is not None else None
        inner = EvaluationReceipt(str(row["inner_receipt_id"]))
        expected = (
            self.profile_digest,
            lane.digest,
            scope.value,
            digest_bytes(candidate),
            reservation_id,
            self._receipt(
                str(row["evaluation_key"]),
                scope,
                digest_bytes(candidate),
                reservation_id,
                inner,
            ).value,
        )
        actual = (
            str(row["profile_digest"]),
            str(row["lane_digest"]),
            str(row["scope"]),
            str(row["candidate_digest"]),
            row["reservation_id"],
            receipt.value,
        )
        if actual != expected:
            raise RuntimeError("split evaluator receipt binding differs")
        result = evaluator.resolve(inner, candidate, reservation, scope)
        self._used_authorities[receipt.value] = evaluator.used_seconds(inner)
        return result

    def used_seconds(self, receipt: EvaluationReceipt) -> int:
        try:
            return self._used_authorities[receipt.value]
        except KeyError as error:
            raise RuntimeError(
                "split evaluator receipt must be resolved before accounting"
            ) from error

    def _bind(
        self,
        candidate: bytes,
        reservation: EvaluationReservation | None,
        evaluation_key: str,
        scope: EvaluationScope,
        inner: EvaluationReceipt,
    ) -> EvaluationReceipt:
        if not evaluation_key:
            raise ValueError("evaluation key is required")
        lane = self._lane(scope)
        candidate_digest = digest_bytes(candidate)
        reservation_id = reservation.reservation_id if reservation is not None else None
        receipt = self._receipt(
            evaluation_key,
            scope,
            candidate_digest,
            reservation_id,
            inner,
        )
        expected = (
            receipt.value,
            evaluation_key,
            self.profile_digest,
            lane.digest,
            scope.value,
            candidate_digest,
            reservation_id,
            inner.value,
        )
        with self._lock:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM split_evaluation_receipts "
                    "WHERE evaluation_key = ?",
                    (evaluation_key,),
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO split_evaluation_receipts("
                        "receipt_id, evaluation_key, profile_digest, lane_digest, "
                        "scope, candidate_digest, reservation_id, inner_receipt_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        expected,
                    )
                else:
                    actual = tuple(
                        row[name]
                        for name in (
                            "receipt_id",
                            "evaluation_key",
                            "profile_digest",
                            "lane_digest",
                            "scope",
                            "candidate_digest",
                            "reservation_id",
                            "inner_receipt_id",
                        )
                    )
                    if actual != expected:
                        connection.rollback()
                        raise RuntimeError("split evaluation key changed across retry")
                connection.commit()
        return receipt

    def _row(self, receipt: EvaluationReceipt) -> sqlite3.Row:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM split_evaluation_receipts WHERE receipt_id = ?",
                (receipt.value,),
            ).fetchone()
        if row is None:
            raise RuntimeError("split evaluation receipt is unavailable")
        return row

    def _lane(self, scope: EvaluationScope) -> EvaluationLaneProfile:
        return (
            self._profile.visible
            if scope is EvaluationScope.VISIBLE
            else self._profile.hidden
        )

    def _evaluator(self, scope: EvaluationScope) -> CandidateEvaluator:
        return (
            self._visible_evaluator
            if scope is EvaluationScope.VISIBLE
            else self._hidden_evaluator
        )

    def _receipt(
        self,
        evaluation_key: str,
        scope: EvaluationScope,
        candidate_digest: str,
        reservation_id: str | None,
        inner: EvaluationReceipt,
    ) -> EvaluationReceipt:
        return EvaluationReceipt(
            "evalreceipt-"
            + digest_value(
                {
                    "profile_digest": self.profile_digest,
                    "lane_digest": self._lane(scope).digest,
                    "evaluation_key": evaluation_key,
                    "scope": scope.value,
                    "candidate_digest": candidate_digest,
                    "reservation_id": reservation_id,
                    "inner_receipt_id": inner.value,
                }
            )[7:39]
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection


def _validate_evaluation_key(key: str, scope: EvaluationScope) -> None:
    prefixes = (
        ("visible:", "reference:")
        if scope is EvaluationScope.VISIBLE
        else ("hidden:",)
    )
    if not key.startswith(prefixes) or not _SAFE_ID.fullmatch(key):
        raise ValueError("evaluation key is invalid for its scope")
