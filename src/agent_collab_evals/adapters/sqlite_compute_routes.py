"""Retained exact-request routes for a staged, single-controller solo pilot."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from ..canonical import digest_value
from ..compute_backend import ComputeExecutionRequest, FrozenComputeRunManifest
from ..ports import ComputeEvidenceResolver, ComputeExecutionTransport
from ..solo_evaluation_closure import EvaluationComputeSource
from .sqlite_compute_spend import SqliteComputeSpendAuthorizationService
from .sqlite_execution_backend import SqliteComputeBackend


@dataclass(frozen=True)
class ComputeRouteAdapter:
    """Trusted host factories, never supplied by an agent or stored as code."""

    transport_profile_digest: str
    evidence_profile_digest: str
    transport: Callable[[Path, SqliteComputeSpendAuthorizationService], ComputeExecutionTransport]
    evidence: Callable[[Path], ComputeEvidenceResolver]

    @property
    def backend_profile_digest(self) -> str:
        return SqliteComputeBackend.profile_digest_for(
            self.transport_profile_digest, self.evidence_profile_digest,
        )


class SqliteComputeRouteInventory:
    """Retain routes before dispatch, then freeze the full inventory for closure.

    The host must authorize each admitted request separately. Registration and
    reconstruction never issue spend authority. Transports must consume the
    supplied durable authorization service before their external side effect.
    One host owns registration; this is not a dynamic multi-agent scheduler.
    """

    def __init__(self, root: Path, campaign_run_id: str,
                 adapters: Mapping[str, ComputeRouteAdapter], *,
                 expected_seal_digest: str | None = None) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._run_id = campaign_run_id
        self._adapters = dict(adapters)
        self._instances = {}
        self._expected_seal = expected_seal_digest
        with closing(self._connect()) as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS route_inventory(
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    campaign_run_id TEXT NOT NULL, seal_digest TEXT
                );
                CREATE TABLE IF NOT EXISTS compute_routes(
                    route_id TEXT PRIMARY KEY, adapter_id TEXT NOT NULL,
                    manifest_digest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS route_requests(
                    request_digest TEXT PRIMARY KEY, execution_key TEXT NOT NULL,
                    campaign_run_id TEXT NOT NULL, route_id TEXT NOT NULL,
                    UNIQUE(campaign_run_id, execution_key)
                );
            """)
            connection.execute("INSERT OR IGNORE INTO route_inventory VALUES (1, ?, NULL)", (campaign_run_id,))
            connection.commit()
            self._check(connection)

    def backend(self, adapter_id: str) -> RoutedComputeBackend:
        if adapter_id not in self._adapters:
            raise ValueError("compute adapter is not configured")
        return RoutedComputeBackend(self, adapter_id)

    def register(self, adapter_id: str, requests: tuple[ComputeExecutionRequest, ...]) -> str:
        adapter = self._adapters[adapter_id]
        if not requests or len({request.campaign_run_id for request in requests}) != 1:
            raise ValueError("a route requires requests from exactly one campaign")
        run_id = requests[0].campaign_run_id
        if run_id not in {self._run_id, "registered-reference"}:
            raise ValueError("compute route is outside this solo pilot")
        route_id = digest_value({"adapter": adapter_id, "requests": sorted(request.request_digest for request in requests)})[7:]
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._check(connection)
            if connection.execute("SELECT 1 FROM compute_routes WHERE route_id = ?", (route_id,)).fetchone():
                self._source(route_id)  # Verify the retained manifest on retry.
                return route_id
            if connection.execute("SELECT seal_digest FROM route_inventory").fetchone()[0] is not None:
                raise RuntimeError("compute route inventory is sealed")
            manifest = FrozenComputeRunManifest.load_or_create(
                self._route_root(route_id) / "manifest.json", campaign_run_id=run_id,
                compute_enabled=True, transport_profile_digest=adapter.transport_profile_digest,
                backend_profile_digest=adapter.backend_profile_digest, requests=requests,
            )
            connection.execute("INSERT INTO compute_routes VALUES (?, ?, ?)", (route_id, adapter_id, manifest.manifest_digest))
            connection.executemany("INSERT INTO route_requests VALUES (?, ?, ?, ?)",
                [(request.request_digest, request.execution_key, run_id, route_id) for request in requests])
            connection.commit()
        return route_id

    def authorize(self, request: ComputeExecutionRequest, *, approval_reference: str):
        """Issue exact authority only after the caller obtains explicit approval."""
        route_id, _ = self._route(request)
        source, spend = self._source(route_id)
        return spend.issue(request, source.manifest.transport_profile_digest, approval_reference)

    def seal(self) -> str:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._check(connection)
            digest = self._digest(connection)
            connection.execute("UPDATE route_inventory SET seal_digest = ?", (digest,))
            connection.commit()
        self._expected_seal = digest
        return digest

    def require_authorized(self, requests: tuple[ComputeExecutionRequest, ...]) -> None:
        """Refuse unapproved collection before it can fail an admission ledger."""
        for request in requests:
            route_id, _ = self._route(request)
            source, spend = self._source(route_id)
            if spend.request_status(request, source.manifest.transport_profile_digest) not in {"issued", "consumed"}:
                raise RuntimeError("pilot compute request needs explicit authorization")

    def cleanup(self, canceller) -> tuple[dict, ...]:
        """Visit retained requests on abort, including an unsealed partial run.

        No cancellation changes evaluation status or releases reserved budget.
        Failures are retained per request so remaining calls still get cleanup.
        """
        with closing(self._connect()) as connection:
            self._check(connection)
            routes = connection.execute("SELECT route_id, adapter_id FROM compute_routes ORDER BY route_id").fetchall()
        results = []
        for route_id, adapter_id in routes:
            try:
                source, spend = self._source(route_id)
                transport = self._adapters[adapter_id].transport(self._route_root(route_id), spend)
            except Exception as error:
                results.append({"route_id": route_id, "status": "cleanup_failed", "error_type": type(error).__name__})
                continue
            for request in source.manifest.requests():
                try:
                    status = spend.request_status(request, source.manifest.transport_profile_digest)
                    if status != "consumed":
                        result = {"status": "not_dispatched"}
                    else:
                        try:
                            source.backend.resolve(request)
                        except Exception:
                            source.backend.validate_cleanup_dispatch(request)
                            result = transport.cleanup(request, canceller)
                        else:
                            result = {"status": "terminal_evidence_verified", "terminal_confirmed": True}
                    results.append({"request_digest": request.request_digest, **result})
                except Exception as error:
                    results.append({"request_digest": request.request_digest, "status": "cleanup_failed",
                        "error_type": type(error).__name__, "terminal_confirmed": False})
        return tuple(results)

    def sources(self) -> tuple[EvaluationComputeSource, ...]:
        with closing(self._connect()) as connection:
            self._check(connection)
            if connection.execute("SELECT seal_digest FROM route_inventory").fetchone()[0] is None:
                raise RuntimeError("closure requires a sealed compute route inventory")
            rows = connection.execute("SELECT route_id FROM compute_routes ORDER BY route_id").fetchall()
        if not rows:
            raise RuntimeError("compute route inventory is empty")
        return tuple(self._source(row[0])[0] for row in rows)

    def _route(self, request):
        with closing(self._connect()) as connection:
            self._check(connection)
            row = connection.execute(
                "SELECT r.route_id, r.adapter_id FROM route_requests q JOIN compute_routes r "
                "ON q.route_id = r.route_id WHERE q.request_digest = ? AND q.execution_key = ? AND q.campaign_run_id = ?",
                (request.request_digest, request.execution_key, request.campaign_run_id),
            ).fetchone()
        if row is None:
            raise RuntimeError("compute request has no retained route")
        return tuple(row)

    def _source(self, route_id):
        with closing(self._connect()) as connection:
            self._check(connection)
            row = connection.execute("SELECT adapter_id, manifest_digest FROM compute_routes WHERE route_id = ?", (route_id,)).fetchone()
        if row is None:
            raise RuntimeError("compute route is unavailable")
        adapter = self._adapters[row[0]]
        root = self._route_root(route_id)
        manifest = FrozenComputeRunManifest.load(root / "manifest.json", expected_digest=row[1])
        manifest.assert_backend_profiles(adapter.backend_profile_digest, adapter.transport_profile_digest)
        if route_id not in self._instances:
            spend = SqliteComputeSpendAuthorizationService(root / "spend.sqlite3", manifest)
            transport, evidence = adapter.transport(root, spend), adapter.evidence(root)
            if transport.profile_digest != adapter.transport_profile_digest or evidence.profile_digest != adapter.evidence_profile_digest:
                raise RuntimeError("compute factory differs from its configured profile")
            backend = SqliteComputeBackend(root / "executions.sqlite3", transport, evidence, manifest)
            self._instances[route_id] = (backend, spend)
        backend, spend = self._instances[route_id]
        return EvaluationComputeSource(manifest, backend), spend

    def _route_root(self, route_id):
        if len(route_id) != 64 or any(char not in "0123456789abcdef" for char in route_id):
            raise ValueError("invalid compute route ID")
        return self.root / "routes" / route_id

    def _check(self, connection):
        row = connection.execute("SELECT campaign_run_id, seal_digest FROM route_inventory").fetchone()
        if row is None or row[0] != self._run_id:
            raise RuntimeError("compute inventory campaign differs")
        digest = self._digest(connection)
        if (row[1] is not None and row[1] != digest) or (self._expected_seal is not None and row[1] != self._expected_seal):
            raise RuntimeError("compute inventory differs from its seal")

    def _digest(self, connection):
        return digest_value({
            "schema_version": "solo-compute-inventory/v1", "campaign_run_id": self._run_id,
            "routes": [tuple(row) for row in connection.execute("SELECT * FROM compute_routes ORDER BY route_id")],
            "requests": [tuple(row) for row in connection.execute("SELECT * FROM route_requests ORDER BY request_digest")],
        })

    def _connect(self):
        connection = sqlite3.connect(self.root / "inventory.sqlite3")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection


class RoutedComputeBackend:
    """Provider-neutral backend that resolves only retained exact requests."""

    def __init__(self, inventory: SqliteComputeRouteInventory, adapter_id: str):
        self._inventory, self._adapter_id = inventory, adapter_id
        self.profile_digest = inventory._adapters[adapter_id].backend_profile_digest

    def _backend(self, request):
        route_id, adapter_id = self._inventory._route(request)
        if adapter_id != self._adapter_id:
            raise RuntimeError("request belongs to another compute adapter")
        source, _ = self._inventory._source(route_id)
        source.manifest.assert_authorized(request)
        return source.backend

    def submit(self, request, candidate):
        return self._backend(request).submit(request, candidate)

    def collect(self, request, *, timeout_seconds):
        return self._backend(request).collect(request, timeout_seconds=timeout_seconds)

    def resolve(self, request):
        return self._backend(request).resolve(request)

    def reconcile(self, campaign_run_id):
        return tuple(receipt for source in self._inventory.sources()
                     if source.manifest.campaign_run_id == campaign_run_id
                     and source.backend.profile_digest == self.profile_digest
                     for receipt in source.backend.reconcile(campaign_run_id))
