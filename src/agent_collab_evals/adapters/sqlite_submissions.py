"""Durable actor-private submissions and authoritative candidate selection."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing, suppress
from pathlib import Path
from typing import Any, Mapping

from ..artifacts import ArtifactRef, TrustedServiceTransport
from ..canonical import canonical_json_bytes, digest_value, parse_json
from ..collaboration import SessionTransport
from ..evaluation import (
    CandidateReceipt,
    CandidateRecord,
    EvaluationInProgress,
    EvaluationReceipt,
    EvaluationReservation,
    EvaluationReservationStatus,
    EvaluationResult,
    EvaluationScope,
    SelectionReceipt,
    SelectionResult,
    SubmissionPolicy,
    SubmissionSet,
)
from ..ports import CandidateEvaluator, ComputeBroker, StorageBackend
from ..session_identity import SessionIdentityRegistry


class SqliteSubmissionRegistry:
    """Keep submissions private and persist condition-blind selection."""

    def __init__(
        self,
        database: Path,
        sessions: SessionIdentityRegistry,
        storage: StorageBackend,
        compute: ComputeBroker,
        evaluator: CandidateEvaluator,
        service: TrustedServiceTransport,
    ) -> None:
        self._database = database
        self._sessions = sessions
        self._storage = storage
        self._compute = compute
        self._evaluator = evaluator
        self._service = service
        self._lock = threading.RLock()
        self._job_authorities: dict[tuple[str, str], tuple[str, ...]] = {}
        database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def initialize(
        self,
        campaign_run_id: str,
        job_id: str,
        actor_ids: tuple[str, ...],
        policy: SubmissionPolicy,
        default_artifact_ref: ArtifactRef,
        default_evaluation_receipt: EvaluationReceipt,
    ) -> None:
        if not campaign_run_id or not job_id:
            raise ValueError("submission campaign and job IDs are required")
        if not actor_ids or len(set(actor_ids)) != len(actor_ids):
            raise ValueError("submission actor roster must be nonempty and unique")
        reference, content = self._read_artifact_ref(
            campaign_run_id, default_artifact_ref, "candidate_lifecycle"
        )
        self._evaluator.resolve(
            default_evaluation_receipt,
            content,
            None,
            EvaluationScope.VISIBLE,
        )
        actor_ids_json = canonical_json_bytes(sorted(actor_ids)).decode()
        policy_json = canonical_json_bytes(policy).decode()
        expected = (
            actor_ids_json,
            policy_json,
            default_artifact_ref.value,
            reference.digest,
            default_evaluation_receipt.value,
            self._evaluator.profile_digest,
        )
        with self._transaction() as connection:
            row = self._job_row(connection, campaign_run_id, job_id, required=False)
            if row is not None:
                actual = tuple(
                    str(row[key])
                    for key in (
                        "actor_ids_json",
                        "policy_json",
                        "default_artifact_ref",
                        "default_artifact_digest",
                        "default_evaluation_receipt",
                        "evaluator_profile_digest",
                    )
                )
                if actual != expected:
                    raise ValueError("submission job changed across restart")
            else:
                connection.execute(
                    "INSERT INTO submission_jobs("
                    "campaign_run_id, job_id, actor_ids_json, policy_json, "
                    "default_artifact_ref, default_artifact_digest, "
                    "default_evaluation_receipt, evaluator_profile_digest, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open')",
                    (campaign_run_id, job_id, *expected),
                )
                self._audit(
                    connection,
                    campaign_run_id,
                    None,
                    "submissions.initialized",
                    {"job_id": job_id, "actor_ids": sorted(actor_ids)},
                )
        self._job_authorities[(campaign_run_id, job_id)] = expected

    def submit(
        self,
        session: SessionTransport,
        job_id: str,
        artifact_ref: ArtifactRef,
        idempotency_key: str,
    ) -> CandidateReceipt:
        """Admit through a recoverable provisional-to-reserved state machine."""

        if not job_id or not idempotency_key:
            raise ValueError("submission job and idempotency key are required")
        context = self._sessions.resolve(session)
        artifact = self._storage.describe_owned(session, artifact_ref)
        receipt = CandidateReceipt(
            "candidate-"
            + digest_value(
                {
                    "campaign_run_id": context.campaign_run_id,
                    "job_id": job_id,
                    "owner_actor_id": context.actor_id,
                    "idempotency_key": idempotency_key,
                }
            )[7:39]
        )
        with self._transaction() as connection:
            job = self._job_row(connection, context.campaign_run_id, job_id)
            if str(job["status"]) != "open":
                raise RuntimeError("submissions are closed")
            actor_ids = parse_json(str(job["actor_ids_json"]))
            if context.actor_id not in actor_ids:
                raise PermissionError("actor is not registered for submissions")
            existing = connection.execute(
                "SELECT * FROM candidates WHERE campaign_run_id = ? "
                "AND job_id = ? AND owner_actor_id = ? AND idempotency_key = ?",
                (
                    context.campaign_run_id,
                    job_id,
                    context.actor_id,
                    idempotency_key,
                ),
            ).fetchone()
            if existing is not None:
                self._verify_submission_identity(
                    existing, artifact_ref, artifact.digest
                )
                if str(existing["admission_status"]) == "admitted":
                    return CandidateReceipt(str(existing["receipt_id"]))
                if str(existing["admission_status"]) != "provisional":
                    raise RuntimeError("candidate admission has an invalid status")
            else:
                policy = _policy(str(job["policy_json"]))
                count = connection.execute(
                    "SELECT COUNT(*) AS value FROM candidates "
                    "WHERE campaign_run_id = ? AND job_id = ? AND owner_actor_id = ?",
                    (context.campaign_run_id, job_id, context.actor_id),
                ).fetchone()
                if int(count["value"]) >= policy.per_actor_candidate_limit:
                    raise ValueError("actor candidate submission limit is exhausted")
                connection.execute(
                    "INSERT INTO candidates("
                    "receipt_id, idempotency_key, campaign_run_id, job_id, "
                    "owner_actor_id, artifact_ref, artifact_digest, admission_status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'provisional')",
                    (
                        receipt.value,
                        idempotency_key,
                        context.campaign_run_id,
                        job_id,
                        context.actor_id,
                        artifact_ref.value,
                        artifact.digest,
                    ),
                )
                self._audit(
                    connection,
                    context.campaign_run_id,
                    context.actor_id,
                    "candidate.provisioned",
                    {"job_id": job_id, "receipt_id": receipt.value},
                )

        policy = _policy(str(job["policy_json"]))
        reservation = self._compute.reserve_visible_evaluation(
            session,
            f"candidate:{receipt.value}",
            artifact_ref,
            policy.visible_evaluation_seconds,
        )
        with self._transaction() as connection:
            current = self._candidate_row(connection, receipt)
            self._verify_submission_identity(current, artifact_ref, artifact.digest)
            status = str(current["admission_status"])
            if status == "admitted":
                if str(current["reservation_id"]) != reservation.reservation_id:
                    raise RuntimeError("candidate reservation changed across retry")
                return receipt
            if status != "provisional":
                raise RuntimeError("candidate admission has an invalid status")
            connection.execute(
                "UPDATE candidates SET admission_status = 'admitted', "
                "reservation_id = ? WHERE receipt_id = ?",
                (reservation.reservation_id, receipt.value),
            )
            self._audit(
                connection,
                context.campaign_run_id,
                context.actor_id,
                "candidate.admitted",
                {
                    "job_id": job_id,
                    "receipt_id": receipt.value,
                    "artifact_ref": artifact_ref.value,
                    "artifact_digest": artifact.digest,
                    "evaluation_reservation_id": reservation.reservation_id,
                },
            )
        return receipt

    def evaluate_visible(self, receipt: CandidateReceipt) -> None:
        with closing(self._connect()) as connection:
            row = self._candidate_row(connection, receipt)
            if str(row["admission_status"]) != "admitted":
                raise RuntimeError("candidate admission is incomplete")
            if row["visible_evaluation_receipt"] is not None or row[
                "evaluation_failure"
            ] is not None:
                return
        try:
            artifact, content = self._read_candidate(row, "candidate_lifecycle")
            if (
                artifact.owner_actor_id != str(row["owner_actor_id"])
                or artifact.digest != str(row["artifact_digest"])
            ):
                raise RuntimeError("candidate artifact binding changed")
            reservation = self._reservation(row, EvaluationScope.VISIBLE)
            evaluation_receipt = self._evaluator.visible_evaluate(
                content,
                reservation,
                f"visible:{receipt.value}",
            )
            self._evaluator.resolve(
                evaluation_receipt,
                content,
                reservation,
                EvaluationScope.VISIBLE,
            )
        except EvaluationInProgress:
            return
        except Exception as error:
            reason = f"{type(error).__name__}:{error}"
            with suppress(Exception):
                self._compute.fail(str(row["reservation_id"]), reason)
            with self._transaction() as connection:
                connection.execute(
                    "UPDATE candidates SET evaluation_failure = ? "
                    "WHERE receipt_id = ? AND visible_evaluation_receipt IS NULL",
                    (reason, receipt.value),
                )
                self._audit(
                    connection,
                    str(row["campaign_run_id"]),
                    str(row["owner_actor_id"]),
                    "candidate.visible_evaluation_failed",
                    {"receipt_id": receipt.value, "reason": reason},
                )
            return

        self._compute.complete(
            str(row["reservation_id"]),
            self._evaluator.used_seconds(evaluation_receipt),
        )
        with self._transaction() as connection:
            current = self._candidate_row(connection, receipt)
            stored = current["visible_evaluation_receipt"]
            if stored is not None and str(stored) != evaluation_receipt.value:
                raise RuntimeError("candidate evaluation receipt changed")
            if current["evaluation_failure"] is not None:
                raise RuntimeError(
                    "candidate evaluation has conflicting terminal states"
                )
            if stored is None:
                connection.execute(
                    "UPDATE candidates SET visible_evaluation_receipt = ? "
                    "WHERE receipt_id = ?",
                    (evaluation_receipt.value, receipt.value),
                )
                self._audit(
                    connection,
                    str(row["campaign_run_id"]),
                    str(row["owner_actor_id"]),
                    "candidate.visible_evaluated",
                    {
                        "receipt_id": receipt.value,
                        "evaluation_receipt": evaluation_receipt.value,
                    },
                )

    def visible_result(
        self, session: SessionTransport, receipt: CandidateReceipt
    ) -> EvaluationResult | None:
        context = self._sessions.resolve(session)
        with closing(self._connect()) as connection:
            try:
                row = self._candidate_row(connection, receipt)
            except KeyError as error:
                raise PermissionError(
                    "candidate is unavailable to this actor"
                ) from error
        if (
            str(row["campaign_run_id"]) != context.campaign_run_id
            or str(row["owner_actor_id"]) != context.actor_id
        ):
            raise PermissionError("candidate is unavailable to this actor")
        if not self._compute.is_visible_result_released(
            context.campaign_run_id, context.actor_id
        ):
            return None
        if row["evaluation_failure"] is not None:
            raise RuntimeError("visible evaluation failed")
        if row["visible_evaluation_receipt"] is None:
            return None
        return self._candidate(row).visible_result

    def close(self, campaign_run_id: str, job_id: str) -> SubmissionSet:
        with self._transaction() as connection:
            job = self._job_row(connection, campaign_run_id, job_id)
            rows = connection.execute(
                "SELECT * FROM candidates WHERE campaign_run_id = ? AND job_id = ? "
                "ORDER BY receipt_id",
                (campaign_run_id, job_id),
            ).fetchall()
            self._validate_compute_bindings(campaign_run_id, rows)
            candidates = tuple(self._candidate(row) for row in rows)
            default_result = self._reference_result(job)
            if str(job["status"]) == "open":
                connection.execute(
                    "UPDATE submission_jobs SET status = 'closed' "
                    "WHERE campaign_run_id = ? AND job_id = ?",
                    (campaign_run_id, job_id),
                )
                self._audit(
                    connection,
                    campaign_run_id,
                    None,
                    "submissions.closed",
                    {"job_id": job_id, "candidate_count": len(rows)},
                )
            elif str(job["status"]) != "closed":
                raise RuntimeError("submission job has an invalid status")
        return SubmissionSet(
            campaign_run_id=campaign_run_id,
            job_id=job_id,
            candidates=candidates,
            default_artifact_ref=ArtifactRef(str(job["default_artifact_ref"])),
            default_evaluation_receipt=EvaluationReceipt(
                str(job["default_evaluation_receipt"])
            ),
            default_result=default_result,
        )

    def select(self, submissions: SubmissionSet) -> SelectionResult:
        current = self.close(submissions.campaign_run_id, submissions.job_id)
        if current != submissions:
            raise ValueError("submission set differs from the durable registry")
        with self._transaction() as connection:
            job = self._job_row(
                connection, submissions.campaign_run_id, submissions.job_id
            )
            if str(job["status"]) != "closed":
                raise RuntimeError("candidate selection requires closed submissions")
            selection = self._derive_selection(submissions, job)
            document = canonical_json_bytes(_selection_document(selection)).decode()
            stored = connection.execute(
                "SELECT * FROM selections WHERE campaign_run_id = ? AND job_id = ?",
                (submissions.campaign_run_id, submissions.job_id),
            ).fetchone()
            if stored is None:
                connection.execute(
                    "INSERT INTO selections("
                    "selection_receipt, campaign_run_id, job_id, selection_json) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        selection.receipt.value,
                        submissions.campaign_run_id,
                        submissions.job_id,
                        document,
                    ),
                )
                self._audit(
                    connection,
                    submissions.campaign_run_id,
                    None,
                    "candidate.selected",
                    {
                        "job_id": submissions.job_id,
                        "selection_receipt": selection.receipt.value,
                        "selection_digest": selection.selection_digest,
                    },
                )
            elif (
                str(stored["selection_receipt"]) != selection.receipt.value
                or str(stored["selection_json"]) != document
            ):
                raise RuntimeError("persisted selection differs from recomputation")
        return selection

    def evaluate_hidden(
        self, selection_receipt: SelectionReceipt, *, reserved_seconds: int
    ) -> EvaluationResult:
        selection = self._authoritative_selection(selection_receipt)
        if selection.selected_artifact_ref is None:
            raise RuntimeError("authoritative selection has no artifact")
        if selection.used_default:
            with closing(self._connect()) as connection:
                job = self._job_row(
                    connection, selection.campaign_run_id, selection.job_id
                )
            artifact, content = self._read_artifact_ref(
                selection.campaign_run_id,
                selection.selected_artifact_ref,
                "hidden_evaluation",
            )
            if artifact.digest != str(job["default_artifact_digest"]):
                raise RuntimeError("reference artifact binding differs")
            candidate_receipt = None
        else:
            assert selection.selected_receipt is not None
            with closing(self._connect()) as connection:
                candidate = self._candidate_row(
                    connection, selection.selected_receipt
                )
            artifact, content = self._read_candidate(
                candidate, "hidden_evaluation"
            )
            if artifact.digest != str(candidate["artifact_digest"]):
                raise RuntimeError("hidden evaluation artifact binding differs")
            candidate_receipt = selection.selected_receipt.value
        if artifact.ref != selection.selected_artifact_ref:
            raise RuntimeError("hidden evaluation artifact differs from selection")

        reservation = self._compute.reserve_hidden_evaluation(
            self._service,
            f"hidden:{selection_receipt.value}",
            selection.campaign_run_id,
            selection.selected_artifact_ref,
            reserved_seconds,
        )
        with closing(self._connect()) as connection:
            stored = connection.execute(
                "SELECT * FROM hidden_evaluations WHERE selection_receipt = ?",
                (selection_receipt.value,),
            ).fetchone()
        if stored is not None:
            if str(stored["reservation_id"]) != reservation.reservation_id:
                raise RuntimeError("hidden evaluation reservation changed")
            return self._evaluator.resolve(
                EvaluationReceipt(str(stored["evaluation_receipt"])),
                content,
                reservation,
                EvaluationScope.HIDDEN,
            )
        try:
            evaluation_receipt = self._evaluator.hidden_evaluate(
                content,
                reservation,
                f"hidden:{selection_receipt.value}",
            )
            result = self._evaluator.resolve(
                evaluation_receipt,
                content,
                reservation,
                EvaluationScope.HIDDEN,
            )
        except EvaluationInProgress:
            raise
        except Exception as error:
            self._compute.fail(
                reservation.reservation_id,
                f"{type(error).__name__}:{error}",
            )
            raise
        self._compute.complete(
            reservation.reservation_id,
            self._evaluator.used_seconds(evaluation_receipt),
        )
        with self._transaction() as connection:
            inserted = connection.execute(
                "INSERT OR IGNORE INTO hidden_evaluations("
                "selection_receipt, campaign_run_id, job_id, receipt_id, "
                "reservation_id, evaluation_receipt) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    selection_receipt.value,
                    selection.campaign_run_id,
                    selection.job_id,
                    candidate_receipt,
                    reservation.reservation_id,
                    evaluation_receipt.value,
                ),
            )
            stored = connection.execute(
                "SELECT * FROM hidden_evaluations WHERE selection_receipt = ?",
                (selection_receipt.value,),
            ).fetchone()
            assert stored is not None
            if (
                str(stored["reservation_id"]) != reservation.reservation_id
                or str(stored["evaluation_receipt"]) != evaluation_receipt.value
            ):
                raise RuntimeError("hidden evaluation changed across retry")
            if inserted.rowcount == 1:
                self._audit(
                    connection,
                    selection.campaign_run_id,
                    None,
                    "candidate.hidden_evaluated",
                    {
                        "selection_receipt": selection_receipt.value,
                        "evaluation_receipt": evaluation_receipt.value,
                    },
                )
        return result

    def _authoritative_selection(
        self, receipt: SelectionReceipt
    ) -> SelectionResult:
        with closing(self._connect()) as connection:
            stored = connection.execute(
                "SELECT * FROM selections WHERE selection_receipt = ?",
                (receipt.value,),
            ).fetchone()
        if stored is None:
            raise PermissionError("selection receipt is unavailable")
        campaign_run_id = str(stored["campaign_run_id"])
        job_id = str(stored["job_id"])
        submissions = self.close(campaign_run_id, job_id)
        with closing(self._connect()) as connection:
            job = self._job_row(connection, campaign_run_id, job_id)
        selection = self._derive_selection(submissions, job)
        if selection.receipt != receipt or canonical_json_bytes(
            _selection_document(selection)
        ).decode() != str(stored["selection_json"]):
            raise RuntimeError("persisted selection differs from recomputation")
        return selection

    def _derive_selection(
        self, submissions: SubmissionSet, job: sqlite3.Row
    ) -> SelectionResult:
        eligible = [
            candidate
            for candidate in submissions.candidates
            if candidate.visible_result is not None
            and candidate.visible_result.eligible
            and candidate.evaluation_failure is None
        ]
        eligible.sort(
            key=lambda candidate: (
                -candidate.visible_result.criterion_units,  # type: ignore[union-attr]
                candidate.artifact_digest,
                candidate.receipt.value,
            )
        )
        if (
            not eligible
            or (
                submissions.default_result.eligible
                and submissions.default_result.criterion_units
                >= eligible[0].visible_result.criterion_units  # type: ignore[union-attr]
            )
        ):
            selected = None
            result = submissions.default_result
            artifact_ref = submissions.default_artifact_ref
        else:
            selected = eligible[0]
            assert selected.visible_result is not None
            result = selected.visible_result
            artifact_ref = selected.artifact_ref
        selection_digest = digest_value(
            {
                "campaign_run_id": submissions.campaign_run_id,
                "job_id": submissions.job_id,
                "candidate_evaluation_receipts": [
                    (
                        candidate.receipt.value,
                        candidate.evaluation_receipt.value
                        if candidate.evaluation_receipt is not None
                        else None,
                    )
                    for candidate in submissions.candidates
                ],
                "default_evaluation_receipt": (
                    submissions.default_evaluation_receipt.value
                ),
                "selected_receipt": (
                    selected.receipt.value if selected is not None else None
                ),
                "selected_artifact_ref": artifact_ref.value,
                "result_evidence_digest": result.evidence_digest,
                "selection_rule": _policy(str(job["policy_json"])).selection_rule,
            }
        )
        receipt = SelectionReceipt("selection-" + selection_digest[7:39])
        return SelectionResult(
            receipt=receipt,
            campaign_run_id=submissions.campaign_run_id,
            job_id=submissions.job_id,
            selected_receipt=(selected.receipt if selected is not None else None),
            selected_artifact_ref=artifact_ref,
            result=result,
            used_default=selected is None,
            selection_digest=selection_digest,
        )

    def _validate_compute_bindings(
        self, campaign_run_id: str, rows: list[sqlite3.Row]
    ) -> None:
        if any(str(row["admission_status"]) != "admitted" for row in rows):
            raise RuntimeError("candidate admission is not terminal")
        snapshot = self._compute.snapshot(campaign_run_id)
        reservations = {
            reservation.reservation_id: reservation
            for reservation in snapshot.reservations
            if reservation.scope is EvaluationScope.VISIBLE
        }
        expected_ids = {str(row["reservation_id"]) for row in rows}
        if set(reservations) != expected_ids:
            raise RuntimeError(
                "visible compute reservations differ from candidate submissions"
            )
        for row in rows:
            artifact, _ = self._read_candidate(row, "candidate_lifecycle")
            if (
                artifact.owner_actor_id != str(row["owner_actor_id"])
                or artifact.digest != str(row["artifact_digest"])
                or artifact.ref.value != str(row["artifact_ref"])
            ):
                raise RuntimeError("candidate artifact binding differs")
            reservation = reservations[str(row["reservation_id"])]
            if (
                reservation.campaign_run_id != campaign_run_id
                or reservation.actor_id != str(row["owner_actor_id"])
                or reservation.artifact_ref.value != str(row["artifact_ref"])
            ):
                raise RuntimeError("candidate compute reservation binding differs")
            if row["visible_evaluation_receipt"] is not None:
                expected_status = EvaluationReservationStatus.COMPLETE
                self._candidate(row)
            elif row["evaluation_failure"] is not None:
                expected_status = EvaluationReservationStatus.FAILED
            else:
                raise RuntimeError("candidate visible evaluation is not terminal")
            if reservation.status is not expected_status:
                raise RuntimeError("candidate evaluation and compute states differ")

    def _candidate(self, row: sqlite3.Row) -> CandidateRecord:
        evaluation_receipt = (
            EvaluationReceipt(str(row["visible_evaluation_receipt"]))
            if row["visible_evaluation_receipt"] is not None
            else None
        )
        result = None
        if evaluation_receipt is not None:
            _, content = self._read_candidate(row, "candidate_lifecycle")
            result = self._evaluator.resolve(
                evaluation_receipt,
                content,
                self._reservation(row, EvaluationScope.VISIBLE),
                EvaluationScope.VISIBLE,
            )
        return CandidateRecord(
            receipt=CandidateReceipt(str(row["receipt_id"])),
            idempotency_key=str(row["idempotency_key"]),
            campaign_run_id=str(row["campaign_run_id"]),
            job_id=str(row["job_id"]),
            owner_actor_id=str(row["owner_actor_id"]),
            artifact_ref=ArtifactRef(str(row["artifact_ref"])),
            artifact_digest=str(row["artifact_digest"]),
            reservation_id=str(row["reservation_id"]),
            evaluation_receipt=evaluation_receipt,
            visible_result=result,
            evaluation_failure=(
                str(row["evaluation_failure"])
                if row["evaluation_failure"] is not None
                else None
            ),
        )

    def _reference_result(self, job: sqlite3.Row) -> EvaluationResult:
        _, content = self._read_artifact_ref(
            str(job["campaign_run_id"]),
            ArtifactRef(str(job["default_artifact_ref"])),
            "candidate_lifecycle",
        )
        return self._evaluator.resolve(
            EvaluationReceipt(str(job["default_evaluation_receipt"])),
            content,
            None,
            EvaluationScope.VISIBLE,
        )

    def _reservation(
        self, row: sqlite3.Row, scope: EvaluationScope
    ) -> EvaluationReservation:
        reservation_id = str(row["reservation_id"])
        snapshot = self._compute.snapshot(str(row["campaign_run_id"]))
        reservation = next(
            (
                item
                for item in snapshot.reservations
                if item.reservation_id == reservation_id and item.scope is scope
            ),
            None,
        )
        if reservation is None:
            raise RuntimeError("candidate evaluation reservation is missing")
        return reservation

    def _read_candidate(
        self, row: sqlite3.Row, purpose: str
    ) -> tuple[Any, bytes]:
        return self._read_artifact_ref(
            str(row["campaign_run_id"]),
            ArtifactRef(str(row["artifact_ref"])),
            purpose,
        )

    def _read_artifact_ref(
        self, campaign_run_id: str, artifact_ref: ArtifactRef, purpose: str
    ) -> tuple[Any, bytes]:
        authorization = self._storage.authorize_read(
            self._service, campaign_run_id, artifact_ref, purpose
        )
        return self._storage.trusted_read(self._service, authorization, purpose)

    @staticmethod
    def _verify_submission_identity(
        row: sqlite3.Row, artifact_ref: ArtifactRef, artifact_digest: str
    ) -> None:
        if (
            str(row["artifact_ref"]) != artifact_ref.value
            or str(row["artifact_digest"]) != artifact_digest
        ):
            raise ValueError("submission idempotency key was reused differently")

    def _job_row(
        self,
        connection: sqlite3.Connection,
        campaign_run_id: str,
        job_id: str,
        *,
        required: bool = True,
    ) -> sqlite3.Row | None:
        row = connection.execute(
            "SELECT * FROM submission_jobs WHERE campaign_run_id = ? AND job_id = ?",
            (campaign_run_id, job_id),
        ).fetchone()
        if row is None and required:
            raise KeyError("submission job is not initialized")
        if row is not None and required:
            expected = self._job_authorities.get((campaign_run_id, job_id))
            if expected is None:
                raise RuntimeError(
                    "submission job was not initialized against its authority"
                )
            actual = tuple(
                str(row[key])
                for key in (
                    "actor_ids_json",
                    "policy_json",
                    "default_artifact_ref",
                    "default_artifact_digest",
                    "default_evaluation_receipt",
                    "evaluator_profile_digest",
                )
            )
            if actual != expected:
                raise RuntimeError("stored submission job differs from its authority")
        return row

    @staticmethod
    def _candidate_row(
        connection: sqlite3.Connection, receipt: CandidateReceipt
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM candidates WHERE receipt_id = ?", (receipt.value,)
        ).fetchone()
        if row is None:
            raise KeyError("unknown candidate receipt")
        return row

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        campaign_run_id: str,
        actor_id: str | None,
        kind: str,
        details: Mapping[str, object],
    ) -> None:
        connection.execute(
            "INSERT INTO submission_audit("
            "campaign_run_id, actor_id, kind, details_json) VALUES (?, ?, ?, ?)",
            (
                campaign_run_id,
                actor_id,
                kind,
                canonical_json_bytes(details).decode(),
            ),
        )

    def _initialize_schema(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS submission_jobs(
                    campaign_run_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    actor_ids_json TEXT NOT NULL,
                    policy_json TEXT NOT NULL,
                    default_artifact_ref TEXT NOT NULL,
                    default_artifact_digest TEXT NOT NULL,
                    default_evaluation_receipt TEXT NOT NULL,
                    evaluator_profile_digest TEXT NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY(campaign_run_id, job_id)
                );
                CREATE TABLE IF NOT EXISTS candidates(
                    receipt_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL,
                    campaign_run_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    owner_actor_id TEXT NOT NULL,
                    artifact_ref TEXT NOT NULL,
                    artifact_digest TEXT NOT NULL,
                    admission_status TEXT NOT NULL,
                    reservation_id TEXT UNIQUE,
                    visible_evaluation_receipt TEXT,
                    evaluation_failure TEXT,
                    UNIQUE(campaign_run_id, job_id, owner_actor_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS selections(
                    selection_receipt TEXT PRIMARY KEY,
                    campaign_run_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    selection_json TEXT NOT NULL,
                    UNIQUE(campaign_run_id, job_id)
                );
                CREATE TABLE IF NOT EXISTS hidden_evaluations(
                    selection_receipt TEXT PRIMARY KEY,
                    campaign_run_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    receipt_id TEXT,
                    reservation_id TEXT NOT NULL,
                    evaluation_receipt TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS submission_audit(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_run_id TEXT NOT NULL,
                    actor_id TEXT,
                    kind TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                """
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    class _Transaction:
        def __init__(self, registry: "SqliteSubmissionRegistry") -> None:
            self._registry = registry
            self._connection: sqlite3.Connection | None = None

        def __enter__(self) -> sqlite3.Connection:
            self._registry._lock.acquire()
            self._connection = self._registry._connect()
            self._connection.execute("BEGIN IMMEDIATE")
            return self._connection

        def __exit__(self, exception_type, exception, traceback) -> None:
            assert self._connection is not None
            try:
                if exception_type is None:
                    self._connection.commit()
                else:
                    self._connection.rollback()
            finally:
                self._connection.close()
                self._registry._lock.release()

    def _transaction(self) -> "SqliteSubmissionRegistry._Transaction":
        return self._Transaction(self)


