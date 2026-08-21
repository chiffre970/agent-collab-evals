"""Small composition root for local validation and fake lifecycle runs."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Sequence

from .adapters.fake_harness import FakeHarnessRuntime
from .adapters.local_events import LocalEventSink
from .adapters.local_snapshots import LocalCampaignSnapshotStore
from .adapters.no_model_budget import NoModelBudgetReconciler
from .campaigns.model_serving import ModelServingCampaign
from .controller import CampaignController
from .domain import CoordinationCondition, OrganisationSpec


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
    else:  # pragma: no cover - argparse enforces the command set.
        raise AssertionError(f"unhandled command: {arguments.command}")
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
