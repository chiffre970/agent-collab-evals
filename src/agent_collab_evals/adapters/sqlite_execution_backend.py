"""Durable idempotent execution around an external compute transport."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Mapping

from ..canonical import (
    canonical_json_bytes,
    digest_bytes,
    digest_value,
    parse_json,
)
from ..compute_backend import (
    ComputeEvidencePointer,
    ComputeExecutionReceipt,
    ComputeExecutionRequest,
    ComputeExecutionStatus,
    DefinitiveDispatchError,
    FrozenComputeRunManifest,
)
from ..ports import ComputeEvidenceResolver, ComputeExecutionTransport


class SqliteComputeBackend:
    """Persist dispatch intent before any external GPU side effect."""

    def __init__(
        self,
        database: Path,
        transport: ComputeExecutionTransport,
        evidence: ComputeEvidenceResolver,
        authority: FrozenComputeRunManifest,
    ) -> None:
        self._database = database
        self._transport = transport
        self._evidence = evidence
        self._authority = authority
        self._lock = threading.RLock()
        self._profile_digest = self.profile_digest_for(
            transport.profile_digest, evidence.profile_digest
        )
        authority.assert_backend_profiles(
            self._profile_digest, transport.profile_digest
        )
        database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @staticmethod
    def profile_digest_for(
        transport_profile_digest: str, evidence_profile_digest: str
    ) -> str:
        return digest_value(
            {
                "adapter": "sqlite-compute-backend/v0alpha2",
                "transport_profile_digest": transport_profile_digest,
                "evidence_profile_digest": evidence_profile_digest,
                "authority": "frozen_compute_run_manifest",
                "ambiguous_dispatch_policy": "fail_closed_no_redispatch",
            }
        )

    @property
    def profile_digest(self) -> str:
        return self._profile_digest

    def submit(
        self, request: ComputeExecutionRequest, candidate: bytes
    ) -> ComputeExecutionReceipt:
        if digest_bytes(candidate) != request.candidate_digest:
            raise ValueError("candidate bytes differ from the compute request")
        request_json = canonical_json_bytes(_request_document(request)).decode()
        self._authority.assert_authorized(request)
        claimed = False
        with self._transaction() as connection:
            row = self._execution_row(
                connection, request.execution_key, required=False
            )
            if row is None:
                execution_id = "execution-" + request.request_digest[7:39]
                connection.execute(
                    "INSERT INTO compute_executions("
                    "execution_id, execution_key, campaign_run_id, request_digest, "
                    "request_json, candidate_digest, transport_profile_digest, "
                    "evidence_profile_digest, run_manifest_digest, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'registered')",
                    (
                        execution_id,
                        request.execution_key,
                        request.campaign_run_id,
                        request.request_digest,
                        request_json,
                        request.candidate_digest,
                        self._transport.profile_digest,
                        self._evidence.profile_digest,
                        self._authority.manifest_digest,
                    ),
                )
                self._audit(
                    connection,
                    request.campaign_run_id,
                    request.execution_key,
                    "compute_execution.registered",
                    {"request_digest": request.request_digest},
                )
                row = self._execution_row(connection, request.execution_key)
            self._validate_row(row, request, request_json)
            status = ComputeExecutionStatus(str(row["status"]))
            if status is ComputeExecutionStatus.REGISTERED:
                connection.execute(
                    "UPDATE compute_executions SET status = 'dispatching' "
                    "WHERE execution_key = ?",
                    (request.execution_key,),
                )
                self._audit(
                    connection,
                    request.campaign_run_id,
                    request.execution_key,
                    "compute_execution.dispatch_claimed",
                    {},
                )
                claimed = True
            else:
                return self._receipt(row)

        if not claimed:
            raise AssertionError("compute dispatch claim was lost")
        try:
            dispatch = self._transport.dispatch(request, candidate)
            dispatch_evidence = self._evidence.resolve_dispatch(
                request, dispatch.external_call_id
            )
            if digest_bytes(dispatch_evidence) != dispatch.dispatch_evidence_digest:
                raise RuntimeError("compute dispatch evidence digest differs")
        except DefinitiveDispatchError as error:
            return self._terminal_without_dispatch(request, str(error))
        except Exception as error:
            reason = f"{type(error).__name__}:{error}"
            with self._transaction() as connection:
                row = self._execution_row(connection, request.execution_key)
                self._validate_row(row, request, request_json)
                connection.execute(
                    "UPDATE compute_executions SET status = 'ambiguous', failure = ? "
                    "WHERE execution_key = ? AND status = 'dispatching'",
                    (reason, request.execution_key),
                )
                self._audit(
                    connection,
                    request.campaign_run_id,
                    request.execution_key,
                    "compute_execution.dispatch_ambiguous",
                    {"reason": reason},
                )
                return self._receipt(
                    self._execution_row(connection, request.execution_key)
                )

        with self._transaction() as connection:
            row = self._execution_row(connection, request.execution_key)
            self._validate_row(row, request, request_json)
            if ComputeExecutionStatus(str(row["status"])) is not (
                ComputeExecutionStatus.DISPATCHING
            ):
                raise RuntimeError("compute dispatch state changed unexpectedly")
            connection.execute(
                "UPDATE compute_executions SET status = 'dispatched', "
                "external_call_id = ?, dispatch_evidence_digest = ? "
                "WHERE execution_key = ?",
                (
                    dispatch.external_call_id,
                    dispatch.dispatch_evidence_digest,
                    request.execution_key,
                ),
            )
            self._audit(
                connection,
                request.campaign_run_id,
                request.execution_key,
                "compute_execution.dispatched",
                {
                    "external_call_id": dispatch.external_call_id,
                    "dispatch_evidence_digest": dispatch.dispatch_evidence_digest,
                },
            )
            return self._receipt(
                self._execution_row(connection, request.execution_key)
            )

    def collect(
        self, request: ComputeExecutionRequest, *, timeout_seconds: int
    ) -> ComputeExecutionReceipt:
        if type(timeout_seconds) is not int or not 0 <= timeout_seconds <= 300:
            raise ValueError("compute collection timeout must be between 0 and 300")
        request_json = canonical_json_bytes(_request_document(request)).decode()
        self._authority.assert_authorized(request)
        with closing(self._connect()) as connection:
            row = self._execution_row(connection, request.execution_key)
            self._validate_row(row, request, request_json)
        status = ComputeExecutionStatus(str(row["status"]))
        if status in {
            ComputeExecutionStatus.COMPLETE,
            ComputeExecutionStatus.FAILED,
        }:
            if row["evidence_locator"] is not None:
                self._resolve_evidence(request, row)
            return self._receipt(row)
        if status is not ComputeExecutionStatus.DISPATCHED:
            raise RuntimeError(
                f"compute execution cannot be collected from {status.value}"
            )
        self._resolve_dispatch(request, row)
        external_call_id = str(row["external_call_id"])
        try:
            poll = self._transport.poll(
                request, external_call_id, timeout_seconds
            )
        except TimeoutError:
            return self._receipt(row)
        if poll.status is ComputeExecutionStatus.DISPATCHED:
            return self._receipt(row)
        assert poll.evidence is not None and poll.used_seconds is not None
        evidence_document = self._load_and_validate_evidence(
            request,
            external_call_id,
            poll.evidence,
            expected_status=poll.status,
            expected_used_seconds=poll.used_seconds,
            expected_failure=poll.failure,
        )
        with self._transaction() as connection:
            current = self._execution_row(connection, request.execution_key)
            self._validate_row(current, request, request_json)
            current_status = ComputeExecutionStatus(str(current["status"]))
            if current_status in {
                ComputeExecutionStatus.COMPLETE,
                ComputeExecutionStatus.FAILED,
            }:
                self._resolve_evidence(request, current)
                return self._receipt(current)
            if current_status is not ComputeExecutionStatus.DISPATCHED:
                raise RuntimeError("compute execution changed during collection")
            connection.execute(
                "UPDATE compute_executions SET status = ?, evidence_locator = ?, "
                "evidence_digest = ?, used_seconds = ?, failure = ? "
                "WHERE execution_key = ?",
                (
                    poll.status.value,
                    poll.evidence.locator,
                    poll.evidence.digest,
                    poll.used_seconds,
                    poll.failure,
                    request.execution_key,
                ),
            )
            self._audit(
                connection,
                request.campaign_run_id,
                request.execution_key,
                f"compute_execution.{poll.status.value}",
                {
                    "evidence_digest": poll.evidence.digest,
                    "used_seconds": poll.used_seconds,
                    "result_digest": digest_value(evidence_document["result"]),
                },
            )
            return self._receipt(
                self._execution_row(connection, request.execution_key)
            )

    def resolve(
        self, request: ComputeExecutionRequest
    ) -> tuple[ComputeExecutionReceipt, Mapping[str, object]]:
        request_json = canonical_json_bytes(_request_document(request)).decode()
        self._authority.assert_authorized(request)
        with closing(self._connect()) as connection:
            row = self._execution_row(connection, request.execution_key)
            self._validate_row(row, request, request_json)
        status = ComputeExecutionStatus(str(row["status"]))
        if status not in {
            ComputeExecutionStatus.COMPLETE,
            ComputeExecutionStatus.FAILED,
        } or row["evidence_locator"] is None:
            raise RuntimeError("compute execution has no resolvable terminal evidence")
        self._resolve_dispatch(request, row)
        document = self._resolve_evidence(request, row)
        return self._receipt(row), document

    def reconcile(
        self, campaign_run_id: str
    ) -> tuple[ComputeExecutionReceipt, ...]:
        if campaign_run_id != self._authority.campaign_run_id:
            raise ValueError("compute authority belongs to another campaign")
        authorized_requests = self._authority.requests()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM compute_executions WHERE campaign_run_id = ? "
                "ORDER BY execution_key",
                (campaign_run_id,),
            ).fetchall()
        expected_keys = tuple(request.execution_key for request in authorized_requests)
        actual_keys = tuple(str(row["execution_key"]) for row in rows)
        if actual_keys != tuple(sorted(expected_keys)):
            raise RuntimeError("compute ledger differs from the frozen run plan")
        receipts: list[ComputeExecutionReceipt] = []
        for row in rows:
            request = self._authority.request(str(row["execution_key"]))
            request_json = canonical_json_bytes(_request_document(request)).decode()
            status = ComputeExecutionStatus(str(row["status"]))
            if status not in {
                ComputeExecutionStatus.COMPLETE,
                ComputeExecutionStatus.FAILED,
            }:
                raise RuntimeError(
                    f"compute execution is not reconcilable: {status.value}"
                )
            self._validate_row(row, request, request_json)
            if row["external_call_id"] is not None:
                self._resolve_dispatch(request, row)
            if row["evidence_locator"] is not None:
                self._resolve_evidence(request, row)
            receipts.append(self._receipt(row))
        return tuple(receipts)

    def _terminal_without_dispatch(
        self, request: ComputeExecutionRequest, reason: str
    ) -> ComputeExecutionReceipt:
        with self._transaction() as connection:
            row = self._execution_row(connection, request.execution_key)
            connection.execute(
                "UPDATE compute_executions SET status = 'failed', failure = ? "
                "WHERE execution_key = ? AND status = 'dispatching'",
                (reason or "dispatch rejected", request.execution_key),
            )
            self._audit(
                connection,
                request.campaign_run_id,
                request.execution_key,
                "compute_execution.dispatch_rejected",
                {"reason": reason or "dispatch rejected"},
            )
            return self._receipt(
                self._execution_row(connection, request.execution_key)
            )

    def _validate_row(
        self,
        row: sqlite3.Row,
        request: ComputeExecutionRequest,
        request_json: str,
    ) -> None:
        expected = (
            request.campaign_run_id,
            request.request_digest,
            request_json,
            request.candidate_digest,
            self._transport.profile_digest,
            self._evidence.profile_digest,
            self._authority.manifest_digest,
        )
        actual = tuple(
            str(row[key])
            for key in (
                "campaign_run_id",
                "request_digest",
                "request_json",
                "candidate_digest",
                "transport_profile_digest",
                "evidence_profile_digest",
                "run_manifest_digest",
            )
        )
        if actual != expected:
            raise RuntimeError("stored compute execution differs from its authority")
        if str(row["execution_id"]) != "execution-" + request.request_digest[7:39]:
            raise RuntimeError("stored compute execution ID differs")

    def _resolve_evidence(
        self, request: ComputeExecutionRequest, row: sqlite3.Row
    ) -> Mapping[str, object]:
        pointer = ComputeEvidencePointer(
            str(row["evidence_locator"]), str(row["evidence_digest"])
        )
        return self._load_and_validate_evidence(
            request,
            str(row["external_call_id"]),
            pointer,
            expected_status=ComputeExecutionStatus(str(row["status"])),
            expected_used_seconds=int(row["used_seconds"]),
            expected_failure=(
                str(row["failure"]) if row["failure"] is not None else None
            ),
        )

    def _resolve_dispatch(
        self, request: ComputeExecutionRequest, row: sqlite3.Row
    ) -> bytes:
        external_call_id = row["external_call_id"]
        expected_digest = row["dispatch_evidence_digest"]
        if not isinstance(external_call_id, str) or not isinstance(
            expected_digest, str
        ):
            raise RuntimeError("compute dispatch evidence is incomplete")
        content = self._evidence.resolve_dispatch(request, external_call_id)
        if digest_bytes(content) != expected_digest:
            raise RuntimeError("compute dispatch evidence digest differs")
        return content

    def _load_and_validate_evidence(
        self,
        request: ComputeExecutionRequest,
        external_call_id: str,
        pointer: ComputeEvidencePointer,
        *,
        expected_status: ComputeExecutionStatus,
        expected_used_seconds: int,
        expected_failure: str | None,
    ) -> Mapping[str, object]:
        if not 0 <= expected_used_seconds <= request.maximum_seconds:
            raise RuntimeError("compute evidence use exceeds its reservation")
        evidence = self._evidence.resolve(pointer)
        if digest_bytes(evidence) != pointer.digest:
            raise RuntimeError("compute evidence bytes differ from their pointer")
        document = parse_json(evidence.decode("utf-8"))
        if not isinstance(document, dict) or set(document) != {
            "schema_version",
            "request_digest",
            "candidate_digest",
            "candidate_manifest_digest",
            "evaluator_profile_digest",
            "transport_profile_digest",
            "evidence_profile_digest",
            "external_call_id",
            "status",
            "used_seconds",
            "failure",
            "result",
        }:
            raise RuntimeError("compute evidence fields differ")
        expected = {
            "schema_version": "compute-execution-evidence/v0alpha1",
            "request_digest": request.request_digest,
            "candidate_digest": request.candidate_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "evaluator_profile_digest": request.evaluator_profile_digest,
            "transport_profile_digest": self._transport.profile_digest,
            "evidence_profile_digest": self._evidence.profile_digest,
            "external_call_id": external_call_id,
            "status": expected_status.value,
            "used_seconds": expected_used_seconds,
            "failure": expected_failure,
        }
        if any(document.get(key) != value for key, value in expected.items()):
            raise RuntimeError("compute evidence identity differs")
        if not isinstance(document["result"], dict):
            raise RuntimeError("compute evidence result must be an object")
        return document

    @staticmethod
    def _receipt(row: sqlite3.Row) -> ComputeExecutionReceipt:
        evidence = None
        if row["evidence_locator"] is not None:
            evidence = ComputeEvidencePointer(
                str(row["evidence_locator"]), str(row["evidence_digest"])
            )
        return ComputeExecutionReceipt(
            execution_id=str(row["execution_id"]),
            execution_key=str(row["execution_key"]),
            request_digest=str(row["request_digest"]),
            status=ComputeExecutionStatus(str(row["status"])),
            external_call_id=(
                str(row["external_call_id"])
                if row["external_call_id"] is not None
                else None
            ),
            evidence=evidence,
            used_seconds=(
                int(row["used_seconds"])
                if row["used_seconds"] is not None
                else None
            ),
            failure=(str(row["failure"]) if row["failure"] is not None else None),
        )

    @staticmethod
    def _execution_row(
        connection: sqlite3.Connection,
        execution_key: str,
        *,
        required: bool = True,
    ) -> sqlite3.Row | None:
        row = connection.execute(
            "SELECT * FROM compute_executions WHERE execution_key = ?",
            (execution_key,),
        ).fetchone()
        if row is None and required:
            raise KeyError("compute execution is not registered")
        return row

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        campaign_run_id: str,
        execution_key: str,
        kind: str,
        details: Mapping[str, object],
    ) -> None:
        connection.execute(
            "INSERT INTO compute_execution_audit("
            "campaign_run_id, execution_key, kind, details_json) "
            "VALUES (?, ?, ?, ?)",
            (
                campaign_run_id,
                execution_key,
                kind,
                canonical_json_bytes(details).decode(),
            ),
        )

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS compute_executions(
                    execution_id TEXT PRIMARY KEY,
                    execution_key TEXT NOT NULL UNIQUE,
                    campaign_run_id TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    candidate_digest TEXT NOT NULL,
                    transport_profile_digest TEXT NOT NULL,
                    evidence_profile_digest TEXT NOT NULL,
                    run_manifest_digest TEXT NOT NULL,
                    status TEXT NOT NULL,
                    external_call_id TEXT,
                    dispatch_evidence_digest TEXT,
                    evidence_locator TEXT,
                    evidence_digest TEXT,
                    used_seconds INTEGER,
                    failure TEXT
                );
                CREATE INDEX IF NOT EXISTS compute_executions_campaign
                ON compute_executions(campaign_run_id, execution_key);
                CREATE TABLE IF NOT EXISTS compute_execution_audit(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_run_id TEXT NOT NULL,
                    execution_key TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(compute_executions)"
                ).fetchall()
            }
            if "run_manifest_digest" not in columns:
                connection.execute(
                    "ALTER TABLE compute_executions "
                    "ADD COLUMN run_manifest_digest TEXT"
                )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    class _Transaction:
        def __init__(self, backend: "SqliteComputeBackend") -> None:
            self._backend = backend
            self._connection: sqlite3.Connection | None = None

        def __enter__(self) -> sqlite3.Connection:
            self._backend._lock.acquire()
            self._connection = self._backend._connect()
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
                self._backend._lock.release()

    def _transaction(self) -> "SqliteComputeBackend._Transaction":
        return self._Transaction(self)


def _request_document(request: ComputeExecutionRequest) -> dict[str, object]:
    return request.document
