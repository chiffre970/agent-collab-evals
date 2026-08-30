"""Run one authorized hidden-quality repetition through durable compute."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from agent_collab_evals.adapters.compute_quality_backend import (
    ComputeQualityRepetitionProfile,
)
from agent_collab_evals.adapters.modal_vllm_quality_compute import (
    ModalVllmQualityCliTransport,
    ModalVllmQualityEvidenceResolver,
    ModalVllmQualityProfile,
)
from agent_collab_evals.adapters.sqlite_compute_spend import (
    SqliteComputeSpendAuthorizationService,
)
from agent_collab_evals.adapters.sqlite_execution_backend import SqliteComputeBackend
from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.campaigns.serving_workload import (
    HiddenWorkloadExpectations,
    load_hidden_workload,
)
from agent_collab_evals.canonical import digest_bytes
from agent_collab_evals.compute_backend import (
    ComputeExecutionRequest,
    ComputeExecutionStatus,
    FrozenComputeRunManifest,
)
from agent_collab_evals.evaluation import EvaluationScope


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hidden-manifest", type=Path, required=True)
    parser.add_argument("--hidden-manifest-digest", required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--campaign-run-id", required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--modal-environment", default="dev")
    parser.add_argument("--modal-client-version", default="1.5.4")
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
    campaign = ModelServingCampaign.load(campaign_manifest)
    policy = campaign.quality_policy()
    expectations = HiddenWorkloadExpectations(
        campaign_manifest_digest=campaign.manifest_digest,
        hidden_contract_digest=campaign.transitive_digests["hidden_contract"],
        quality_profile_digest=campaign.transitive_digests["quality_profile"],
        quality_policy_digest=campaign.transitive_digests["quality_policy"],
        quality_workload_digest=policy.quality_workload_digest,
        public_correctness_digest=campaign.transitive_digests["public_correctness"],
        public_performance_digest=campaign.transitive_digests["public_profile"],
        required_gates=tuple(campaign.hidden_contract()["required_gates"]),
    )
    hidden = load_hidden_workload(
        args.hidden_manifest,
        expectations,
        campaign.benchmark_plan(),
        registered_manifest_digest=args.hidden_manifest_digest,
    )
    modal_profile = ModalVllmQualityProfile.create(
        profile_id="modal-quality-live-conformance-v0",
        campaign=campaign,
        campaign_manifest=campaign_manifest,
        hidden_workload=hidden,
        modal_script=(
            repository_root / "campaigns/model_serving_v0/reference/modal_vllm.py"
        ),
        modal_environment=args.modal_environment,
        modal_client_version=args.modal_client_version,
        attempt=1,
        maximum_collection_seconds=args.collection_seconds,
        evidence_volume=args.evidence_volume,
    )
    candidate = campaign.reference_candidate_path.read_bytes()
    descriptor = campaign.validate_reference_candidate()
    modal_cli = repository_root / ".venv/bin/modal"
    authorization_profile = SqliteComputeSpendAuthorizationService.profile_digest_for()
    transport_profile = ModalVllmQualityCliTransport.profile_digest_for(
        modal_profile.digest, modal_cli, authorization_profile
    )
    evidence_profile = ModalVllmQualityEvidenceResolver.profile_digest_for(
        modal_profile.digest
    )
    backend_profile = SqliteComputeBackend.profile_digest_for(
        transport_profile, evidence_profile
    )
    repetition_profile = ComputeQualityRepetitionProfile(
        profile_id="modal-quality-live-repetition-v0",
        campaign_manifest_digest=campaign.manifest_digest,
        hidden_workload_manifest_digest=hidden.manifest_digest,
        quality_profile_digest=modal_profile.quality_profile_digest,
        quality_workload_digest=modal_profile.quality_workload_digest,
        compute_execution_profile_digest=backend_profile,
        repetitions=campaign.quality_profile().repetitions,
        maximum_collection_seconds=args.collection_seconds,
    )
    request = ComputeExecutionRequest(
        execution_key="hidden:live-conformance:quality:1:reference",
        campaign_run_id=args.campaign_run_id,
        reservation_id="evaluation-" + "c" * 32,
        scope=EvaluationScope.HIDDEN,
        candidate_digest=digest_bytes(candidate),
        candidate_manifest_digest=descriptor.manifest_digest,
        evaluator_profile_digest=repetition_profile.digest,
        maximum_seconds=args.maximum_seconds,
    )
    state_root = args.state_root.resolve()
    state_root.mkdir(parents=True, exist_ok=True)
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
    transport = ModalVllmQualityCliTransport(
        modal_profile, repository_root, state_root, modal_cli, authorizations
    )
    resolver = ModalVllmQualityEvidenceResolver(
        modal_profile, state_root, transport.profile_digest
    )
    backend = SqliteComputeBackend(
        state_root / "compute.sqlite3", transport, resolver, authority
    )
    authorizations.issue(
        request, transport.profile_digest, args.approval_reference
    )
    receipt = backend.submit(request, candidate)
    deadline = time.monotonic() + args.maximum_seconds + 60
    while receipt.status is ComputeExecutionStatus.DISPATCHED:
        if time.monotonic() >= deadline:
            break
        receipt = backend.collect(request, timeout_seconds=args.collection_seconds)
    output: dict[str, object] = {
        "status": receipt.status.value,
        "execution_id": receipt.execution_id,
        "request_digest": request.request_digest,
        "run_manifest_digest": authority.manifest_digest,
        "transport_profile_digest": transport.profile_digest,
        "backend_profile_digest": backend.profile_digest,
        "used_seconds": receipt.used_seconds,
        "failure": receipt.failure,
    }
    if receipt.status in {
        ComputeExecutionStatus.COMPLETE,
        ComputeExecutionStatus.FAILED,
    }:
        reconciled = backend.reconcile(request.campaign_run_id)
        output["reconciled"] = len(reconciled) == 1
        if receipt.status is ComputeExecutionStatus.COMPLETE:
            _, evidence = backend.resolve(request)
            output["evidence_digest"] = receipt.evidence.digest
            output["quality_score_ppm"] = evidence["result"][
                "quality_evaluation"
            ]["run"]["score_ppm"]
    print(json.dumps(output, indent=2, sort_keys=True))
    if receipt.status is not ComputeExecutionStatus.COMPLETE:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
