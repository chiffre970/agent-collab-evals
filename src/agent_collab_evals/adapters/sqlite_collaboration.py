"""Small durable collaboration backend with server-derived session identity."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..canonical import canonical_json_bytes, digest_value, parse_json
from ..collaboration import (
    CollaborationEntry,
    CollaborationScope,
    CollaborationSnapshot,
    CollaborationVisibility,
    Notification,
    Page,
    SessionContext,
    SessionTransport,
)
from ..session_identity import SessionIdentityRegistry


class _AuditedAuthorizationError(PermissionError):
    """Marks a denial whose audit event must commit before propagating."""


class SqliteCollaborationBackend:
    """Implements the actor-private/shared twin modes without coordination policy."""

    def __init__(
        self, database_path: Path, identities: SessionIdentityRegistry
    ) -> None:
        self._database_path = database_path
        self._identities = identities
        self._lock = threading.RLock()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def provision(
        self, campaign_run_id: str, visibility: CollaborationVisibility
    ) -> CollaborationScope:
        if not campaign_run_id:
            raise ValueError("campaign_run_id is required")
        if visibility is CollaborationVisibility.NONE:
            raise ValueError("a collaboration scope cannot use none visibility")
        scope_id = "collab:" + hashlib.sha256(
            campaign_run_id.encode("utf-8")
        ).hexdigest()[:24]
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT campaign_run_id, visibility FROM scopes WHERE scope_id = ?",
                (scope_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["campaign_run_id"] != campaign_run_id
                    or existing["visibility"] != visibility.value
                ):
                    raise ValueError("collaboration scope provision mismatch")
                return CollaborationScope(scope_id, campaign_run_id, visibility)
            connection.execute(
                "INSERT INTO scopes(scope_id, campaign_run_id, visibility) VALUES (?, ?, ?)",
                (scope_id, campaign_run_id, visibility.value),
            )
            self._append_audit(
                connection,
                scope_id,
                "scope.provisioned",
                None,
                {"visibility": visibility.value},
            )
        return CollaborationScope(scope_id, campaign_run_id, visibility)

    def publish(
        self,
        scope: CollaborationScope,
        session: SessionTransport,
        idempotency_key: str,
        body: str,
        reply_to: str | None = None,
        publication_ids: tuple[str, ...] = (),
    ) -> CollaborationEntry:
        context = self._identities.resolve(session)
        self._validate_publish(idempotency_key, body, publication_ids)
        request = {
            "body": body,
            "reply_to": reply_to,
            "publication_ids": list(publication_ids),
        }
        request_digest = digest_value(request)
        with self._transaction() as connection:
            visibility = self._authorize(connection, scope, context)
            existing = connection.execute(
                """
                SELECT entry_id, request_digest
                FROM entries
                WHERE scope_id = ? AND actor_id = ? AND idempotency_key = ?
                """,
                (scope.scope_id, context.actor_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["request_digest"] != request_digest:
                    raise ValueError(
                        "idempotency key reused with different collaboration content"
                    )
                return self._entry_by_id(
                    connection,
                    scope.scope_id,
                    str(existing["entry_id"]),
                    visibility,
                )

            thread_root: str | None = None
            if reply_to is not None:
                parent = self._visible_entry(
                    connection, scope.scope_id, context, visibility, reply_to
                )
                thread_root = parent.thread_root
            sequence = self._next_sequence(connection, "entries", scope.scope_id)
            actor_sequence = self._next_actor_sequence(
                connection, scope.scope_id, context.actor_id
            )
            entry_id = f"entry-{secrets.token_hex(16)}"
            thread_root = thread_root or entry_id
            connection.execute(
                """
                INSERT INTO entries(
                    scope_id, sequence, actor_sequence, entry_id, actor_id, body, reply_to,
                    thread_root, publication_ids, idempotency_key, request_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope.scope_id,
                    sequence,
                    actor_sequence,
                    entry_id,
                    context.actor_id,
                    body,
                    reply_to,
                    thread_root,
                    canonical_json_bytes(list(publication_ids)).decode("utf-8"),
                    idempotency_key,
                    request_digest,
                ),
            )
            self._append_audit(
                connection,
                scope.scope_id,
                "entry.published",
                context.actor_id,
                {
                    "entry_id": entry_id,
                    "reply_to": reply_to,
                    "publication_ids": list(publication_ids),
                },
            )
            return self._entry_by_id(
                connection, scope.scope_id, entry_id, visibility
            )

    def list_recent(
        self,
        scope: CollaborationScope,
        session: SessionTransport,
        cursor: str | None = None,
        limit: int = 50,
    ) -> Page[CollaborationEntry]:
        return self._entry_page(scope, session, "recent", None, cursor, limit)

    def search(
        self,
        scope: CollaborationScope,
        session: SessionTransport,
        query: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> Page[CollaborationEntry]:
        normalized = " ".join(query.casefold().split())
        if not normalized:
            raise ValueError("search query is required")
        if len(normalized) > 512:
            raise ValueError("search query exceeds 512 characters")
        return self._entry_page(
            scope, session, "search", normalized, cursor, limit
        )

    def get_thread(
        self,
        scope: CollaborationScope,
        session: SessionTransport,
        entry_id: str,
    ) -> tuple[CollaborationEntry, ...]:
        context = self._identities.resolve(session)
        with self._transaction() as connection:
            visibility = self._authorize(connection, scope, context)
            entry = self._visible_entry(
                connection, scope.scope_id, context, visibility, entry_id
            )
            clause, parameters = self._visibility_clause(context, visibility)
            rows = connection.execute(
                f"""
                SELECT * FROM entries
                WHERE scope_id = ? AND thread_root = ? {clause}
                ORDER BY sequence ASC
                """,
                (scope.scope_id, entry.thread_root, *parameters),
            ).fetchall()
            result = tuple(
                self._row_to_entry(row, visibility) for row in rows
            )
            self._append_read_audit(
                connection, scope.scope_id, context.actor_id, "thread", result
            )
            return result

    def notifications(
        self,
        scope: CollaborationScope,
        session: SessionTransport,
        cursor: str | None = None,
        limit: int = 50,
    ) -> Page[Notification]:
        self._validate_limit(limit)
        context = self._identities.resolve(session)
        operation = "notifications"
        with self._transaction() as connection:
            visibility = self._authorize(connection, scope, context)
            after = self._cursor_after(
                connection, cursor, scope, context, operation, ""
            )
            if visibility is CollaborationVisibility.ACTOR_PRIVATE:
                rows: list[sqlite3.Row] = []
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM entries
                    WHERE scope_id = ? AND sequence > ? AND actor_id != ?
                    ORDER BY sequence ASC LIMIT ?
                    """,
                    (scope.scope_id, after, context.actor_id, limit + 1),
                ).fetchall()
            selected = rows[:limit]
            items = tuple(
                Notification(
                    sequence=int(row["sequence"]),
                    entry_id=str(row["entry_id"]),
                    actor_id=str(row["actor_id"]),
                    kind="peer_entry",
                )
                for row in selected
            )
            if len(rows) > limit:
                watermark = int(selected[-1]["sequence"])
            else:
                sequence_column = self._sequence_column(visibility)
                actor_clause = (
                    "AND actor_id = ?"
                    if visibility is CollaborationVisibility.ACTOR_PRIVATE
                    else ""
                )
                actor_parameters: tuple[object, ...] = (
                    (context.actor_id,)
                    if visibility is CollaborationVisibility.ACTOR_PRIVATE
                    else ()
                )
                watermark_row = connection.execute(
                    f"""
                    SELECT COALESCE(MAX({sequence_column}), 0) AS value
                    FROM entries WHERE scope_id = ? {actor_clause}
                    """,
                    (scope.scope_id, *actor_parameters),
                ).fetchone()
                watermark = int(watermark_row["value"])
            next_cursor = self._encode_cursor(
                connection, scope, context, operation, "", watermark
            )
            self._append_audit(
                connection,
                scope.scope_id,
                "notifications.read",
                context.actor_id,
                {"entry_ids": [item.entry_id for item in items]},
            )
            return Page(items, next_cursor)

    def export(self, scope: CollaborationScope) -> CollaborationSnapshot:
        with self._transaction() as connection:
            stored = self._stored_scope(connection, scope.scope_id)
            if stored != scope:
                raise PermissionError("collaboration scope does not match storage")
            entries = tuple(
                self._row_to_entry(row)
                for row in connection.execute(
                    "SELECT * FROM entries WHERE scope_id = ? ORDER BY sequence",
                    (scope.scope_id,),
                ).fetchall()
            )
            audit = tuple(
                {
                    "sequence": int(row["sequence"]),
                    "kind": str(row["kind"]),
                    "actor_id": row["actor_id"],
                    "details": parse_json(str(row["details"])),
                }
                for row in connection.execute(
                    "SELECT * FROM audit WHERE scope_id = ? ORDER BY sequence",
                    (scope.scope_id,),
                ).fetchall()
            )
            return CollaborationSnapshot(scope, entries, audit)

    def reset(self, scope: CollaborationScope) -> None:
        with self._transaction() as connection:
            stored = self._stored_scope(connection, scope.scope_id)
            if stored != scope:
                raise PermissionError("collaboration scope does not match storage")
            connection.execute("DELETE FROM audit WHERE scope_id = ?", (scope.scope_id,))
            connection.execute("DELETE FROM entries WHERE scope_id = ?", (scope.scope_id,))
            connection.execute("DELETE FROM scopes WHERE scope_id = ?", (scope.scope_id,))

    def _entry_page(
        self,
        scope: CollaborationScope,
        session: SessionTransport,
        operation: str,
        search_query: str | None,
        cursor: str | None,
        limit: int,
    ) -> Page[CollaborationEntry]:
        self._validate_limit(limit)
        context = self._identities.resolve(session)
        query_binding = digest_value(search_query or "")
        with self._transaction() as connection:
            visibility = self._authorize(connection, scope, context)
            after = self._cursor_after(
                connection,
                cursor,
                scope,
                context,
                operation,
                query_binding,
            )
            clause, parameters = self._visibility_clause(context, visibility)
            sequence_column = self._sequence_column(visibility)
            search_clause = ""
            search_parameters: tuple[object, ...] = ()
            if search_query is not None:
                escaped = (
                    search_query.replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                search_clause = "AND lower(body) LIKE ? ESCAPE '\\'"
                search_parameters = (f"%{escaped}%",)
            rows = connection.execute(
                f"""
                SELECT * FROM entries
                WHERE scope_id = ? AND {sequence_column} > ? {clause} {search_clause}
                ORDER BY {sequence_column} ASC LIMIT ?
                """,
                (
                    scope.scope_id,
                    after,
                    *parameters,
                    *search_parameters,
                    limit + 1,
                ),
            ).fetchall()
            selected = rows[:limit]
            items = tuple(
                self._row_to_entry(row, visibility) for row in selected
            )
            next_cursor = self._next_cursor(
                connection,
                scope,
                context,
                operation,
                query_binding,
                selected,
                len(rows) > limit,
                sequence_column,
            )
            self._append_read_audit(
                connection, scope.scope_id, context.actor_id, operation, items
            )
            return Page(items, next_cursor)

    def _authorize(
        self,
        connection: sqlite3.Connection,
        scope: CollaborationScope,
        context: SessionContext,
    ) -> CollaborationVisibility:
        stored = self._stored_scope(connection, scope.scope_id)
        if stored != scope:
            self._append_audit(
                connection,
                stored.scope_id,
                "authorization.denied",
                context.actor_id,
                {"reason": "scope_mismatch"},
            )
            raise _AuditedAuthorizationError(
                "collaboration scope does not match storage"
            )
        if context.campaign_run_id != scope.campaign_run_id:
            self._append_audit(
                connection,
                stored.scope_id,
                "authorization.denied",
                context.actor_id,
                {"reason": "cross_campaign_session"},
            )
            raise _AuditedAuthorizationError(
                "session belongs to a different campaign"
            )
        return stored.visibility

    @staticmethod
    def _visibility_clause(
        context: SessionContext, visibility: CollaborationVisibility
    ) -> tuple[str, tuple[object, ...]]:
        if visibility is CollaborationVisibility.ACTOR_PRIVATE:
            return "AND actor_id = ?", (context.actor_id,)
        if visibility is CollaborationVisibility.ORGANISATION_SHARED:
            return "", ()
        raise PermissionError("collaboration tool is unavailable")

    def _visible_entry(
        self,
        connection: sqlite3.Connection,
        scope_id: str,
        context: SessionContext,
        visibility: CollaborationVisibility,
        entry_id: str,
    ) -> CollaborationEntry:
        clause, parameters = self._visibility_clause(context, visibility)
        row = connection.execute(
            f"SELECT * FROM entries WHERE scope_id = ? AND entry_id = ? {clause}",
            (scope_id, entry_id, *parameters),
        ).fetchone()
        if row is None:
            self._append_audit(
                connection,
                scope_id,
                "authorization.denied",
                context.actor_id,
                {"reason": "entry_not_visible", "entry_id": entry_id},
            )
            raise _AuditedAuthorizationError(
                "collaboration entry is not visible"
            )
        return self._row_to_entry(row, visibility)

    @staticmethod
    def _row_to_entry(
        row: sqlite3.Row,
        visibility: CollaborationVisibility | None = None,
    ) -> CollaborationEntry:
        publication_ids = parse_json(str(row["publication_ids"]))
        if not isinstance(publication_ids, list):
            raise RuntimeError("stored publication identifiers are invalid")
        return CollaborationEntry(
            entry_id=str(row["entry_id"]),
            sequence=int(
                row["actor_sequence"]
                if visibility is CollaborationVisibility.ACTOR_PRIVATE
                else row["sequence"]
            ),
            actor_id=str(row["actor_id"]),
            body=str(row["body"]),
            reply_to=str(row["reply_to"]) if row["reply_to"] is not None else None,
            thread_root=str(row["thread_root"]),
            publication_ids=tuple(str(item) for item in publication_ids),
        )

    def _entry_by_id(
        self,
        connection: sqlite3.Connection,
        scope_id: str,
        entry_id: str,
        visibility: CollaborationVisibility,
    ) -> CollaborationEntry:
        row = connection.execute(
            "SELECT * FROM entries WHERE scope_id = ? AND entry_id = ?",
            (scope_id, entry_id),
        ).fetchone()
        if row is None:
            raise RuntimeError("stored collaboration entry disappeared")
        return self._row_to_entry(row, visibility)

    def _cursor_after(
        self,
        connection: sqlite3.Connection,
        cursor: str | None,
        scope: CollaborationScope,
        context: SessionContext,
        operation: str,
        query_binding: str,
    ) -> int:
        if cursor is None:
            return 0
        try:
            encoded_payload, encoded_signature = cursor.split(".", 1)
            payload_bytes = base64.urlsafe_b64decode(
                encoded_payload + "=" * (-len(encoded_payload) % 4)
            )
            signature = base64.urlsafe_b64decode(
                encoded_signature + "=" * (-len(encoded_signature) % 4)
            )
            expected = hmac.digest(
                self._cursor_key(connection), payload_bytes, "sha256"
            )
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            payload = parse_json(payload_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            self._append_audit(
                connection,
                scope.scope_id,
                "authorization.denied",
                context.actor_id,
                {"reason": "invalid_cursor", "operation": operation},
            )
            raise _AuditedAuthorizationError(
                "invalid collaboration cursor"
            ) from error
        expected_payload = {
            "scope_id": scope.scope_id,
            "actor_id": context.actor_id,
            "operation": operation,
            "query_binding": query_binding,
        }
        if not isinstance(payload, dict) or any(
            payload.get(key) != value for key, value in expected_payload.items()
        ):
            self._append_audit(
                connection,
                scope.scope_id,
                "authorization.denied",
                context.actor_id,
                {"reason": "cursor_view_mismatch", "operation": operation},
            )
            raise _AuditedAuthorizationError(
                "collaboration cursor belongs to another view"
            )
        after = payload.get("after")
        if type(after) is not int or after < 0:
            raise ValueError("invalid collaboration cursor position")
        return after

    def _next_cursor(
        self,
        connection: sqlite3.Connection,
        scope: CollaborationScope,
        context: SessionContext,
        operation: str,
        query_binding: str,
        rows: Iterable[sqlite3.Row],
        has_more: bool,
        sequence_column: str = "sequence",
    ) -> str | None:
        rows = tuple(rows)
        if not has_more or not rows:
            return None
        return self._encode_cursor(
            connection,
            scope,
            context,
            operation,
            query_binding,
            int(rows[-1][sequence_column]),
        )

    def _encode_cursor(
        self,
        connection: sqlite3.Connection,
        scope: CollaborationScope,
        context: SessionContext,
        operation: str,
        query_binding: str,
        after: int,
    ) -> str:
        payload = canonical_json_bytes(
            {
                "scope_id": scope.scope_id,
                "actor_id": context.actor_id,
                "operation": operation,
                "query_binding": query_binding,
                "after": after,
            }
        )
        signature = hmac.digest(self._cursor_key(connection), payload, "sha256")
        return (
            base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
            + "."
            + base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
        )

    @staticmethod
    def _validate_publish(
        idempotency_key: str, body: str, publication_ids: tuple[str, ...]
    ) -> None:
        if not idempotency_key or len(idempotency_key) > 256:
            raise ValueError("idempotency key must contain 1 to 256 characters")
        if not body or len(body) > 32_768:
            raise ValueError("entry body must contain 1 to 32768 characters")
        if len(publication_ids) > 16 or any(not item for item in publication_ids):
            raise ValueError("publication identifiers are invalid")
        if len(set(publication_ids)) != len(publication_ids):
            raise ValueError("publication identifiers must be unique")

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("page limit must be an integer from 1 to 100")

    @staticmethod
    def _next_sequence(
        connection: sqlite3.Connection, table: str, scope_id: str
    ) -> int:
        if table not in {"entries", "audit"}:
            raise ValueError("unsupported sequence table")
        row = connection.execute(
            f"SELECT COALESCE(MAX(sequence), 0) + 1 AS value FROM {table} WHERE scope_id = ?",
            (scope_id,),
        ).fetchone()
        return int(row["value"])

    @staticmethod
    def _next_actor_sequence(
        connection: sqlite3.Connection, scope_id: str, actor_id: str
    ) -> int:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(actor_sequence), 0) + 1 AS value
            FROM entries WHERE scope_id = ? AND actor_id = ?
            """,
            (scope_id, actor_id),
        ).fetchone()
        return int(row["value"])

    @staticmethod
    def _sequence_column(visibility: CollaborationVisibility) -> str:
        if visibility is CollaborationVisibility.ACTOR_PRIVATE:
            return "actor_sequence"
        if visibility is CollaborationVisibility.ORGANISATION_SHARED:
            return "sequence"
        raise PermissionError("collaboration tool is unavailable")

    def _append_audit(
        self,
        connection: sqlite3.Connection,
        scope_id: str,
        kind: str,
        actor_id: str | None,
        details: Mapping[str, object],
    ) -> None:
        sequence = self._next_sequence(connection, "audit", scope_id)
        connection.execute(
            "INSERT INTO audit(scope_id, sequence, kind, actor_id, details) VALUES (?, ?, ?, ?, ?)",
            (
                scope_id,
                sequence,
                kind,
                actor_id,
                canonical_json_bytes(details).decode("utf-8"),
            ),
        )

    def _append_read_audit(
        self,
        connection: sqlite3.Connection,
        scope_id: str,
        actor_id: str,
        operation: str,
        entries: Iterable[CollaborationEntry],
    ) -> None:
        self._append_audit(
            connection,
            scope_id,
            f"{operation}.read",
            actor_id,
            {"entry_ids": [entry.entry_id for entry in entries]},
        )

    @staticmethod
    def _stored_scope(
        connection: sqlite3.Connection, scope_id: str
    ) -> CollaborationScope:
        row = connection.execute(
            "SELECT * FROM scopes WHERE scope_id = ?", (scope_id,)
        ).fetchone()
        if row is None:
            raise KeyError("unknown collaboration scope")
        return CollaborationScope(
            scope_id=str(row["scope_id"]),
            campaign_run_id=str(row["campaign_run_id"]),
            visibility=CollaborationVisibility(str(row["visibility"])),
        )

    def _cursor_key(self, connection: sqlite3.Connection) -> bytes:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'cursor_hmac_key'"
        ).fetchone()
        if row is None:
            raise RuntimeError("cursor signing key is absent")
        return bytes.fromhex(str(row["value"]))

    def _initialize(self) -> None:
        with closing(self._connection()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scopes(
                    scope_id TEXT PRIMARY KEY,
                    campaign_run_id TEXT NOT NULL UNIQUE,
                    visibility TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS entries(
                    scope_id TEXT NOT NULL REFERENCES scopes(scope_id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    actor_sequence INTEGER NOT NULL,
                    entry_id TEXT NOT NULL UNIQUE,
                    actor_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    reply_to TEXT,
                    thread_root TEXT NOT NULL,
                    publication_ids TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    PRIMARY KEY(scope_id, sequence),
                    UNIQUE(scope_id, actor_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS entries_thread
                    ON entries(scope_id, thread_root, sequence);
                CREATE INDEX IF NOT EXISTS entries_actor
                    ON entries(scope_id, actor_id, sequence);
                CREATE TABLE IF NOT EXISTS audit(
                    scope_id TEXT NOT NULL REFERENCES scopes(scope_id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    actor_id TEXT,
                    details TEXT NOT NULL,
                    PRIMARY KEY(scope_id, sequence)
                );
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(entries)")
            }
            if "actor_sequence" not in columns:
                connection.execute(
                    "ALTER TABLE entries ADD COLUMN actor_sequence INTEGER"
                )
            counters: dict[tuple[str, str], int] = {}
            for row in connection.execute(
                """
                SELECT scope_id, actor_id, sequence, actor_sequence
                FROM entries ORDER BY scope_id, actor_id, sequence
                """
            ):
                key = (str(row["scope_id"]), str(row["actor_id"]))
                counters[key] = counters.get(key, 0) + 1
                if row["actor_sequence"] is None:
                    connection.execute(
                        """
                        UPDATE entries SET actor_sequence = ?
                        WHERE scope_id = ? AND sequence = ?
                        """,
                        (counters[key], key[0], int(row["sequence"])),
                    )
                elif int(row["actor_sequence"]) != counters[key]:
                    raise RuntimeError(
                        "stored actor-local entry sequence is inconsistent"
                    )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS entries_actor_sequence
                ON entries(scope_id, actor_id, actor_sequence)
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES ('cursor_hmac_key', ?)",
                (secrets.token_hex(32),),
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
        def __init__(self, backend: "SqliteCollaborationBackend") -> None:
            self._backend = backend
            self._connection: sqlite3.Connection | None = None

        def __enter__(self) -> sqlite3.Connection:
            self._backend._lock.acquire()
            self._connection = self._backend._connection()
            self._connection.execute("BEGIN IMMEDIATE")
            return self._connection

        def __exit__(self, exception_type, exception, traceback) -> None:
            assert self._connection is not None
            try:
                if exception_type is None or (
                    exception_type is not None
                    and issubclass(exception_type, _AuditedAuthorizationError)
                ):
                    self._connection.commit()
                else:
                    self._connection.rollback()
            finally:
                self._connection.close()
                self._backend._lock.release()

    def _transaction(self) -> "SqliteCollaborationBackend._Transaction":
        return self._Transaction(self)
