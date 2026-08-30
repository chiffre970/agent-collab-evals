"""Qualify one authorized hidden correctness or performance execution."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from agent_collab_evals.adapters.compute_candidate_evaluator import (
    ComputeCandidateEvaluationProfile,
    ComputeCandidateEvaluator,
)
from agent_collab_evals.adapters.modal_vllm_compute import ModalVllmCliTransport
from agent_collab_evals.adapters.modal_vllm_correctness_compute import (
    ModalVllmCorrectnessCliTransport,
    ModalVllmCorrectnessEvidenceResolver,
    ModalVllmCorrectnessProfile,
)
from agent_collab_evals.adapters.modal_vllm_performance_compute import (
    ModalVllmHiddenPerformanceEvidenceResolver,
    ModalVllmHiddenPerformanceProfile,
)
from agent_collab_evals.adapters.sqlite_compute_spend import (
    SqliteComputeSpendAuthorizationService,
)
from agent_collab_evals.adapters.sqlite_execution_backend import SqliteComputeBackend
from agent_collab_evals.artifacts import ArtifactRef
from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.campaigns.serving_workload import (
    HiddenWorkloadBundle,
    HiddenWorkloadExpectations,
    load_hidden_workload,
)
from agent_collab_evals.canonical import digest_bytes
from agent_collab_evals.compute_backend import (
    ComputeExecutionRequest,
    ComputeExecutionStatus,
    FrozenComputeRunManifest,
)
from agent_collab_evals.evaluation import (
    EvaluationReservation,
    EvaluationReservationStatus,
    EvaluationScope,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase", choices=("correctness", "performance"), required=True
    )
    parser.add_argument("--hidden-manifest", type=Path, required=True)
    parser.add_argument("--hidden-manifest-digest", required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--campaign-run-id", required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--modal-environment", default="dev")
    parser.add_argument("--modal-client-version", default="1.5.4")
    parser.add_argument("--modal-cli", type=Path)
    parser.add_argument("--maximum-seconds", type=int, default=1_800)
    parser.add_argument("--collection-seconds", type=int, default=300)
    parser.add_argument(
        "--evidence-volume",
        default="agent-collab-evals-evaluator-evidence-v2",
    )
    args = parser.parse_args()
    if not args.approval_reference.startswith("approved:"):
        raise ValueError("approval reference must start with 'approved:'")

    repository_root = Path(__file__).resolve().parents[2]
    campaign_manifest = repository_root / "campaigns/model_serving_v0/campaign.toml"
    modal_script = repository_root / "campaigns/model_serving_v0/reference/modal_vllm.py"
    modal_cli = (
        args.modal_cli.resolve(strict=True)
        if args.modal_cli is not None
        else repository_root / ".venv/bin/modal"
    )
    campaign = ModelServingCampaign.load(campaign_manifest)
    hidden = _hidden_bundle(
        campaign,
        args.hidden_manifest,
        args.hidden_manifest_digest,
    )
    state_root = args.state_root.resolve()
    state_root.mkdir(parents=True, exist_ok=True)
    candidate = campaign.reference_candidate_path.read_bytes()
    descriptor = campaign.validate_reference_candidate()
    authorization_profile = (
        SqliteComputeSpendAuthorizationService.profile_digest_for()
    )

    if args.phase == "correctness":
        modal_profile = ModalVllmCorrectnessProfile.create(
            profile_id="modal-hidden-correctness-live-conformance-v0",
            campaign=campaign,
            campaign_manifest=campaign_manifest,
            hidden_workload=hidden,
            modal_script=modal_script,
            modal_environment=args.modal_environment,
            modal_client_version=args.modal_client_version,
            attempt=1,
            maximum_collection_seconds=args.collection_seconds,
            evidence_volume=args.evidence_volume,
        )
        transport_profile = ModalVllmCorrectnessCliTransport.profile_digest_for(
            modal_profile.digest, modal_cli, authorization_profile
        )
        evidence_profile = ModalVllmCorrectnessEvidenceResolver.profile_digest_for(
            modal_profile.digest
        )
        workload_digest = modal_profile.correctness_workload_digest
        evaluation_key = "hidden:reference:correctness"
    else:
        modal_profile = ModalVllmHiddenPerformanceProfile.create(
            profile_id="modal-hidden-performance-live-conformance-v0",
            campaign=campaign,
            campaign_manifest=campaign_manifest,
            hidden_workload=hidden,
            modal_script=modal_script,
            modal_environment=args.modal_environment,
            modal_client_version=args.modal_client_version,
            repetition=1,
            attempt=1,
            maximum_collection_seconds=args.collection_seconds,
            evidence_volume=args.evidence_volume,
        )
        transport_profile = ModalVllmCliTransport.profile_digest_for(
            modal_profile.digest, modal_cli, authorization_profile
        )
        evidence_profile = (
            ModalVllmHiddenPerformanceEvidenceResolver.profile_digest_for(
                modal_profile.digest
            )
        )
        workload_digest = modal_profile.performance_profile_digest
        evaluation_key = "hidden:reference:performance"

    backend_profile = SqliteComputeBackend.profile_digest_for(
        transport_profile, evidence_profile
    )
    evaluator_profile = ComputeCandidateEvaluationProfile(
        profile_id=f"hidden-{args.phase}-live-conformance-v0",
        phase=args.phase,
        campaign_manifest_digest=campaign.manifest_digest,
        hidden_workload_manifest_digest=hidden.manifest_digest,
        workload_digest=workload_digest,
        compute_execution_profile_digest=backend_profile,
        maximum_collection_seconds=args.collection_seconds,
    )
    reservation = EvaluationReservation(
        reservation_id="evaluation-" + "c" * 32,
        reservation_key=f"hidden:live-conformance:{args.phase}",
        campaign_run_id=args.campaign_run_id,
        actor_id=None,
        artifact_ref=ArtifactRef("artifact-" + "c" * 32),
        scope=EvaluationScope.HIDDEN,
        reserved_seconds=args.maximum_seconds,
        status=EvaluationReservationStatus.RESERVED,
    )
    request = ComputeExecutionRequest(
        execution_key=evaluation_key,
        campaign_run_id=reservation.campaign_run_id,
        reservation_id=reservation.reservation_id,
        scope=EvaluationScope.HIDDEN,
        candidate_digest=digest_bytes(candidate),
        candidate_manifest_digest=descriptor.manifest_digest,
        evaluator_profile_digest=evaluator_profile.digest,
        maximum_seconds=reservation.reserved_seconds,
    )
    authority = FrozenComputeRunManifest.load_or_create(
        state_root / "compute-run-manifest.json",
        campaign_run_id=request.campaign_run_id,
        compute_enabled=True,
        transport_profile_digest=transport_profile,
        backend_profile_digest=backend_profile,
        requests=(request,),
    )
    authorizations = SqliteComputeSpendAuthorizationService(
        state_root / "compute-spend.sqlite3", authority
    )
    if args.phase == "correctness":
        transport = ModalVllmCorrectnessCliTransport(
            modal_profile,
            repository_root,
            state_root,
            modal_cli,
            authorizations,
            evaluator_profile_digest=evaluator_profile.digest,
        )
        resolver = ModalVllmCorrectnessEvidenceResolver(
            modal_profile, state_root, transport.profile_digest
        )
    else:
        transport = ModalVllmCliTransport(
            modal_profile,
            repository_root,
            state_root,
            modal_cli,
            authorizations,
            evaluator_profile_digest=evaluator_profile.digest,
        )
        resolver = ModalVllmHiddenPerformanceEvidenceResolver(
            modal_profile,
            repository_root,
            state_root,
            transport.profile_digest,
        )
    backend = SqliteComputeBackend(
        state_root / "compute.sqlite3", transport, resolver, authority
    )
    evaluator = ComputeCandidateEvaluator(
        state_root / "candidate-evaluator.sqlite3",
        campaign,
        evaluator_profile,
        backend,
    )
    authorizations.issue(
        request, transport.profile_digest, args.approval_reference
    )
    execution = backend.submit(request, candidate)
    deadline = time.monotonic() + args.maximum_seconds + 60
    while execution.status is ComputeExecutionStatus.DISPATCHED:
        if time.monotonic() >= deadline:
            break
        execution = backend.collect(
            request, timeout_seconds=args.collection_seconds
        )
    output: dict[str, object] = {
        "phase": args.phase,
        "status": execution.status.value,
        "execution_id": execution.execution_id,
        "request_digest": request.request_digest,
        "run_manifest_digest": authority.manifest_digest,
        "transport_profile_digest": transport.profile_digest,
        "evidence_profile_digest": resolver.profile_digest,
        "backend_profile_digest": backend.profile_digest,
        "evaluator_profile_digest": evaluator.profile_digest,
        "used_seconds": execution.used_seconds,
        "failure": execution.failure,
    }
    if execution.status in {
        ComputeExecutionStatus.COMPLETE,
        ComputeExecutionStatus.FAILED,
    }:
        reconciled = backend.reconcile(request.campaign_run_id)
        output["reconciled"] = len(reconciled) == 1
        if execution.status is ComputeExecutionStatus.COMPLETE:
            receipt = evaluator.hidden_evaluate(
                candidate, reservation, evaluation_key
            )
            result = evaluator.resolve(
                receipt, candidate, reservation, EvaluationScope.HIDDEN
            )
            output.update(
                {
                    "eligible": result.eligible,
                    "criterion_units": result.criterion_units,
                    "result_evidence_digest": result.evidence_digest,
                }
            )
    print(json.dumps(output, indent=2, sort_keys=True))
    if execution.status is not ComputeExecutionStatus.COMPLETE:
        raise SystemExit(2)


def _hidden_bundle(
    campaign: ModelServingCampaign,
    manifest: Path,
    manifest_digest: str,
) -> HiddenWorkloadBundle:
    policy = campaign.quality_policy()
    expectations = HiddenWorkloadExpectations(
        campaign_manifest_digest=campaign.manifest_digest,
        hidden_contract_digest=campaign.transitive_digests["hidden_contract"],
        quality_profile_digest=campaign.transitive_digests["quality_profile"],
        quality_policy_digest=campaign.transitive_digests["quality_policy"],
        quality_workload_digest=policy.quality_workload_digest,
        public_correctness_digest=campaign.transitive_digests[
            "public_correctness"
        ],
        public_performance_digest=campaign.transitive_digests["public_profile"],
        required_gates=tuple(campaign.hidden_contract()["required_gates"]),
    )
    return load_hidden_workload(
        manifest,
        expectations,
        campaign.benchmark_plan(),
        registered_manifest_digest=manifest_digest,
    )


if __name__ == "__main__":
    main()
