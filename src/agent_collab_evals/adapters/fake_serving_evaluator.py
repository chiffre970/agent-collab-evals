"""Deterministic evaluator-owned receipt ledger for local conformance tests."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Mapping

from ..campaigns.model_serving import ModelServingCampaign
from ..canonical import canonical_json_bytes, digest_bytes, digest_value, parse_json
from ..evaluation import (
    EvaluationReceipt,
    EvaluationReservation,
    EvaluationResult,
    EvaluationScope,
)


class FakeModelServingEvaluator:
    """Issue durable receipts for deterministic, no-GPU candidate evaluations."""

    def __init__(
        self,
        database: Path,
        campaign: ModelServingCampaign,
        visible_scores: Mapping[str, int],
        hidden_scores: Mapping[str, int],
        *,
        visible_used_seconds: int = 1,
        hidden_used_seconds: int = 1,
    ) -> None:
        if any(type(value) is not int for value in visible_scores.values()):
            raise ValueError("fake visible scores must use integer units")
        if any(type(value) is not int for value in hidden_scores.values()):
            raise ValueError("fake hidden scores must use integer units")
        if type(visible_used_seconds) is not int or visible_used_seconds < 0:
            raise ValueError("fake visible duration must be nonnegative")
        if type(hidden_used_seconds) is not int or hidden_used_seconds < 0:
            raise ValueError("fake hidden duration must be nonnegative")
        self._database = database
        self._campaign = campaign
        self._visible_scores = dict(visible_scores)
        self._hidden_scores = dict(hidden_scores)
        self._visible_used_seconds = visible_used_seconds
        self._hidden_used_seconds = hidden_used_seconds
        self._lock = threading.RLock()
        self._profile_digest = digest_value(
            {
                "adapter": "fake-model-serving-evaluator/v2",
                "campaign_manifest_digest": campaign.manifest_digest,
                "visible_scores": self._visible_scores,
                "hidden_scores": self._hidden_scores,
                "visible_used_seconds": visible_used_seconds,
                "hidden_used_seconds": hidden_used_seconds,
            }
        )
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS evaluation_receipts(
                    receipt_id TEXT PRIMARY KEY,
                    evaluation_key TEXT NOT NULL UNIQUE,
                    profile_digest TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    candidate_digest TEXT NOT NULL,
                    reservation_id TEXT,
                    result_json TEXT NOT NULL,
                    result_digest TEXT NOT NULL
                )
                """
            )
            connection.commit()

    @property
    def profile_digest(self) -> str:
        return self._profile_digest

    @property
    def visible_used_seconds(self) -> int:
        return self._visible_used_seconds

    @property
    def hidden_used_seconds(self) -> int:
        return self._hidden_used_seconds

    def visible_evaluate(
        self,
        candidate: bytes,
        reservation: EvaluationReservation | None,
        evaluation_key: str,
    ) -> EvaluationReceipt:
        if reservation is not None and reservation.scope is not EvaluationScope.VISIBLE:
            raise ValueError("visible evaluator received a hidden reservation")
        return self._issue(
            candidate,
            reservation,
            evaluation_key,
            EvaluationScope.VISIBLE,
            self._visible_scores,
        )

    def hidden_evaluate(
        self,
        candidate: bytes,
        reservation: EvaluationReservation,
        evaluation_key: str,
    ) -> EvaluationReceipt:
        if reservation.scope is not EvaluationScope.HIDDEN:
            raise ValueError("hidden evaluator received a visible reservation")
        return self._issue(
            candidate,
            reservation,
            evaluation_key,
            EvaluationScope.HIDDEN,
            self._hidden_scores,
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
                "SELECT * FROM evaluation_receipts WHERE receipt_id = ?",
                (receipt.value,),
            ).fetchone()
        if row is None:
            raise RuntimeError("evaluation receipt is not held by this evaluator")
        reservation_id = (
            reservation.reservation_id if reservation is not None else None
        )
        scores = (
            self._visible_scores
            if scope is EvaluationScope.VISIBLE
            else self._hidden_scores
        )
        expected_result = self._evaluate(
            candidate,
            scores,
            scope,
            str(row["evaluation_key"]),
        )
        result_json = canonical_json_bytes(_result_document(expected_result)).decode()
        expected = (
            self.profile_digest,
            scope.value,
            digest_bytes(candidate),
            reservation_id,
            result_json,
            digest_value(_result_document(expected_result)),
        )
        actual = tuple(
            row[key]
            for key in (
                "profile_digest",
                "scope",
                "candidate_digest",
                "reservation_id",
                "result_json",
                "result_digest",
            )
        )
        if actual != expected:
            raise RuntimeError(
                "evaluation receipt evidence differs from evaluator authority"
            )
        expected_receipt = self._receipt(
            scope, str(row["evaluation_key"])
        )
        if expected_receipt != receipt:
            raise RuntimeError("evaluation receipt identity differs from its evidence")
        return expected_result

    def used_seconds(self, receipt: EvaluationReceipt) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT scope FROM evaluation_receipts WHERE receipt_id = ?",
                (receipt.value,),
            ).fetchone()
        if row is None:
            raise RuntimeError("evaluation receipt is not held by this evaluator")
        scope = EvaluationScope(str(row["scope"]))
        return (
            self.visible_used_seconds
            if scope is EvaluationScope.VISIBLE
            else self.hidden_used_seconds
        )

    def _issue(
        self,
        candidate: bytes,
        reservation: EvaluationReservation | None,
        evaluation_key: str,
        scope: EvaluationScope,
        scores: Mapping[str, int],
    ) -> EvaluationReceipt:
        if not evaluation_key:
            raise ValueError("evaluation key is required")
        receipt = self._receipt(scope, evaluation_key)
        binding = (
            receipt.value,
            evaluation_key,
            self.profile_digest,
            scope.value,
            digest_bytes(candidate),
            reservation.reservation_id if reservation is not None else None,
        )
        with self._lock:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM evaluation_receipts WHERE evaluation_key = ?",
                    (evaluation_key,),
                ).fetchone()
                if row is None:
                    result = self._evaluate(
                        candidate, scores, scope, evaluation_key
                    )
                    result_document = _result_document(result)
                    connection.execute(
                        "INSERT INTO evaluation_receipts("
                        "receipt_id, evaluation_key, profile_digest, scope, "
                        "candidate_digest, reservation_id, result_json, result_digest) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            *binding,
                            canonical_json_bytes(result_document).decode(),
                            digest_value(result_document),
                        ),
                    )
                    connection.commit()
                else:
                    actual = tuple(
                        row[key]
                        for key in (
                            "receipt_id",
                            "evaluation_key",
                            "profile_digest",
                            "scope",
                            "candidate_digest",
                            "reservation_id",
                        )
                    )
                    if actual != binding:
                        connection.rollback()
                        raise RuntimeError(
                            "evaluation key was reused with different evidence"
                        )
                    connection.commit()
        self.resolve(receipt, candidate, reservation, scope)
        return receipt

    def _evaluate(
        self,
        candidate: bytes,
        scores: Mapping[str, int],
        scope: EvaluationScope,
        evaluation_key: str,
    ) -> EvaluationResult:
        document = parse_json(candidate.decode("utf-8"))
        descriptor = self._campaign.validate_candidate_document(document)
        score = scores.get(descriptor.candidate_id)
        eligible = score is not None
        failures = () if eligible else ("candidate_not_in_fake_score_fixture",)
        criterion = score if score is not None else 0
        evidence_digest = digest_value(
            {
                "profile_digest": self.profile_digest,
                "scope": scope.value,
                "evaluation_key": evaluation_key,
                "candidate_digest": digest_bytes(candidate),
                "candidate_manifest_digest": descriptor.manifest_digest,
                "eligible": eligible,
                "criterion_units": criterion,
                "failures": failures,
            }
        )
        return EvaluationResult(
            eligible=eligible,
            criterion_units=criterion,
            failures=failures,
            evidence_digest=evidence_digest,
            diagnostics={
                "candidate_id": descriptor.candidate_id,
                "engine": descriptor.engine,
                "engine_version": descriptor.engine_version,
                "scope": scope.value,
            },
        )

    def _receipt(
        self, scope: EvaluationScope, evaluation_key: str
    ) -> EvaluationReceipt:
        return EvaluationReceipt(
            "evalreceipt-"
            + digest_value(
                {
                    "profile_digest": self.profile_digest,
                    "scope": scope.value,
                    "evaluation_key": evaluation_key,
                }
            )[7:39]
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
