"""Durable delivery outbox for retryable harness side effects."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping

from ..canonical import canonical_json_bytes, digest_value, parse_json
from ..delivery import (
    DeliveryIntent,
    DeliveryReconciliation,
    HarnessDeliveryReceipt,
    job_document,
    job_from_document,
)
from ..domain import Job, SessionHandle


class SqliteDeliveryOutbox:
    """Persist complete recipient sets before calling the harness runtime."""

    def __init__(self, database: Path) -> None:
        self._database = database.resolve()
        self._lock = threading.RLock()
        self._database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @staticmethod
    def profile_digest_for() -> str:
        return digest_value(
            {
                "adapter": "sqlite-delivery-outbox/v1",
                "admission": "complete_recipient_set_before_side_effect",
                "runtime_contract": "idempotent_delivery_receipt",
                "completion": "all_recipients_acknowledged",
                "reconciliation": "exact_jobs_sessions_receipts_and_audit",
            }
        )

    @property
    def profile_digest(self) -> str:
        return self.profile_digest_for()

    def prepare(
        self,
        campaign_run_id: str,
        sessions: tuple[SessionHandle, ...],
        job: Job,
    ) -> tuple[DeliveryIntent, ...]:
        session_ids = _session_ids(sessions)
        job_value = job_document(job)
        stable_job = job_from_document(job_value)
        job_json = canonical_json_bytes(job_value).decode("utf-8")
        job_digest = digest_value(job_value)
        recipient_json = canonical_json_bytes(list(session_ids)).decode("utf-8")
        recipient_digest = digest_value(list(session_ids))
        intents = tuple(
            DeliveryIntent.create(campaign_run_id, session, stable_job)
            for session in sessions
        )
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM delivery_jobs WHERE campaign_run_id = ? AND job_id = ?",
                (campaign_run_id, stable_job.job_id),
            ).fetchone()
            expected = (job_digest, job_json, recipient_digest, recipient_json)
            if existing is None:
                connection.execute(
                    "INSERT INTO delivery_jobs("
                    "campaign_run_id, job_id, job_digest, job_json, "
                    "recipient_digest, recipient_json, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'prepared')",
                    (campaign_run_id, stable_job.job_id, *expected),
                )
                for intent in intents:
                    connection.execute(
                        "INSERT INTO delivery_intents("
                        "intent_id, campaign_run_id, job_id, session_id, "
                        "job_digest, status) VALUES (?, ?, ?, ?, ?, 'prepared')",
                        (
                            intent.intent_id,
                            campaign_run_id,
                            stable_job.job_id,
                            intent.session.value,
                            job_digest,
                        ),
                    )
                self._audit(
                    connection,
                    campaign_run_id,
                    stable_job.job_id,
                    "delivery.prepared",
                    {
                        "job_digest": job_digest,
                        "recipient_digest": recipient_digest,
                        "intent_ids": [intent.intent_id for intent in intents],
                    },
                )
            else:
                actual = tuple(
                    str(existing[name])
                    for name in (
                        "job_digest",
                        "job_json",
                        "recipient_digest",
                        "recipient_json",
                    )
                )
                if actual != expected:
                    raise RuntimeError("delivery job already differs")
                self._validate_job_row(connection, existing)
                rows = connection.execute(
                    "SELECT * FROM delivery_intents "
                    "WHERE campaign_run_id = ? AND job_id = ? ORDER BY rowid",
                    (campaign_run_id, stable_job.job_id),
                ).fetchall()
                if len(rows) != len(intents):
                    raise RuntimeError("delivery recipient set differs")
                for row, intent in zip(rows, intents, strict=True):
                    self._validate_intent_row(row, intent)
        return intents

    def acknowledged(
        self, intent: DeliveryIntent
    ) -> HarnessDeliveryReceipt | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM delivery_intents WHERE intent_id = ?",
                (intent.intent_id,),
            ).fetchone()
        if row is None:
            raise KeyError("delivery intent is unknown")
        self._validate_intent_row(row, intent)
        status = str(row["status"])
        if status == "prepared":
            return None
        if status != "acknowledged":
            raise RuntimeError("delivery intent status is invalid")
        return self._receipt_from_row(row)

    def acknowledge(
        self, intent: DeliveryIntent, receipt: HarnessDeliveryReceipt
    ) -> HarnessDeliveryReceipt:
        self._validate_receipt(intent, receipt)
        receipt_json = canonical_json_bytes(receipt.document).decode("utf-8")
        receipt_digest = digest_value(receipt.document)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM delivery_intents WHERE intent_id = ?",
                (intent.intent_id,),
            ).fetchone()
            if row is None:
                raise KeyError("delivery intent is unknown")
            self._validate_intent_row(row, intent)
            if str(row["status"]) == "prepared":
                updated = connection.execute(
                    "UPDATE delivery_intents SET status = 'acknowledged', "
                    "receipt_json = ?, receipt_digest = ? "
                    "WHERE intent_id = ? AND status = 'prepared'",
                    (receipt_json, receipt_digest, intent.intent_id),
                )
                if updated.rowcount != 1:
                    raise RuntimeError("delivery acknowledgement raced")
                self._audit(
                    connection,
                    intent.campaign_run_id,
                    intent.intent_id,
                    "delivery.acknowledged",
                    {
                        "job_id": intent.job.job_id,
                        "session_id": intent.session.value,
                        "receipt_digest": receipt_digest,
                    },
                )
            elif (
                str(row["status"]) != "acknowledged"
                or row["receipt_json"] != receipt_json
                or row["receipt_digest"] != receipt_digest
            ):
                raise RuntimeError("delivery acknowledgement already differs")
        return receipt

    def complete(
        self, campaign_run_id: str, job_id: str
    ) -> tuple[HarnessDeliveryReceipt, ...]:
        with self._transaction() as connection:
            job_row = connection.execute(
                "SELECT * FROM delivery_jobs WHERE campaign_run_id = ? AND job_id = ?",
                (campaign_run_id, job_id),
            ).fetchone()
            if job_row is None:
                raise KeyError("delivery job is unknown")
            self._validate_job_row(connection, job_row)
            receipts = self._job_receipts(connection, job_row)
            completion_digest = digest_value(
                [receipt.document for receipt in receipts]
            )
            status = str(job_row["status"])
            if status == "prepared":
                updated = connection.execute(
                    "UPDATE delivery_jobs SET status = 'complete', "
                    "completion_digest = ? "
                    "WHERE campaign_run_id = ? AND job_id = ? AND status = 'prepared'",
                    (completion_digest, campaign_run_id, job_id),
                )
                if updated.rowcount != 1:
                    raise RuntimeError("delivery completion raced")
                self._audit(
                    connection,
                    campaign_run_id,
                    job_id,
                    "delivery.completed",
                    {
                        "completion_digest": completion_digest,
                        "receipt_ids": [receipt.receipt_id for receipt in receipts],
                    },
                )
            elif (
                status != "complete"
                or str(job_row["completion_digest"]) != completion_digest
            ):
                raise RuntimeError("delivery completion already differs")
        return receipts

    def read_job(self, campaign_run_id: str, job_id: str) -> Job:
        """Read and validate a retained job without creating an outbox intent."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM delivery_jobs WHERE campaign_run_id = ? AND job_id = ?",
                (campaign_run_id, job_id),
            ).fetchone()
            if row is None:
                raise KeyError("delivery job is unknown")
            self._validate_job_row(connection, row)
            return job_from_document(parse_json(row["job_json"]))

    def completed_job_ids(self, campaign_run_id: str) -> tuple[str, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM delivery_jobs WHERE campaign_run_id = ? "
                "ORDER BY job_sequence",
                (campaign_run_id,),
            ).fetchall()
            completed: list[str] = []
            for row in rows:
                self._validate_job_row(connection, row)
                if str(row["status"]) == "complete":
                    self._job_receipts(connection, row)
                    completed.append(str(row["job_id"]))
        return tuple(completed)

    def reconcile(
        self,
        campaign_run_id: str,
        sessions: tuple[SessionHandle, ...],
        delivered_job_ids: tuple[str, ...],
    ) -> DeliveryReconciliation:
        expected_sessions = _session_ids(sessions)
        if len(set(delivered_job_ids)) != len(delivered_job_ids):
            raise RuntimeError("delivered job identifiers repeat")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM delivery_jobs WHERE campaign_run_id = ? "
                "ORDER BY job_sequence",
                (campaign_run_id,),
            ).fetchall()
            if tuple(str(row["job_id"]) for row in rows) != delivered_job_ids:
                raise RuntimeError("delivery ledger job set differs")
            receipts: list[HarnessDeliveryReceipt] = []
            for row in rows:
                self._validate_job_row(connection, row)
                recipients = self._recipients(row)
                if recipients != expected_sessions:
                    raise RuntimeError("delivery ledger session set differs")
                if str(row["status"]) != "complete":
                    raise RuntimeError("delivery ledger contains incomplete work")
                current = self._job_receipts(connection, row)
                completion_digest = digest_value(
                    [receipt.document for receipt in current]
                )
                if row["completion_digest"] != completion_digest:
                    raise RuntimeError("delivery completion digest differs")
                receipts.extend(current)
            self._validate_audit(connection, campaign_run_id, rows)
        return DeliveryReconciliation.create(
            campaign_run_id, delivered_job_ids, tuple(receipts)
        )

    def _job_receipts(
        self, connection: sqlite3.Connection, job_row: sqlite3.Row
    ) -> tuple[HarnessDeliveryReceipt, ...]:
        rows = connection.execute(
            "SELECT * FROM delivery_intents "
            "WHERE campaign_run_id = ? AND job_id = ? ORDER BY rowid",
            (job_row["campaign_run_id"], job_row["job_id"]),
        ).fetchall()
        recipients = self._recipients(job_row)
        if tuple(str(row["session_id"]) for row in rows) != recipients:
            raise RuntimeError("delivery intent recipient set differs")
        receipts: list[HarnessDeliveryReceipt] = []
        for row in rows:
            intent = self._intent_from_row(job_row, row)
            self._validate_intent_row(row, intent)
            if str(row["status"]) != "acknowledged":
                raise RuntimeError("delivery receipt is pending")
            receipt = self._receipt_from_row(row)
            self._validate_receipt(intent, receipt)
            receipts.append(receipt)
        return tuple(receipts)

    def _validate_job_row(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> Job:
        try:
            value = parse_json(str(row["job_json"]))
        except (json.JSONDecodeError, ValueError) as error:
            raise RuntimeError("delivery job JSON is invalid") from error
        if not isinstance(value, dict):
            raise RuntimeError("delivery job JSON must be an object")
        if canonical_json_bytes(value).decode("utf-8") != row["job_json"]:
            raise RuntimeError("delivery job JSON is not canonical")
        job = job_from_document(value)
        if (
            job.job_id != row["job_id"]
            or digest_value(value) != row["job_digest"]
        ):
            raise RuntimeError("delivery job digest differs")
        recipients = self._recipients(row)
        if digest_value(list(recipients)) != row["recipient_digest"]:
            raise RuntimeError("delivery recipient digest differs")
        if str(row["status"]) not in {"prepared", "complete"}:
            raise RuntimeError("delivery job status is invalid")
        return job

    @staticmethod
    def _recipients(row: sqlite3.Row) -> tuple[str, ...]:
        try:
            value = parse_json(str(row["recipient_json"]))
        except (json.JSONDecodeError, ValueError) as error:
            raise RuntimeError("delivery recipient JSON is invalid") from error
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item for item in value
        ):
            raise RuntimeError("delivery recipients are invalid")
        if len(set(value)) != len(value):
            raise RuntimeError("delivery recipients repeat")
        if canonical_json_bytes(value).decode("utf-8") != row["recipient_json"]:
            raise RuntimeError("delivery recipient JSON is not canonical")
        return tuple(value)

    @staticmethod
    def _intent_from_row(
        job_row: sqlite3.Row, intent_row: sqlite3.Row
    ) -> DeliveryIntent:
        value = parse_json(str(job_row["job_json"]))
        assert isinstance(value, dict)
        return DeliveryIntent.create(
            str(intent_row["campaign_run_id"]),
            SessionHandle(str(intent_row["session_id"])),
            job_from_document(value),
        )

    @staticmethod
    def _validate_intent_row(row: sqlite3.Row, intent: DeliveryIntent) -> None:
        expected = (
            intent.intent_id,
            intent.campaign_run_id,
            intent.job.job_id,
            intent.session.value,
            digest_value(job_document(intent.job)),
        )
        actual = tuple(
            str(row[name])
            for name in (
                "intent_id",
                "campaign_run_id",
                "job_id",
                "session_id",
                "job_digest",
            )
        )
        if actual != expected:
            raise RuntimeError("delivery intent differs")
        if str(row["status"]) not in {"prepared", "acknowledged"}:
            raise RuntimeError("delivery intent status is invalid")

    @staticmethod
    def _validate_receipt(
        intent: DeliveryIntent, receipt: HarnessDeliveryReceipt
    ) -> None:
        if (
            receipt.session_id != intent.session.value
            or receipt.job_id != intent.job.job_id
            or receipt.materials_digest != intent.job.materials_digest
        ):
            raise RuntimeError("harness delivery receipt differs from intent")

    @staticmethod
    def _receipt_from_row(row: sqlite3.Row) -> HarnessDeliveryReceipt:
        receipt_json = row["receipt_json"]
        receipt_digest = row["receipt_digest"]
        if not isinstance(receipt_json, str) or not isinstance(
            receipt_digest, str
        ):
            raise RuntimeError("delivery receipt is missing")
        try:
            value = parse_json(receipt_json)
        except (json.JSONDecodeError, ValueError) as error:
            raise RuntimeError("delivery receipt JSON is invalid") from error
        if not isinstance(value, dict):
            raise RuntimeError("delivery receipt JSON must be an object")
        if canonical_json_bytes(value).decode("utf-8") != receipt_json:
            raise RuntimeError("delivery receipt JSON is not canonical")
        if digest_value(value) != receipt_digest:
            raise RuntimeError("delivery receipt digest differs")
        return HarnessDeliveryReceipt.from_document(value)

    def _validate_audit(
        self,
        connection: sqlite3.Connection,
        campaign_run_id: str,
        job_rows: list[sqlite3.Row],
    ) -> None:
        rows = connection.execute(
            "SELECT * FROM delivery_audit WHERE campaign_run_id = ? "
            "ORDER BY sequence",
            (campaign_run_id,),
        ).fetchall()
        expected: list[tuple[str, str, Mapping[str, Any]]] = []
        for job_row in job_rows:
            recipients = self._recipients(job_row)
            intent_rows = connection.execute(
                "SELECT * FROM delivery_intents "
                "WHERE campaign_run_id = ? AND job_id = ? ORDER BY rowid",
                (campaign_run_id, job_row["job_id"]),
            ).fetchall()
            expected.append(
                (
                    str(job_row["job_id"]),
                    "delivery.prepared",
                    {
                        "job_digest": str(job_row["job_digest"]),
                        "recipient_digest": str(job_row["recipient_digest"]),
                        "intent_ids": [str(row["intent_id"]) for row in intent_rows],
                    },
                )
            )
            for intent_row, session_id in zip(
                intent_rows, recipients, strict=True
            ):
                expected.append(
                    (
                        str(intent_row["intent_id"]),
                        "delivery.acknowledged",
                        {
                            "job_id": str(job_row["job_id"]),
                            "session_id": session_id,
                            "receipt_digest": str(intent_row["receipt_digest"]),
                        },
                    )
                )
            receipts = self._job_receipts(connection, job_row)
            expected.append(
                (
                    str(job_row["job_id"]),
                    "delivery.completed",
                    {
                        "completion_digest": str(job_row["completion_digest"]),
                        "receipt_ids": [receipt.receipt_id for receipt in receipts],
                    },
                )
            )
        observed_records: list[tuple[str, str, Mapping[str, Any]]] = []
        for row in rows:
            try:
                observed = parse_json(str(row["payload_json"]))
            except (json.JSONDecodeError, ValueError) as error:
                raise RuntimeError("delivery audit JSON is invalid") from error
            if (
                not isinstance(observed, dict)
                or canonical_json_bytes(observed).decode("utf-8")
                != row["payload_json"]
                or digest_value(observed) != row["payload_digest"]
            ):
                raise RuntimeError("delivery audit record differs")
            observed_records.append(
                (str(row["subject_id"]), str(row["kind"]), observed)
            )
        sort_key = lambda item: canonical_json_bytes(  # noqa: E731
            {"subject_id": item[0], "kind": item[1], "payload": item[2]}
        )
        if sorted(observed_records, key=sort_key) != sorted(expected, key=sort_key):
            raise RuntimeError("delivery audit record set differs")

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        campaign_run_id: str,
        subject_id: str,
        kind: str,
        payload: Mapping[str, Any],
    ) -> None:
        payload_json = canonical_json_bytes(payload).decode("utf-8")
        connection.execute(
            "INSERT INTO delivery_audit("
            "campaign_run_id, subject_id, kind, payload_json, payload_digest) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                campaign_run_id,
                subject_id,
                kind,
                payload_json,
                digest_value(payload),
            ),
        )

    def _initialize(self) -> None:
        with self._transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS delivery_jobs (
                    job_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_run_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    job_digest TEXT NOT NULL,
                    job_json TEXT NOT NULL,
                    recipient_digest TEXT NOT NULL,
                    recipient_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('prepared', 'complete')),
                    completion_digest TEXT,
                    UNIQUE (campaign_run_id, job_id)
                );
                CREATE TABLE IF NOT EXISTS delivery_intents (
                    intent_id TEXT PRIMARY KEY,
                    campaign_run_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    job_digest TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('prepared', 'acknowledged')
                    ),
                    receipt_json TEXT,
                    receipt_digest TEXT,
                    UNIQUE (campaign_run_id, job_id, session_id),
                    FOREIGN KEY (campaign_run_id, job_id)
                        REFERENCES delivery_jobs(campaign_run_id, job_id)
                );
                CREATE TABLE IF NOT EXISTS delivery_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_run_id TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_digest TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS delivery_jobs_campaign
                    ON delivery_jobs(campaign_run_id, job_sequence);
                CREATE INDEX IF NOT EXISTS delivery_intents_job
                    ON delivery_intents(campaign_run_id, job_id);
                CREATE INDEX IF NOT EXISTS delivery_audit_campaign
                    ON delivery_audit(campaign_run_id, sequence);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    class _Transaction:
        def __init__(self, outbox: "SqliteDeliveryOutbox") -> None:
            self._outbox = outbox
            self._connection: sqlite3.Connection | None = None

        def __enter__(self) -> sqlite3.Connection:
            self._outbox._lock.acquire()
            self._connection = self._outbox._connect()
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
                self._outbox._lock.release()

    def _transaction(self) -> "SqliteDeliveryOutbox._Transaction":
        return self._Transaction(self)


def _session_ids(sessions: tuple[SessionHandle, ...]) -> tuple[str, ...]:
    if not sessions or any(not session.value for session in sessions):
        raise ValueError("delivery sessions are required")
    values = tuple(session.value for session in sessions)
    if len(set(values)) != len(values):
        raise ValueError("delivery sessions repeat")
    return values
