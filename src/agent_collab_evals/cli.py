"""Small composition root for local validation and fake lifecycle runs."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Sequence

from .adapters.fake_serving_evaluator import FakeModelServingEvaluator
from .adapters.fake_harness import FakeHarnessRuntime
from .adapters.local_artifact_storage import LocalArtifactStorage
from .adapters.local_events import LocalEventSink
from .adapters.local_snapshots import LocalCampaignSnapshotStore
from .adapters.no_model_budget import NoModelBudgetReconciler
from .adapters.sqlite_compute import SqliteComputeBroker
from .adapters.sqlite_submissions import SqliteSubmissionRegistry
from .artifacts import ArtifactStoragePolicy
from .campaigns.model_serving import ModelServingCampaign
from .canonical import digest_value
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
)
from .service_identity import ServiceIdentityRegistry
from .session_identity import SessionIdentityRegistry


DEFAULT_CAMPAIGN = Path("campaigns/model_serving_v0/campaign.toml")


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

    events = LocalEventSink(resolved_state_root / "events")
    snapshots = LocalCampaignSnapshotStore(resolved_state_root / "snapshots")
    first_harness = FakeHarnessRuntime()
    first_controller = CampaignController(
        first_harness, events, NoModelBudgetReconciler()
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
        FakeHarnessRuntime(), events, NoModelBudgetReconciler()
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
    else:  # pragma: no cover - argparse enforces the command set.
        raise AssertionError(f"unhandled command: {arguments.command}")
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
