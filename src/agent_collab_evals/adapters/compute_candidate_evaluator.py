"""Candidate evaluator backed by one durable hidden compute execution."""

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
from ..evaluation import (
    EvaluationInProgress,
    EvaluationReceipt,
    EvaluationReservation,
    EvaluationResult,
    EvaluationScope,
)
from ..ports import ComputeBackend


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}")
_PHASES = {"correctness", "performance"}


@dataclass(frozen=True, slots=True)
class ComputeCandidateEvaluationProfile:
    """Frozen authority for one hidden candidate-evaluation phase."""

    profile_id: str
    phase: str
    campaign_manifest_digest: str
    hidden_workload_manifest_digest: str
    workload_digest: str
    compute_execution_profile_digest: str
    maximum_collection_seconds: int

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.profile_id):
            raise ValueError("compute candidate profile ID is invalid")
        if self.phase not in _PHASES:
            raise ValueError("compute candidate phase is invalid")
        for value in (
            self.campaign_manifest_digest,
            self.hidden_workload_manifest_digest,
            self.workload_digest,
            self.compute_execution_profile_digest,
        ):
            if not _DIGEST.fullmatch(value):
                raise ValueError("compute candidate profile digest is invalid")
        if (
            type(self.maximum_collection_seconds) is not int
            or not 0 <= self.maximum_collection_seconds <= 300
        ):
            raise ValueError("compute candidate collection limit is invalid")

    @property
    def digest(self) -> str:
        return digest_value(
            {
                "adapter": "compute-candidate-evaluator/v0alpha1",
                "profile_id": self.profile_id,
                "phase": self.phase,
                "campaign_manifest_digest": self.campaign_manifest_digest,
                "hidden_workload_manifest_digest": (
                    self.hidden_workload_manifest_digest
                ),
                "workload_digest": self.workload_digest,
                "compute_execution_profile_digest": (
                    self.compute_execution_profile_digest
                ),
                "maximum_collection_seconds": self.maximum_collection_seconds,
            }
        )


