"""Owner-only immutable artifact storage with trusted one-use reads."""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import threading
from contextlib import closing, suppress
from pathlib import Path
from typing import AbstractSet, Mapping

from ..artifacts import (
    ArtifactReadAuthorization,
    ArtifactRecord,
    ArtifactRef,
    ArtifactStoragePolicy,
    StorageSeal,
    TrustedServiceTransport,
)
from ..canonical import canonical_json_bytes, digest_value
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
        policy: ArtifactStoragePolicy,
        trusted_read_policies: Mapping[str, AbstractSet[str]],
    ) -> None:
        self._root = root
        self._blob_root = root / "blobs"
        self._database_path = root / "artifacts.sqlite3"
        self._sessions = sessions
        self._services = services
        self._policy = policy
        self._trusted_read_policies = {
            service_name: frozenset(purposes)
            for service_name, purposes in trusted_read_policies.items()
        }
        self._lock = threading.RLock()
        self._authorizations: dict[
            int, tuple[object, object, ArtifactRef, str]
        ] = {}
        self._blob_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def open_campaign(
        self, campaign_run_id: str, actor_ids: tuple[str, ...]
    ) -> None:
        if not campaign_run_id:
            raise ValueError("campaign_run_id is required")
        if not actor_ids or any(not actor_id for actor_id in actor_ids):
            raise ValueError("campaign actor roster must not be empty")
        if len(set(actor_ids)) != len(actor_ids):
            raise ValueError("campaign actor roster must be unique")
        if (
            self._policy.max_actor_bytes * len(actor_ids)
            > self._policy.max_campaign_bytes
        ):
            raise ValueError(
                "campaign storage quota must cover every registered actor allocation"
            )
        requested = tuple(sorted(actor_ids))
        with self._transaction() as connection:
            policy_values = (
                self._policy.max_artifact_bytes,
                self._policy.max_actor_bytes,
                self._policy.max_campaign_bytes,
            )
            stored_policy = connection.execute(
                """
                SELECT max_artifact_bytes, max_actor_bytes, max_campaign_bytes
                FROM storage_campaigns WHERE campaign_run_id = ?
                """,
                (campaign_run_id,),
            ).fetchone()
            existing = tuple(
                str(row["actor_id"])
                for row in connection.execute(
                    """
                    SELECT actor_id FROM storage_campaign_actors
                    WHERE campaign_run_id = ? ORDER BY actor_id
                    """,
                    (campaign_run_id,),
                )
            )
            if stored_policy is not None:
                stored_values = (
                    int(stored_policy["max_artifact_bytes"]),
                    int(stored_policy["max_actor_bytes"]),
                    int(stored_policy["max_campaign_bytes"]),
                )
                if stored_values != policy_values:
                    raise ValueError("campaign artifact storage policy changed")
                if existing != requested:
                    raise ValueError("campaign artifact actor roster changed")
                self._validate_registered_usage(
                    connection, campaign_run_id, frozenset(requested)
                )
                return
            if existing:
                raise RuntimeError("campaign artifact roster has no policy record")
            legacy_artifacts = connection.execute(
                """
                SELECT COUNT(*) AS value FROM artifacts
                WHERE campaign_run_id = ?
                """,
                (campaign_run_id,),
            ).fetchone()
            if int(legacy_artifacts["value"]) != 0:
                raise RuntimeError(
                    "pre-roster campaign artifacts require explicit migration"
                )
            connection.execute(
                """
                INSERT INTO storage_campaigns(
                    campaign_run_id, max_artifact_bytes,
                    max_actor_bytes, max_campaign_bytes
                ) VALUES (?, ?, ?, ?)
                """,
                (campaign_run_id, *policy_values),
            )
            connection.executemany(
                """
                INSERT INTO storage_campaign_actors(campaign_run_id, actor_id)
                VALUES (?, ?)
                """,
                ((campaign_run_id, actor_id) for actor_id in requested),
            )

    def put(
        self,
        session: SessionTransport,
        content: bytes,
        media_type: str = "application/octet-stream",
        *,
        idempotency_key: str | None = None,
    ) -> ArtifactRecord:
        context = self._sessions.resolve(session)
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 256
        ):
            raise ValueError("artifact idempotency key is invalid")
        if not content:
            raise ValueError("artifact content must not be empty")
        if len(content) > self._policy.max_artifact_bytes:
            raise ValueError("artifact exceeds the per-artifact storage limit")
        if not media_type or len(media_type) > 255:
            raise ValueError("media_type must contain 1 to 255 characters")
        ref = ArtifactRef(f"artifact-{secrets.token_hex(16)}")
        digest = hashlib.sha256(content).hexdigest()
        destination = self._blob_path(ref)
        created = False
        try:
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            created = True
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            self._sync_blob_directory()
            with self._transaction() as connection:
                if idempotency_key is not None:
                    previous = connection.execute(
                        "SELECT artifact_ref FROM artifact_write_keys "
                        "WHERE campaign_run_id=? AND actor_id=? AND idempotency_key=?",
                        (context.campaign_run_id, context.actor_id, idempotency_key),
                    ).fetchone()
                    if previous is not None:
                        record = self._stored_record(connection, ArtifactRef(previous["artifact_ref"]))
                        self._require_owner(record, context.campaign_run_id, context.actor_id)
                        if record.digest != digest or record.media_type != media_type or self._verified_content(record) != content:
                            raise ValueError("artifact idempotency key was reused differently")
                        # This attempt's unadmitted blob is not part of the store.
                        destination.unlink()
                        self._sync_blob_directory()
                        created = False
                        return record
                self._enforce_quotas(
                    connection,
                    context.campaign_run_id,
                    context.actor_id,
                    len(content),
                )
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
                if idempotency_key is not None:
                    connection.execute(
                        "INSERT INTO artifact_write_keys VALUES (?, ?, ?, ?)",
                        (context.campaign_run_id, context.actor_id, idempotency_key, ref.value),
                    )
        except Exception:
            if created:
                destination.unlink(missing_ok=True)
                with suppress(OSError):
                    self._sync_blob_directory()
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
        permitted_purposes = self._trusted_read_policies.get(service_name)
        if permitted_purposes is None or purpose not in permitted_purposes:
            raise PermissionError(
                "service is not permitted to read artifacts for this purpose"
            )
        with self._transaction() as connection:
            record = self._stored_record(connection, ref)
            if record.campaign_run_id != campaign_run_id:
                raise PermissionError("artifact belongs to a different campaign")
        identity = object()
        authorization = ArtifactReadAuthorization(identity)
        with self._lock:
            self._authorizations[id(identity)] = (
                identity,
                service._identity,
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
        self._services.resolve(service)
        with self._lock:
            binding = self._authorizations.pop(
                id(authorization._identity), None
            )
        if binding is None or binding[0] is not authorization._identity:
            raise PermissionError("unknown or consumed artifact authorization")
        _, authorized_service_identity, ref, authorized_purpose = binding
        if (
            authorized_service_identity is not service._identity
            or authorized_purpose != purpose
        ):
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

    def seal(
        self, campaign_run_id: str, final_manifest: Mapping[str, object]
    ) -> StorageSeal:
        if not campaign_run_id or not isinstance(final_manifest, Mapping):
            raise ValueError("campaign seal identity and manifest are required")
        manifest_bytes = canonical_json_bytes(final_manifest)
        final_manifest_digest = digest_value(final_manifest)
        with self._transaction() as connection:
            campaign = connection.execute(
                "SELECT * FROM storage_campaigns WHERE campaign_run_id = ?",
                (campaign_run_id,),
            ).fetchone()
            if campaign is None:
                raise KeyError("campaign storage is not registered")
            artifact_documents: list[dict[str, object]] = []
            for row in connection.execute(
                "SELECT * FROM artifacts WHERE campaign_run_id = ? "
                "ORDER BY artifact_ref",
                (campaign_run_id,),
            ):
                record = ArtifactRecord(
                    ref=ArtifactRef(str(row["artifact_ref"])),
                    campaign_run_id=str(row["campaign_run_id"]),
                    owner_actor_id=str(row["owner_actor_id"]),
                    digest=str(row["digest"]),
                    media_type=str(row["media_type"]),
                    size_bytes=int(row["size_bytes"]),
                )
                self._verified_content(record)
                artifact_documents.append(
                    {
                        "artifact_ref": record.ref.value,
                        "owner_actor_id": record.owner_actor_id,
                        "digest": record.digest,
                        "media_type": record.media_type,
                        "size_bytes": record.size_bytes,
                    }
                )
            artifacts = tuple(artifact_documents)
            roster = tuple(
                str(row["actor_id"])
                for row in connection.execute(
                    "SELECT actor_id FROM storage_campaign_actors "
                    "WHERE campaign_run_id = ? ORDER BY actor_id",
                    (campaign_run_id,),
                )
            )
            seal_document = {
                "schema_version": "artifact-storage-seal/v1",
                "campaign_run_id": campaign_run_id,
                "policy": {
                    "max_artifact_bytes": int(campaign["max_artifact_bytes"]),
                    "max_actor_bytes": int(campaign["max_actor_bytes"]),
                    "max_campaign_bytes": int(campaign["max_campaign_bytes"]),
                },
                "actor_roster": roster,
                "artifacts": artifacts,
                "final_manifest_digest": final_manifest_digest,
            }
            seal_digest = digest_value(seal_document)
            existing = connection.execute(
                "SELECT * FROM storage_seals WHERE campaign_run_id = ?",
                (campaign_run_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["final_manifest_json"])
                    != manifest_bytes.decode()
                    or str(existing["seal_digest"]) != seal_digest
                ):
                    raise ValueError("campaign storage was already sealed differently")
            else:
                connection.execute(
                    "INSERT INTO storage_seals("
                    "campaign_run_id, final_manifest_json, seal_digest) "
                    "VALUES (?, ?, ?)",
                    (campaign_run_id, manifest_bytes.decode(), seal_digest),
                )
        return StorageSeal(
            campaign_run_id=campaign_run_id,
            artifact_count=len(artifacts),
            total_bytes=sum(item["size_bytes"] for item in artifacts),
            final_manifest_digest=final_manifest_digest,
            seal_digest=seal_digest,
        )

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

    def _enforce_quotas(
        self,
        connection: sqlite3.Connection,
        campaign_run_id: str,
        actor_id: str,
        additional_bytes: int,
    ) -> None:
        stored_policy = connection.execute(
            """
            SELECT max_artifact_bytes, max_actor_bytes, max_campaign_bytes
            FROM storage_campaigns WHERE campaign_run_id = ?
            """,
            (campaign_run_id,),
        ).fetchone()
        if stored_policy is None:
            raise PermissionError("campaign storage is not registered")
        if connection.execute(
            "SELECT 1 FROM storage_seals WHERE campaign_run_id = ?",
            (campaign_run_id,),
        ).fetchone() is not None:
            raise RuntimeError("campaign artifact storage is sealed")
        if (
            int(stored_policy["max_artifact_bytes"]),
            int(stored_policy["max_actor_bytes"]),
            int(stored_policy["max_campaign_bytes"]),
        ) != (
            self._policy.max_artifact_bytes,
            self._policy.max_actor_bytes,
            self._policy.max_campaign_bytes,
        ):
            raise RuntimeError("campaign artifact storage policy changed")
        registered = connection.execute(
            """
            SELECT 1 FROM storage_campaign_actors
            WHERE campaign_run_id = ? AND actor_id = ?
            """,
            (campaign_run_id, actor_id),
        ).fetchone()
        if registered is None:
            raise PermissionError("actor is not registered for campaign storage")
        campaign_row = connection.execute(
            """
            SELECT COALESCE(SUM(size_bytes), 0) AS used
            FROM artifacts WHERE campaign_run_id = ?
            """,
            (campaign_run_id,),
        ).fetchone()
        actor_row = connection.execute(
            """
            SELECT COALESCE(SUM(size_bytes), 0) AS used
            FROM artifacts WHERE campaign_run_id = ? AND owner_actor_id = ?
            """,
            (campaign_run_id, actor_id),
        ).fetchone()
        if int(actor_row["used"]) + additional_bytes > self._policy.max_actor_bytes:
            raise ValueError("artifact exceeds the actor storage quota")
        if (
            int(campaign_row["used"]) + additional_bytes
            > self._policy.max_campaign_bytes
        ):
            raise ValueError("artifact exceeds the campaign storage quota")

    def _validate_registered_usage(
        self,
        connection: sqlite3.Connection,
        campaign_run_id: str,
        actor_ids: frozenset[str],
    ) -> None:
        total_bytes = 0
        rows = connection.execute(
            """
            SELECT owner_actor_id, SUM(size_bytes) AS used,
                   MAX(size_bytes) AS largest
            FROM artifacts WHERE campaign_run_id = ?
            GROUP BY owner_actor_id
            """,
            (campaign_run_id,),
        ).fetchall()
        for row in rows:
            owner_actor_id = str(row["owner_actor_id"])
            if owner_actor_id not in actor_ids:
                raise RuntimeError(
                    "campaign artifacts include an owner outside the registered roster"
                )
            used = int(row["used"])
            largest = int(row["largest"])
            if largest > self._policy.max_artifact_bytes:
                raise RuntimeError(
                    "stored artifact exceeds the registered artifact limit"
                )
            if used > self._policy.max_actor_bytes:
                raise RuntimeError(
                    "stored actor usage exceeds the registered actor allocation"
                )
            total_bytes += used
        if total_bytes > self._policy.max_campaign_bytes:
            raise RuntimeError(
                "stored campaign usage exceeds the registered campaign allocation"
            )

    def _sync_blob_directory(self) -> None:
        descriptor = os.open(self._blob_root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

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
                CREATE TABLE IF NOT EXISTS storage_campaigns(
                    campaign_run_id TEXT PRIMARY KEY,
                    max_artifact_bytes INTEGER NOT NULL,
                    max_actor_bytes INTEGER NOT NULL,
                    max_campaign_bytes INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifact_write_keys(
                    campaign_run_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    artifact_ref TEXT NOT NULL REFERENCES artifacts(artifact_ref),
                    PRIMARY KEY(campaign_run_id, actor_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS storage_campaign_actors(
                    campaign_run_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    PRIMARY KEY(campaign_run_id, actor_id),
                    FOREIGN KEY(campaign_run_id)
                        REFERENCES storage_campaigns(campaign_run_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS artifact_audit(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_run_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    artifact_ref TEXT NOT NULL,
                    purpose TEXT
                );
                CREATE TABLE IF NOT EXISTS storage_seals(
                    campaign_run_id TEXT PRIMARY KEY,
                    final_manifest_json TEXT NOT NULL,
                    seal_digest TEXT NOT NULL
                );
                """
            )
            connection.commit()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
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
