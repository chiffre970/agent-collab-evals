"""Read-only, evidence-bound recovery planning for an aborted solo pilot.

This module has no runtime, transport, or authorization service. It retains
verified results with their original profiles; it does not reopen the campaign
or make a continuation scoreable. Missing work needs separate spend authority.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Callable

from .adapters.sqlite_execution_backend import SqliteComputeBackend
from .canonical import canonical_json_bytes, digest_bytes, digest_file, digest_value, parse_json
from .compute_backend import ComputeExecutionStatus, FrozenComputeRunManifest
from .pilot_evidence import retain_document


class _NoDispatchTransport:
    def __init__(self, profile_digest: str):
        self.profile_digest = profile_digest

    def dispatch(self, *args, **kwargs):
        raise PermissionError("recovery planning cannot dispatch compute")

    def poll(self, *args, **kwargs):
        raise PermissionError("recovery planning cannot poll providers")


def plan_evaluation_recovery(
    source_root: Path,
    expected_audit_digest: str,
    resolver_factory: Callable,
    output_root: Path,
) -> dict:
    """Retain reusable evidence and an exact outstanding-work inventory.

    The trusted resolver factory must reconstruct original source profiles, not
    current replacements. A locally retained terminal result can be inspected
    after an unacknowledged collection, without changing its source ledger.
    """
    root = source_root.resolve(strict=True)
    output = output_root.resolve()
    if output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError("recovery output must be separate from the source run")
    audit_path = root / "audit.json"
    if digest_file(audit_path) != expected_audit_digest:
        raise RuntimeError("source audit digest differs")
    audit = parse_json(audit_path.read_text())
    if audit.get("status") != "aborted" or audit.get("scoreable") is not False:
        raise ValueError("recovery requires an aborted, non-scoreable source run")
    if audit.get("budget_reconciliation", {}).get("valid") is not True:
        raise RuntimeError("source model budget is not reconciled")
    config_path = root / "run-config.json"
    if digest_file(config_path) != audit.get("run_config_digest"):
        raise RuntimeError("source run configuration digest differs")
    inventory = root / "evaluation/compute"
    with closing(_read_only(inventory / "inventory.sqlite3")) as connection:
        identity = connection.execute("SELECT campaign_run_id, seal_digest FROM route_inventory").fetchall()
        routes = connection.execute("SELECT * FROM compute_routes ORDER BY route_id").fetchall()
        requests = connection.execute("SELECT * FROM route_requests ORDER BY request_digest").fetchall()
    seal = digest_value({"schema_version": "solo-compute-inventory/v1", "campaign_run_id": audit["run_id"],
        "routes": [tuple(row) for row in routes], "requests": [tuple(row) for row in requests]})
    recorded = parse_json((root / "compute-inventory-seal.json").read_text())
    if identity != [(audit["run_id"], seal)] or recorded != {"inventory_digest": seal}:
        raise RuntimeError("source compute inventory seal differs")
    observed_requests, entries, retained = [], [], []
    for route_id, adapter_id, manifest_digest in routes:
        if not re.fullmatch(r"[0-9a-f]{64}", route_id):
            raise ValueError("source compute route ID is invalid")
        route = inventory / "routes" / route_id
        manifest = FrozenComputeRunManifest.load(route / "manifest.json", expected_digest=manifest_digest)
        resolver = resolver_factory(adapter_id, route, manifest)
        database = route / "executions.sqlite3"
        backend = (SqliteComputeBackend(database, _NoDispatchTransport(manifest.transport_profile_digest),
            resolver, manifest, read_only=True) if database.is_file() else None)
        planned = manifest.requests()
        if database.is_file():
            with closing(_read_only(database)) as connection:
                keys = {row[0] for row in connection.execute("SELECT execution_key FROM compute_executions")}
            if not keys.issubset({request.execution_key for request in planned}):
                raise RuntimeError("source execution ledger contains unplanned work")
        for request in planned:
            observed_requests.append((request.request_digest, request.execution_key, request.campaign_run_id, route_id))
            spend = _spend_status(route, request, manifest)
            receipt = backend.inspect(request) if backend is not None else None
            entry = {"route_id": route_id, "adapter_id": adapter_id, "request": request.document,
                "request_digest": request.request_digest, "source_manifest_digest": manifest_digest,
                "source_transport_profile_digest": manifest.transport_profile_digest,
                "source_evidence_profile_digest": resolver.profile_digest,
                "source_ledger_status": receipt.status.value if receipt else None,
                "source_call_id": receipt.external_call_id if receipt else None}
            evidence = None
            if receipt is None or receipt.status is ComputeExecutionStatus.REGISTERED:
                if spend == "consumed":
                    raise RuntimeError("missing execution has consumed authority; delivery is uncertain")
                entry["action"] = "execute_missing"
            elif receipt.status is ComputeExecutionStatus.COMPLETE:
                if spend != "consumed":
                    raise RuntimeError("completed execution lacks consumed authority")
                _, evidence = backend.resolve(request)
                entry["action"] = "reuse_verified"
            elif receipt.status is ComputeExecutionStatus.DISPATCHED:
                if spend != "consumed":
                    raise RuntimeError("dispatched execution lacks consumed authority")
                # This is a local bundle lookup, never a provider poll. Absence or
                # corrupt evidence must not be converted into permission to retry.
                pointer, status, used, failure = resolver.pointer(request, receipt.external_call_id)
                content = resolver.resolve(pointer)
                if digest_bytes(content) != pointer.digest:
                    raise RuntimeError("retained terminal evidence digest differs")
                evidence = parse_json(content.decode())
                expected = {"schema_version": "compute-execution-evidence/v0alpha1",
                    "request_digest": request.request_digest, "candidate_digest": request.candidate_digest,
                    "candidate_manifest_digest": request.candidate_manifest_digest,
                    "evaluator_profile_digest": request.evaluator_profile_digest,
                    "transport_profile_digest": manifest.transport_profile_digest,
                    "evidence_profile_digest": resolver.profile_digest, "external_call_id": receipt.external_call_id,
                    "status": status.value, "used_seconds": used, "failure": failure}
                if any(evidence.get(key) != value for key, value in expected.items()):
                    raise RuntimeError("retained terminal evidence identity differs")
                if type(used) is not int or not 0 <= used <= request.maximum_seconds:
                    raise RuntimeError("retained compute use exceeds its allowance")
                if status is ComputeExecutionStatus.COMPLETE:
                    entry["action"] = "reuse_verified"
                elif status is ComputeExecutionStatus.FAILED and failure and "driver_version differs" in failure:
                    entry["action"] = "replace_environment_rejected"
                else:
                    raise RuntimeError("terminal failure needs an explicit recovery policy")
            else:
                raise RuntimeError("uncertain or failed execution needs an explicit recovery policy")
            if evidence is not None:
                name = "evidence/" + request.request_digest[7:] + ".json"
                entry["retained_evidence"] = {"file": name, "digest": digest_value(evidence)}
                entry["observed_used_seconds"] = evidence["used_seconds"]
                entry["failure"] = evidence["failure"]
                retained.append((name, evidence))
            entries.append(entry)
    if sorted(observed_requests) != [tuple(row) for row in requests]:
        raise RuntimeError("source manifests differ from the sealed request inventory")
    if digest_file(audit_path) != expected_audit_digest:
        raise RuntimeError("source audit changed during recovery planning")
    document = {"schema_version": "solo-evaluation-recovery-plan/v1", "source_run_id": audit["run_id"],
        "source_audit_digest": expected_audit_digest, "source_run_config_digest": audit["run_config_digest"],
        "source_inventory_digest": seal, "source_root": str(root), "source_status": "aborted",
        "scoreable": False, "execution_authorized": False, "agent_reruns": 0, "new_provider_calls": 0,
        "profile_policy": "retain_original_provenance_separately_bind_replacement_profiles",
        "entries": entries, "summary": {action: sum(entry["action"] == action for entry in entries)
            for action in ("reuse_verified", "replace_environment_rejected", "execute_missing")}}
    # Publish the plan last, after every evidence snapshot is retained.
    for name, evidence in retained:
        retain_document(output / name, evidence)
    retain_document(output / "plan.json", document)
    return document


def _read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve(strict=True).as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def _spend_status(route, request, manifest):
    path = route / "spend.sqlite3"
    if not path.is_file():
        return None
    with closing(_read_only(path)) as connection:
        rows = connection.execute("SELECT campaign_run_id, transport_profile_digest, run_manifest_digest, status "
            "FROM compute_spend_authorizations WHERE request_digest = ?", (request.request_digest,)).fetchall()
    if not rows:
        return None
    if len(rows) != 1 or rows[0][:3] != (request.campaign_run_id, manifest.transport_profile_digest, manifest.manifest_digest):
        raise RuntimeError("retained spend authority identity differs")
    if rows[0][3] not in {"issued", "consumed"}:
        raise RuntimeError("retained spend authority status is invalid")
    return rows[0][3]
