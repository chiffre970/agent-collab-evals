"""Durable publication authorization registry."""

from __future__ import annotations

import secrets
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Mapping

from ..artifacts import (
    ArtifactRef,
    PublicationAudience,
    PublicationId,
    PublicationRecord,
    PublicationSnapshot,
    PublicationStatus,
    TrustedServiceTransport,
)
from ..canonical import canonical_json_bytes, parse_json
from ..service_identity import ServiceIdentityRegistry


class SqlitePublicationRegistry:
    """Persists publication state without reading artifact bytes."""

    def __init__(
        self, database_path: Path, services: ServiceIdentityRegistry
    ) -> None:
        self._database_path = database_path
        self._services = services
        self._lock = threading.RLock()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def prepare(
        self,
        service: TrustedServiceTransport,
        publication_key: str,
        campaign_run_id: str,
        owner_actor_id: str,
        artifact_ref: ArtifactRef,
        audience: PublicationAudience,
    ) -> PublicationId:
        service_name = self._authorize(service)
        if not publication_key or len(publication_key) > 256:
            raise ValueError("publication_key must contain 1 to 256 characters")
        if not campaign_run_id or not owner_actor_id or not artifact_ref.value:
            raise ValueError("campaign, owner, and artifact are required")
        requested = (
            campaign_run_id,
            owner_actor_id,
            artifact_ref.value,
            audience.value,
        )
        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM publications
                WHERE campaign_run_id = ? AND publication_key = ?
                """,
                (campaign_run_id, publication_key),
            ).fetchone()
            if existing is not None:
                stored = (
                    str(existing["campaign_run_id"]),
                    str(existing["owner_actor_id"]),
                    str(existing["artifact_ref"]),
                    str(existing["audience"]),
                )
                if stored != requested:
                    raise ValueError(
                        "publication key reused with different arguments"
                    )
                if existing["status"] == PublicationStatus.ABORTED.value:
                    raise RuntimeError("publication preparation was aborted")
                return PublicationId(str(existing["publication_id"]))

            publication_id = PublicationId(f"publication-{secrets.token_hex(16)}")
            connection.execute(
                """
                INSERT INTO publications(
                    publication_id, publication_key, campaign_run_id,
                    owner_actor_id, artifact_ref, audience, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    publication_id.value,
                    publication_key,
                    campaign_run_id,
                    owner_actor_id,
                    artifact_ref.value,
                    audience.value,
                    PublicationStatus.PREPARED.value,
                ),
            )
            self._append_audit(
                connection,
                campaign_run_id,
                "publication.prepared",
                service_name,
                {"publication_id": publication_id.value},
            )
            return publication_id

    def bind(
        self,
        service: TrustedServiceTransport,
        publication_id: PublicationId,
        entry_id: str,
    ) -> None:
        service_name = self._authorize(service)
        if not entry_id:
            raise ValueError("entry_id is required")
        with self._transaction() as connection:
            record = self._stored_record(connection, publication_id)
            if record.status is PublicationStatus.ABORTED:
                raise RuntimeError("aborted publication cannot be bound")
            if record.status is PublicationStatus.BOUND:
                if record.entry_id != entry_id:
                    raise ValueError("publication is already bound to another entry")
                return
            connection.execute(
                """
                UPDATE publications SET status = ?, entry_id = ?
                WHERE publication_id = ?
                """,
                (
                    PublicationStatus.BOUND.value,
                    entry_id,
                    publication_id.value,
                ),
            )
            self._append_audit(
                connection,
                record.campaign_run_id,
                "publication.bound",
                service_name,
                {"publication_id": publication_id.value, "entry_id": entry_id},
            )

    def abort(
        self,
        service: TrustedServiceTransport,
        publication_id: PublicationId,
        reason: str,
    ) -> None:
        service_name = self._authorize(service)
        if not reason:
            raise ValueError("abort reason is required")
        with self._transaction() as connection:
            record = self._stored_record(connection, publication_id)
            if record.status is PublicationStatus.BOUND:
                raise RuntimeError("bound publication cannot be aborted")
            if record.status is PublicationStatus.ABORTED:
                if record.abort_reason != reason:
                    raise ValueError("publication was aborted for another reason")
                return
            connection.execute(
                """
                UPDATE publications SET status = ?, abort_reason = ?
                WHERE publication_id = ?
                """,
                (
                    PublicationStatus.ABORTED.value,
                    reason,
                    publication_id.value,
                ),
            )
            self._append_audit(
                connection,
                record.campaign_run_id,
                "publication.aborted",
                service_name,
                {"publication_id": publication_id.value, "reason": reason},
            )

    def resolve(
        self,
        service: TrustedServiceTransport,
        campaign_run_id: str,
        publication_id: PublicationId,
    ) -> PublicationRecord:
        service_name = self._authorize(service)
        with self._transaction() as connection:
            record = self._stored_record(connection, publication_id)
            if record.campaign_run_id != campaign_run_id:
                raise PermissionError("publication belongs to a different campaign")
            if record.status is not PublicationStatus.BOUND:
                raise KeyError("publication is not active")
            self._append_audit(
                connection,
                campaign_run_id,
                "publication.resolved",
                service_name,
                {"publication_id": publication_id.value},
            )
            return record

    def export(self, campaign_run_id: str) -> PublicationSnapshot:
        with self._transaction() as connection:
            records = tuple(
                self._row_to_record(row)
                for row in connection.execute(
                    """
                    SELECT * FROM publications
                    WHERE campaign_run_id = ? ORDER BY rowid
                    """,
                    (campaign_run_id,),
                ).fetchall()
            )
            audit = tuple(
                {
                    "sequence": int(row["sequence"]),
                    "kind": str(row["kind"]),
                    "service_name": str(row["service_name"]),
                    "details": parse_json(str(row["details"])),
                }
                for row in connection.execute(
                    """
                    SELECT * FROM publication_audit
                    WHERE campaign_run_id = ? ORDER BY sequence
                    """,
                    (campaign_run_id,),
                ).fetchall()
            )
            return PublicationSnapshot(campaign_run_id, records, audit)

    def reset(self, campaign_run_id: str) -> None:
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM publication_audit WHERE campaign_run_id = ?",
                (campaign_run_id,),
            )
            connection.execute(
                "DELETE FROM publications WHERE campaign_run_id = ?",
                (campaign_run_id,),
            )

    def _authorize(self, service: TrustedServiceTransport) -> str:
        service_name = self._services.resolve(service)
        if service_name != "artifact_service":
            raise PermissionError("service cannot manage publications")
        return service_name

    @staticmethod
    def _stored_record(
        connection: sqlite3.Connection, publication_id: PublicationId
    ) -> PublicationRecord:
        row = connection.execute(
            "SELECT * FROM publications WHERE publication_id = ?",
            (publication_id.value,),
        ).fetchone()
        if row is None:
            raise KeyError("unknown publication")
        return SqlitePublicationRegistry._row_to_record(row)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> PublicationRecord:
        return PublicationRecord(
            publication_id=PublicationId(str(row["publication_id"])),
            publication_key=str(row["publication_key"]),
            campaign_run_id=str(row["campaign_run_id"]),
            owner_actor_id=str(row["owner_actor_id"]),
            artifact_ref=ArtifactRef(str(row["artifact_ref"])),
            audience=PublicationAudience(str(row["audience"])),
            status=PublicationStatus(str(row["status"])),
            entry_id=str(row["entry_id"]) if row["entry_id"] else None,
            abort_reason=(
                str(row["abort_reason"]) if row["abort_reason"] else None
            ),
        )

    @staticmethod
    def _append_audit(
        connection: sqlite3.Connection,
        campaign_run_id: str,
        kind: str,
        service_name: str,
        details: Mapping[str, object],
    ) -> None:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1 AS value
            FROM publication_audit WHERE campaign_run_id = ?
            """,
            (campaign_run_id,),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO publication_audit(
                campaign_run_id, sequence, kind, service_name, details
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                campaign_run_id,
                int(row["value"]),
                kind,
                service_name,
                canonical_json_bytes(details).decode("utf-8"),
            ),
        )

    def _initialize(self) -> None:
        with closing(self._connection()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS publications(
                    publication_id TEXT PRIMARY KEY,
                    publication_key TEXT NOT NULL,
                    campaign_run_id TEXT NOT NULL,
                    owner_actor_id TEXT NOT NULL,
                    artifact_ref TEXT NOT NULL,
                    audience TEXT NOT NULL,
                    status TEXT NOT NULL,
                    entry_id TEXT,
                    abort_reason TEXT,
                    UNIQUE(campaign_run_id, publication_key)
                );
                CREATE TABLE IF NOT EXISTS publication_audit(
                    campaign_run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    service_name TEXT NOT NULL,
                    details TEXT NOT NULL,
                    PRIMARY KEY(campaign_run_id, sequence)
                );
                """
            )
            connection.commit()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    class _Transaction:
        def __init__(self, registry: "SqlitePublicationRegistry") -> None:
            self._registry = registry
            self._connection: sqlite3.Connection | None = None

        def __enter__(self) -> sqlite3.Connection:
            self._registry._lock.acquire()
            self._connection = self._registry._connection()
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

    def _transaction(self) -> "SqlitePublicationRegistry._Transaction":
        return self._Transaction(self)