class ComputeCandidateEvaluator:
    """Map one hidden phase to durable compute and normalized evidence."""

    def __init__(
        self,
        database: Path,
        campaign: ModelServingCampaign,
        profile: ComputeCandidateEvaluationProfile,
        backend: ComputeBackend,
    ) -> None:
        if campaign.manifest_digest != profile.campaign_manifest_digest:
            raise ValueError("compute candidate campaign differs from the profile")
        if backend.profile_digest != profile.compute_execution_profile_digest:
            raise ValueError("compute candidate backend differs from the profile")
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
                CREATE TABLE IF NOT EXISTS compute_candidate_receipts(
                    receipt_id TEXT PRIMARY KEY,
                    evaluation_key TEXT NOT NULL UNIQUE,
                    profile_digest TEXT NOT NULL,
                    candidate_digest TEXT NOT NULL,
                    reservation_id TEXT NOT NULL,
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

    def visible_evaluate(
        self,
        candidate: bytes,
        reservation: EvaluationReservation | None,
        evaluation_key: str,
    ) -> EvaluationReceipt:
        raise RuntimeError("hidden compute candidate evaluator cannot serve visible work")

    def hidden_evaluate(
        self,
        candidate: bytes,
        reservation: EvaluationReservation,
        evaluation_key: str,
    ) -> EvaluationReceipt:
        request = self._request(candidate, reservation, evaluation_key)
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
                    "candidate phase remains nonterminal at the caller deadline"
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
                "candidate phase execution failed: "
                f"{execution.status.value}:{execution.failure or 'unknown'}"
            )
        _, evidence = self._backend.resolve(request)
        self._result(evidence, request)
        receipt = self._receipt(execution.execution_id, request)
        expected = (
            receipt.value,
            evaluation_key,
            self.profile_digest,
            request.candidate_digest,
            reservation.reservation_id,
            canonical_json_bytes(request.document).decode(),
            execution.execution_id,
            request.request_digest,
        )
        with self._lock:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM compute_candidate_receipts "
                    "WHERE evaluation_key = ?",
                    (evaluation_key,),
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO compute_candidate_receipts("
                        "receipt_id, evaluation_key, profile_digest, "
                        "candidate_digest, reservation_id, request_json, "
                        "execution_id, execution_request_digest) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        expected,
                    )
                elif self._stored_values(row) != expected:
                    connection.rollback()
                    raise RuntimeError("candidate phase key changed across retry")
                connection.commit()
        return receipt

    def resolve(
        self,
        receipt: EvaluationReceipt,
        candidate: bytes,
        reservation: EvaluationReservation | None,
        scope: EvaluationScope,
    ) -> EvaluationResult:
        if reservation is None:
            raise ValueError("candidate phase resolution requires a reservation")
        if scope is not EvaluationScope.HIDDEN:
            raise ValueError("candidate phase receipt has the wrong scope")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM compute_candidate_receipts WHERE receipt_id = ?",
                (receipt.value,),
            ).fetchone()
        if row is None:
            raise RuntimeError("candidate phase receipt is unavailable")
        request = self._request(
            candidate, reservation, str(row["evaluation_key"])
        )
        expected_receipt = self._receipt(str(row["execution_id"]), request)
        expected = (
            expected_receipt.value,
            request.execution_key,
            self.profile_digest,
            request.candidate_digest,
            reservation.reservation_id,
            canonical_json_bytes(request.document).decode(),
            str(row["execution_id"]),
            request.request_digest,
        )
        if receipt != expected_receipt or self._stored_values(row) != expected:
            raise RuntimeError("candidate phase receipt binding differs")
        execution, evidence = self._backend.resolve(request)
        if execution.execution_id != str(row["execution_id"]):
            raise RuntimeError("candidate phase execution identity differs")
        if execution.used_seconds is None:
            raise RuntimeError("candidate phase execution has no measured usage")
        result = self._result(evidence, request)
        self._used_authorities[receipt.value] = execution.used_seconds
        return result

    def used_seconds(self, receipt: EvaluationReceipt) -> int:
        try:
            return self._used_authorities[receipt.value]
        except KeyError as error:
            raise RuntimeError(
                "candidate phase receipt must be resolved before accounting"
            ) from error

    def _request(
        self,
        candidate: bytes,
        reservation: EvaluationReservation,
        evaluation_key: str,
    ) -> ComputeExecutionRequest:
        if reservation.scope is not EvaluationScope.HIDDEN:
            raise ValueError("candidate phase requires a hidden reservation")
        if reservation.actor_id is not None:
            raise ValueError("hidden candidate phase cannot name an actor")
        if not evaluation_key.endswith(f":{self._profile.phase}"):
            raise ValueError("candidate phase key differs from its profile")
        descriptor = self._campaign.validate_candidate_document(
            parse_json(candidate.decode("utf-8"))
        )
        return ComputeExecutionRequest(
            execution_key=evaluation_key,
            campaign_run_id=reservation.campaign_run_id,
            reservation_id=reservation.reservation_id,
            scope=EvaluationScope.HIDDEN,
            candidate_digest=digest_bytes(candidate),
            candidate_manifest_digest=descriptor.manifest_digest,
            evaluator_profile_digest=self.profile_digest,
            maximum_seconds=reservation.reserved_seconds,
        )

    def _result(
        self,
        evidence: Mapping[str, object],
        request: ComputeExecutionRequest,
    ) -> EvaluationResult:
        result = evidence.get("result")
        if not isinstance(result, Mapping) or set(result) != {
            "candidate_evaluation"
        }:
            raise RuntimeError("candidate phase result envelope differs")
        record = result.get("candidate_evaluation")
        expected_fields = {
            "schema_version",
            "phase",
            "campaign_manifest_digest",
            "hidden_workload_manifest_digest",
            "workload_digest",
            "candidate_digest",
            "candidate_manifest_digest",
            "eligible",
            "criterion_units",
            "failures",
            "diagnostics",
            "result_evidence_digest",
        }
        if not isinstance(record, Mapping) or set(record) != expected_fields:
            raise RuntimeError("candidate phase evidence fields differ")
        expected_identity = {
            "schema_version": "serving-candidate-compute-evidence/v0alpha1",
            "phase": self._profile.phase,
            "campaign_manifest_digest": self._profile.campaign_manifest_digest,
            "hidden_workload_manifest_digest": (
                self._profile.hidden_workload_manifest_digest
            ),
            "workload_digest": self._profile.workload_digest,
            "candidate_digest": request.candidate_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
        }
        if any(record.get(key) != value for key, value in expected_identity.items()):
            raise RuntimeError("candidate phase evidence identity differs")
        eligible = record.get("eligible")
        criterion_units = record.get("criterion_units")
        failures = record.get("failures")
        diagnostics = record.get("diagnostics")
        if (
            type(eligible) is not bool
            or type(criterion_units) is not int
            or not isinstance(failures, list)
            or any(not isinstance(value, str) or not value for value in failures)
            or not isinstance(diagnostics, Mapping)
        ):
            raise RuntimeError("candidate phase normalized result is invalid")
        authority = {
            key: record[key]
            for key in expected_fields
            if key != "result_evidence_digest"
        }
        evidence_digest = record.get("result_evidence_digest")
        if (
            not isinstance(evidence_digest, str)
            or not _DIGEST.fullmatch(evidence_digest)
            or digest_value(authority) != evidence_digest
        ):
            raise RuntimeError("candidate phase result evidence digest differs")
        return EvaluationResult(
            eligible=eligible,
            criterion_units=criterion_units,
            failures=tuple(failures),
            evidence_digest=evidence_digest,
            diagnostics=dict(diagnostics),
        )

    def _receipt(
        self, execution_id: str, request: ComputeExecutionRequest
    ) -> EvaluationReceipt:
        return EvaluationReceipt(
            "evalreceipt-"
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
                "evaluation_key",
                "profile_digest",
                "candidate_digest",
                "reservation_id",
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
