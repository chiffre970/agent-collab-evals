"""Quality-repetition adapter over the durable compute execution port."""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ..campaigns.model_serving import ModelServingCampaign
from ..canonical import canonical_json_bytes, digest_bytes, digest_value, parse_json
from ..compute_backend import ComputeExecutionRequest, ComputeExecutionStatus
from ..evaluation import EvaluationInProgress, EvaluationReservation, EvaluationScope
from ..ports import ComputeBackend
from .quality_series_evaluator import QualityRepetitionReceipt


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}")


@dataclass(frozen=True, slots=True)
class ComputeQualityRepetitionProfile:
    """Frozen identity for quality evidence produced by a compute backend."""

    profile_id: str
    campaign_manifest_digest: str
    hidden_workload_manifest_digest: str
    quality_profile_digest: str
    quality_workload_digest: str
    compute_execution_profile_digest: str
    repetitions: int
    maximum_collection_seconds: int

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.profile_id):
            raise ValueError("compute quality profile ID is invalid")
        for value in (
            self.campaign_manifest_digest,
            self.hidden_workload_manifest_digest,
            self.quality_profile_digest,
            self.quality_workload_digest,
            self.compute_execution_profile_digest,
        ):
            if not _is_digest(value):
                raise ValueError("compute quality profile digest is invalid")
        if type(self.repetitions) is not int or self.repetitions < 1:
            raise ValueError("compute quality repetitions must be positive")
        if (
            type(self.maximum_collection_seconds) is not int
            or not 0 <= self.maximum_collection_seconds <= 300
        ):
            raise ValueError("compute quality collection limit is invalid")

    @property
    def digest(self) -> str:
        return digest_value(
            {
                "adapter": "compute-quality-repetition-backend/v0alpha1",
                "profile_id": self.profile_id,
                "campaign_manifest_digest": self.campaign_manifest_digest,
                "hidden_workload_manifest_digest": (
                    self.hidden_workload_manifest_digest
                ),
                "quality_profile_digest": self.quality_profile_digest,
                "quality_workload_digest": self.quality_workload_digest,
                "compute_execution_profile_digest": (
                    self.compute_execution_profile_digest
                ),
                "repetitions": self.repetitions,
                "maximum_collection_seconds": self.maximum_collection_seconds,
            }
        )


