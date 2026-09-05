"""No-spend solo rehearsal composed from the real runtime and control adapters."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapters.darwin_sandbox import DarwinSandboxExec
from .adapters.deterministic_model import DeterministicToolModelUpstream
from .adapters.local_events import LocalEventSink
from .adapters.no_compute_reconciliation import NoComputeExecutionReconciler
from .adapters.opencode_harness import OpenCodeHarnessRuntime, OpenCodeRuntimeProfile
from .adapters.provider_receipts import OpenRouterReceiptVerifier
from .adapters.sqlite_budget import SqliteBudgetAccount
from .adapters.sqlite_delivery import SqliteDeliveryOutbox
from .adapters.sqlite_collaboration import SqliteCollaborationBackend
from .budget import ActorBudgetAllocation, BudgetPlan, BudgetSnapshot
from .campaigns.model_serving import ModelServingCampaign
from .canonical import (
    canonical_json_bytes,
    digest_bytes,
    digest_file,
    digest_value,
    load_json,
)
from .compute_backend import FrozenComputeRunManifest
from .controller import CampaignController
from .candidate_gateway import SessionToolGateway
from .native_admission import NativeAdmissionTools
from .delivery import job_document
from .collaboration import CollaborationVisibility
from .domain import (
    CoordinationCondition,
    Job,
    OrganisationSpec,
    SessionHandle,
    top_level_actor_count,
)
from .model_gateway import ModelBudgetGateway, ModelGatewayProfile
from .peer_tool import PeerToolGateway, PeerToolIntegrationProfile
from .registered_profiles import load_collaboration_profile
from .sandbox import SandboxProfile
from .session_identity import SessionIdentityRegistry


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}")
_BUDGET_LIMIT_USD_NANOS = 1_000_000_000


def _coordination_job() -> Job:
    return Job(
        "coordination-conformance",
        "Exercise the available coordination surface once more, then finish.",
        digest_value({"adapter_rehearsal": "coordination_surface_v1"}),
        {},
    )


@dataclass(frozen=True, slots=True)
class AdapterRehearsalResult:
    run_id: str
    condition: CoordinationCondition
    audit_path: Path
    audit_digest: str
    synthetic_model_calls: int
    charged_usd_nanos: int


def run_adapter_condition_rehearsal(
    *,
    campaign_path: Path,
    state_root: Path,
    run_id: str,
    condition: CoordinationCondition,
    organisation_size: int = 4,
    task_seed: int = 1729,
    repository_root: Path | None = None,
    runtime_timeout_seconds: int = 90,
) -> AdapterRehearsalResult:
    """Exercise real adapters while making external model and compute spend impossible."""

    if not _SAFE_ID.fullmatch(run_id):
        raise ValueError("adapter rehearsal run ID is invalid")
    repository = (repository_root or Path(__file__).resolve().parents[2]).resolve()
    run_root = state_root.resolve() / run_id
    audit_path = run_root / "adapter-rehearsal-audit.json"
    if audit_path.exists():
        raise FileExistsError("adapter rehearsal audit already exists")

    campaign = ModelServingCampaign.load(campaign_path.resolve())
    materialized = campaign.materialize(task_seed)
    runtime_profile = OpenCodeRuntimeProfile.load(
        repository
        / "config/runtime_profiles/opencode-deepseek-v4-flash-development.json",
        repository_root=repository,
    )
    gateway_profile = ModelGatewayProfile.load(
        repository
        / "config/gateway_profiles/openrouter-deepinfra-local-conformance-v0.json",
        repository_root=repository,
    )
    sandbox_profile = SandboxProfile.load(
        repository / "config/sandbox_profiles/darwin-loopback-network-v0.json"
    )
    if gateway_profile.status != "conformance_only":
        raise RuntimeError("adapter rehearsal requires a conformance-only gateway")
    if gateway_profile.rate_card.catalog_id != "synthetic-local-conformance-catalog":
        raise RuntimeError("adapter rehearsal requires the synthetic rate card")
    if sandbox_profile.credential_environment_allowlist:
        raise RuntimeError("adapter rehearsal sandbox must receive no credentials")

    actor_count = top_level_actor_count(condition, organisation_size)
    if _BUDGET_LIMIT_USD_NANOS % actor_count:
        raise ValueError("adapter rehearsal budget does not partition by actor")
    actor_limit = _BUDGET_LIMIT_USD_NANOS // actor_count
    allocations = tuple(
        ActorBudgetAllocation(run_id, f"{run_id}:actor:{ordinal}", actor_limit)
        for ordinal in range(actor_count)
    )
    budget_plan_path = run_root / "budget-plan.json"
    budget_plan_document = {
        "schema_version": "budget-plan/v1",
        "plan_id": f"{run_id}-synthetic-budget",
        "status": "conformance_only",
        "campaign_run_id": run_id,
        "organisation_limit_usd_nanos": _BUDGET_LIMIT_USD_NANOS,
        "allocations": [
            {
                "actor_id": allocation.actor_id,
                "limit_usd_nanos": allocation.limit_usd_nanos,
            }
            for allocation in allocations
        ],
        "rate_card_digest": digest_value(gateway_profile.rate_card),
    }
    _write_once(budget_plan_path, canonical_json_bytes(budget_plan_document))
    budget_plan = BudgetPlan.load(
        budget_plan_path, expected_digest=digest_file(budget_plan_path)
    )
    account = SqliteBudgetAccount(
        run_root / "budget.sqlite3",
        gateway_profile.rate_card,
        require_metadata_receipts=False,
        budget_plan=budget_plan,
        receipt_verifier=OpenRouterReceiptVerifier(
            gateway_profile, require_metadata_receipt=False
        ),
    )
    account.open_campaign(run_id, _BUDGET_LIMIT_USD_NANOS, allocations)
    compute_authority = FrozenComputeRunManifest.load_or_create(
        run_root / "compute-run.json",
        campaign_run_id=run_id,
        compute_enabled=False,
        transport_profile_digest=None,
        backend_profile_digest=None,
        requests=(),
    )
    upstream = DeterministicToolModelUpstream(
        model=gateway_profile.expected_returned_model,
        provider=gateway_profile.expected_provider,
        peer_actor_count=actor_count,
    )
    gateway = ModelBudgetGateway(gateway_profile, account, upstream)
    identities = SessionIdentityRegistry()
    collaboration_profile = load_collaboration_profile(
        repository / "config/collaboration_profiles/sqlite-peer-v1.json"
    )
    collaboration = SqliteCollaborationBackend(
        run_root / "collaboration.sqlite3",
        identities,
        registered_profile=collaboration_profile,
    )
    peer_profile = PeerToolIntegrationProfile.load(
        repository / "config/peer_tool_profiles/peer-tool-v0.json",
        repository_root=repository,
    )
    peer_gateway = PeerToolGateway(collaboration, identities)
    events = LocalEventSink(run_root / "events")
    delivery = SqliteDeliveryOutbox(run_root / "delivery.sqlite3")
    native_gateway = None
    if condition is CoordinationCondition.NATIVE_MULTIAGENT:
        native_sessions = SessionIdentityRegistry()
        native_gateway = SessionToolGateway(
            NativeAdmissionTools(run_root / "native-admission", native_sessions, run_id, organisation_size),
            native_sessions,
        )
    runtime: OpenCodeHarnessRuntime | None = None
    handle = None
    stopped = False
    try:
        runtime = OpenCodeHarnessRuntime(
            runtime_profile,
            run_root / "runtime-state",
            gateway,
            process_sandbox=DarwinSandboxExec(sandbox_profile),
            peer_profile=peer_profile,
            peer_gateway=peer_gateway,
            native_gateway=native_gateway,
            timeout_seconds=runtime_timeout_seconds,
        )
        controller = CampaignController(
            runtime,
            events,
            account,
            NoComputeExecutionReconciler(compute_authority),
            delivery,
        )
        handle = controller.start(
            OrganisationSpec(
                campaign_run_id=run_id,
                condition=condition,
                organisation_size=organisation_size,
                workspace_root=run_root / "workspace",
                model_endpoint=gateway.endpoint,
            )
        )
        for job in materialized.jobs:
            controller.deliver(handle, job)
        controller.deliver(handle, _coordination_job())
        result = controller.close(handle, "no-spend real-adapter rehearsal")
        stopped = True
    except BaseException:
        if runtime is not None and handle is not None and not stopped:
            try:
                runtime.stop(handle.organisation, "adapter rehearsal failed")
            except BaseException:
                pass
        raise
    finally:
        if native_gateway is not None:
            native_gateway.close()
        peer_gateway.close()
        gateway.close()

    requests = upstream.requests
    if not requests:
        raise RuntimeError("real-adapter rehearsal made no synthetic model request")
    budget_reconciliation = account.reconcile(run_id)
    if not budget_reconciliation.valid:
        raise RuntimeError("real-adapter rehearsal budget did not reconcile")
    delivery_reconciliation = delivery.reconcile(
        run_id, handle.sessions, tuple(result.delivered_job_ids)
    )
    compute_receipts = NoComputeExecutionReconciler(compute_authority).reconcile(run_id)
    event_log = events.read(run_id)
    budget_snapshot = account.snapshot(run_id)
    snapshot_document = {
        "organisation_id": result.final_harness_snapshot.organisation_id,
        "payload": result.final_harness_snapshot.payload,
    }
    snapshot_content = canonical_json_bytes(snapshot_document)
    snapshot_path = run_root / "final-harness-snapshot.json"
    _write_once(snapshot_path, snapshot_content)
    request_content = canonical_json_bytes(
        [request.decode("utf-8") for request in upstream.raw_requests]
    )
    _write_once(run_root / "model-requests.json", request_content)
    collaboration_evidence = _collaboration_evidence(
        collaboration, run_id, condition
    )
    treatment_evidence = _treatment_evidence(
        condition,
        actor_count,
        requests,
        _receipt_tool_calls(budget_snapshot),
        collaboration_evidence,
        snapshot_document,
    )
    if not treatment_evidence["complete"]:
        raise RuntimeError("real-adapter treatment surface was not exercised")
    audit = {
        "schema_version": "real-adapter-condition-rehearsal/v3",
        "run_id": run_id,
        "execution_class": "local_synthetic_model",
        "scoreable": False,
        "condition": condition.value,
        "organisation_size": organisation_size,
        "top_level_actor_count": actor_count,
        "treatment_surfaces_exercised": treatment_evidence["complete"],
        "treatment_evidence": treatment_evidence,
        "model_requests_digest": digest_bytes(request_content),
        "campaign_manifest_digest": campaign.manifest_digest,
        "task_material_digest": materialized.material_digest,
        "task_seed": task_seed,
        "runtime_profile_digest": runtime_profile.resolved_digest,
        "native_admission_profile_digest": runtime.capabilities()["native_admission_profile_digest"],
        "gateway_profile_digest": gateway_profile.resolved_digest,
        "sandbox_profile_digest": sandbox_profile.resolved_digest,
        "sandbox_enforcement": DarwinSandboxExec(sandbox_profile).evidence(),
        "collaboration_profile_digest": collaboration.profile_digest,
        "peer_tool_profile_digest": peer_profile.resolved_digest,
        "collaboration_evidence": collaboration_evidence,
        "budget_plan_digest": budget_plan.source_digest,
        "budget_reconciliation": budget_reconciliation.evidence(),
        "charged_usd_nanos": budget_snapshot.organisation_charged_usd_nanos,
        "compute_authority_digest": compute_authority.manifest_digest,
        "compute_execution_count": len(compute_receipts),
        "delivery_profile_digest": delivery.profile_digest,
        "delivery_reconciliation_digest": delivery_reconciliation.evidence_digest,
        "delivery_receipt_ids": [
            receipt.receipt_id for receipt in delivery_reconciliation.receipts
        ],
        "session_ids": [session.value for session in handle.sessions],
        "delivered_job_ids": list(result.delivered_job_ids),
        "synthetic_model_calls": len(requests),
        "external_model_calls": 0,
        "external_compute_executions": 0,
        "event_count": len(event_log),
        "event_log_digest": digest_value(event_log),
        "final_harness_snapshot_path": snapshot_path.name,
        "final_harness_snapshot_digest": digest_bytes(snapshot_content),
    }
    audit_content = canonical_json_bytes(audit)
    _write_once(audit_path, audit_content)
    return AdapterRehearsalResult(
        run_id=run_id,
        condition=condition,
        audit_path=audit_path,
        audit_digest=digest_bytes(audit_content),
        synthetic_model_calls=len(requests),
        charged_usd_nanos=budget_snapshot.organisation_charged_usd_nanos,
    )


def run_solo_adapter_rehearsal(
    *,
    campaign_path: Path,
    state_root: Path,
    run_id: str,
    task_seed: int = 1729,
    repository_root: Path | None = None,
) -> AdapterRehearsalResult:
    """Compatibility wrapper for the original solo real-adapter rehearsal."""

    return run_adapter_condition_rehearsal(
        campaign_path=campaign_path,
        state_root=state_root,
        run_id=run_id,
        condition=CoordinationCondition.SOLO,
        organisation_size=1,
        task_seed=task_seed,
        repository_root=repository_root,
    )


def verify_adapter_condition_rehearsal(
    audit_path: Path,
    *,
    expected_digest: str,
    campaign_path: Path,
    expected_condition: CoordinationCondition | None = None,
    repository_root: Path | None = None,
) -> AdapterRehearsalResult:
    """Reconcile a retained rehearsal from its independent durable stores."""

    repository = (repository_root or Path(__file__).resolve().parents[2]).resolve()
    content = audit_path.resolve(strict=True).read_bytes()
    if digest_bytes(content) != expected_digest:
        raise RuntimeError("adapter rehearsal audit digest differs")
    with audit_path.open("r", encoding="utf-8") as source:
        audit = load_json(source)
    expected_fields = {
        "schema_version",
        "run_id",
        "execution_class",
        "scoreable",
        "condition",
        "organisation_size",
        "top_level_actor_count",
        "treatment_surfaces_exercised",
        "treatment_evidence",
        "model_requests_digest",
        "campaign_manifest_digest",
        "task_material_digest",
        "task_seed",
        "runtime_profile_digest",
        "native_admission_profile_digest",
        "gateway_profile_digest",
        "sandbox_profile_digest",
        "sandbox_enforcement",
        "collaboration_profile_digest",
        "peer_tool_profile_digest",
        "collaboration_evidence",
        "budget_plan_digest",
        "budget_reconciliation",
        "charged_usd_nanos",
        "compute_authority_digest",
        "compute_execution_count",
        "delivery_profile_digest",
        "delivery_reconciliation_digest",
        "delivery_receipt_ids",
        "session_ids",
        "delivered_job_ids",
        "synthetic_model_calls",
        "external_model_calls",
        "external_compute_executions",
        "event_count",
        "event_log_digest",
        "final_harness_snapshot_path",
        "final_harness_snapshot_digest",
    }
    if not isinstance(audit, dict) or set(audit) != expected_fields:
        raise RuntimeError("adapter rehearsal audit fields differ")
    if canonical_json_bytes(audit) != content:
        raise RuntimeError("adapter rehearsal audit is not canonical")
    fixed = {
        "schema_version": "real-adapter-condition-rehearsal/v3",
        "execution_class": "local_synthetic_model",
        "scoreable": False,
        "external_model_calls": 0,
        "external_compute_executions": 0,
        "compute_execution_count": 0,
        "final_harness_snapshot_path": "final-harness-snapshot.json",
    }
    if any(audit.get(key) != value for key, value in fixed.items()):
        raise RuntimeError("adapter rehearsal audit semantics differ")
    run_id = audit["run_id"]
    if not isinstance(run_id, str) or not _SAFE_ID.fullmatch(run_id):
        raise RuntimeError("adapter rehearsal run ID is invalid")
    try:
        condition = CoordinationCondition(audit["condition"])
    except (TypeError, ValueError) as error:
        raise RuntimeError("adapter rehearsal condition is invalid") from error
    if expected_condition is not None and condition is not expected_condition:
        raise RuntimeError("adapter rehearsal condition differs")
    organisation_size = audit["organisation_size"]
    if type(organisation_size) is not int or organisation_size < 1:
        raise RuntimeError("adapter rehearsal organisation size is invalid")
    actor_count = top_level_actor_count(condition, organisation_size)
    if audit["top_level_actor_count"] != actor_count:
        raise RuntimeError("adapter rehearsal actor count differs")
    run_root = audit_path.resolve().parent
    campaign = ModelServingCampaign.load(campaign_path.resolve())
    if type(audit["task_seed"]) is not int:
        raise RuntimeError("adapter rehearsal task seed is invalid")
    materialized = campaign.materialize(audit["task_seed"])
    if materialized.material_digest != audit["task_material_digest"]:
        raise RuntimeError("adapter rehearsal task material digest differs")
    runtime_profile = OpenCodeRuntimeProfile.load(
        repository
        / "config/runtime_profiles/opencode-deepseek-v4-flash-development.json",
        repository_root=repository,
    )
    gateway_profile = ModelGatewayProfile.load(
        repository
        / "config/gateway_profiles/openrouter-deepinfra-local-conformance-v0.json",
        repository_root=repository,
    )
    sandbox_profile = SandboxProfile.load(
        repository / "config/sandbox_profiles/darwin-loopback-network-v0.json"
    )
    collaboration_profile = load_collaboration_profile(
        repository / "config/collaboration_profiles/sqlite-peer-v1.json"
    )
    peer_profile = PeerToolIntegrationProfile.load(
        repository / "config/peer_tool_profiles/peer-tool-v0.json",
        repository_root=repository,
    )
    expected_profiles = {
        "campaign_manifest_digest": campaign.manifest_digest,
        "runtime_profile_digest": runtime_profile.resolved_digest,
        "gateway_profile_digest": gateway_profile.resolved_digest,
        "sandbox_profile_digest": sandbox_profile.resolved_digest,
        "sandbox_enforcement": DarwinSandboxExec(sandbox_profile).evidence(),
        "collaboration_profile_digest": collaboration_profile.authority_digest,
        "peer_tool_profile_digest": peer_profile.resolved_digest,
        "delivery_profile_digest": SqliteDeliveryOutbox.profile_digest_for(),
    }
    if any(audit.get(key) != value for key, value in expected_profiles.items()):
        raise RuntimeError("adapter rehearsal profile evidence differs")

    budget_plan = BudgetPlan.load(
        run_root / "budget-plan.json", expected_digest=audit["budget_plan_digest"]
    )
    account = SqliteBudgetAccount(
        run_root / "budget.sqlite3",
        gateway_profile.rate_card,
        require_metadata_receipts=False,
        budget_plan=budget_plan,
        receipt_verifier=OpenRouterReceiptVerifier(
            gateway_profile, require_metadata_receipt=False
        ),
    )
    budget = account.reconcile(run_id)
    budget_snapshot = account.snapshot(run_id)
    if not budget.valid or budget.evidence() != audit["budget_reconciliation"]:
        raise RuntimeError("adapter rehearsal budget evidence differs")
    if (
        budget_snapshot.organisation_charged_usd_nanos
        != audit["charged_usd_nanos"]
        or len(budget_snapshot.charges) != audit["synthetic_model_calls"]
    ):
        raise RuntimeError("adapter rehearsal model accounting differs")

    compute = FrozenComputeRunManifest.load(
        run_root / "compute-run.json",
        expected_digest=audit["compute_authority_digest"],
    )
    compute.assert_no_compute(run_id)
    session_values = audit["session_ids"]
    job_values = audit["delivered_job_ids"]
    if (
        not isinstance(session_values, list)
        or len(session_values) != actor_count
        or not all(isinstance(value, str) and value for value in session_values)
        or not isinstance(job_values, list)
        or not all(isinstance(value, str) and value for value in job_values)
    ):
        raise RuntimeError("adapter rehearsal delivery identity is invalid")
    outbox = SqliteDeliveryOutbox(run_root / "delivery.sqlite3")
    expected_jobs = (*materialized.jobs, _coordination_job())
    if job_values != [job.job_id for job in expected_jobs]:
        raise RuntimeError("adapter rehearsal materialized job set differs")
    for job in expected_jobs:
        if job_document(outbox.read_job(run_id, job.job_id)) != job_document(job):
            raise RuntimeError("adapter rehearsal materialized outbox job differs")
    delivery = outbox.reconcile(
        run_id,
        tuple(SessionHandle(value) for value in session_values),
        tuple(job_values),
    )
    if (
        delivery.evidence_digest != audit["delivery_reconciliation_digest"]
        or [receipt.receipt_id for receipt in delivery.receipts]
        != audit["delivery_receipt_ids"]
    ):
        raise RuntimeError("adapter rehearsal delivery evidence differs")
    event_log = LocalEventSink(run_root / "events").read(run_id)
    if (
        len(event_log) != audit["event_count"]
        or digest_value(event_log) != audit["event_log_digest"]
    ):
        raise RuntimeError("adapter rehearsal event evidence differs")
    snapshot = run_root / "final-harness-snapshot.json"
    if digest_file(snapshot) != audit["final_harness_snapshot_digest"]:
        raise RuntimeError("adapter rehearsal harness snapshot differs")
    with snapshot.open("r", encoding="utf-8") as stream:
        snapshot_document = load_json(stream)
    snapshot_payload = snapshot_document["payload"]
    snapshot_spec = snapshot_payload["spec"]
    if (
        snapshot_spec["campaign_run_id"] != run_id
        or snapshot_spec["condition"] != condition.value
        or snapshot_spec["organisation_size"] != organisation_size
        or snapshot_payload.get("stopped") is not True
        or [actor["session_id"] for actor in snapshot_payload["sessions"]] != session_values
        or [actor["actor_ordinal"] for actor in snapshot_payload["sessions"]]
        != list(range(actor_count))
    ):
        raise RuntimeError("adapter rehearsal snapshot identity differs")
    if condition is CoordinationCondition.NATIVE_MULTIAGENT:
        if not (run_root / "native-admission/native.sqlite3").is_file():
            raise RuntimeError("native admission ledger is missing")
        native_tools = NativeAdmissionTools(
            run_root / "native-admission", SessionIdentityRegistry(), run_id, organisation_size
        )
        expected_native_profile = digest_value({
            "gateway": digest_value({"transport": "development-session-tools-http/v1", "tools": native_tools.profile_digest}),
            "hook": digest_file(repository / "scripts/runtime/native_admission_plugin.mjs"),
            "status": "development_enforcement_integration",
        })
        native_evidence = []
        for actor in snapshot_payload["sessions"]:
            children = tuple(
                value["session_id"] for value in actor["checkpoint"]["reconciliation"]["sessions"]
                if value["session_id"] != actor["session_id"]
            )
            evidence = native_tools.reconcile(actor["session_id"], children)
            if not evidence["valid"]:
                raise RuntimeError("native admission ledger does not reconcile")
            native_evidence.append(evidence)
        if snapshot_payload.get("native_admission_evidence") != native_evidence:
            raise RuntimeError("native admission snapshot evidence differs")
    else:
        expected_native_profile = None
    if audit["native_admission_profile_digest"] != expected_native_profile or snapshot_payload.get("native_admission_profile_digest") != expected_native_profile:
        raise RuntimeError("native admission profile differs")
    for actor in snapshot_payload["sessions"]:
        expected_receipts = [
            receipt.document for receipt in delivery.receipts
            if receipt.session_id == actor["session_id"]
        ]
        if actor["delivered_jobs"] != expected_receipts:
            raise RuntimeError("adapter rehearsal snapshot delivery evidence differs")
    request_path = run_root / "model-requests.json"
    if digest_file(request_path) != audit["model_requests_digest"]:
        raise RuntimeError("adapter rehearsal request trace differs")
    with request_path.open("r", encoding="utf-8") as stream:
        raw_requests = load_json(stream)
    if not isinstance(raw_requests, list) or not all(
        isinstance(request, str) for request in raw_requests
    ):
        raise RuntimeError("adapter rehearsal request trace is invalid")
    request_digests = Counter(
        digest_bytes(request.encode("utf-8")) for request in raw_requests
    )
    budget_request_digests = Counter(
        event["details"]["request_digest"]
        for event in budget_snapshot.audit_events
        if event["kind"] == "reservation.created"
    )
    if request_digests != budget_request_digests:
        raise RuntimeError("adapter rehearsal requests do not match budget evidence")
    collaboration = SqliteCollaborationBackend(
        run_root / "collaboration.sqlite3",
        SessionIdentityRegistry(),
        registered_profile=collaboration_profile,
    )
    collaboration_evidence = _collaboration_evidence(
        collaboration, run_id, condition
    )
    if collaboration_evidence != audit["collaboration_evidence"]:
        raise RuntimeError("adapter rehearsal collaboration evidence differs")
    treatment = audit["treatment_evidence"]
    reconstructed_treatment = _treatment_evidence(
        condition, actor_count,
        tuple(json.loads(request) for request in raw_requests),
        _receipt_tool_calls(budget_snapshot), collaboration_evidence,
        snapshot_document,
    )
    if treatment != reconstructed_treatment:
        raise RuntimeError("adapter rehearsal treatment evidence differs from retained records")
    treatment_fields = {
        "complete",
        "task_offered_request_count",
        "native_task_calls",
        "native_child_model_calls",
        "peer_tool_offered_request_count",
        "peer_publish_calls",
        "peer_list_recent_calls",
        "peer_sessions_completed",
    }
    if (
        not isinstance(treatment, dict)
        or set(treatment) != treatment_fields
        or treatment.get("complete") is not True
        or audit["treatment_surfaces_exercised"] is not True
        or any(
            type(treatment[name]) is not int or treatment[name] < 0
            for name in treatment_fields - {"complete"}
        )
    ):
        raise RuntimeError("adapter rehearsal treatment evidence is incomplete")
    native_complete = (
        treatment["native_task_calls"] >= 1
        and treatment["native_child_model_calls"] >= 1
        and treatment["peer_publish_calls"] == 0
        and treatment["peer_list_recent_calls"] == 0
    )
    peer_complete = (
        treatment["native_task_calls"] == 0
        and treatment["peer_publish_calls"] >= actor_count
        and treatment["peer_list_recent_calls"] >= actor_count
        and treatment["peer_sessions_completed"] >= actor_count
    )
    condition_semantics = {
        CoordinationCondition.SOLO: (
            treatment["native_task_calls"] == 0
            and treatment["peer_publish_calls"] == 0
            and treatment["peer_list_recent_calls"] == 0
        ),
        CoordinationCondition.NATIVE_MULTIAGENT: native_complete,
        CoordinationCondition.PEER_ISOLATED: (
            peer_complete
            and collaboration_evidence["visibility"] == "actor_private"
            and collaboration_evidence["entry_count"] == actor_count
            and collaboration_evidence["cross_actor_read_count"] == 0
        ),
        CoordinationCondition.PEER_COLLAB: (
            peer_complete
            and collaboration_evidence["visibility"] == "organisation_shared"
            and collaboration_evidence["entry_count"] == actor_count
            and collaboration_evidence["cross_actor_read_count"] > 0
        ),
    }
    if not condition_semantics[condition]:
        raise RuntimeError("adapter rehearsal treatment semantics differ")
    return AdapterRehearsalResult(
        run_id=run_id,
        condition=condition,
        audit_path=audit_path.resolve(),
        audit_digest=expected_digest,
        synthetic_model_calls=audit["synthetic_model_calls"],
        charged_usd_nanos=audit["charged_usd_nanos"],
    )


def verify_solo_adapter_rehearsal(
    audit_path: Path,
    *,
    expected_digest: str,
    campaign_path: Path,
    repository_root: Path | None = None,
) -> AdapterRehearsalResult:
    """Compatibility wrapper for the original solo audit verifier."""

    return verify_adapter_condition_rehearsal(
        audit_path,
        expected_digest=expected_digest,
        campaign_path=campaign_path,
        expected_condition=CoordinationCondition.SOLO,
        repository_root=repository_root,
    )


def _collaboration_evidence(
    backend: SqliteCollaborationBackend,
    run_id: str,
    condition: CoordinationCondition,
) -> dict[str, Any]:
    if condition not in {
        CoordinationCondition.PEER_ISOLATED,
        CoordinationCondition.PEER_COLLAB,
    }:
        return {
            "enabled": False,
            "visibility": "none",
            "entry_count": 0,
            "recent_read_count": 0,
            "cross_actor_read_count": 0,
            "export_digest": None,
        }
    visibility = (
        CollaborationVisibility.ACTOR_PRIVATE
        if condition is CoordinationCondition.PEER_ISOLATED
        else CollaborationVisibility.ORGANISATION_SHARED
    )
    export = backend.export(backend.provision(run_id, visibility))
    owners = {entry.entry_id: entry.actor_id for entry in export.entries}
    reads = [
        event for event in export.audit_events if event["kind"] == "recent.read"
    ]
    cross_actor_reads = sum(
        owners[entry_id] != event["actor_id"]
        for event in reads
        for entry_id in event["details"]["entry_ids"]
    )
    return {
        "enabled": True,
        "visibility": visibility.value,
        "entry_count": len(export.entries),
        "recent_read_count": len(reads),
        "cross_actor_read_count": cross_actor_reads,
        "export_digest": digest_value(
            {
                "scope": export.scope,
                "entries": export.entries,
                "audit_events": export.audit_events,
            }
        ),
    }


def _treatment_evidence(
    condition: CoordinationCondition,
    actor_count: int,
    requests: tuple[dict[str, Any], ...],
    tool_calls: tuple[str, ...],
    collaboration: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    offered = [_offered_tools(request) for request in requests]
    task_offered = sum("task" in names for names in offered)
    peer_offered = sum("peer_publish" in names for names in offered)
    completed_tools, native_child_calls = _runtime_tool_evidence(snapshot, actor_count)
    if Counter(completed_tools) != Counter(tool_calls):
        raise RuntimeError("model tool calls differ from completed runtime tools")
    native_task_calls = tool_calls.count("task")
    peer_publish_calls = tool_calls.count("peer_publish")
    peer_list_calls = tool_calls.count("peer_list_recent")
    peer_sessions_completed = min(peer_publish_calls, peer_list_calls)
    if condition is CoordinationCondition.SOLO:
        complete = bool(requests) and task_offered == 0 and peer_offered == 0
    elif condition is CoordinationCondition.NATIVE_MULTIAGENT:
        complete = (
            native_task_calls >= 1
            and native_child_calls >= 1
            and peer_offered == 0
        )
    elif condition is CoordinationCondition.PEER_ISOLATED:
        complete = (
            peer_sessions_completed >= actor_count
            and collaboration["entry_count"] == actor_count
            and collaboration["cross_actor_read_count"] == 0
        )
    else:
        complete = (
            peer_sessions_completed >= actor_count
            and collaboration["entry_count"] == actor_count
            and collaboration["cross_actor_read_count"] > 0
        )
    return {
        "complete": complete,
        "task_offered_request_count": task_offered,
        "native_task_calls": native_task_calls,
        "native_child_model_calls": native_child_calls,
        "peer_tool_offered_request_count": peer_offered,
        "peer_publish_calls": peer_publish_calls,
        "peer_list_recent_calls": peer_list_calls,
        "peer_sessions_completed": peer_sessions_completed,
    }


def _receipt_tool_calls(snapshot: BudgetSnapshot) -> tuple[str, ...]:
    """Read tool names from the synthetic streams already verified by the budget gate."""
    calls: list[str] = []
    for charge in snapshot.charges:
        names: dict[tuple[int, int], str] = {}
        for line in charge.usage.raw_receipt.decode("utf-8").splitlines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[6:])
            for choice in chunk.get("choices", []):
                for call in choice.get("delta", {}).get("tool_calls", []):
                    key = (choice["index"], call["index"])
                    names[key] = names.get(key, "") + call.get("function", {}).get("name", "")
        if any(not name for name in names.values()):
            raise RuntimeError("synthetic tool receipt is incomplete")
        calls.extend(names.values())
    return tuple(calls)


def _runtime_tool_evidence(
    snapshot: dict[str, Any], actor_count: int
) -> tuple[tuple[str, ...], int]:
    """Reconstruct completed calls and child responses from retained session messages."""
    actors = snapshot["payload"]["sessions"]
    if len(actors) != actor_count:
        raise RuntimeError("runtime treatment actor count differs")
    tools: list[str] = []
    child_responses = 0
    seen: set[str] = set()
    for actor in actors:
        checkpoint = actor["checkpoint"]
        if checkpoint.get("complete") is not True:
            raise RuntimeError("runtime treatment checkpoint is incomplete")
        sessions = checkpoint["reconciliation"]["sessions"]
        ids = {session["session_id"] for session in sessions}
        if actor["session_id"] not in ids or len(ids) != len(sessions) or seen & ids:
            raise RuntimeError("runtime treatment session identities differ")
        seen.update(ids)
        for session in sessions:
            if session["parent_id"] is not None and session["parent_id"] not in ids:
                raise RuntimeError("runtime treatment child parent is missing")
            messages_json = session["messages_json"]
            messages = json.loads(messages_json)
            if (
                digest_bytes(messages_json.encode("utf-8")) != session["messages_digest"]
                or len(messages) != session["message_count"]
                or [message["info"]["id"] for message in messages] != session["message_ids"]
            ):
                raise RuntimeError("runtime treatment message evidence differs")
            for message in messages:
                if message["info"]["role"] != "assistant":
                    continue
                if session["parent_id"] is not None:
                    child_responses += 1
                for part in message["parts"]:
                    if part["type"] == "tool":
                        if part["state"]["status"] != "completed":
                            raise RuntimeError("runtime treatment tool did not complete")
                        tools.append(part["tool"])
    return tuple(tools), child_responses


def _offered_tools(request: dict[str, Any]) -> set[str]:
    return {
        str(tool["function"]["name"])
        for tool in request.get("tools", [])
        if isinstance(tool, dict)
        and isinstance(tool.get("function"), dict)
        and isinstance(tool["function"].get("name"), str)
    }


def _tool_result_count(request: dict[str, Any]) -> int:
    messages = request.get("messages", [])
    if not isinstance(messages, list):
        return 0
    last_user = max(
        (
            index
            for index, message in enumerate(messages)
            if isinstance(message, dict) and message.get("role") == "user"
        ),
        default=-1,
    )
    return sum(
        isinstance(message, dict) and message.get("role") == "tool"
        for message in messages[last_user + 1 :]
    )


def _request_user_text(request: dict[str, Any]) -> str:
    messages = request.get("messages", [])
    if not isinstance(messages, list):
        return ""
    values: list[str] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            values.append(content)
        elif isinstance(content, list):
            values.extend(
                str(part["text"])
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
    return "\n".join(values)


def _write_once(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        written = 0
        while written < len(content):
            count = os.write(descriptor, content[written:])
            if count == 0:
                raise OSError("write made no progress")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
