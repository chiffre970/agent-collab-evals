"""Candidate evaluator composed from durable Modal compute executions."""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Mapping

from ..campaigns.model_serving import ModelServingCampaign
from ..canonical import canonical_json_bytes, digest_bytes, digest_value, parse_json
from ..compute_backend import (
    ComputeExecutionRequest,
    ComputeExecutionStatus,
)
from ..evaluation import (
    EvaluationReceipt,
    EvaluationInProgress,
    EvaluationReservation,
    EvaluationResult,
    EvaluationScope,
)
from ..ports import ComputeBackend
from .modal_vllm_compute import ModalVllmComputeProfile


class ModalServingDevelopmentEvaluator:
    """Run the public serving workload through the development Modal profile."""

    def __init__(
        self,
        database: Path,
        campaign: ModelServingCampaign,
        profile: ModalVllmComputeProfile,
        backend: ComputeBackend,
    ) -> None:
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
                CREATE TABLE IF NOT EXISTS modal_evaluation_receipts(
                    receipt_id TEXT PRIMARY KEY,
                    evaluation_key TEXT NOT NULL UNIQUE,
                    candidate_digest TEXT NOT NULL,
                    reservation_id TEXT,
                    request_json TEXT NOT NULL,
                    execution_id TEXT NOT NULL,
                    execution_request_digest TEXT NOT NULL,
                    scope TEXT NOT NULL
                )
                """
            )
            connection.commit()

    @property
    def profile_digest(self) -> str:
        return self._profile.evaluator_profile_digest

    def visible_evaluate(
        self,
        candidate: bytes,
        reservation: EvaluationReservation | None,
        evaluation_key: str,
    ) -> EvaluationReceipt:
        return self._evaluate(
            candidate, reservation, evaluation_key, EvaluationScope.VISIBLE
        )

    def hidden_evaluate(
        self,
        candidate: bytes,
        reservation: EvaluationReservation,
        evaluation_key: str,
    ) -> EvaluationReceipt:
        raise RuntimeError(
            "the development Modal profile contains no hidden workload"
        )

    def resolve(
        self,
        receipt: EvaluationReceipt,
        candidate: bytes,
        reservation: EvaluationReservation | None,
        scope: EvaluationScope,
    ) -> EvaluationResult:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM modal_evaluation_receipts WHERE receipt_id = ?",
                (receipt.value,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Modal evaluation receipt is unavailable")
        if scope is not EvaluationScope.VISIBLE:
            raise RuntimeError("development Modal evidence is visible-only")
        request = self._request(
            candidate,
            reservation,
            str(row["evaluation_key"]),
            scope,
        )
        expected = (
            digest_bytes(candidate),
            reservation.reservation_id if reservation is not None else None,
            canonical_json_bytes(_request_document(request)).decode(),
            request.request_digest,
            scope.value,
        )
        actual = tuple(
            row[key]
            for key in (
                "candidate_digest",
                "reservation_id",
                "request_json",
                "execution_request_digest",
                "scope",
            )
        )
        if actual != expected:
            raise RuntimeError("Modal evaluation receipt binding differs")
        execution, evidence = self._backend.resolve(request)
        if execution.execution_id != str(row["execution_id"]):
            raise RuntimeError("Modal evaluator execution identity differs")
        if execution.used_seconds is None:
            raise RuntimeError("Modal execution has no measured usage")
        self._used_authorities[receipt.value] = execution.used_seconds
        return _evaluation_result(evidence)

    def used_seconds(self, receipt: EvaluationReceipt) -> int:
        try:
            return self._used_authorities[receipt.value]
        except KeyError as error:
            raise RuntimeError(
                "Modal evaluation receipt must be resolved before accounting"
            ) from error

    def _evaluate(
        self,
        candidate: bytes,
        reservation: EvaluationReservation | None,
        evaluation_key: str,
        scope: EvaluationScope,
    ) -> EvaluationReceipt:
        request = self._request(candidate, reservation, evaluation_key, scope)
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
                    "Modal evaluation remains nonterminal at the caller deadline"
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
                "Modal evaluation did not complete successfully: "
                f"{execution.status.value}:{execution.failure or 'unknown'}"
            )
        _, evidence = self._backend.resolve(request)
        _evaluation_result(evidence)
        receipt = EvaluationReceipt(
            "evalreceipt-"
            + digest_value(
                {
                    "evaluator_profile_digest": self.profile_digest,
                    "evaluation_key": evaluation_key,
                    "execution_id": execution.execution_id,
                    "execution_request_digest": request.request_digest,
                }
            )[7:39]
        )
        request_json = canonical_json_bytes(_request_document(request)).decode()
        with self._lock:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM modal_evaluation_receipts "
                    "WHERE evaluation_key = ?",
                    (evaluation_key,),
                ).fetchone()
                expected = (
                    receipt.value,
                    evaluation_key,
                    request.candidate_digest,
                    reservation.reservation_id if reservation is not None else None,
                    request_json,
                    execution.execution_id,
                    request.request_digest,
                    scope.value,
                )
                if row is None:
                    connection.execute(
                        "INSERT INTO modal_evaluation_receipts("
                        "receipt_id, evaluation_key, candidate_digest, "
                        "reservation_id, request_json, execution_id, "
                        "execution_request_digest, scope) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        expected,
                    )
                else:
                    actual = tuple(
                        row[key]
                        for key in (
                            "receipt_id",
                            "evaluation_key",
                            "candidate_digest",
                            "reservation_id",
                            "request_json",
                            "execution_id",
                            "execution_request_digest",
                            "scope",
                        )
                    )
                    if actual != expected:
                        connection.rollback()
                        raise RuntimeError(
                            "Modal evaluation key changed across retry"
                        )
                connection.commit()
        return receipt

    def _request(
        self,
        candidate: bytes,
        reservation: EvaluationReservation | None,
        evaluation_key: str,
        scope: EvaluationScope,
    ) -> ComputeExecutionRequest:
        if scope is not EvaluationScope.VISIBLE:
            raise RuntimeError("development Modal profile is visible-only")
        descriptor = self._campaign.validate_candidate_document(
            parse_json(candidate.decode("utf-8"))
        )
        maximum_seconds = (
            reservation.reserved_seconds
            if reservation is not None
            else self._campaign.measurement_profile().repetition_timeout_seconds
        )
        reservation_id = (
            reservation.reservation_id
            if reservation is not None
            else "reference-" + digest_bytes(candidate)[7:39]
        )
        if reservation is not None and reservation.scope is not scope:
            raise ValueError("evaluation reservation scope differs")
        return ComputeExecutionRequest(
            execution_key=evaluation_key,
            campaign_run_id=(
                reservation.campaign_run_id
                if reservation is not None
                else "registered-reference"
            ),
            reservation_id=reservation_id,
            scope=scope,
            candidate_digest=digest_bytes(candidate),
            candidate_manifest_digest=descriptor.manifest_digest,
            evaluator_profile_digest=self.profile_digest,
            maximum_seconds=maximum_seconds,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection


def _evaluation_result(evidence: Mapping[str, object]) -> EvaluationResult:
    result = evidence.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Modal execution evidence has no normalized result")
    performance = result.get("performance_score")
    failures: list[str] = []
    if not isinstance(performance, dict):
        failures.append("performance_score_missing")
        criterion_units = 0
        eligible = False
    else:
        criterion_units = performance.get("scalar_ppm")
        eligible = performance.get("eligible") is True
        if type(criterion_units) is not int:
            raise RuntimeError("Modal performance score is not integer ppm")
        declared = performance.get("failures", [])
        if not isinstance(declared, list) or any(
            not isinstance(item, str) or not item for item in declared
        ):
            raise RuntimeError("Modal performance failures are invalid")
        failures.extend(declared)
    if result.get("valid") is not True:
        eligible = False
        failures.append("measurement_invalid")
    evidence_digest = digest_value(evidence)
    return EvaluationResult(
        eligible=eligible,
        criterion_units=criterion_units,
        failures=tuple(dict.fromkeys(failures)),
        evidence_digest=evidence_digest,
        diagnostics={
            "candidate_id": result.get("candidate_id"),
            "modal_function_call_id": result.get("modal_function_call_id"),
            "measurement_profile_digest": result.get(
                "measurement_profile_digest"
            ),
            "scoring_profile_digest": result.get("scoring_profile_digest"),
            "development_single_repetition": True,
        },
    )


def _request_document(request: ComputeExecutionRequest) -> dict[str, object]:
    return {
        "execution_key": request.execution_key,
        "campaign_run_id": request.campaign_run_id,
        "reservation_id": request.reservation_id,
        "scope": request.scope.value,
        "candidate_digest": request.candidate_digest,
        "candidate_manifest_digest": request.candidate_manifest_digest,
        "evaluator_profile_digest": request.evaluator_profile_digest,
        "maximum_seconds": request.maximum_seconds,
    }
