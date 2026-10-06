"""Shared solo/peer pilot lifecycle beneath explicit execution authority."""

import re
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .adapters.darwin_sandbox import DarwinSandboxExec
from .adapters.fake_harness import FakeHarnessRuntime
from .adapters.local_events import LocalEventSink
from .adapters.opencode_harness import OpenCodeHarnessRuntime, OpenCodeRuntimeProfile
from .adapters.oci_sandbox import OciSandboxExec, OciSandboxProfile
from .adapters.provider_receipts import OpenRouterReceiptVerifier
from .adapters.sqlite_budget import SqliteBudgetAccount
from .adapters.sqlite_delivery import SqliteDeliveryOutbox
from .adapters.sqlite_collaboration import SqliteCollaborationBackend
from .budget import ActorBudgetAllocation, BudgetPlan
from .candidate_gateway import CandidateToolGateway
from .candidate_rehearsal import _CandidateModel
from .candidate_services import create_candidate_services, create_solo_candidate_services
from .campaigns.model_serving import ModelServingCampaign
from .canonical import canonical_json_bytes, digest_bytes, digest_value, parse_json
from .controller import CampaignController
from .domain import AgentIdentity, CampaignStatus, CoordinationCondition, OrganisationSpec
from .collaboration import CollaborationVisibility
from .evaluation import ActorComputeAllocation, ComputePlan, EvaluationScope, SubmissionPolicy
from .model_gateway import ModelBudgetGateway, ModelGatewayProfile
from .peer_tool import PeerToolGateway, PeerToolIntegrationProfile
from .session_identity import SessionIdentityRegistry
from .pilot_evidence import retain_bytes, retain_document
from .sandbox import SandboxProfile
from .solo_pilot_materials import materialize_solo_pilot, materialize_peer_pilot
from .solo_pilot_runner import CandidatePilotRunner
from .solo_pilot_stack import build_no_spend_stack


class PilotAborted(RuntimeError):
    pass


@dataclass(frozen=True)
class PilotRuntimeDependencies:
    """Runtime-only wiring; it cannot replace model or compute dependencies."""

    harness: Callable
    gateway_options: Callable
    sandbox_evidence: dict
    source_profile_digest: str


def make_opencode_runtime_dependencies(runtime_profile, sandbox_profile, process_sandbox, *, timeout_seconds):
    """Use the same sandbox and broker wiring for synthetic and live transports."""
    is_oci = isinstance(sandbox_profile, OciSandboxProfile)
    actual_source = (process_sandbox.source_profile_digest if isinstance(process_sandbox, OciSandboxExec)
                     else process_sandbox.profile_digest)
    if actual_source != sandbox_profile.resolved_digest:
        raise ValueError("runtime sandbox source differs from the configuration")

    def harness(root, services, gateway, candidate_gateway, *, peer_profile=None, peer_gateway=None):
        return OpenCodeHarnessRuntime(runtime_profile, root / "runtime", gateway,
            process_sandbox=process_sandbox, candidate_gateway=candidate_gateway,
            peer_profile=peer_profile, peer_gateway=peer_gateway,
            timeout_seconds=timeout_seconds)

    def gateway_options(root):
        if not is_oci:
            return {}, {}
        return tuple({"serve_http": False, "unix_socket_root": root / "brokers" / label,
                      "advertised_endpoint": getattr(sandbox_profile, f"container_{label}_endpoint")}
                     for label in ("model", "candidate"))

    return PilotRuntimeDependencies(harness, gateway_options, dict(process_sandbox.evidence()),
                                    sandbox_profile.resolved_digest)


@dataclass(frozen=True)
class LivePilotDependencies:
    """Host-owned dependencies; constructing these is not spend authorization."""

    build_stack: Callable
    upstream: Callable
    authorize: Callable
    cleanup: Callable
    harness: Callable
    model_limit_usd_nanos: int
    public_seconds: int
    gateway_options: Callable | None = None
    sandbox_evidence: dict | None = None
    spend_guard: object | None = None
    operator_authorization: dict | None = None

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in (self.model_limit_usd_nanos, self.public_seconds)):
            raise ValueError("live pilot model and compute limits must be positive integers")
        if any(not callable(value) for value in (self.build_stack, self.upstream, self.authorize, self.cleanup, self.harness)):
            raise ValueError("live pilot requires explicit host factories and authorization")
        if self.gateway_options is not None and not callable(self.gateway_options):
            raise ValueError("live gateway options require a host factory")


