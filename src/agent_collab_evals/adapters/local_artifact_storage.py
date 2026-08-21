"""Owner-only immutable artifact storage with trusted one-use reads."""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from ..artifacts import (
    ArtifactReadAuthorization,
    ArtifactRecord,
    ArtifactRef,
    TrustedServiceTransport,
)
from ..collaboration import SessionTransport
from ..service_identity import ServiceIdentityRegistry
from ..session_identity import SessionIdentityRegistry


class LocalArtifactStorage:
    """Stores opaque immutable blobs outside actor workspaces."""

    def __init__(
        self,
        root: Path,
        sessions: SessionIdentityRegistry,
        services: ServiceIdentityRegistry,
    ) -> None:
        self._root = root
        self._blob_root = root / "blobs"
        self._database_path = root / "artifacts.sqlite3"
        self._sessions = sessions
        self._services = services
        self._lock = threading.RLock()
        self._authorizations: dict[
            int, tuple[object, str, ArtifactRef, str]
        ] = {}
        self._blob_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def put(
        self,
        session: SessionTransport,
        content: bytes,
        media_type: str = "application/octet-stream",
    ) -> ArtifactRecord:
        context = self._sessions.resolve(session)
        if not content:
            raise ValueError("artifact content must not be empty")
        if not media_type or len(media_type) > 255:
            raise ValueError("media_type must contain 1 to 255 characters")
        ref = ArtifactRef(f"artifact-{secrets.token_hex(16)}")
        digest = hashlib.sha256(content).hexdigest()
        destination = self._blob_path(ref)
        try:
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            with self._transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO artifacts(
                        artifact_ref, campaign_run_id, owner_actor_id,
                        digest, media_type, size_bytes
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ref.value,
                        context.campaign_run_id,
                        context.actor_id,
                        digest,
                        media_type,
                        len(content),
                    ),
                )
                self._append_audit(
                    connection,
                    context.campaign_run_id,
                    context.actor_id,
                    "artifact.stored",
                    ref,
                    None,
                )
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return ArtifactRecord(
            ref, context.campaign_run_id, context.actor_id, digest, media_type, len(content)
        )

    def describe_owned(
        self, session: SessionTransport, ref: ArtifactRef
    ) -> ArtifactRecord:
        context = self._sessions.resolve(session)
        with self._transaction() as connection:
            record = self._stored_record(connection, ref)
            self._require_owner(record, context.campaign_run_id, context.actor_id)
            return record

    def read_owned(self, session: SessionTransport, ref: ArtifactRef) -> bytes:
        context = self._sessions.resolve(session)
        with self._transaction() as connection:
            record = self._stored_record(connection, ref)
            self._require_owner(record, context.campaign_run_id, context.actor_id)
            content = self._verified_content(record)
            self._append_audit(
                connection,
                record.campaign_run_id,
                record.owner_actor_id,
                "artifact.owner_read",
                ref,
                None,
            )
            return content

    def authorize_read(
        self,
        service: TrustedServiceTransport,
        campaign_run_id: str,
        ref: ArtifactRef,
        purpose: str,
    ) -> ArtifactReadAuthorization:
        service_name = self._services.resolve(service)
        if not purpose:
            raise ValueError("read purpose is required")
        with self._transaction() as connection:
            record = self._stored_record(connection, ref)
            if record.campaign_run_id != campaign_run_id:
                raise PermissionError("artifact belongs to a different campaign")
        identity = object()
        authorization = ArtifactReadAuthorization(identity)
        with self._lock:
            self._authorizations[id(identity)] = (
                identity,
                service_name,
                ref,
                purpose,
            )
        return authorization

    def trusted_read(
        self,
        service: TrustedServiceTransport,
        authorization: ArtifactReadAuthorization,
        purpose: str,
    ) -> tuple[ArtifactRecord, bytes]:
        service_name = self._services.resolve(service)
        with self._lock:
            binding = self._authorizations.pop(
                id(authorization._identity), None
            )
        if binding is None or binding[0] is not authorization._identity:
            raise PermissionError("unknown or consumed artifact authorization")
        _, authorized_service, ref, authorized_purpose = binding
        if authorized_service != service_name or authorized_purpose != purpose:
            raise PermissionError("artifact authorization does not match this read")
        with self._transaction() as connection:
            record = self._stored_record(connection, ref)
            content = self._verified_content(record)
            self._append_audit(
                connection,
                record.campaign_run_id,
                record.owner_actor_id,
                "artifact.trusted_read",
                ref,
                purpose,
            )
            return record, content

    @staticmethod
    def _require_owner(
        record: ArtifactRecord, campaign_run_id: str, actor_id: str
    ) -> None:
        if (
            record.campaign_run_id != campaign_run_id
            or record.owner_actor_id != actor_id
        ):
            raise PermissionError("artifact is owned by another actor or campaign")

    def _verified_content(self, record: ArtifactRecord) -> bytes:
        try:
            content = self._blob_path(record.ref).read_bytes()
        except FileNotFoundError as error:
            raise RuntimeError("stored artifact bytes are missing") from error
        if len(content) != record.size_bytes:
            raise RuntimeError("stored artifact size does not match metadata")
        if hashlib.sha256(content).hexdigest() != record.digest:
            raise RuntimeError("stored artifact digest does not match metadata")
        return content

    def _blob_path(self, ref: ArtifactRef) -> Path:
        if len(ref.value) != 41 or not ref.value.startswith("artifact-") or any(
            character not in "0123456789abcdef" for character in ref.value[9:]
        ):
            raise ValueError("invalid artifact reference")
        return self._blob_root / ref.value

    @staticmethod
    def _stored_record(
        connection: sqlite3.Connection, ref: ArtifactRef
    ) -> ArtifactRecord:
        row = connection.execute(
            "SELECT * FROM artifacts WHERE artifact_ref = ?", (ref.value,)
        ).fetchone()
        if row is None:
            raise KeyError("unknown artifact")
        return ArtifactRecord(
            ref=ArtifactRef(str(row["artifact_ref"])),
            campaign_run_id=str(row["campaign_run_id"]),
            owner_actor_id=str(row["owner_actor_id"]),
            digest=str(row["digest"]),
            media_type=str(row["media_type"]),
            size_bytes=int(row["size_bytes"]),
        )

    @staticmethod
    def _append_audit(
        connection: sqlite3.Connection,
        campaign_run_id: str,
        actor_id: str,
        kind: str,
        ref: ArtifactRef,
        purpose: str | None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO artifact_audit(
                campaign_run_id, actor_id, kind, artifact_ref, purpose
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (campaign_run_id, actor_id, kind, ref.value, purpose),
        )

    def _initialize(self) -> None:
        with closing(self._connection()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS artifacts(
                    artifact_ref TEXT PRIMARY KEY,
                    campaign_run_id TEXT NOT NULL,
                    owner_actor_id TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifact_audit(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_run_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    artifact_ref TEXT NOT NULL,
                    purpose TEXT
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
        def __init__(self, storage: "LocalArtifactStorage") -> None:
            self._storage = storage
            self._connection: sqlite3.Connection | None = None

        def __enter__(self) -> sqlite3.Connection:
            self._storage._lock.acquire()
            self._connection = self._storage._connection()
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
                self._storage._lock.release()

    def _transaction(self) -> "LocalArtifactStorage._Transaction":
        return self._Transaction(self)
