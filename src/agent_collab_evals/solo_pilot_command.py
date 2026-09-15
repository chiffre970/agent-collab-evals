"""Operator entrypoint for a retained, explicitly no-spend solo pilot."""

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
from .budget import ActorBudgetAllocation, BudgetPlan
from .candidate_gateway import CandidateToolGateway
from .candidate_rehearsal import _CandidateModel
from .candidate_services import create_solo_candidate_services
from .campaigns.model_serving import ModelServingCampaign
from .canonical import canonical_json_bytes, digest_bytes, digest_value, parse_json
from .controller import CampaignController
from .domain import AgentIdentity, CampaignStatus, CoordinationCondition, OrganisationSpec
from .evaluation import ActorComputeAllocation, ComputePlan, EvaluationScope, SubmissionPolicy
from .model_gateway import ModelBudgetGateway, ModelGatewayProfile
from .pilot_evidence import retain_bytes, retain_document
from .sandbox import SandboxProfile
from .solo_pilot_materials import materialize_solo_pilot
from .solo_pilot_runner import SoloPilotRunner
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

    def harness(root, services, gateway, candidate_gateway):
        return OpenCodeHarnessRuntime(runtime_profile, root / "runtime", gateway,
            process_sandbox=process_sandbox, candidate_gateway=candidate_gateway,
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

    def __init__(self, services, candidate):
        super().__init__()
        self.services, self.candidate = services, candidate
        self.transports, self.receipts = {}, {}

    def create_primary(self, organisation, actor):
        session = super().create_primary(organisation, actor)
        self.transports[session] = self.services.sessions.bind(actor, session)
        return session

    def deliver(self, session, job):
        transport = self.transports[session]
        if job.job_id == "optimize-serving":
            result = self.services.tools.call(transport, "submit", {"candidate": self.candidate, "idempotency_key": "candidate-1"})
            self.receipts[session] = result["receipt"]
            self.services.tools.call(transport, "evaluate", {"receipt": result["receipt"]})
        else:
            self.services.tools.call(transport, "result", {"receipt": self.receipts[session]})
        return super().deliver(session, job)


def run_solo_pilot(config_path: Path, state_root: Path, run_id: str, *,
                   runtime_dependencies: PilotRuntimeDependencies | None = None) -> dict:
    repository = Path(__file__).resolve().parents[2]
    config = parse_json(config_path.read_text())
    if isinstance(config, dict) and config.get("execution_mode") == "live":
        # Deliberately closed until run-bound dollar and deployment authority
        # can be verified. Testable live composition lives below this boundary.
        raise ValueError("live pilot execution is disabled pending deployment and budget approval; use --check")
    expected = {"schema_version", "execution_mode", "runtime", "campaign", "gateway_profile",
        "runtime_profile", "sandbox_profile", "synthetic_candidate", "synthetic_candidate_public_ppm",
        "synthetic_model_limit_usd_nanos", "task_seed"}
    if not isinstance(config, dict) or set(config) != expected or config["schema_version"] != "solo-pilot-command/v1":
        raise ValueError("pilot configuration fields differ")
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
    return _execute_solo_pilot(config, state_root, run_id, repository, campaign,
        gateway_profile, runtime_profile, sandbox_profile, candidate_bytes,
        runtime_dependencies=runtime_dependencies)


def _execute_solo_pilot(config, state_root, run_id, repository, campaign,
                        gateway_profile, runtime_profile, sandbox_profile,
                        candidate_bytes=None, *, live: LivePilotDependencies | None = None,
                        runtime_dependencies: PilotRuntimeDependencies | None = None):
    """One lifecycle for both transports, below the operator's authority gate."""
    mode = "live" if live is not None else "no_spend"
    if live is not None and runtime_dependencies is not None:
        raise ValueError("select one runtime dependency source")
    runtime_wiring = live if live is not None else runtime_dependencies
    public_seconds = live.public_seconds if live is not None else 60
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
    if runtime_wiring is not None:
        bindings["runtime_sandbox_evidence"] = runtime_wiring.sandbox_evidence
    if live is not None and live.spend_guard is not None:
        bindings["spend_admission"] = live.spend_guard.evidence()
    if live is not None and live.operator_authorization is not None:
        bindings["operator_authorization"] = live.operator_authorization
    config_digest = retain_document(root / "run-config.json", bindings)
    if candidate_bytes is not None:
        retain_bytes(root / "synthetic-input-candidate.json", candidate_bytes)
    gateway = candidate_gateway = runtime = handle = None
    budget = services = stack = None
    stage = "reference"
    error = None
    audit = {"schema_version": "solo-pilot-audit/v1", "run_id": run_id, "execution_mode": mode,
        "runtime": "opencode" if live is not None else config["runtime"], "scoreable": False,
        "live_execution_authorized": live is not None,
        "actual_spend_usd_nanos": None if live is not None else 0,
        "run_config_digest": config_digest, "failure": None, "status": "aborted"}
    if live is None:
        audit.update(external_model_calls=0, external_compute_executions=0)

    def authorize(request):
        if live is not None:
            live.authorize(stack, request, config_digest)
        else:
            stack.inventory.authorize(request, approval_reference=f"no-spend-command:{config_digest}")

    try:
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
        actor = AgentIdentity(run_id, 0)
        compute_plan = ComputePlan("solo-pilot", run_id, public_seconds, (ActorComputeAllocation(run_id, actor.actor_id, public_seconds),),
            stack.hidden_seconds, digest_value({"run_config": config_digest, "actor_seconds": public_seconds, "hidden_seconds": stack.hidden_seconds}))
        services = create_solo_candidate_services(root / "candidate-services", campaign,
            evaluator=stack.evaluator, reference_receipt=reference_receipt, plan=compute_plan, policy=SubmissionPolicy(1, public_seconds))
        runner = SoloPilotRunner(services, stack.inventory, stack.public_plan, stack.hidden_plan, hidden_seconds=stack.hidden_seconds)
        limit = live.model_limit_usd_nanos if live is not None else config["synthetic_model_limit_usd_nanos"]
        allocations = (ActorBudgetAllocation(run_id, actor.actor_id, limit),)
        budget_plan = BudgetPlan.create(plan_id=f"{run_id}-model", status="development" if live is not None else "conformance_only", campaign_run_id=run_id,
            organisation_limit_usd_nanos=limit, allocations=allocations, rate_card_digest=digest_value(gateway_profile.rate_card))
        budget = SqliteBudgetAccount(root / "budget.sqlite3", gateway_profile.rate_card, require_metadata_receipts=live is not None,
            budget_plan=budget_plan, receipt_verifier=OpenRouterReceiptVerifier(gateway_profile, require_metadata_receipt=live is not None))
        budget.open_campaign(run_id, limit, allocations)
        retain_document(root / "compute-plan.json", compute_plan)
        retain_document(root / "budget-plan.json", budget_plan)
        upstream = (live.upstream() if live is not None else _CandidateModel(candidate=candidate,
            model=gateway_profile.expected_returned_model, provider=gateway_profile.expected_provider, peer_actor_count=1))
        endpoint = "fake://no-spend"
        if runtime_wiring is not None:
            model_options, candidate_options = runtime_wiring.gateway_options(root) if runtime_wiring.gateway_options else ({}, {})
            gateway = ModelBudgetGateway(gateway_profile, budget, upstream, **model_options)
            candidate_gateway = CandidateToolGateway(services.tools, services.sessions, **candidate_options)
            runtime = runtime_wiring.harness(root, services, gateway, candidate_gateway)
            endpoint = gateway.endpoint
        elif config["runtime"] == "opencode":
            gateway = ModelBudgetGateway(gateway_profile, budget, upstream)
            candidate_gateway = CandidateToolGateway(services.tools, services.sessions)
            runtime = OpenCodeHarnessRuntime(runtime_profile, root / "runtime", gateway,
                process_sandbox=DarwinSandboxExec(sandbox_profile), candidate_gateway=candidate_gateway, timeout_seconds=90)
            endpoint = gateway.endpoint
        else:
            runtime = _SyntheticCandidateHarness(services, candidate)
        controller = CampaignController(runtime, LocalEventSink(root / "events"), budget, runner, SqliteDeliveryOutbox(root / "delivery.sqlite3"))
        stage = "agent_job"
        handle = controller.start(OrganisationSpec(run_id, CoordinationCondition.SOLO, 1, root / "workspace", endpoint))
        material = materialize_solo_pilot(campaign, config["task_seed"])
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
        closed = controller.close(handle, f"{mode} solo pilot complete")
        compute_receipts = runner.reconcile(run_id)
        snapshot = services.compute.snapshot(run_id)
        artifacts = {"selection.json": selection, "public-submissions.json": submissions,
            "hidden-result.json": {"execution_mode": mode, "result": hidden_result}, "compute-receipts.json": compute_receipts,
            "compute-snapshot.json": snapshot, "runtime-snapshot.json": closed.final_harness_snapshot,
            "model-budget-snapshot.json": _retain_budget_snapshot(root, budget.snapshot(run_id))}
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
                runtime.stop(handle.organisation, "solo pilot aborted")
        except BaseException as caught:
            error = error or caught
            audit["cleanup_failure"] = type(caught).__name__
        finally:
            for service in (candidate_gateway, gateway):
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
        raise PilotAborted(f"Solo pilot aborted ({type(error).__name__}); evidence: {root / 'audit.json'}") from error
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