class _SyntheticCandidateHarness(FakeHarnessRuntime):
    """No-process option for exercising the same command in ordinary unit tests."""

    def __init__(self, services, candidate, *, collaboration=None, peer_sessions=None, peer_scope=None):
        super().__init__()
        self.services, self.candidate = services, candidate
        self.transports, self.receipts = {}, {}
        self.collaboration, self.peer_sessions, self.peer_scope = collaboration, peer_sessions, peer_scope
        self.peer_transports = {}

    def create_primary(self, organisation, actor):
        session = super().create_primary(organisation, actor)
        self.transports[session] = self.services.sessions.bind(actor, session)
        if self.peer_sessions is not None:
            self.peer_transports[session] = self.peer_sessions.bind(actor, session)
        return session

    def deliver(self, session, job):
        transport = self.transports[session]
        if job.job_id == "optimize-serving":
            if self.collaboration is not None:
                self.collaboration.publish(self.peer_scope, self.peer_transports[session],
                    "finding", "synthetic candidate finding", None)
            result = self.services.tools.call(transport, "submit", {"candidate": self.candidate, "idempotency_key": "candidate-1"})
            self.receipts[session] = result["receipt"]
            self.services.tools.call(transport, "evaluate", {"receipt": result["receipt"]})
        else:
            if self.collaboration is not None:
                self.collaboration.list_recent(self.peer_scope, self.peer_transports[session])
            self.services.tools.call(transport, "result", {"receipt": self.receipts[session]})
        return super().deliver(session, job)


