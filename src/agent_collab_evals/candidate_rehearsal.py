"""Real solo agent-to-candidate wiring with synthetic model and evaluator only."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .adapters.darwin_sandbox import DarwinSandboxExec
from .adapters.deterministic_model import DeterministicToolModelUpstream
from .adapters.fake_serving_evaluator import FakeModelServingEvaluator
from .adapters.local_artifact_storage import LocalArtifactStorage
from .adapters.local_events import LocalEventSink
from .adapters.local_snapshots import LocalCampaignSnapshotStore
from .adapters.opencode_harness import OpenCodeHarnessRuntime, OpenCodeRuntimeProfile
from .adapters.provider_receipts import OpenRouterReceiptVerifier
from .adapters.sqlite_budget import SqliteBudgetAccount
from .adapters.sqlite_compute import SqliteComputeBroker
from .adapters.sqlite_delivery import SqliteDeliveryOutbox
from .adapters.sqlite_submissions import SqliteSubmissionRegistry
from .artifacts import ArtifactStoragePolicy
from .budget import ActorBudgetAllocation, BudgetPlan
from .candidate_gateway import CandidateToolGateway
from .candidate_tools import CandidateTools
from .campaigns.model_serving import ModelServingCampaign
from .canonical import canonical_json_bytes, digest_bytes, digest_value
from .controller import CampaignController
from .domain import AgentIdentity, CoordinationCondition, Job, OrganisationSpec, SessionHandle
from .evaluation import ActorComputeAllocation, ComputePlan, SubmissionPolicy
from .model_gateway import ModelBudgetGateway, ModelGatewayProfile
from .sandbox import SandboxProfile
from .service_identity import ServiceIdentityRegistry
from .session_identity import SessionIdentityRegistry


@dataclass
class CandidateServices:
    sessions: SessionIdentityRegistry
    storage: LocalArtifactStorage
    compute: SqliteComputeBroker
    evaluator: FakeModelServingEvaluator
    submissions: SqliteSubmissionRegistry
    tools: CandidateTools
    plan: ComputePlan


def create_synthetic_candidate_services(root: Path, campaign: ModelServingCampaign, run_id: str) -> CandidateServices:
    sessions = SessionIdentityRegistry()
    services = ServiceIdentityRegistry()
    service = services.bind("submission_registry")
    actor = AgentIdentity(run_id, 0)
    storage = LocalArtifactStorage(
        root / "artifacts", sessions, services,
        ArtifactStoragePolicy(32768, 131072, 131072),
        {"submission_registry": frozenset({"candidate_lifecycle", "hidden_evaluation"})},
    )
    storage.open_campaign(run_id, (actor.actor_id,))
    plan = ComputePlan(
        "synthetic-solo-candidate-v1", run_id, 60,
        (ActorComputeAllocation(run_id, actor.actor_id, 60),), 60,
        digest_value({"mode": "synthetic", "run_id": run_id, "actor_seconds": 60, "hidden_seconds": 60}),
    )
    compute = SqliteComputeBroker(root / "compute.sqlite3", sessions, services, plan, hidden_evaluator_service="submission_registry")
    evaluator = FakeModelServingEvaluator(
        root / "evaluator.sqlite3", campaign,
        {"stock-vllm-0.21.0": 1000000, "vllm-0.21.0-stream-interval-10": 1100000},
        {"stock-vllm-0.21.0": 1000000, "vllm-0.21.0-stream-interval-10": 1050000},
    )
    submissions = SqliteSubmissionRegistry(root / "submissions.sqlite3", sessions, storage, compute, evaluator, service)
    bootstrap = sessions.bind(actor, SessionHandle(f"{run_id}-reference-bootstrap"))
    try:
        reference = (campaign.root / "reference/candidate.json").read_bytes()
        artifact = storage.put(bootstrap, reference, "application/json", idempotency_key="reference:optimize-serving")
        reference_receipt = evaluator.visible_evaluate(reference, None, f"reference:{run_id}")
        submissions.initialize(run_id, "optimize-serving", (actor.actor_id,), SubmissionPolicy(1, 60), artifact.ref, reference_receipt)
    finally:
        sessions.revoke(bootstrap)
    tools = CandidateTools(
        sessions, storage, submissions, campaign.validate_candidate_document,
        campaign_run_id=run_id, job_id="optimize-serving",
        candidate_policy_digest=campaign.manifest_digest,
    )
    return CandidateServices(sessions, storage, compute, evaluator, submissions, tools, plan)


class _CandidateModel(DeterministicToolModelUpstream):
    def __init__(self, *, candidate: dict, **kwargs):
        super().__init__(**kwargs)
        self.candidate = candidate

    def stream(self, request: bytes):
        value = json.loads(request)
        with self._condition:
            self._requests.append(value)
            self._raw_requests.append(request)
            ordinal = len(self._requests)
        request_id = f"local-candidate-{ordinal:04d}"
        messages = value["messages"]
        last_user = max(index for index, message in enumerate(messages) if message["role"] == "user")
        results = [message for message in messages[last_user + 1:] if message["role"] == "tool"]
        read_phase = "read-candidate-result" in json.dumps(messages[last_user])
        if not read_phase and not results:
            return self._tool_stream(request_id, "candidate_submit", {"candidate": self.candidate, "idempotency_key": "candidate-1"})
        if (not read_phase and len(results) == 1) or (read_phase and not results):
            receipts = re.findall(r"candidate-[0-9a-f]{32}", json.dumps(messages))
            if not receipts:
                raise RuntimeError("candidate tool did not return an admission receipt")
            return self._tool_stream(request_id, "candidate_result" if read_phase else "candidate_evaluate", {"receipt": receipts[-1]})
        return self._text_stream(request_id, "CANDIDATE_REHEARSAL_OK")


class _SyntheticComputeGate:
    """Close-time gate for simulated reservations, not a no-compute assertion."""

    def __init__(self, services: CandidateServices):
        self.services = services

    def reconcile(self, run_id: str):
        if type(self.services.evaluator) is not FakeModelServingEvaluator:
            raise RuntimeError("synthetic gate cannot authorize an external evaluator")
        snapshot = self.services.compute.snapshot(run_id)
        if any(reservation.status.value != "complete" for reservation in snapshot.reservations):
            raise RuntimeError("synthetic compute has incomplete reservations")
        return ()  # No external compute executions; simulated accounting is retained separately.


def run_candidate_rehearsal(
    campaign_path: Path, state_root: Path, run_id: str, *, restart_runtime: bool = False,
) -> dict[str, object]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", run_id):
        raise ValueError("candidate rehearsal run ID is invalid")
    root = state_root.resolve() / run_id
    root.mkdir(parents=True, exist_ok=False)
    repository = Path(__file__).resolve().parents[2]
    campaign = ModelServingCampaign.load(campaign_path.resolve())
    services = create_synthetic_candidate_services(root, campaign, run_id)
    gateway_profile = ModelGatewayProfile.load(
        repository / "config/gateway_profiles/openrouter-deepinfra-local-conformance-v0.json",
        repository_root=repository,
    )
    if gateway_profile.status != "conformance_only":
        raise RuntimeError("candidate rehearsal requires a synthetic provider profile")
    actor = AgentIdentity(run_id, 0)
    budget_document = {
        "schema_version": "budget-plan/v1", "plan_id": f"{run_id}-synthetic-budget",
        "status": "conformance_only", "campaign_run_id": run_id,
        "organisation_limit_usd_nanos": 1000000000,
        "allocations": [{"actor_id": actor.actor_id, "limit_usd_nanos": 1000000000}],
        "rate_card_digest": digest_value(gateway_profile.rate_card),
    }
    _retain(root / "budget-plan.json", budget_document)
    plan = BudgetPlan.load(root / "budget-plan.json", expected_digest=digest_value(budget_document))
    account = SqliteBudgetAccount(
        root / "budget.sqlite3", gateway_profile.rate_card, require_metadata_receipts=False,
        budget_plan=plan, receipt_verifier=OpenRouterReceiptVerifier(gateway_profile, require_metadata_receipt=False),
    )
    account.open_campaign(run_id, 1000000000, (ActorBudgetAllocation(run_id, actor.actor_id, 1000000000),))
    candidate = json.loads((campaign.root / "candidates/vllm-stream-interval-10.json").read_bytes())
    upstream = _CandidateModel(
        candidate=candidate, model=gateway_profile.expected_returned_model,
        provider=gateway_profile.expected_provider, peer_actor_count=1,
    )
    gateway = ModelBudgetGateway(gateway_profile, account, upstream)
    candidate_gateway = None
    runtime = None
    handle = None
    restart_evidence = None
    try:
        candidate_gateway = CandidateToolGateway(services.tools, services.sessions)
        runtime = OpenCodeHarnessRuntime(
            OpenCodeRuntimeProfile.load(repository / "config/runtime_profiles/opencode-deepseek-v4-flash-development.json"),
            root / "runtime", gateway,
            process_sandbox=DarwinSandboxExec(SandboxProfile.load(repository / "config/sandbox_profiles/darwin-loopback-network-v0.json")),
            candidate_gateway=candidate_gateway, timeout_seconds=90,
        )
        controller = CampaignController(runtime, LocalEventSink(root / "events"), account, _SyntheticComputeGate(services), SqliteDeliveryOutbox(root / "delivery.sqlite3"))
        handle = controller.start(OrganisationSpec(run_id, CoordinationCondition.SOLO, 1, root / "workspace", gateway.endpoint))
        material = campaign.materialize(1729)
        for job in material.jobs:
            controller.deliver(handle, job)
        if restart_runtime:
            store = LocalCampaignSnapshotStore(root / "snapshots")
            checkpoint = controller.snapshot(handle)
            store.save(checkpoint)
            original_session = handle.sessions[0]
            original_access = runtime._session(original_session).candidate_access
            original_compute = services.compute.snapshot(run_id)
            runtime.suspend(handle.organisation)
            handle = None
            candidate_gateway.close()
            # Reconstruct services from their durable stores, not the old objects.
            services = create_synthetic_candidate_services(root, campaign, run_id)
            candidate_gateway = CandidateToolGateway(services.tools, services.sessions)
            runtime = OpenCodeHarnessRuntime(
                OpenCodeRuntimeProfile.load(repository / "config/runtime_profiles/opencode-deepseek-v4-flash-development.json"),
                root / "runtime", gateway,
                process_sandbox=DarwinSandboxExec(SandboxProfile.load(repository / "config/sandbox_profiles/darwin-loopback-network-v0.json")),
                candidate_gateway=candidate_gateway, timeout_seconds=90,
            )
            controller = CampaignController(runtime, LocalEventSink(root / "events"), account, _SyntheticComputeGate(services), SqliteDeliveryOutbox(root / "delivery.sqlite3"))
            retained_checkpoint = store.load(run_id)
            handle = controller.resume(retained_checkpoint)
            resumed_access = runtime._session(handle.sessions[0]).candidate_access
            # Replay acknowledged delivery: neither tools nor model should run again.
            requests_before_replay = len(upstream.requests)
            for job in material.jobs:
                controller.deliver(handle, job)
            restart_evidence = {
                "checkpoint_digest": digest_value(retained_checkpoint),
                "same_session": handle.sessions == (original_session,),
                "capability_rotated": original_access.token_id != resumed_access.token_id,
                "original_token_id": original_access.token_id,
                "resumed_token_id": resumed_access.token_id,
                "replayed_model_calls": len(upstream.requests) - requests_before_replay,
                "compute_unchanged": original_compute == services.compute.snapshot(run_id),
                "model_gateway_restarted": False,
            }
            if not (
                restart_evidence["same_session"] and restart_evidence["capability_rotated"]
                and restart_evidence["compute_unchanged"] and restart_evidence["replayed_model_calls"] == 0
            ):
                raise RuntimeError("candidate restart did not preserve the durable lifecycle")
        # Fixed solo release boundary: after the first mission finishes. Agent
        # calls cannot release results or choose this boundary.
        services.compute.release_visible_results(run_id, actor.actor_id)
        controller.deliver(handle, Job("read-candidate-result", "Read your released candidate result.", digest_value({"phase": "public-result-v1"}), {}))
        submissions = services.submissions.close(run_id, "optimize-serving")
        selection = services.submissions.select(submissions)
        services.submissions.evaluate_hidden(selection.receipt, reserved_seconds=60)
        result = controller.close(handle, "synthetic candidate wiring complete")
        compute_snapshot = services.compute.snapshot(run_id)
        _retain(root / "compute-plan.json", asdict(services.plan))
        _retain(root / "compute-snapshot.json", asdict(compute_snapshot))
        _retain(root / "runtime-snapshot.json", asdict(result.final_harness_snapshot))
        _retain(root / "model-requests.json", [request.decode() for request in upstream.raw_requests])
        seal = services.storage.seal(run_id, {"selection_digest": selection.selection_digest, "compute_plan_digest": services.plan.source_digest})
        audit = {
            "schema_version": "solo-candidate-rehearsal/v2", "scoreable": False,
            "run_id": run_id, "external_model_calls": 0, "external_compute_executions": 0,
            "evaluation_mode": "synthetic", "synthetic_model_calls": len(upstream.requests),
            "tools_called": list(upstream.tool_calls), "used_default": selection.used_default,
            "selection_receipt": selection.receipt.value, "selection_digest": selection.selection_digest,
            "storage_seal_digest": seal.seal_digest,
            "budget_reconciliation": account.reconcile(run_id).evidence(),
            "compute_snapshot_digest": digest_value(compute_snapshot),
            "runtime_snapshot_digest": digest_value(result.final_harness_snapshot),
            "candidate_tool_profile_digest": runtime.capabilities()["candidate_tool_profile_digest"],
            "live_evaluation_authorized": False,
            "restart_evidence": restart_evidence,
        }
        _retain(root / "audit.json", audit)
        return audit
    finally:
        try:
            if runtime is not None and handle is not None and not runtime._organisation(handle.organisation).stopped:
                runtime.stop(handle.organisation, "candidate rehearsal cleanup")
        finally:
            if candidate_gateway is not None:
                candidate_gateway.close()
            gateway.close()


def _retain(path: Path, value: object) -> None:
    with path.open("xb") as stream:
        stream.write(canonical_json_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
