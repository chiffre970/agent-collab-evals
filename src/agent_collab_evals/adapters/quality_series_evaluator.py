"""Paired reference-relative quality evaluation for serving candidates."""

from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from ..artifacts import ArtifactRef
from ..canonical import canonical_json_bytes, digest_bytes, digest_value
from ..campaigns.serving_quality import QualityPolicy, evaluate_quality_series
from ..evaluation import (
    EvaluationReceipt,
    EvaluationReservation,
    EvaluationResult,
    EvaluationScope,
)


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}")


@dataclass(frozen=True, slots=True)
class QualityRepetitionReceipt:
    value: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"qualityreceipt-[0-9a-f]{32}", self.value):
            raise ValueError("quality repetition receipt is invalid")


class QualityRepetitionBackend(Protocol):
    """Evaluator-owned execution and evidence for one served quality run."""

    @property
    def profile_digest(self) -> str: ...

    def evaluate(
        self,
        candidate: bytes,
        reservation: EvaluationReservation,
        execution_key: str,
        *,
        role: str,
        repetition: int,
    ) -> QualityRepetitionReceipt: ...

    def resolve(
        self,
        receipt: QualityRepetitionReceipt,
        candidate: bytes,
        reservation: EvaluationReservation,
        *,
        role: str,
        repetition: int,
    ) -> Mapping[str, object]: ...

    def used_seconds(self, receipt: QualityRepetitionReceipt) -> int: ...


@dataclass(frozen=True, slots=True)
class QualitySeriesProfile:
    """Registered schedule and authority for paired hidden quality."""

    profile_id: str
    campaign_manifest_digest: str
    hidden_workload_manifest_digest: str
    quality_profile_digest: str
    quality_policy_digest: str
    quality_policy_authority_digest: str
    quality_workload_digest: str
    reference_artifact_ref: str
    reference_candidate_digest: str
    repetition_backend_profile_digest: str
    repetitions: int
    repetition_reserved_seconds: int
    role_order_by_repetition: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.profile_id):
            raise ValueError("quality series profile ID is invalid")
        ArtifactRef(self.reference_artifact_ref)
        for name in (
            "campaign_manifest_digest",
            "hidden_workload_manifest_digest",
            "quality_profile_digest",
            "quality_policy_digest",
            "quality_policy_authority_digest",
            "quality_workload_digest",
            "reference_candidate_digest",
            "repetition_backend_profile_digest",
        ):
            if not _DIGEST.fullmatch(getattr(self, name)):
                raise ValueError(f"quality series {name} must be SHA-256")
        if type(self.repetitions) is not int or self.repetitions < 1:
            raise ValueError("quality series repetitions must be positive")
        if (
            type(self.repetition_reserved_seconds) is not int
            or self.repetition_reserved_seconds < 1
        ):
            raise ValueError("quality repetition allowance must be positive")
        if len(self.role_order_by_repetition) != self.repetitions or any(
            tuple(order) not in {
                ("reference", "candidate"),
                ("candidate", "reference"),
            }
            for order in self.role_order_by_repetition
        ):
            raise ValueError("quality pair order must contain both roles")

    @property
    def reserved_seconds(self) -> int:
        return self.repetitions * 2 * self.repetition_reserved_seconds

    @property
    def digest(self) -> str:
        return digest_value(
            {
                "adapter": "paired-quality-series-evaluator/v0alpha1",
                "profile_id": self.profile_id,
                "campaign_manifest_digest": self.campaign_manifest_digest,
                "hidden_workload_manifest_digest": (
                    self.hidden_workload_manifest_digest
                ),
                "quality_profile_digest": self.quality_profile_digest,
                "quality_policy_digest": self.quality_policy_digest,
                "quality_policy_authority_digest": (
                    self.quality_policy_authority_digest
                ),
                "quality_workload_digest": self.quality_workload_digest,
                "reference_artifact_ref": self.reference_artifact_ref,
                "reference_candidate_digest": self.reference_candidate_digest,
                "repetition_backend_profile_digest": (
                    self.repetition_backend_profile_digest
                ),
                "repetitions": self.repetitions,
                "repetition_reserved_seconds": self.repetition_reserved_seconds,
                "role_order_by_repetition": self.role_order_by_repetition,
            }
        )