def run_solo_pilot(config_path: Path, state_root: Path, run_id: str, *,
                   runtime_dependencies: PilotRuntimeDependencies | None = None,
                   _peer_configuration: bool = False) -> dict:
    repository = Path(__file__).resolve().parents[2]
    config = parse_json(config_path.read_text())
    if isinstance(config, dict) and config.get("execution_mode") == "live":
        # Deliberately closed until run-bound dollar and deployment authority
        # can be verified. Testable live composition lives below this boundary.
        raise ValueError("live pilot execution is disabled pending deployment and budget approval; use --check")
    expected = {"schema_version", "execution_mode", "runtime", "campaign", "gateway_profile",
        "runtime_profile", "sandbox_profile", "synthetic_candidate", "synthetic_candidate_public_ppm",
        "synthetic_model_limit_usd_nanos", "task_seed"}
    schema = "solo-pilot-command/v1"
    condition, organisation_size = CoordinationCondition.SOLO, 1
    if _peer_configuration:
        expected.update({"condition", "organisation_size", "public_compute_seconds", "peer_tool_profile"})
        schema = "peer-pilot-command/v1"
    if not isinstance(config, dict) or set(config) != expected or config["schema_version"] != schema:
        raise ValueError("pilot configuration fields differ")
    if _peer_configuration:
        condition = CoordinationCondition(config["condition"])
        if condition not in {CoordinationCondition.PEER_ISOLATED, CoordinationCondition.PEER_COLLAB}:
            raise ValueError("peer pilot accepts only the two peer conditions")
        organisation_size = config["organisation_size"]
        if type(organisation_size) is not int or not 2 <= organisation_size <= 8:
            raise ValueError("bounded peer pilot requires two to eight actors")
        for field in ("synthetic_model_limit_usd_nanos", "public_compute_seconds"):
            limit = config[field]
            if type(limit) is not int or limit < organisation_size or limit % organisation_size:
                raise ValueError("peer pilot organisation allowances must partition equally")
    if config["execution_mode"] != "no_spend":
        raise ValueError("live pilot execution is disabled pending deployment and budget approval")
    if config["runtime"] not in {"opencode", "fake"}:
        raise ValueError("no-spend pilot runtime must be opencode or fake")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", run_id):
        raise ValueError("pilot run ID is invalid")
    for field in ("synthetic_candidate_public_ppm", "synthetic_model_limit_usd_nanos", "task_seed"):
        if type(config[field]) is not int or config[field] < 0:
            raise ValueError(f"pilot {field} must be a nonnegative integer")
    if config["synthetic_model_limit_usd_nanos"] < 1:
        raise ValueError("pilot synthetic model budget must be positive")

    def path(field):
        resolved = (repository / config[field]).resolve(strict=True)
        resolved.relative_to(repository)
        return resolved

    campaign = ModelServingCampaign.load(path("campaign"))
    gateway_profile = ModelGatewayProfile.load(path("gateway_profile"), repository_root=repository)
    runtime_profile = OpenCodeRuntimeProfile.load(path("runtime_profile"), repository_root=repository)
    sandbox_path = path("sandbox_profile")
    sandbox_document = parse_json(sandbox_path.read_text())
    if not isinstance(sandbox_document, dict):
        raise ValueError("sandbox profile must be an object")
    if sandbox_document.get("schema_version") == "oci-process-sandbox-profile/v2":
        sandbox_profile = OciSandboxProfile.load(sandbox_path, repository_root=repository)
        if runtime_dependencies is None:
            raise ValueError("OCI pilot requires explicit host runtime dependencies")
    else:
        sandbox_profile = SandboxProfile.load(sandbox_path)
    if runtime_dependencies is not None:
        if config["runtime"] != "opencode" or runtime_dependencies.source_profile_digest != sandbox_profile.resolved_digest:
            raise ValueError("pilot runtime dependencies differ from the configuration")
    if gateway_profile.status != "conformance_only":
        raise ValueError("no-spend pilot requires a synthetic model gateway profile")
    candidate_bytes = path("synthetic_candidate").read_bytes()
    candidate = parse_json(candidate_bytes.decode())
    campaign.validate_candidate_document(candidate)
    peer_profile = (PeerToolIntegrationProfile.load(path("peer_tool_profile"), repository_root=repository)
                    if _peer_configuration else None)
    return _execute_solo_pilot(config, state_root, run_id, repository, campaign,
        gateway_profile, runtime_profile, sandbox_profile, candidate_bytes,
        runtime_dependencies=runtime_dependencies, condition=condition,
        organisation_size=organisation_size, peer_profile=peer_profile)


def run_peer_pilot(config_path, state_root, run_id, *, runtime_dependencies=None):
    """Exercise the shared pilot lifecycle; paid peer execution stays disabled."""
    return run_solo_pilot(config_path, state_root, run_id,
        runtime_dependencies=runtime_dependencies, _peer_configuration=True)


