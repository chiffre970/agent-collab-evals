"""Derive a nonregistered hidden-performance proposal from three references."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.campaigns.serving_performance_calibration import (
    PerformanceCalibrationPlan,
    derive_performance_calibration,
    load_calibration_bundle,
)
from agent_collab_evals.canonical import canonical_json_bytes, digest_file


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--performance-profile", type=Path, required=True)
    parser.add_argument("--calibration-plan", type=Path, required=True)
    parser.add_argument(
        "--receipt", type=Path, action="append", required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    campaign = ModelServingCampaign.load(args.campaign)
    calibration_plan = PerformanceCalibrationPlan.load(args.calibration_plan)
    profile_digest = digest_file(args.performance_profile)
    bundles = tuple(
        load_calibration_bundle(
            receipt,
            performance_profile_digest=profile_digest,
        )
        for receipt in args.receipt
    )
    proposal = derive_performance_calibration(
        calibration_plan,
        args.performance_profile,
        bundles,
        model_source=_modal_model_source(campaign),
        reference_candidate_manifest_digest=(
            campaign.validate_reference_candidate().manifest_digest
        ),
        prior_slos_ms=campaign.scoring_profile().goodput_slos_ms_by_bucket,
    )
    _write_once(args.output, canonical_json_bytes(proposal) + b"\n")
    print(args.output.resolve())


def _modal_model_source(campaign: ModelServingCampaign) -> str:
    repository = campaign.target_model_id.replace("/", "--")
    return (
        f"/cache/huggingface/hub/models--{repository}/snapshots/"
        f"{campaign.target_model_revision}"
    )


def _write_once(path: Path, content: bytes) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise RuntimeError("calibration proposal already differs")
        return
    with path.open("xb") as target:
        target.write(content)
        target.flush()
        os.fsync(target.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    main()