class PairedQualitySeriesEvaluator:
    """Apply the frozen quality policy to fresh paired served-output runs."""

    def __init__(
        self,
        database: Path,
        profile: QualitySeriesProfile,
        policy: QualityPolicy,
        reference_candidate: bytes,
        backend: QualityRepetitionBackend,
    ) -> None:
        if policy.digest != profile.quality_policy_digest:
            raise ValueError("quality policy differs from the series profile")
        if quality_policy_authority_digest(policy) != (
            profile.quality_policy_authority_digest
        ):
            raise ValueError("quality policy authority differs from the profile")
        if policy.quality_profile_digest != profile.quality_profile_digest:
            raise ValueError("quality profile differs from the series profile")
        if policy.quality_workload_digest != profile.quality_workload_digest:
            raise ValueError("quality workload differs from the series profile")
        if policy.repetitions != profile.repetitions:
            raise ValueError("quality repetitions differ from the policy")
        if digest_bytes(reference_candidate) != profile.reference_candidate_digest:
            raise ValueError("reference candidate differs from the series profile")
        if backend.profile_digest != profile.repetition_backend_profile_digest:
            raise ValueError("quality backend differs from the series profile")
        self._database = database
        self._profile = profile
        self._policy = policy
        self._reference_candidate = reference_candidate
        self._backend = backend
        self._lock = threading.RLock()
        self._used_authorities: dict[str, int] = {}
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quality_series_receipts(
                    receipt_id TEXT PRIMARY KEY,
                    evaluation_key TEXT NOT NULL UNIQUE,
                    profile_digest TEXT NOT NULL,
                    candidate_digest TEXT NOT NULL,
                    reservation_id TEXT NOT NULL,
                    run_receipts_json TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    decision_digest TEXT NOT NULL,
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
        raise RuntimeError("paired hidden quality cannot serve visible work")

    def hidden_evaluate(
        self,
        candidate: bytes,
        reservation: EvaluationReservation,
        evaluation_key: str,
    ) -> EvaluationReceipt:
        self._validate_request(reservation, evaluation_key)
        receipts: dict[str, QualityRepetitionReceipt] = {}
        runs: dict[str, Mapping[str, object]] = {}
        for repetition, order in enumerate(
            self._profile.role_order_by_repetition, start=1
        ):
            for role in order:
                key = _run_key(role, repetition)
                run_reservation = self._run_reservation(
                    reservation, role, repetition
                )
                run_candidate = self._candidate_for_role(candidate, role)
                run_receipt = self._backend.evaluate(
                    run_candidate,
                    run_reservation,
                    f"{evaluation_key}:quality:{repetition}:{role}",
                    role=role,
                    repetition=repetition,
                )
                receipts[key] = run_receipt
                runs[key] = self._backend.resolve(
                    run_receipt,
                    run_candidate,
                    run_reservation,
                    role=role,
                    repetition=repetition,
                )
        decision = self._decision(runs)
        result = self._result(candidate, reservation, evaluation_key, receipts, decision)
        receipt = self._receipt(
            candidate, reservation, evaluation_key, receipts
        )
        expected = self._row_values(
            receipt,
            candidate,
            reservation,
            evaluation_key,
            receipts,
            decision,
            result,
        )
        with self._lock:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM quality_series_receipts WHERE evaluation_key = ?",
                    (evaluation_key,),
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO quality_series_receipts("
                        "receipt_id, evaluation_key, profile_digest, "
                        "candidate_digest, reservation_id, run_receipts_json, "
                        "decision_json, decision_digest, result_json, result_digest) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        expected,
                    )
                elif self._stored_values(row) != expected:
                    connection.rollback()
                    raise RuntimeError("quality series changed across retry")
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
            raise ValueError("quality series resolution requires a reservation")
        if scope is not EvaluationScope.HIDDEN:
            raise ValueError("quality series receipt has the wrong scope")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM quality_series_receipts WHERE receipt_id = ?",
                (receipt.value,),
            ).fetchone()
        if row is None:
            raise RuntimeError("quality series receipt is unavailable")
        evaluation_key = str(row["evaluation_key"])
        self._validate_request(reservation, evaluation_key)
        receipt_document = _object_from_json(str(row["run_receipts_json"]))
        expected_keys = {
            _run_key(role, repetition)
            for repetition in range(1, self._profile.repetitions + 1)
            for role in ("reference", "candidate")
        }
        if set(receipt_document) != expected_keys:
            raise RuntimeError("quality series run receipt set differs")
        receipts = {
            key: QualityRepetitionReceipt(str(value))
            for key, value in receipt_document.items()
        }
        runs: dict[str, Mapping[str, object]] = {}
        for repetition in range(1, self._profile.repetitions + 1):
            for role in ("reference", "candidate"):
                key = _run_key(role, repetition)
                runs[key] = self._backend.resolve(
                    receipts[key],
                    self._candidate_for_role(candidate, role),
                    self._run_reservation(reservation, role, repetition),
                    role=role,
                    repetition=repetition,
                )
        decision = self._decision(runs)
        result = self._result(candidate, reservation, evaluation_key, receipts, decision)
        expected_receipt = self._receipt(
            candidate, reservation, evaluation_key, receipts
        )
        expected = self._row_values(
            expected_receipt,
            candidate,
            reservation,
            evaluation_key,
            receipts,
            decision,
            result,
        )
        if receipt != expected_receipt or self._stored_values(row) != expected:
            raise RuntimeError("quality series receipt binding differs")
        self._used_authorities[receipt.value] = self._used_seconds(receipts)
        return result

    def used_seconds(self, receipt: EvaluationReceipt) -> int:
        try:
            return self._used_authorities[receipt.value]
        except KeyError as error:
            raise RuntimeError(
                "quality series receipt must be resolved before accounting"
            ) from error

    def _validate_request(
        self, reservation: EvaluationReservation, evaluation_key: str
    ) -> None:
        if reservation.scope is not EvaluationScope.HIDDEN:
            raise ValueError("quality series requires hidden scope")
        if reservation.actor_id is not None:
            raise ValueError("quality series reservation cannot name an actor")
        if reservation.reserved_seconds != self._profile.reserved_seconds:
            raise ValueError("quality reservation differs from the paired schedule")
        if not evaluation_key.startswith("hidden:") or not _SAFE_ID.fullmatch(
            evaluation_key
        ):
            raise ValueError("quality series evaluation key is invalid")

    def _run_reservation(
        self,
        outer: EvaluationReservation,
        role: str,
        repetition: int,
    ) -> EvaluationReservation:
        return EvaluationReservation(
            reservation_id=(
                "evaluation-"
                + digest_value(
                    {
                        "profile_digest": self.profile_digest,
                        "outer_reservation_id": outer.reservation_id,
                        "role": role,
                        "repetition": repetition,
                    }
                )[7:39]
            ),
            reservation_key=f"{outer.reservation_key}:quality:{repetition}:{role}",
            campaign_run_id=outer.campaign_run_id,
            actor_id=None,
            artifact_ref=(
                ArtifactRef(self._profile.reference_artifact_ref)
                if role == "reference"
                else outer.artifact_ref
            ),
            scope=EvaluationScope.HIDDEN,
            reserved_seconds=self._profile.repetition_reserved_seconds,
            status=outer.status,
        )

    def _candidate_for_role(self, candidate: bytes, role: str) -> bytes:
        return self._reference_candidate if role == "reference" else candidate

    def _decision(
        self, runs: Mapping[str, Mapping[str, object]]
    ) -> Mapping[str, object]:
        references = tuple(
            runs[_run_key("reference", repetition)]
            for repetition in range(1, self._profile.repetitions + 1)
        )
        candidates = tuple(
            runs[_run_key("candidate", repetition)]
            for repetition in range(1, self._profile.repetitions + 1)
        )
        return evaluate_quality_series(self._policy, references, candidates)

    def _result(
        self,
        candidate: bytes,
        reservation: EvaluationReservation,
        evaluation_key: str,
        receipts: Mapping[str, QualityRepetitionReceipt],
        decision: Mapping[str, object],
    ) -> EvaluationResult:
        aggregate = decision.get("aggregate")
        if not isinstance(aggregate, Mapping):
            raise RuntimeError("quality decision has no aggregate result")
        criterion = aggregate.get("delta_ppm")
        failures = decision.get("failures")
        eligible = decision.get("eligible")
        if (
            type(criterion) is not int
            or type(eligible) is not bool
            or not isinstance(failures, list)
            or any(not isinstance(value, str) or not value for value in failures)
        ):
            raise RuntimeError("quality decision result is invalid")
        decision_digest = digest_value(decision)
        evidence_digest = digest_value(
            {
                "profile_digest": self.profile_digest,
                "evaluation_key": evaluation_key,
                "candidate_digest": digest_bytes(candidate),
                "reservation_id": reservation.reservation_id,
                "run_receipts": {
                    key: receipts[key].value for key in sorted(receipts)
                },
                "decision_digest": decision_digest,
            }
        )
        return EvaluationResult(
            eligible=eligible,
            criterion_units=criterion,
            failures=tuple(failures),
            evidence_digest=evidence_digest,
            diagnostics={
                "quality_policy_digest": self._policy.digest,
                "quality_decision_digest": decision_digest,
                "aggregate_delta_ppm": criterion,
                "aggregate_lower_bound_ppm": aggregate.get("lower_bound_ppm"),
                "paired_repetitions": self._profile.repetitions,
            },
        )

    def _used_seconds(
        self, receipts: Mapping[str, QualityRepetitionReceipt]
    ) -> int:
        return sum(
            self._backend.used_seconds(receipts[key]) for key in sorted(receipts)
        )

    def _receipt(
        self,
        candidate: bytes,
        reservation: EvaluationReservation,
        evaluation_key: str,
        receipts: Mapping[str, QualityRepetitionReceipt],
    ) -> EvaluationReceipt:
        return EvaluationReceipt(
            "evalreceipt-"
            + digest_value(
                {
                    "profile_digest": self.profile_digest,
                    "evaluation_key": evaluation_key,
                    "candidate_digest": digest_bytes(candidate),
                    "reservation_id": reservation.reservation_id,
                    "run_receipts": {
                        key: receipts[key].value for key in sorted(receipts)
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
        receipts: Mapping[str, QualityRepetitionReceipt],
        decision: Mapping[str, object],
        result: EvaluationResult,
    ) -> tuple[object, ...]:
        receipt_document = {
            key: receipts[key].value for key in sorted(receipts)
        }
        result_document = _result_document(result)
        return (
            receipt.value,
            evaluation_key,
            self.profile_digest,
            digest_bytes(candidate),
            reservation.reservation_id,
            canonical_json_bytes(receipt_document).decode(),
            canonical_json_bytes(decision).decode(),
            digest_value(decision),
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
                "run_receipts_json",
                "decision_json",
                "decision_digest",
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


def _run_key(role: str, repetition: int) -> str:
    return f"{role}:{repetition}"


def quality_policy_authority_digest(policy: QualityPolicy) -> str:
    """Bind parsed policy values without a host-specific filesystem path."""

    return digest_value(
        {
            "digest": policy.digest,
            "quality_profile_digest": policy.quality_profile_digest,
            "quality_workload_digest": policy.quality_workload_digest,
            "repetitions": policy.repetitions,
            "case_count": policy.case_count,
            "families": policy.families,
            "aggregate_margin_ppm": policy.aggregate_margin_ppm,
            "family_margin_ppm": policy.family_margin_ppm,
            "confidence_ppm": policy.confidence_ppm,
            "bootstrap_resamples": policy.bootstrap_resamples,
            "bootstrap_seed": policy.bootstrap_seed,
            "reference_measurement_id": policy.reference_measurement_id,
            "reference_receipt_digests": policy.reference_receipt_digests,
            "clean_control_measurement_id": policy.clean_control_measurement_id,
            "clean_control_receipt_digests": policy.clean_control_receipt_digests,
        }
    )


def _object_from_json(value: str) -> Mapping[str, object]:
    from ..canonical import parse_json

    document = parse_json(value)
    if not isinstance(document, dict):
        raise RuntimeError("quality series receipt document is invalid")
    return document


def _result_document(result: EvaluationResult) -> dict[str, object]:
    return {
        "eligible": result.eligible,
        "criterion_units": result.criterion_units,
        "failures": list(result.failures),
        "evidence_digest": result.evidence_digest,
        "diagnostics": dict(result.diagnostics),
    }