def _execute_solo_pilot(config, state_root, run_id, repository, campaign,
                        gateway_profile, runtime_profile, sandbox_profile,
                        candidate_bytes=None, *, live: LivePilotDependencies | None = None,
                        runtime_dependencies: PilotRuntimeDependencies | None = None,
                        condition=CoordinationCondition.SOLO, organisation_size=1,
                        peer_profile=None):
    """One lifecycle for both transports, below the operator's authority gate."""
    mode = "live" if live is not None else "no_spend"
    if live is not None and runtime_dependencies is not None:
        raise ValueError("select one runtime dependency source")
    runtime_wiring = live if live is not None else runtime_dependencies
    is_peer = condition in {CoordinationCondition.PEER_ISOLATED, CoordinationCondition.PEER_COLLAB}
    if condition is not CoordinationCondition.SOLO and not is_peer:
        raise ValueError("candidate pilot supports only solo and peer conditions")
    if type(organisation_size) is not int or (not is_peer and organisation_size != 1):
        raise ValueError("candidate pilot actor count differs from its condition")
    if is_peer and (live is not None or peer_profile is None):
        raise ValueError("peer pilot requires a pinned peer profile and no-spend execution")
    public_seconds = (live.public_seconds if live is not None else
                      config["public_compute_seconds"] // organisation_size if is_peer else 60)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", run_id):
        raise ValueError("pilot run ID is invalid")
    candidate = parse_json(candidate_bytes.decode()) if candidate_bytes is not None else None
    root = state_root.resolve() / run_id
    root.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    bindings = {"config": config, "config_digest": digest_value(config),
        "campaign_manifest_digest": campaign.manifest_digest,
        "gateway_profile_digest": gateway_profile.resolved_digest,
        "runtime_profile_digest": runtime_profile.resolved_digest,
        "sandbox_profile_digest": digest_value(sandbox_profile),
        "synthetic_candidate_digest": digest_bytes(candidate_bytes) if candidate_bytes is not None else None,
        "platform_source_digest": digest_value({str(item.relative_to(repository)): digest_bytes(item.read_bytes())
            for item in sorted((repository / "src/agent_collab_evals").rglob("*.py"))}),
        "git_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, check=True, capture_output=True, text=True).stdout.strip(),
        "git_dirty": bool(subprocess.run(["git", "status", "--porcelain"], cwd=repository, check=True, capture_output=True, text=True).stdout)}
    if is_peer:
        bindings["peer_tool_profile_digest"] = peer_profile.resolved_digest
    if runtime_wiring is not None:
        bindings["runtime_sandbox_evidence"] = runtime_wiring.sandbox_evidence
    if live is not None and live.spend_guard is not None:
        bindings["spend_admission"] = live.spend_guard.evidence()
    if live is not None and live.operator_authorization is not None:
        bindings["operator_authorization"] = live.operator_authorization
    config_digest = retain_document(root / "run-config.json", bindings)
    if candidate_bytes is not None:
        retain_bytes(root / "synthetic-input-candidate.json", candidate_bytes)
    gateway = candidate_gateway = peer_gateway = runtime = handle = None
    collaboration = peer_scope = peer_sessions = None
    budget = services = stack = None
    stage = "reference"
    error = None
    audit = {"schema_version": "solo-pilot-audit/v1", "run_id": run_id, "execution_mode": mode,
        "runtime": "opencode" if live is not None else config["runtime"], "scoreable": False,
        "live_execution_authorized": live is not None,
        "actual_spend_usd_nanos": None if live is not None else 0,
        "run_config_digest": config_digest, "failure": None, "status": "aborted"}
    if is_peer:
        audit.update(schema_version="peer-pilot-audit/v1", condition=condition.value,
                     organisation_size=organisation_size)
    if live is None:
        audit.update(external_model_calls=0, external_compute_executions=0)

    def authorize(request):
        if live is not None:
            live.authorize(stack, request, config_digest)
        else:
            stack.inventory.authorize(request, approval_reference=f"no-spend-command:{config_digest}")

    try:
        stage = "runtime_preflight"
        model_options, candidate_options = ({}, {})
        if runtime_wiring is not None and runtime_wiring.gateway_options is not None:
            model_options, candidate_options = runtime_wiring.gateway_options(root)
            for gateway_type, options in ((ModelBudgetGateway, model_options), (CandidateToolGateway, candidate_options)):
                if options.get("unix_socket_root") is not None:
                    gateway_type.unix_socket_path(options["unix_socket_root"])
        if live is not None and live.spend_guard is not None:
            stage = "spend_admission"
            live.spend_guard.begin(run_id, config_digest)
        stage = "reference"
        stack = (live.build_stack(root / "evaluation", run_id) if live is not None else
            build_no_spend_stack(root / "evaluation", campaign, run_id, repository,
                candidate_public_ppm=config["synthetic_candidate_public_ppm"]))
        reference = campaign.reference_candidate_path.read_bytes()
        reference_request = stack.public.prepare_visible_request(reference, None, "visible:reference")
        stack.inventory.register("public", (reference_request,))
        authorize(reference_request)
        reference_receipt = stack.evaluator.visible_evaluate(reference, None, "visible:reference")
        reference_result = stack.evaluator.resolve(reference_receipt, reference, None, EvaluationScope.VISIBLE)
        audit["reference_evidence_digest"] = retain_document(root / "reference-result.json", {
            "receipt": asdict(reference_receipt), "result": asdict(reference_result),
        })
        if not reference_result.eligible or reference_result.failures:
            raise RuntimeError("reference evaluation failed; the agent was not started")
        actors = tuple(AgentIdentity(run_id, ordinal) for ordinal in range(organisation_size))
        compute_plan = ComputePlan("candidate-pilot", run_id, public_seconds * organisation_size,
            tuple(ActorComputeAllocation(run_id, actor.actor_id, public_seconds) for actor in actors),
            stack.hidden_seconds, digest_value({"run_config": config_digest, "actor_seconds": public_seconds,
                                               "actor_count": organisation_size, "hidden_seconds": stack.hidden_seconds}))
        factory = create_candidate_services if is_peer else create_solo_candidate_services
        services = factory(root / "candidate-services", campaign,
            evaluator=stack.evaluator, reference_receipt=reference_receipt, plan=compute_plan, policy=SubmissionPolicy(1, public_seconds))
        runner = CandidatePilotRunner(services, stack.inventory, stack.public_plan, stack.hidden_plan, hidden_seconds=stack.hidden_seconds)
        limit = live.model_limit_usd_nanos if live is not None else config["synthetic_model_limit_usd_nanos"]
        actor_limit = limit // organisation_size
        allocations = tuple(ActorBudgetAllocation(run_id, actor.actor_id, actor_limit) for actor in actors)
        budget_plan = BudgetPlan.create(plan_id=f"{run_id}-model", status="development" if live is not None else "conformance_only", campaign_run_id=run_id,
            organisation_limit_usd_nanos=limit, allocations=allocations, rate_card_digest=digest_value(gateway_profile.rate_card))
        budget = SqliteBudgetAccount(root / "budget.sqlite3", gateway_profile.rate_card, require_metadata_receipts=live is not None,
            budget_plan=budget_plan, receipt_verifier=OpenRouterReceiptVerifier(gateway_profile, require_metadata_receipt=live is not None))
        budget.open_campaign(run_id, limit, allocations)
        retain_document(root / "compute-plan.json", compute_plan)
        retain_document(root / "budget-plan.json", budget_plan)
        if is_peer:
            peer_sessions = SessionIdentityRegistry()
            collaboration = SqliteCollaborationBackend(root / "collaboration.sqlite3", peer_sessions)
            visibility = (CollaborationVisibility.ACTOR_PRIVATE if condition is CoordinationCondition.PEER_ISOLATED
                          else CollaborationVisibility.ORGANISATION_SHARED)
            peer_scope = collaboration.provision(run_id, visibility)
        if live is not None:
            upstream = live.upstream()
        elif is_peer:
            from .peer_pilot_model import PeerCandidateModel
            upstream = PeerCandidateModel(candidate=candidate, model=gateway_profile.expected_returned_model,
                provider=gateway_profile.expected_provider, peer_actor_count=organisation_size)
        else:
            upstream = _CandidateModel(candidate=candidate, model=gateway_profile.expected_returned_model,
                provider=gateway_profile.expected_provider, peer_actor_count=1)
        endpoint = "fake://no-spend"
        peer_options = {}
        if is_peer and isinstance(sandbox_profile, OciSandboxProfile):
            peer_options = {"serve_http": False, "unix_socket_root": root / "brokers" / "peer",
                            "advertised_endpoint": sandbox_profile.container_peer_endpoint}
            PeerToolGateway.unix_socket_path(peer_options["unix_socket_root"])
        if is_peer and (runtime_wiring is not None or config["runtime"] == "opencode"):
            peer_gateway = PeerToolGateway(collaboration, peer_sessions, **peer_options)
        if runtime_wiring is not None:
            gateway = ModelBudgetGateway(gateway_profile, budget, upstream, **model_options)
            candidate_gateway = CandidateToolGateway(services.tools, services.sessions, **candidate_options)
            peer_arguments = {"peer_profile": peer_profile, "peer_gateway": peer_gateway} if is_peer else {}
            runtime = runtime_wiring.harness(root, services, gateway, candidate_gateway, **peer_arguments)
            endpoint = gateway.endpoint
        elif config["runtime"] == "opencode":
            gateway = ModelBudgetGateway(gateway_profile, budget, upstream)
            candidate_gateway = CandidateToolGateway(services.tools, services.sessions)
            runtime = OpenCodeHarnessRuntime(runtime_profile, root / "runtime", gateway,
                process_sandbox=DarwinSandboxExec(sandbox_profile), candidate_gateway=candidate_gateway,
                peer_profile=peer_profile, peer_gateway=peer_gateway, timeout_seconds=90)
            endpoint = gateway.endpoint
        else:
            runtime = _SyntheticCandidateHarness(services, candidate, collaboration=collaboration,
                peer_sessions=peer_sessions, peer_scope=peer_scope)
        controller = CampaignController(runtime, LocalEventSink(root / "events"), budget, runner, SqliteDeliveryOutbox(root / "delivery.sqlite3"))
        stage = "agent_job"
        handle = controller.start(OrganisationSpec(run_id, condition, organisation_size, root / "workspace", endpoint))
        materializer = materialize_peer_pilot if is_peer else materialize_solo_pilot
        material = materializer(campaign, config["task_seed"], reference_result=reference_result,
            model_limit_usd_nanos=actor_limit, public_compute_seconds=public_seconds,
            **({"organisation_size": organisation_size} if is_peer else {}))
        retain_document(root / "task.json", material)
        for job in material.jobs:
            controller.deliver(handle, job)
        stage = "public_evaluation"
        for request in runner.prepare_public():
            authorize(request)
        feedback = runner.collect_public()
        stage = "public_feedback"
        controller.deliver(handle, feedback)
        stage = "hidden_evaluation"
        hidden_requests = runner.prepare_hidden()
        inventory_digest = stack.inventory.seal()
        retain_document(root / "compute-inventory-seal.json", {"inventory_digest": inventory_digest})
        for request in hidden_requests:
            authorize(request)
        hidden_result = runner.collect_hidden()
        submissions = services.submissions.close(run_id, "optimize-serving")
        selection = services.submissions.select(submissions)
        prepared = services.submissions.prepare_hidden_evaluation(selection.receipt, reserved_seconds=stack.hidden_seconds)
        selected_digest = retain_bytes(root / "selected-candidate.json", prepared.candidate)
        audit.update(selected_artifact_digest=selected_digest, used_default=selection.used_default,
            inventory_digest=inventory_digest)
        retain_document(root / "selection.json", selection)
        retain_document(root / "hidden-result.json", {"execution_mode": mode, "result": hidden_result})
        stage = "closure"
        closed = controller.close(handle, f"{mode} candidate pilot complete")
        compute_receipts = runner.reconcile(run_id)
        snapshot = services.compute.snapshot(run_id)
        artifacts = {"selection.json": selection, "public-submissions.json": submissions,
            "hidden-result.json": {"execution_mode": mode, "result": hidden_result}, "compute-receipts.json": compute_receipts,
            "compute-snapshot.json": snapshot, "runtime-snapshot.json": closed.final_harness_snapshot,
            "model-budget-snapshot.json": _retain_budget_snapshot(root, budget.snapshot(run_id))}
        if is_peer:
            exported = collaboration.export(peer_scope)
            artifacts["collaboration-snapshot.json"] = exported
            owners = {entry.entry_id: entry.actor_id for entry in exported.entries}
            reads = [event for event in exported.audit_events if event["kind"] == "recent.read"]
            audit.update(collaboration_visibility=peer_scope.visibility.value,
                peer_entry_count=len(exported.entries), peer_read_count=len(reads),
                cross_actor_read_count=sum(owners[entry_id] != event["actor_id"]
                    for event in reads for entry_id in event["details"]["entry_ids"]))
        if live is None:
            artifacts["model-requests.json"] = [item.decode() for item in upstream.raw_requests]
        evidence_digests = {name: retain_document(root / name, value) for name, value in artifacts.items()}
        storage_seal = services.storage.seal(run_id, {"selection_digest": selection.selection_digest, "inventory_digest": inventory_digest})
        audit.update(status="complete", selected_artifact_digest=selected_digest, used_default=selection.used_default,
            evidence_digests=evidence_digests, inventory_digest=inventory_digest,
            storage_seal_digest=storage_seal.seal_digest, hidden_result=asdict(hidden_result),
            public_result=asdict(selection.result), budget_reconciliation=budget.reconcile(run_id).evidence())
        if live is None:
            audit.update(synthetic_compute_executions=len(compute_receipts),
                synthetic_compute_seconds=sum(item.used_seconds for item in compute_receipts), synthetic_model_calls=len(upstream.requests))
        else:
            audit.update(external_compute_executions=len(compute_receipts),
                measured_compute_seconds=sum(item.used_seconds for item in compute_receipts),
                model_charged_usd_nanos=budget.snapshot(run_id).organisation_charged_usd_nanos,
                billing_status="provider_compute_billing_unreconciled")
    except BaseException as caught:
        error = caught
        audit["failure"] = {"type": type(caught).__name__, "stage": stage}
    finally:
        try:
            if runtime is not None and handle is not None and handle.status is CampaignStatus.ACTIVE:
                runtime.stop(handle.organisation, "candidate pilot aborted")
        except BaseException as caught:
            error = error or caught
            audit["cleanup_failure"] = type(caught).__name__
        finally:
            for service in (peer_gateway, candidate_gateway, gateway):
                if service is not None:
                    try:
                        service.close()
                    except BaseException as caught:
                        error = error or caught
                        audit["cleanup_failure"] = type(caught).__name__
    if live is not None and live.spend_guard is not None:
        try:
            audit["spend_admission"] = live.spend_guard.snapshot()
        except Exception as caught:
            error = error or caught
            audit["spend_admission_failure"] = type(caught).__name__
    if error is not None:
        audit["status"] = "aborted"
        if live is not None and stack is not None:
            try:
                audit["remote_cleanup"] = live.cleanup(stack)
            except BaseException as caught:
                audit["remote_cleanup"] = {"status": "cleanup_failed", "error_type": type(caught).__name__}
        # Keep partial accounting on abort, without presenting it as valid closure.
        for name, resolve in (
            ("budget_reconciliation", lambda: budget.reconcile(run_id).evidence()),
            ("partial_compute_snapshot", lambda: asdict(services.compute.snapshot(run_id))),
        ):
            try:
                if (name == "budget_reconciliation" and budget is not None) or (name != "budget_reconciliation" and services is not None):
                    audit[name] = resolve()
            except Exception as caught:
                audit[name + "_failure"] = type(caught).__name__
    audit["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    audit_digest = retain_document(root / "audit.json", audit)
    if error is not None:
        raise PilotAborted(f"Candidate pilot aborted ({type(error).__name__}); evidence: {root / 'audit.json'}") from error
    return {"ok": True, "run_id": run_id, "status": "complete", "execution_mode": mode,
        "scoreable": False, "audit_path": str(root / "audit.json"), "audit_digest": audit_digest,
        "selected_candidate_path": str(root / "selected-candidate.json"),
        "compute_executions": len(compute_receipts), "actual_spend_usd_nanos": audit["actual_spend_usd_nanos"],
        **({"synthetic_compute_executions": audit["synthetic_compute_executions"]} if live is None else {})}


def _retain_budget_snapshot(root, snapshot):
    """Keep raw provider bytes intact and put digest references in JSON."""
    document = asdict(snapshot)
    for charge in document["charges"]:
        for field in ("raw_receipt", "raw_metadata_receipt"):
            content = charge["usage"].pop(field)
            digest = digest_bytes(content)
            retain_bytes(root / "provider-receipts" / digest[7:], content)
            charge["usage"][field + "_digest"] = digest
    return document