def _policy(value: str) -> SubmissionPolicy:
    document = parse_json(value)
    if not isinstance(document, dict):
        raise RuntimeError("stored submission policy is invalid")
    return SubmissionPolicy(
        per_actor_candidate_limit=document["per_actor_candidate_limit"],
        visible_evaluation_seconds=document["visible_evaluation_seconds"],
        selection_rule=document["selection_rule"],
    )


def _result_document(result: EvaluationResult) -> dict[str, object]:
    return {
        "eligible": result.eligible,
        "criterion_units": result.criterion_units,
        "failures": list(result.failures),
        "evidence_digest": result.evidence_digest,
        "diagnostics": dict(result.diagnostics),
    }


def _selection_document(selection: SelectionResult) -> dict[str, object]:
    return {
        "selection_receipt": selection.receipt.value,
        "campaign_run_id": selection.campaign_run_id,
        "job_id": selection.job_id,
        "selected_receipt": (
            selection.selected_receipt.value
            if selection.selected_receipt is not None
            else None
        ),
        "selected_artifact_ref": (
            selection.selected_artifact_ref.value
            if selection.selected_artifact_ref is not None
            else None
        ),
        "result": _result_document(selection.result),
        "used_default": selection.used_default,
        "selection_digest": selection.selection_digest,
    }