class ComputeQualityRepetitionBackend:
    """Run and resolve one hidden quality repetition through ComputeBackend."""

    def __init__(
        self,
        database: Path,
        campaign: ModelServingCampaign,
        profile: ComputeQualityRepetitionProfile,
        backend: ComputeBackend,
    ) -> None:
        if campaign.manifest_digest != profile.campaign_manifest_digest:
            raise ValueError("quality compute campaign differs from the profile")
        if backend.profile_digest != profile.compute_execution_profile_digest:
            raise ValueError("quality compute backend differs from the profile")
        self._database = database
        self._campaign = campaign
        self._profile = profile
        self._backend = backend
        self._lock = threading.RLock()
        self._used_authorities: dict[str, int] = {}
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS compute_quality_receipts(
                    receipt_id TEXT PRIMARY KEY,
                    execution_key TEXT NOT NULL UNIQUE,
                    profile_digest TEXT NOT NULL,
                    candidate_digest TEXT NOT NULL,
                    reservation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    repetition INTEGER NOT NULL,
                    request_json TEXT NOT NULL,
                    execution_id TEXT NOT NULL,
                    execution_request_digest TEXT NOT NULL
                )
                """
            )
            connection.commit()

    @property
    def profile_digest(self) -> str:
        return self._profile.digest

    def evaluate(
        self,
        candidate: bytes,
        reservation: EvaluationReservation,
        execution_key: str,
        *,
        role: str,
        repetition: int,
    ) -> QualityRepetitionReceipt:
        request = self._request(
            candidate, reservation, execution_key, role, repetition
        )
        execution = self._backend.submit(request, candidate)
        deadline = time.monotonic() + request.maximum_seconds + 60
        nonterminal = {
            ComputeExecutionStatus.REGISTERED,
            ComputeExecutionStatus.DISPATCHING,
            ComputeExecutionStatus.DISPATCHED,
        }
        while execution.status in nonterminal:
            remaining = max(0, round(deadline - time.monotonic()))
            if remaining == 0:
                raise EvaluationInProgress(
                    "quality repetition remains nonterminal at the caller deadline"
                )
            if execution.status is ComputeExecutionStatus.DISPATCHED:
                execution = self._backend.collect(
                    request,
                    timeout_seconds=min(
                        self._profile.maximum_collection_seconds, remaining
                    ),
                )
            else:
                time.sleep(min(0.05, remaining))
                execution = self._backend.submit(request, candidate)
        if execution.status is not ComputeExecutionStatus.COMPLETE:
            raise RuntimeError(
                "quality compute execution failed: "
                f"{execution.status.value}:{execution.failure or 'unknown'}"
            )
        _, evidence = self._backend.resolve(request)
        self._quality_run(evidence, request, role, repetition)
        receipt = self._receipt(execution.execution_id, request)
        expected = (
            receipt.value,
            execution_key,
            self.profile_digest,
            request.candidate_digest,
            reservation.reservation_id,
            role,
            repetition,
            canonical_json_bytes(request.document).decode(),
            execution.execution_id,
            request.request_digest,
        )
        with self._lock:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM compute_quality_receipts WHERE execution_key = ?",
                    (execution_key,),
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO compute_quality_receipts("
                        "receipt_id, execution_key, profile_digest, "
                        "candidate_digest, reservation_id, role, repetition, "
                        "request_json, execution_id, execution_request_digest) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        expected,
                    )
                elif self._stored_values(row) != expected:
                    connection.rollback()
                    raise RuntimeError("quality execution key changed across retry")
                connection.commit()
        return receipt

    def resolve(
        self,
        receipt: QualityRepetitionReceipt,
        candidate: bytes,
        reservation: EvaluationReservation,
        *,
        role: str,
        repetition: int,
    ) -> Mapping[str, object]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM compute_quality_receipts WHERE receipt_id = ?",
                (receipt.value,),
            ).fetchone()
        if row is None:
            raise RuntimeError("quality compute receipt is unavailable")
        request = self._request(
            candidate,
            reservation,
            str(row["execution_key"]),
            role,
            repetition,
        )
        expected_receipt = self._receipt(str(row["execution_id"]), request)
        expected = (
            expected_receipt.value,
            request.execution_key,
            self.profile_digest,
            request.candidate_digest,
            reservation.reservation_id,
            role,
            repetition,
            canonical_json_bytes(request.document).decode(),
            str(row["execution_id"]),
            request.request_digest,
        )
        if receipt != expected_receipt or self._stored_values(row) != expected:
            raise RuntimeError("quality compute receipt binding differs")
        execution, evidence = self._backend.resolve(request)
        if execution.execution_id != str(row["execution_id"]):
            raise RuntimeError("quality compute execution identity differs")
        if execution.used_seconds is None:
            raise RuntimeError("quality compute execution has no measured usage")
        run = self._quality_run(evidence, request, role, repetition)
        self._used_authorities[receipt.value] = execution.used_seconds
        return run

    def used_seconds(self, receipt: QualityRepetitionReceipt) -> int:
        try:
            return self._used_authorities[receipt.value]
        except KeyError as error:
            raise RuntimeError(
                "quality compute receipt must be resolved before accounting"
            ) from error

    def _request(
        self,
        candidate: bytes,
        reservation: EvaluationReservation,
        execution_key: str,
        role: str,
        repetition: int,
    ) -> ComputeExecutionRequest:
        if reservation.scope is not EvaluationScope.HIDDEN:
            raise ValueError("quality compute requires a hidden reservation")
        if role not in {"reference", "candidate"}:
            raise ValueError("quality compute role is invalid")
        if (
            type(repetition) is not int
            or not 1 <= repetition <= self._profile.repetitions
        ):
            raise ValueError("quality compute repetition is invalid")
        if not execution_key.endswith(f":quality:{repetition}:{role}"):
            raise ValueError("quality execution key differs from role or repetition")
        descriptor = self._campaign.validate_candidate_document(
            parse_json(candidate.decode("utf-8"))
        )
        return ComputeExecutionRequest(
            execution_key=execution_key,
            campaign_run_id=reservation.campaign_run_id,
            reservation_id=reservation.reservation_id,
            scope=EvaluationScope.HIDDEN,
            candidate_digest=digest_bytes(candidate),
            candidate_manifest_digest=descriptor.manifest_digest,
            evaluator_profile_digest=self.profile_digest,
            maximum_seconds=reservation.reserved_seconds,
        )

    def _quality_run(
        self,
        evidence: Mapping[str, object],
        request: ComputeExecutionRequest,
        role: str,
        repetition: int,
    ) -> Mapping[str, object]:
        record = evidence.get("quality_evaluation")
        if not isinstance(record, Mapping) or set(record) != {
            "schema_version",
            "campaign_manifest_digest",
            "hidden_workload_manifest_digest",
            "quality_profile_digest",
            "quality_workload_digest",
            "candidate_digest",
            "candidate_manifest_digest",
            "role",
            "repetition",
            "run",
        }:
            raise RuntimeError("quality compute evidence fields differ")
        expected = {
            "schema_version": "serving-quality-compute-evidence/v0alpha1",
            "campaign_manifest_digest": self._profile.campaign_manifest_digest,
            "hidden_workload_manifest_digest": (
                self._profile.hidden_workload_manifest_digest
            ),
            "quality_profile_digest": self._profile.quality_profile_digest,
            "quality_workload_digest": self._profile.quality_workload_digest,
            "candidate_digest": request.candidate_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "role": role,
            "repetition": repetition,
        }
        if any(record.get(key) != value for key, value in expected.items()):
            raise RuntimeError("quality compute evidence identity differs")
        run = record.get("run")
        if not isinstance(run, Mapping):
            raise RuntimeError("quality compute evidence has no normalized run")
        return run

    def _receipt(
        self, execution_id: str, request: ComputeExecutionRequest
    ) -> QualityRepetitionReceipt:
        return QualityRepetitionReceipt(
            "qualityreceipt-"
            + digest_value(
                {
                    "profile_digest": self.profile_digest,
                    "execution_id": execution_id,
                    "request_digest": request.request_digest,
                }
            )[7:39]
        )

    @staticmethod
    def _stored_values(row: sqlite3.Row) -> tuple[object, ...]:
        return tuple(
            row[name]
            for name in (
                "receipt_id",
                "execution_key",
                "profile_digest",
                "candidate_digest",
                "reservation_id",
                "role",
                "repetition",
                "request_json",
                "execution_id",
                "execution_request_digest",
            )
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None
