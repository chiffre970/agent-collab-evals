"""Small composition root for local validation and fake lifecycle runs."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Sequence

from .adapter_rehearsal import (
    run_solo_adapter_rehearsal,
    verify_solo_adapter_rehearsal,
)
from .candidate_rehearsal import run_candidate_rehearsal
from .readiness import readiness_report
from .adapters.fake_serving_evaluator import FakeModelServingEvaluator
from .adapters.fake_harness import FakeHarnessRuntime
from .adapters.local_artifact_storage import LocalArtifactStorage
from .adapters.local_events import LocalEventSink
from .adapters.local_snapshots import LocalCampaignSnapshotStore
from .adapters.modal_vllm_compute import (
    ModalVllmCliTransport,
    ModalVllmComputeProfile,
    ModalVllmEvidenceResolver,
)
from .adapters.no_model_budget import NoModelBudgetReconciler
from .adapters.no_compute_reconciliation import NoComputeExecutionReconciler
from .adapters.sqlite_compute_spend import (
    SqliteComputeSpendAuthorizationService,
)
from .adapters.sqlite_compute import SqliteComputeBroker
from .adapters.sqlite_delivery import SqliteDeliveryOutbox
from .adapters.sqlite_execution_backend import SqliteComputeBackend
from .adapters.sqlite_submissions import SqliteSubmissionRegistry
from .artifacts import ArtifactStoragePolicy
from .campaigns.model_serving import ModelServingCampaign
from .canonical import digest_bytes, digest_value
from .compute_backend import (
    ComputeExecutionRequest,
    ComputeExecutionStatus,
    FrozenComputeRunManifest,
)
from .controller import CampaignController
from .domain import (
    AgentIdentity,
    CoordinationCondition,
    OrganisationSpec,
    SessionHandle,
)
from .evaluation import (
    ActorComputeAllocation,
    ComputePlan,
    SubmissionPolicy,
    EvaluationScope,
)
from .service_identity import ServiceIdentityRegistry
from .session_identity import SessionIdentityRegistry
from .study_registration import StudyCompositionCandidate
from .study_rehearsal import (
    NoSpendStudyAuthority,
    NoSpendStudyRunner,
    verify_no_spend_study_audit,
)
from .study_schedule import BlockInput, RandomizedBlockPlan


DEFAULT_CAMPAIGN = Path("campaigns/model_serving_v0/campaign.toml")
DEFAULT_MODAL_COMPUTE_PROFILE = Path(
    "config/compute/modal-vllm-development.json"
)
DEFAULT_STUDY_CANDIDATE = Path(
    "config/studies/model-serving-flash-v0.registration-candidate.json"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="collab-evals")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate-scenario", help="validate and summarize a campaign pack"
    )
    validate.add_argument("campaign", nargs="?", type=Path, default=DEFAULT_CAMPAIGN)

    fake = subparsers.add_parser(
        "fake-solo", help="run the durable solo lifecycle against local fakes"
    )
    fake.add_argument("campaign", nargs="?", type=Path, default=DEFAULT_CAMPAIGN)
    fake.add_argument("--state-root", type=Path, default=Path("tmp/fake-solo"))
    fake.add_argument("--task-seed", type=int, default=1729)
    fake.add_argument("--run-id")

    lifecycle = subparsers.add_parser(
        "fake-candidate-lifecycle",
        help="run submission, evaluation, selection, and sealing against local fakes",
    )
    lifecycle.add_argument(
        "campaign", nargs="?", type=Path, default=DEFAULT_CAMPAIGN
    )
    lifecycle.add_argument(
        "--state-root", type=Path, default=Path("tmp/fake-candidate-lifecycle")
    )
    lifecycle.add_argument("--run-id")

    modal_compute = subparsers.add_parser(
        "modal-compute-development",
        help="dispatch or collect one explicitly billable Modal evaluator repetition",
    )
    modal_compute.add_argument(
        "--profile", type=Path, default=DEFAULT_MODAL_COMPUTE_PROFILE
    )
    modal_compute.add_argument(
        "--candidate",
        type=Path,
        default=Path("campaigns/model_serving_v0/reference/candidate.json"),
    )
    modal_compute.add_argument(
        "--state-root", type=Path, default=Path("tmp/modal-compute-development")
    )
    modal_compute.add_argument("--run-id", default="modal-development-reference-v1")
    action = modal_compute.add_mutually_exclusive_group(required=True)
    action.add_argument("--dispatch", action="store_true")
    action.add_argument("--collect", action="store_true")
    modal_compute.add_argument("--collect-timeout-seconds", type=int, default=0)
    modal_compute.add_argument("--allow-gpu-spend", action="store_true")

    rehearsal = subparsers.add_parser(
        "rehearse-study",
        help="run one complete four-condition structural rehearsal without spend",
    )
    rehearsal.add_argument(
        "--composition", type=Path, default=DEFAULT_STUDY_CANDIDATE
    )
    rehearsal.add_argument(
        "--state-root", type=Path, default=Path("tmp/study-rehearsals")
    )
    rehearsal.add_argument("--rehearsal-id")
    rehearsal.add_argument("--master-seed", type=int, default=970)
    rehearsal.add_argument("--task-seed", type=int, default=1729)
    rehearsal.add_argument("--organisation-size", type=int, default=4)

    adapter_rehearsal = subparsers.add_parser(
        "rehearse-solo-adapters",
        help=(
            "run real OpenCode and control adapters against a local synthetic "
            "model without external spend"
        ),
    )
    adapter_rehearsal.add_argument(
        "campaign", nargs="?", type=Path, default=DEFAULT_CAMPAIGN
    )
    adapter_rehearsal.add_argument(
        "--state-root", type=Path, default=Path("tmp/adapter-rehearsals")
    )
    adapter_rehearsal.add_argument("--run-id")
    adapter_rehearsal.add_argument("--task-seed", type=int, default=1729)
    candidates = subparsers.add_parser(
        "rehearse-solo-candidates", help="exercise real agent candidate tools with synthetic evaluation and no spend"
    )
    candidates.add_argument("campaign", nargs="?", type=Path, default=DEFAULT_CAMPAIGN)
    candidates.add_argument("--state-root", type=Path, default=Path("tmp/candidate-rehearsals"))
    candidates.add_argument("--run-id", required=True)
    candidates.add_argument("--restart-runtime", action="store_true", help="reconstruct candidate services and resume OpenCode between jobs")
    readiness = subparsers.add_parser("readiness", help="inspect remaining deployment and registration gates without running workloads")
    readiness.add_argument("--composition", type=Path, default=DEFAULT_STUDY_CANDIDATE)
    return parser


def _campaign_summary(campaign: ModelServingCampaign) -> dict[str, object]:
    candidate = campaign.validate_reference_candidate()
    buckets = campaign.benchmark_buckets()
    scoring = campaign.scoring_profile()
    return {
        "campaign_id": campaign.raw["campaign_id"],
        "manifest_digest": campaign.manifest_digest,
        "target_model": campaign.target_model_id,
        "target_revision": campaign.target_model_revision,
        "reference_candidate": candidate.candidate_id,
        "scoring_profile_digest": scoring.digest,
        "benchmark_bucket_count": len(buckets),
        "benchmark_point_count": sum(len(bucket.request_rates) for bucket in buckets),
    }


def _validate_scenario(path: Path) -> dict[str, object]:
    return {"ok": True, **_campaign_summary(ModelServingCampaign.load(path))}


def _fake_solo(
    path: Path,
    state_root: Path,
    task_seed: int,
    run_id: str | None,
) -> dict[str, object]:
    campaign = ModelServingCampaign.load(path)
    summary = _campaign_summary(campaign)
    materialized = campaign.materialize(task_seed)
    campaign_run_id = run_id or f"fake-solo-{uuid.uuid4().hex}"
    resolved_state_root = state_root.resolve()
    compute_authority = FrozenComputeRunManifest.load_or_create(
        resolved_state_root / campaign_run_id / "compute-run-manifest.json",
        campaign_run_id=campaign_run_id,
        compute_enabled=False,
        transport_profile_digest=None,
        backend_profile_digest=None,
        requests=(),
    )

    events = LocalEventSink(resolved_state_root / "events")
    delivery_outbox = SqliteDeliveryOutbox(
        resolved_state_root / campaign_run_id / "delivery.sqlite3"
    )
    snapshots = LocalCampaignSnapshotStore(resolved_state_root / "snapshots")
    first_harness = FakeHarnessRuntime()
    first_controller = CampaignController(
        first_harness,
        events,
        NoModelBudgetReconciler(),
        NoComputeExecutionReconciler(compute_authority),
        delivery_outbox,
    )
    handle = first_controller.start(
        OrganisationSpec(
            campaign_run_id=campaign_run_id,
            condition=CoordinationCondition.SOLO,
            organisation_size=1,
            workspace_root=resolved_state_root / "workspaces" / campaign_run_id,
            model_endpoint="fake://model",
        )
    )
    for job in materialized.jobs:
        first_controller.deliver(handle, job)
    snapshots.save(first_controller.snapshot(handle))

    resumed_controller = CampaignController(
        FakeHarnessRuntime(),
        events,
        NoModelBudgetReconciler(),
        NoComputeExecutionReconciler(compute_authority),
        SqliteDeliveryOutbox(
            resolved_state_root / campaign_run_id / "delivery.sqlite3"
        ),
    )
    resumed = resumed_controller.resume(snapshots.load(campaign_run_id))
    result = resumed_controller.close(resumed, "fake vertical slice complete")
    event_log = events.read(campaign_run_id)

    return {
        "ok": True,
        **summary,
        "campaign_run_id": campaign_run_id,
        "condition": CoordinationCondition.SOLO.value,
        "material_digest": materialized.material_digest,
        "delivered_job_ids": list(result.delivered_job_ids),
        "event_kinds": [event["kind"] for event in event_log],
        "session_count": len(resumed.sessions),
        "durable_resume_exercised": True,
    }


def _fake_candidate_lifecycle(
    path: Path, state_root: Path, run_id: str | None
) -> dict[str, object]:
    campaign = ModelServingCampaign.load(path)
    campaign_run_id = run_id or f"fake-candidates-{uuid.uuid4().hex}"
    root = state_root.resolve() / campaign_run_id
    sessions = SessionIdentityRegistry()
    services = ServiceIdentityRegistry()
    service = services.bind("submission_registry")
    actors = tuple(AgentIdentity(campaign_run_id, ordinal) for ordinal in range(2))
    transports = tuple(
        sessions.bind(actor, SessionHandle(f"{campaign_run_id}-session-{index}"))
        for index, actor in enumerate(actors)
    )
    storage = LocalArtifactStorage(
        root / "artifacts",
        sessions,
        services,
        ArtifactStoragePolicy(
            max_artifact_bytes=32_768,
            max_actor_bytes=32_768,
            max_campaign_bytes=65_536,
        ),
        {
            "submission_registry": frozenset(
                {"candidate_lifecycle", "hidden_evaluation"}
            )
        },
    )
    storage.open_campaign(campaign_run_id, tuple(actor.actor_id for actor in actors))
    allocations = tuple(
        ActorComputeAllocation(campaign_run_id, actor.actor_id, 60)
        for actor in actors
    )
    plan = ComputePlan(
        plan_id="fake-candidate-compute-v1",
        campaign_run_id=campaign_run_id,
        organisation_limit_seconds=120,
        actor_allocations=allocations,
        hidden_evaluator_limit_seconds=60,
        source_digest=digest_value(
            {
                "plan_id": "fake-candidate-compute-v1",
                "campaign_run_id": campaign_run_id,
                "actor_limits": {actor.actor_id: 60 for actor in actors},
                "hidden_evaluator_limit_seconds": 60,
            }
        ),
    )
    compute = SqliteComputeBroker(
        root / "compute.sqlite3",
        sessions,
        services,
        plan,
        hidden_evaluator_service="submission_registry",
    )
    evaluator = FakeModelServingEvaluator(
        root / "evaluator.sqlite3",
        campaign,
        {
            "stock-vllm-0.21.0": 1_000_000,
            "vllm-0.21.0-stream-interval-10": 1_001_872,
        },
        {
            "stock-vllm-0.21.0": 1_000_000,
            "vllm-0.21.0-stream-interval-10": 999_202,
        },
    )
    registry = SqliteSubmissionRegistry(
        root / "submissions.sqlite3",
        sessions,
        storage,
        compute,
        evaluator,
        service,
    )
    candidate_paths = (
        campaign.root / "reference/candidate.json",
        campaign.root / "candidates/vllm-stream-interval-10.json",
    )
    artifacts = tuple(
        storage.put(transport, candidate.read_bytes(), "application/json")
        for transport, candidate in zip(transports, candidate_paths, strict=True)
    )
    default_receipt = evaluator.visible_evaluate(
        candidate_paths[0].read_bytes(),
        None,
        f"reference:{campaign_run_id}:optimize-serving",
    )
    registry.initialize(
        campaign_run_id,
        "optimize-serving",
        tuple(actor.actor_id for actor in actors),
        SubmissionPolicy(1, 60),
        artifacts[0].ref,
        default_receipt,
    )
    receipts = tuple(
        registry.submit(
            transport,
            "optimize-serving",
            artifact.ref,
            "candidate-1",
        )
        for transport, artifact in zip(transports, artifacts, strict=True)
    )
    for receipt in receipts:
        registry.evaluate_visible(receipt)
    for actor in actors:
        compute.release_visible_results(campaign_run_id, actor.actor_id)
    submissions = registry.close(campaign_run_id, "optimize-serving")
    selection = registry.select(submissions)
    hidden = registry.evaluate_hidden(selection.receipt, reserved_seconds=60)
    seal = storage.seal(
        campaign_run_id,
        {
            "selection_digest": selection.selection_digest,
            "hidden_evidence_digest": hidden.evidence_digest,
            "compute_plan_digest": plan.source_digest,
            "evaluator_profile_digest": evaluator.profile_digest,
        },
    )
    return {
        "ok": True,
        **_campaign_summary(campaign),
        "campaign_run_id": campaign_run_id,
        "candidate_receipts": [receipt.value for receipt in receipts],
        "selected_receipt": (
            selection.selected_receipt.value
            if selection.selected_receipt is not None
            else None
        ),
        "visible_criterion_units": selection.result.criterion_units,
        "hidden_criterion_units": hidden.criterion_units,
        "selection_receipt": selection.receipt.value,
        "selection_digest": selection.selection_digest,
        "storage_seal_digest": seal.seal_digest,
        "compute_plan_digest": plan.source_digest,
        "evaluator_profile_digest": evaluator.profile_digest,
        "gpu_spend": False,
    }


def _modal_compute_development(arguments: argparse.Namespace) -> dict[str, object]:
    repository_root = Path(__file__).resolve().parents[2]
    profile = ModalVllmComputeProfile.load(
        arguments.profile.resolve(), repository_root=repository_root
    )
    campaign = ModelServingCampaign.load(profile.campaign_manifest)
    candidate_path = arguments.candidate.resolve()
    candidate = candidate_path.read_bytes()
    descriptor = campaign.validate_candidate(candidate_path)
    state_root = arguments.state_root.resolve() / arguments.run_id
    request = ComputeExecutionRequest(
        execution_key=f"development:{descriptor.candidate_id}",
        campaign_run_id=arguments.run_id,
        reservation_id=(
            "development-"
            + digest_value(
                {
                    "run_id": arguments.run_id,
                    "candidate": descriptor.manifest_digest,
                }
            )[7:39]
        ),
        scope=EvaluationScope.VISIBLE,
        candidate_digest=digest_bytes(candidate),
        candidate_manifest_digest=descriptor.manifest_digest,
        evaluator_profile_digest=profile.evaluator_profile_digest,
        maximum_seconds=campaign.measurement_profile().repetition_timeout_seconds,
    )
    if arguments.dispatch and not arguments.allow_gpu_spend:
        raise RuntimeError("--dispatch requires --allow-gpu-spend")
    authorization_profile_digest = (
        SqliteComputeSpendAuthorizationService.profile_digest_for()
    )
    transport_profile_digest = ModalVllmCliTransport.profile_digest_for(
        profile.digest,
        repository_root / ".venv/bin/modal",
        authorization_profile_digest,
    )
    evidence_profile_digest = ModalVllmEvidenceResolver.profile_digest_for(
        profile.digest
    )
    backend_profile_digest = SqliteComputeBackend.profile_digest_for(
        transport_profile_digest, evidence_profile_digest
    )
    run_manifest = FrozenComputeRunManifest.load_or_create(
        state_root / "compute-run-manifest.json",
        campaign_run_id=arguments.run_id,
        compute_enabled=True,
        transport_profile_digest=transport_profile_digest,
        backend_profile_digest=backend_profile_digest,
        requests=(request,),
    )
    spend_authorizations = SqliteComputeSpendAuthorizationService(
        state_root / "compute-spend.sqlite3", run_manifest
    )
    transport = ModalVllmCliTransport(
        profile,
        repository_root,
        state_root,
        repository_root / ".venv/bin/modal",
        spend_authorizations,
    )
    resolver = ModalVllmEvidenceResolver(
        profile, repository_root, state_root, transport.profile_digest
    )
    backend = SqliteComputeBackend(
        state_root / "executions.sqlite3", transport, resolver, run_manifest
    )
    if arguments.dispatch:
        authorization = spend_authorizations.issue(
            request,
            transport.profile_digest,
            "explicit_cli_allow_gpu_spend",
        )
        receipt = backend.submit(request, candidate)
        result = None
        reconciliation = None
    else:
        receipt = backend.collect(
            request, timeout_seconds=arguments.collect_timeout_seconds
        )
        result = None
        if receipt.status in {
            ComputeExecutionStatus.COMPLETE,
            ComputeExecutionStatus.FAILED,
        } and receipt.evidence is not None:
            _, evidence = backend.resolve(request)
            normalized = evidence["result"]
            assert isinstance(normalized, dict)
            performance = normalized.get("performance_score")
            result = {
                "valid": normalized.get("valid"),
                "performance_score": performance,
                "durable_evidence": normalized.get("durable_evidence"),
            }
        reconciliation = None
        if receipt.status in {
            ComputeExecutionStatus.COMPLETE,
            ComputeExecutionStatus.FAILED,
        }:
            reconciled = backend.reconcile(arguments.run_id)
            if len(reconciled) != 1 or reconciled[0] != receipt:
                raise RuntimeError("terminal compute reconciliation differs")
            reconciliation = {
                "valid": True,
                "receipt_count": 1,
                "execution_id": receipt.execution_id,
                "request_digest": receipt.request_digest,
                "status": receipt.status.value,
                "evidence_digest": (
                    receipt.evidence.digest if receipt.evidence is not None else None
                ),
            }
    return {
        "ok": receipt.status not in {
            ComputeExecutionStatus.AMBIGUOUS,
            ComputeExecutionStatus.FAILED,
        },
        "development_only": True,
        "gpu_spend_authorized": bool(
            arguments.dispatch and arguments.allow_gpu_spend
        ),
        "spend_authorization_id": (
            authorization.authorization_id if arguments.dispatch else None
        ),
        "compute_run_manifest_digest": run_manifest.manifest_digest,
        "campaign_run_id": arguments.run_id,
        "candidate_id": descriptor.candidate_id,
        "candidate_manifest_digest": descriptor.manifest_digest,
        "compute_profile_digest": profile.digest,
        "backend_profile_digest": backend.profile_digest,
        "execution_id": receipt.execution_id,
        "execution_status": receipt.status.value,
        "external_call_id": receipt.external_call_id,
        "used_seconds": receipt.used_seconds,
        "failure": receipt.failure,
        "result": result,
        "reconciliation": reconciliation,
    }


def _rehearse_study(arguments: argparse.Namespace) -> dict[str, object]:
    repository_root = Path(__file__).resolve().parents[2]
    composition = StudyCompositionCandidate.load(
        arguments.composition.resolve(), repository_root=repository_root
    )
    rehearsal_id = arguments.rehearsal_id or f"rehearsal-{uuid.uuid4().hex}"
    state_root = arguments.state_root.resolve() / rehearsal_id
    materialized = composition.campaign.materialize(arguments.task_seed)
    plan = RandomizedBlockPlan.create(
        master_seed=arguments.master_seed,
        organisation_size=arguments.organisation_size,
        blocks=(
            BlockInput(
                block_id=f"{rehearsal_id}-block-001",
                replicate_id=f"{rehearsal_id}-replicate-001",
                variant_id=str(composition.campaign.raw["campaign_id"]),
                task_seed=arguments.task_seed,
                task_material_digest=materialized.material_digest,
            ),
        ),
    )
    plan.write_once(state_root / "block-plan.json")
    authority = NoSpendStudyAuthority.create(
        state_root / "authority.json",
        rehearsal_id=rehearsal_id,
        composition=composition,
        block_plan=plan,
        repository_root=repository_root,
    )
    result = NoSpendStudyRunner(
        composition=composition,
        block_plan=plan,
        authority=authority,
        state_root=state_root / "execution",
    ).run()
    verify_no_spend_study_audit(
        result.audit_path,
        expected_digest=result.audit_digest,
        composition=composition,
        block_plan=plan,
        authority=authority,
    )
    return {
        "ok": True,
        "execution_class": "no_spend",
        "scoreable": False,
        "treatment_surfaces_exercised": False,
        "rehearsal_id": result.rehearsal_id,
        "authority_digest": result.authority_digest,
        "block_plan_digest": result.block_plan_digest,
        "audit_path": str(result.audit_path),
        "audit_digest": result.audit_digest,
        "block_count": result.block_count,
        "run_count": result.run_count,
        "model_calls": 0,
        "compute_executions": 0,
    }


def _rehearse_solo_adapters(arguments: argparse.Namespace) -> dict[str, object]:
    run_id = arguments.run_id or f"adapter-rehearsal-{uuid.uuid4().hex}"
    result = run_solo_adapter_rehearsal(
        campaign_path=arguments.campaign,
        state_root=arguments.state_root,
        run_id=run_id,
        task_seed=arguments.task_seed,
    )
    verify_solo_adapter_rehearsal(
        result.audit_path,
        expected_digest=result.audit_digest,
        campaign_path=arguments.campaign,
    )
    return {
        "ok": True,
        "run_id": result.run_id,
        "execution_class": "local_synthetic_model",
        "scoreable": False,
        "external_model_calls": 0,
        "external_compute_executions": 0,
        "synthetic_model_calls": result.synthetic_model_calls,
        "synthetic_charged_usd_nanos": result.charged_usd_nanos,
        "audit_path": str(result.audit_path),
        "audit_digest": result.audit_digest,
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "validate-scenario":
        output = _validate_scenario(arguments.campaign)
    elif arguments.command == "fake-solo":
        output = _fake_solo(
            arguments.campaign,
            arguments.state_root,
            arguments.task_seed,
            arguments.run_id,
        )
    elif arguments.command == "fake-candidate-lifecycle":
        output = _fake_candidate_lifecycle(
            arguments.campaign,
            arguments.state_root,
            arguments.run_id,
        )
    elif arguments.command == "modal-compute-development":
        output = _modal_compute_development(arguments)
    elif arguments.command == "rehearse-study":
        output = _rehearse_study(arguments)
    elif arguments.command == "rehearse-solo-adapters":
        output = _rehearse_solo_adapters(arguments)
    elif arguments.command == "rehearse-solo-candidates":
        output = run_candidate_rehearsal(
            arguments.campaign, arguments.state_root, arguments.run_id,
            restart_runtime=arguments.restart_runtime,
        )
    elif arguments.command == "readiness":
        output = readiness_report(Path(__file__).resolve().parents[2], arguments.composition)
    else:  # pragma: no cover - argparse enforces the command set.
        raise AssertionError(f"unhandled command: {arguments.command}")
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
