"""Durable actor-partitioned compute allowance enforcement."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Mapping

from ..artifacts import ArtifactRef, TrustedServiceTransport
from ..canonical import canonical_json_bytes, digest_value, parse_json
from ..collaboration import SessionTransport
from ..evaluation import (
    ComputePlan,
    ComputeSnapshot,
    EvaluationReservation,
    EvaluationReservationStatus,
    EvaluationScope,
)
from ..service_identity import ServiceIdentityRegistry
from ..session_identity import SessionIdentityRegistry


class SqliteComputeBroker:
    """Reserve fixed compute without exposing peer demand or queue state."""

    def __init__(
        self,
        database: Path,
        sessions: SessionIdentityRegistry,
        services: ServiceIdentityRegistry,
        plan: ComputePlan,
        *,
        hidden_evaluator_service: str,
    ) -> None:
        if not hidden_evaluator_service:
            raise ValueError("hidden evaluator service name is required")
        self._database = database
        self._sessions = sessions
        self._services = services
        self._plan = plan
        self._hidden_evaluator_service = hidden_evaluator_service
        self._lock = threading.RLock()
        database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._register_plan()

    def reserve_visible_evaluation(
        self,
        session: SessionTransport,
        reservation_key: str,
        artifact_ref: ArtifactRef,
        seconds: int,
    ) -> EvaluationReservation:
        context = self._sessions.resolve(session)
        if context.campaign_run_id != self._plan.campaign_run_id:
            raise PermissionError("session belongs to another compute campaign")
        if context.actor_id not in self._plan.actor_limits:
            raise PermissionError("actor has no compute allocation")
        return self._reserve(
            reservation_key,
            context.campaign_run_id,
            context.actor_id,
            artifact_ref,
            EvaluationScope.VISIBLE,
            seconds,
        )

    def reserve_hidden_evaluation(
        self,
        service: TrustedServiceTransport,
        reservation_key: str,
        campaign_run_id: str,
        artifact_ref: ArtifactRef,
        seconds: int,
    ) -> EvaluationReservation:
        if self._services.resolve(service) != self._hidden_evaluator_service:
            raise PermissionError("service cannot reserve hidden evaluation")
        if campaign_run_id != self._plan.campaign_run_id:
            raise PermissionError("hidden evaluation belongs to another campaign")
        return self._reserve(
            reservation_key,
            campaign_run_id,
            None,
            artifact_ref,
            EvaluationScope.HIDDEN,
            seconds,
        )

    def complete(
        self, reservation_id: str, used_seconds: int
    ) -> EvaluationReservation:
        if type(used_seconds) is not int or used_seconds < 0:
            raise ValueError("used compute seconds must be a nonnegative integer")
        with self._transaction() as connection:
            row = self._reservation_row(connection, reservation_id)
            status = EvaluationReservationStatus(str(row["status"]))
            if status is EvaluationReservationStatus.COMPLETE:
                if int(row["used_seconds"]) != used_seconds:
                    raise ValueError("compute completion differs from prior receipt")
                return self._reservation(row)
            if status is not EvaluationReservationStatus.RESERVED:
                raise RuntimeError("compute reservation is not active")
            if used_seconds > int(row["reserved_seconds"]):
                raise RuntimeError("compute use exceeded its reservation")
            connection.execute(
                "UPDATE compute_reservations SET status = 'complete', "
                "used_seconds = ? WHERE reservation_id = ?",
                (used_seconds, reservation_id),
            )
            self._audit(
                connection,
                str(row["campaign_run_id"]),
                row["actor_id"],
                "compute.completed",
                {"reservation_id": reservation_id, "used_seconds": used_seconds},
            )
            updated = self._reservation_row(connection, reservation_id)
            return self._reservation(updated)

    def fail(self, reservation_id: str, reason: str) -> None:
        self._terminate(reservation_id, EvaluationReservationStatus.FAILED, reason)

    def cancel(self, reservation_id: str, reason: str) -> None:
        self._terminate(reservation_id, EvaluationReservationStatus.CANCELLED, reason)

    def release_visible_results(
        self, campaign_run_id: str, actor_id: str
    ) -> None:
        if campaign_run_id != self._plan.campaign_run_id:
            raise PermissionError("release belongs to another compute campaign")
        if actor_id not in self._plan.actor_limits:
            raise PermissionError("actor has no compute allocation")
        with self._transaction() as connection:
            inserted = connection.execute(
                "INSERT OR IGNORE INTO compute_releases(campaign_run_id, actor_id) "
                "VALUES (?, ?)",
                (campaign_run_id, actor_id),
            )
            if inserted.rowcount == 1:
                self._audit(
                    connection,
                    campaign_run_id,
                    actor_id,
                    "compute.visible_results_released",
                    {},
                )

    def is_visible_result_released(
        self, campaign_run_id: str, actor_id: str
    ) -> bool:
        if campaign_run_id != self._plan.campaign_run_id:
            return False
        with closing(self._connect()) as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM compute_releases "
                    "WHERE campaign_run_id = ? AND actor_id = ?",
                    (campaign_run_id, actor_id),
                ).fetchone()
                is not None
            )

    def snapshot(self, campaign_run_id: str) -> ComputeSnapshot:
        if campaign_run_id != self._plan.campaign_run_id:
            raise PermissionError("snapshot belongs to another compute campaign")
        with closing(self._connect()) as connection:
            self._validate_plan_row(connection)
            rows = connection.execute(
                "SELECT * FROM compute_reservations WHERE campaign_run_id = ? "
                "ORDER BY reservation_id",
                (campaign_run_id,),
            ).fetchall()
            releases = tuple(
                str(row["actor_id"])
                for row in connection.execute(
                    "SELECT actor_id FROM compute_releases "
                    "WHERE campaign_run_id = ? ORDER BY actor_id",
                    (campaign_run_id,),
                )
            )
            audit = tuple(
                {
                    "sequence": int(row["sequence"]),
                    "actor_id": row["actor_id"],
                    "kind": str(row["kind"]),
                    "details": parse_json(str(row["details_json"])),
                }
                for row in connection.execute(
                    "SELECT sequence, actor_id, kind, details_json "
                    "FROM compute_audit WHERE campaign_run_id = ? ORDER BY sequence",
                    (campaign_run_id,),
                )
            )
        active_statuses = {
            EvaluationReservationStatus.RESERVED.value,
            EvaluationReservationStatus.COMPLETE.value,
            EvaluationReservationStatus.FAILED.value,
        }
        actor_reserved = {actor_id: 0 for actor_id in self._plan.actor_limits}
        actor_used = {actor_id: 0 for actor_id in self._plan.actor_limits}
        hidden_reserved = 0
        hidden_used = 0
        for row in rows:
            status = EvaluationReservationStatus(str(row["status"]))
            reserved_seconds = int(row["reserved_seconds"])
            used_seconds = row["used_seconds"]
            if reserved_seconds < 1:
                raise RuntimeError("stored compute reservation duration is invalid")
            if status is EvaluationReservationStatus.COMPLETE and (
                type(used_seconds) is not int
                or not 0 <= used_seconds <= reserved_seconds
            ):
                raise RuntimeError("stored compute completion usage is invalid")
            if status is EvaluationReservationStatus.FAILED and (
                used_seconds != reserved_seconds
            ):
                raise RuntimeError("stored failed compute usage is invalid")
            if status in {
                EvaluationReservationStatus.RESERVED,
                EvaluationReservationStatus.CANCELLED,
            } and used_seconds is not None:
                raise RuntimeError("inactive compute reservation has usage")
            scope = EvaluationScope(str(row["scope"]))
            if scope is EvaluationScope.VISIBLE:
                actor_id = str(row["actor_id"])
                if str(row["status"]) in active_statuses:
                    actor_reserved[actor_id] += int(row["reserved_seconds"])
                actor_used[actor_id] += self._charged_use(row)
            else:
                if str(row["status"]) in active_statuses:
                    hidden_reserved += int(row["reserved_seconds"])
                hidden_used += self._charged_use(row)
        if any(
            actor_reserved[actor_id] > limit
            for actor_id, limit in self._plan.actor_limits.items()
        ):
            raise RuntimeError("stored actor compute commitment exceeds its plan")
        if sum(actor_reserved.values()) > self._plan.organisation_limit_seconds:
            raise RuntimeError("stored organisation compute commitment exceeds its plan")
        if hidden_reserved > self._plan.hidden_evaluator_limit_seconds:
            raise RuntimeError("stored hidden compute commitment exceeds its plan")
        if any(actor_id not in self._plan.actor_limits for actor_id in releases):
            raise RuntimeError("stored result release names an unknown actor")
        return ComputeSnapshot(
            campaign_run_id=campaign_run_id,
            organisation_limit_seconds=self._plan.organisation_limit_seconds,
            actor_reserved_seconds=actor_reserved,
            actor_used_seconds=actor_used,
            hidden_reserved_seconds=hidden_reserved,
            hidden_used_seconds=hidden_used,
            released_actor_ids=releases,
            reservations=tuple(self._reservation(row) for row in rows),
            audit_events=audit,
        )

    def _reserve(
        self,
        reservation_key: str,
        campaign_run_id: str,
        actor_id: str | None,
        artifact_ref: ArtifactRef,
        scope: EvaluationScope,
        seconds: int,
    ) -> EvaluationReservation:
        if not reservation_key:
            raise ValueError("compute reservation key is required")
        if type(seconds) is not int or seconds < 1:
            raise ValueError("compute reservation duration must be positive")
        reservation_id = "evaluation-" + digest_value(
            {
                "campaign_run_id": campaign_run_id,
                "reservation_key": reservation_key,
                "scope": scope.value,
            }
        )[7:39]
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM compute_reservations WHERE reservation_key = ?",
                (reservation_key,),
            ).fetchone()
            expected = (
                reservation_id,
                campaign_run_id,
                actor_id,
                artifact_ref.value,
                scope.value,
                seconds,
            )
            if existing is not None:
                actual = tuple(
                    existing[key]
                    for key in (
                        "reservation_id",
                        "campaign_run_id",
                        "actor_id",
                        "artifact_ref",
                        "scope",
                        "reserved_seconds",
                    )
                )
                if actual != expected:
                    raise ValueError("compute reservation key was reused differently")
                if (
                    EvaluationReservationStatus(str(existing["status"]))
                    is EvaluationReservationStatus.CANCELLED
                ):
                    used = self._committed_seconds(
                        connection, campaign_run_id, actor_id, scope
                    )
                    limit = (
                        self._plan.actor_limits[str(actor_id)]
                        if scope is EvaluationScope.VISIBLE
                        else self._plan.hidden_evaluator_limit_seconds
                    )
                    if used + seconds > limit:
                        raise ValueError("compute allocation is exhausted")
                    connection.execute(
                        "UPDATE compute_reservations SET status = 'reserved', "
                        "used_seconds = NULL, failure_reason = NULL "
                        "WHERE reservation_id = ?",
                        (reservation_id,),
                    )
                    self._audit(
                        connection,
                        campaign_run_id,
                        actor_id,
                        "compute.recovered",
                        {"reservation_id": reservation_id},
                    )
                    existing = self._reservation_row(connection, reservation_id)
                return self._reservation(existing)
            used = self._committed_seconds(connection, campaign_run_id, actor_id, scope)
            limit = (
                self._plan.actor_limits[str(actor_id)]
                if scope is EvaluationScope.VISIBLE
                else self._plan.hidden_evaluator_limit_seconds
            )
            if used + seconds > limit:
                raise ValueError("compute allocation is exhausted")
            connection.execute(
                "INSERT INTO compute_reservations("
                "reservation_id, reservation_key, campaign_run_id, actor_id, "
                "artifact_ref, scope, reserved_seconds, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'reserved')",
                (reservation_id, reservation_key, *expected[1:]),
            )
            self._audit(
                connection,
                campaign_run_id,
                actor_id,
                "compute.reserved",
                {
                    "reservation_id": reservation_id,
                    "artifact_ref": artifact_ref.value,
                    "scope": scope.value,
                    "reserved_seconds": seconds,
                },
            )
            return self._reservation(
                self._reservation_row(connection, reservation_id)
            )

    def _terminate(
        self,
        reservation_id: str,
        status: EvaluationReservationStatus,
        reason: str,
    ) -> None:
        if not reason:
            raise ValueError("compute termination reason is required")
        with self._transaction() as connection:
            row = self._reservation_row(connection, reservation_id)
            existing = EvaluationReservationStatus(str(row["status"]))
            if existing is status:
                return
            if existing is not EvaluationReservationStatus.RESERVED:
                raise RuntimeError("compute reservation is not active")
            used_seconds = (
                int(row["reserved_seconds"])
                if status is EvaluationReservationStatus.FAILED
                else None
            )
            connection.execute(
                "UPDATE compute_reservations SET status = ?, used_seconds = ?, "
                "failure_reason = ? WHERE reservation_id = ?",
                (status.value, used_seconds, reason, reservation_id),
            )
            self._audit(
                connection,
                str(row["campaign_run_id"]),
                row["actor_id"],
                f"compute.{status.value}",
                {"reservation_id": reservation_id, "reason": reason},
            )

    @staticmethod
    def _charged_use(row: sqlite3.Row) -> int:
        status = EvaluationReservationStatus(str(row["status"]))
        if status is EvaluationReservationStatus.FAILED:
            return int(row["reserved_seconds"])
        if status is EvaluationReservationStatus.COMPLETE:
            return int(row["used_seconds"])
        return 0

    @staticmethod
    def _committed_seconds(
        connection: sqlite3.Connection,
        campaign_run_id: str,
        actor_id: str | None,
        scope: EvaluationScope,
    ) -> int:
        row = connection.execute(
            "SELECT COALESCE(SUM(reserved_seconds), 0) AS value "
            "FROM compute_reservations WHERE campaign_run_id = ? "
            "AND scope = ? AND actor_id IS ? "
            "AND status IN ('reserved', 'complete', 'failed')",
            (campaign_run_id, scope.value, actor_id),
        ).fetchone()
        return int(row["value"])

    @staticmethod
    def _reservation(row: sqlite3.Row) -> EvaluationReservation:
        return EvaluationReservation(
            reservation_id=str(row["reservation_id"]),
            reservation_key=str(row["reservation_key"]),
            campaign_run_id=str(row["campaign_run_id"]),
            actor_id=(str(row["actor_id"]) if row["actor_id"] is not None else None),
            artifact_ref=ArtifactRef(str(row["artifact_ref"])),
            scope=EvaluationScope(str(row["scope"])),
            reserved_seconds=int(row["reserved_seconds"]),
            status=EvaluationReservationStatus(str(row["status"])),
        )

    @staticmethod
    def _reservation_row(
        connection: sqlite3.Connection, reservation_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM compute_reservations WHERE reservation_id = ?",
            (reservation_id,),
        ).fetchone()
        if row is None:
            raise KeyError("unknown compute reservation")
        return row

    def _register_plan(self) -> None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM compute_plans WHERE campaign_run_id = ?",
                (self._plan.campaign_run_id,),
            ).fetchone()
            expected = self._plan_values()
            if row is not None:
                actual = tuple(
                    row[key]
                    for key in (
                        "plan_id",
                        "organisation_limit_seconds",
                        "actor_limits_json",
                        "hidden_evaluator_limit_seconds",
                        "source_digest",
                    )
                )
                if actual != expected:
                    raise ValueError("compute plan changed across restart")
                return
            connection.execute(
                "INSERT INTO compute_plans("
                "campaign_run_id, plan_id, organisation_limit_seconds, "
                "actor_limits_json, hidden_evaluator_limit_seconds, source_digest) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (self._plan.campaign_run_id, *expected),
            )

    def _validate_plan_row(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT * FROM compute_plans WHERE campaign_run_id = ?",
            (self._plan.campaign_run_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("compute plan is missing")
        actual = tuple(
            row[key]
            for key in (
                "plan_id",
                "organisation_limit_seconds",
                "actor_limits_json",
                "hidden_evaluator_limit_seconds",
                "source_digest",
            )
        )
        if actual != self._plan_values():
            raise RuntimeError("stored compute plan differs from its authority")

    def _plan_values(self) -> tuple[object, ...]:
        return (
            self._plan.plan_id,
            self._plan.organisation_limit_seconds,
            canonical_json_bytes(dict(self._plan.actor_limits)).decode(),
            self._plan.hidden_evaluator_limit_seconds,
            self._plan.source_digest,
        )

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        campaign_run_id: str,
        actor_id: object,
        kind: str,
        details: Mapping[str, object],
    ) -> None:
        connection.execute(
            "INSERT INTO compute_audit("
            "campaign_run_id, actor_id, kind, details_json) VALUES (?, ?, ?, ?)",
            (
                campaign_run_id,
                actor_id,
                kind,
                canonical_json_bytes(details).decode(),
            ),
        )

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS compute_plans(
                    campaign_run_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    organisation_limit_seconds INTEGER NOT NULL,
                    actor_limits_json TEXT NOT NULL,
                    hidden_evaluator_limit_seconds INTEGER NOT NULL,
                    source_digest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS compute_reservations(
                    reservation_id TEXT PRIMARY KEY,
                    reservation_key TEXT NOT NULL UNIQUE,
                    campaign_run_id TEXT NOT NULL,
                    actor_id TEXT,
                    artifact_ref TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    reserved_seconds INTEGER NOT NULL,
                    used_seconds INTEGER,
                    status TEXT NOT NULL,
                    failure_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS compute_releases(
                    campaign_run_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    PRIMARY KEY(campaign_run_id, actor_id)
                );
                CREATE TABLE IF NOT EXISTS compute_audit(
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
        def __init__(self, broker: "SqliteComputeBroker") -> None:
            self._broker = broker
            self._connection: sqlite3.Connection | None = None

        def __enter__(self) -> sqlite3.Connection:
            self._broker._lock.acquire()
            self._connection = self._broker._connect()
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
                self._broker._lock.release()

    def _transaction(self) -> "SqliteComputeBroker._Transaction":
        return self._Transaction(self)
