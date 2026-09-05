"""Durable admission accounting for stock-runtime child sessions.

This service does not itself intercept OpenCode. A registered runtime must
prove that every child creation passes through it before advertising enforcement.
"""

from __future__ import annotations

import secrets
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .canonical import digest_value
from .collaboration import SessionTransport
from .session_identity import SessionIdentityRegistry


@dataclass(frozen=True, slots=True)
class NativeFleetPlan:
    campaign_run_id: str
    primary_session_id: str
    max_identities: int

    def __post_init__(self) -> None:
        if not self.campaign_run_id or not self.primary_session_id:
            raise ValueError("native fleet identity is required")
        if type(self.max_identities) is not int or self.max_identities < 1:
            raise ValueError("native fleet size must be positive")

    @property
    def digest(self) -> str:
        return digest_value(self)


class SqliteNativeAdmission:
    """Reserve a lifetime child slot before stock task execution.

    An interrupted dispatch consumes its slot until explicitly reconciled.
    Completed child identities retain their slots and can be resumed. This
    bounds the durable fleet without assuming that completion deletes a child.
    """

    def __init__(self, database: Path, plan: NativeFleetPlan) -> None:
        self._database = database
        self.plan = plan
        database.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as connection:
            connection.executescript(
                "CREATE TABLE IF NOT EXISTS native_plan ("
                "singleton INTEGER PRIMARY KEY CHECK(singleton=1), digest TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS native_calls ("
                "call_id TEXT PRIMARY KEY, permit_id TEXT UNIQUE NOT NULL, "
                "slot_id TEXT NOT NULL, child_id TEXT, status TEXT NOT NULL "
                "CHECK(status IN ('active','complete')));"
            )
            connection.execute("INSERT OR IGNORE INTO native_plan VALUES (1, ?)", (plan.digest,))
            self._verify_plan(connection)

    def reserve(self, *, caller_session_id: str, call_id: str, task_id: str | None = None) -> str:
        if caller_session_id != self.plan.primary_session_id:
            raise PermissionError("only the registered primary can delegate")
        if not call_id or task_id == "":
            raise ValueError("native call identity is invalid")
        with self._transaction() as connection:
            self._verify_plan(connection)
            rows = connection.execute("SELECT * FROM native_calls").fetchall()
            if any(row["call_id"] == call_id for row in rows):
                # Retrying a stock creation could create another child. Do not
                # return an old permit as permission to execute again.
                raise RuntimeError("native dispatch already admitted; reconcile before retry")
            if task_id is None:
                if len({row["slot_id"] for row in rows}) >= self.plan.max_identities - 1:
                    raise PermissionError("native fleet identity allocation exhausted")
                slot_id = "slot-" + secrets.token_hex(16)
            else:
                known = [row for row in rows if row["child_id"] == task_id]
                if not known:
                    raise PermissionError("native child is not owned by this fleet")
                slot_id = known[0]["slot_id"]
                if any(row["slot_id"] == slot_id and row["status"] == "active" for row in rows):
                    raise PermissionError("native child already has an active call")
            permit_id = "native-" + secrets.token_hex(16)
            connection.execute(
                "INSERT INTO native_calls VALUES (?, ?, ?, ?, 'active')",
                (call_id, permit_id, slot_id, task_id),
            )
            return permit_id

    def complete(self, permit_id: str, *, child_session_id: str) -> None:
        if not child_session_id or child_session_id == self.plan.primary_session_id:
            raise ValueError("native child identity is invalid")
        with self._transaction() as connection:
            self._verify_plan(connection)
            row = connection.execute(
                "SELECT * FROM native_calls WHERE permit_id=?", (permit_id,)
            ).fetchone()
            if row is None:
                raise PermissionError("native permit is unknown")
            if row["child_id"] is not None and row["child_id"] != child_session_id:
                raise RuntimeError("native child binding changed")
            collision = connection.execute(
                "SELECT 1 FROM native_calls WHERE child_id=? AND slot_id<>?",
                (child_session_id, row["slot_id"]),
            ).fetchone()
            if collision is not None:
                raise RuntimeError("native child already belongs to another slot")
            connection.execute(
                "UPDATE native_calls SET child_id=?, status='complete' WHERE permit_id=?",
                (child_session_id, permit_id),
            )

    def reconcile(self, observed_child_ids: tuple[str, ...]) -> dict[str, object]:
        with closing(self._connect()) as connection:
            self._verify_plan(connection)
            rows = [dict(row) for row in connection.execute("SELECT * FROM native_calls ORDER BY call_id")]
        children = {row["child_id"] for row in rows if row["child_id"] is not None}
        unresolved = [row["permit_id"] for row in rows if row["status"] != "complete"]
        valid = (
            not unresolved
            and len(set(observed_child_ids)) == len(observed_child_ids)
            and children == set(observed_child_ids)
            and len({row["slot_id"] for row in rows}) < self.plan.max_identities
        )
        return {
            "valid": valid,
            "plan_digest": self.plan.digest,
            "child_ids": sorted(children),
            "unresolved_permits": unresolved,
            "calls_digest": digest_value(rows),
            "runtime_interception_qualified": False,
        }

    def _verify_plan(self, connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT digest FROM native_plan WHERE singleton=1").fetchone()
        if row is None or row["digest"] != self.plan.digest:
            raise RuntimeError("native fleet plan differs from supplied authority")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            with connection:
                yield connection


class NativeAdmissionTools:
    """Server-owned scope for a separately pinned stock-task admission hook."""

    def __init__(self, root: Path, sessions: SessionIdentityRegistry, campaign_run_id: str, max_identities: int):
        self._root = root
        self._sessions = sessions
        self._campaign_run_id = campaign_run_id
        self._max_identities = max_identities
        self.profile_digest = digest_value({
            "service": "native-admission-tools/v1", "max_identities": max_identities,
            "capacity": "durable_lifetime_child_slots", "recursive_delegation": False,
            "unknown_dispatch": "hold_slot_and_reject_close",
        })

    def _ledger(self, session_id: str) -> SqliteNativeAdmission:
        plan = NativeFleetPlan(self._campaign_run_id, session_id, self._max_identities)
        return SqliteNativeAdmission(self._root / "native.sqlite3", plan)

    def validate_scope(self, campaign_run_id: str, max_identities: int) -> None:
        if (campaign_run_id, max_identities) != (self._campaign_run_id, self._max_identities):
            raise ValueError("native admission scope differs from the organization")

    def call(self, session: SessionTransport, operation: str, arguments: dict) -> dict:
        context = self._sessions.resolve(session)
        if context.campaign_run_id != self._campaign_run_id:
            raise PermissionError("native admission belongs to another campaign")
        ledger = self._ledger(context.session_id)
        if operation == "reserve":
            if set(arguments) != {"session_id", "call_id", "task_id", "subagent_type"}:
                raise ValueError("native reservation fields differ")
            if arguments["session_id"] != context.session_id or arguments["subagent_type"] != "general":
                raise PermissionError("only the primary's general subagent is admitted")
            if not isinstance(arguments["call_id"], str) or (
                arguments["task_id"] is not None and not isinstance(arguments["task_id"], str)
            ):
                raise ValueError("native dispatch identity is invalid")
            return {"permit": ledger.reserve(
                caller_session_id=context.session_id, call_id=arguments["call_id"], task_id=arguments["task_id"]
            )}
        if operation == "complete":
            if set(arguments) != {"permit", "child_session_id"} or any(not isinstance(value, str) for value in arguments.values()):
                raise ValueError("native completion fields differ")
            ledger.complete(arguments["permit"], child_session_id=arguments["child_session_id"])
            return {"complete": True}
        raise PermissionError("native admission operation is not exposed")

    def reconcile(self, primary_session_id: str, child_ids: tuple[str, ...]) -> dict[str, object]:
        return self._ledger(primary_session_id).reconcile(child_ids)
