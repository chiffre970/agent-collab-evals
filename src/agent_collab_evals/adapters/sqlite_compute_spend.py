"""Durable issuance and single-use consumption of compute spend authority."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from ..canonical import canonical_json_bytes, digest_value
from ..compute_backend import (
    ComputeExecutionRequest,
    ComputeSpendAuthorization,
    DefinitiveDispatchError,
    FrozenComputeRunManifest,
)


class SqliteComputeSpendAuthorizationService:
    """Issue and consume request-bound authority against a frozen run plan."""

    def __init__(
        self, database: Path, authority: FrozenComputeRunManifest
    ) -> None:
        self._database = database
        self._authority = authority
        self._lock = threading.RLock()
        self._profile_digest = self.profile_digest_for()
        database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @staticmethod
    def profile_digest_for() -> str:
        return digest_value(
            {
                "adapter": "sqlite-compute-spend-authorization/v0alpha1",
                "authority": "frozen_compute_run_manifest",
                "issuance": "explicit_approval_reference",
                "consumption": "atomic_single_use_before_dispatch",
            }
        )

    @property
    def profile_digest(self) -> str:
        return self._profile_digest

    def issue(
        self,
        request: ComputeExecutionRequest,
        transport_profile_digest: str,
        approval_reference: str,
    ) -> ComputeSpendAuthorization:
        self._validate_request(request, transport_profile_digest)
        if (
            not isinstance(approval_reference, str)
            or not approval_reference.strip()
            or len(approval_reference) > 256
        ):
            raise ValueError("compute spend approval reference is invalid")
        authorization = self._authorization(request, transport_profile_digest)
        approval_digest = digest_value(
            {"approval_reference": approval_reference}
        )
        expected = (
            request.campaign_run_id,
            request.request_digest,
            transport_profile_digest,
            self._authority.manifest_digest,
            approval_digest,
        )
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM compute_spend_authorizations "
                "WHERE authorization_id = ?",
                (authorization.authorization_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO compute_spend_authorizations("
                    "authorization_id, campaign_run_id, request_digest, "
                    "transport_profile_digest, run_manifest_digest, "
                    "approval_digest, status) VALUES (?, ?, ?, ?, ?, ?, 'issued')",
                    (authorization.authorization_id, *expected),
                )
                self._audit(
                    connection,
                    request.campaign_run_id,
                    authorization.authorization_id,
                    "compute_spend.issued",
                    {
                        "request_digest": request.request_digest,
                        "transport_profile_digest": transport_profile_digest,
                        "run_manifest_digest": self._authority.manifest_digest,
                        "approval_digest": approval_digest,
                    },
                )
            else:
                actual = tuple(
                    str(row[key])
                    for key in (
                        "campaign_run_id",
                        "request_digest",
                        "transport_profile_digest",
                        "run_manifest_digest",
                        "approval_digest",
                    )
                )
                if actual != expected:
                    raise RuntimeError("compute spend authorization already differs")
        return authorization

    def consume(
        self,
        request: ComputeExecutionRequest,
        transport_profile_digest: str,
    ) -> ComputeSpendAuthorization:
        self._validate_request(request, transport_profile_digest)
        authorization = self._authorization(request, transport_profile_digest)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM compute_spend_authorizations "
                "WHERE authorization_id = ?",
                (authorization.authorization_id,),
            ).fetchone()
            if row is None or str(row["status"]) != "issued":
                raise DefinitiveDispatchError(
                    "compute dispatch lacks unused durable spend authorization"
                )
            expected = (
                request.campaign_run_id,
                request.request_digest,
                transport_profile_digest,
                self._authority.manifest_digest,
            )
            actual = tuple(
                str(row[key])
                for key in (
                    "campaign_run_id",
                    "request_digest",
                    "transport_profile_digest",
                    "run_manifest_digest",
                )
            )
            if actual != expected:
                raise DefinitiveDispatchError(
                    "durable compute spend authorization differs"
                )
            updated = connection.execute(
                "UPDATE compute_spend_authorizations SET status = 'consumed' "
                "WHERE authorization_id = ? AND status = 'issued'",
                (authorization.authorization_id,),
            )
            if updated.rowcount != 1:
                raise DefinitiveDispatchError(
                    "compute spend authorization was already consumed"
                )
            self._audit(
                connection,
                request.campaign_run_id,
                authorization.authorization_id,
                "compute_spend.consumed",
                {
                    "request_digest": request.request_digest,
                    "transport_profile_digest": transport_profile_digest,
                },
            )
        return authorization

    def status(self, authorization_id: str) -> str:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT status FROM compute_spend_authorizations "
                "WHERE authorization_id = ?",
                (authorization_id,),
            ).fetchone()
        if row is None:
            raise KeyError("compute spend authorization is unknown")
        return str(row["status"])

    def request_status(
        self,
        request: ComputeExecutionRequest,
        transport_profile_digest: str,
    ) -> str | None:
        """Return the durable status for an authorized request, if issued."""
        self._validate_request(request, transport_profile_digest)
        authorization = self._authorization(request, transport_profile_digest)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT campaign_run_id, request_digest, "
                "transport_profile_digest, run_manifest_digest, status "
                "FROM compute_spend_authorizations WHERE authorization_id = ?",
                (authorization.authorization_id,),
            ).fetchone()
        if row is None:
            return None
        expected = (
            request.campaign_run_id,
            request.request_digest,
            transport_profile_digest,
            self._authority.manifest_digest,
        )
        actual = tuple(
            str(row[key])
            for key in (
                "campaign_run_id",
                "request_digest",
                "transport_profile_digest",
                "run_manifest_digest",
            )
        )
        if actual != expected:
            raise RuntimeError("durable compute spend authorization differs")
        status = str(row["status"])
        if status not in {"issued", "consumed"}:
            raise RuntimeError("durable compute spend status is invalid")
        return status

    def _validate_request(
        self,
        request: ComputeExecutionRequest,
        transport_profile_digest: str,
    ) -> None:
        self._authority.assert_authorized(request)
        if self._authority.transport_profile_digest != transport_profile_digest:
            raise ValueError("transport differs from frozen compute authority")

    def _authorization(
        self,
        request: ComputeExecutionRequest,
        transport_profile_digest: str,
    ) -> ComputeSpendAuthorization:
        authorization_id = "spend-" + digest_value(
            {
                "run_manifest_digest": self._authority.manifest_digest,
                "request_digest": request.request_digest,
                "transport_profile_digest": transport_profile_digest,
            }
        )[7:39]
        return ComputeSpendAuthorization(
            authorization_id,
            request.request_digest,
            transport_profile_digest,
        )

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS compute_spend_authorizations(
                    authorization_id TEXT PRIMARY KEY,
                    campaign_run_id TEXT NOT NULL,
                    request_digest TEXT NOT NULL UNIQUE,
                    transport_profile_digest TEXT NOT NULL,
                    run_manifest_digest TEXT NOT NULL,
                    approval_digest TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS compute_spend_audit(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_run_id TEXT NOT NULL,
                    authorization_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                """
            )
            connection.commit()

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        campaign_run_id: str,
        authorization_id: str,
        kind: str,
        details: dict[str, object],
    ) -> None:
        connection.execute(
            "INSERT INTO compute_spend_audit("
            "campaign_run_id, authorization_id, kind, details_json) "
            "VALUES (?, ?, ?, ?)",
            (
                campaign_run_id,
                authorization_id,
                kind,
                canonical_json_bytes(details).decode(),
            ),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    class _Transaction:
        def __init__(
            self, service: "SqliteComputeSpendAuthorizationService"
        ) -> None:
            self._service = service
            self._connection: sqlite3.Connection | None = None

        def __enter__(self) -> sqlite3.Connection:
            self._service._lock.acquire()
            self._connection = self._service._connect()
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
                self._service._lock.release()

    def _transaction(self) -> "SqliteComputeSpendAuthorizationService._Transaction":
        return self._Transaction(self)
