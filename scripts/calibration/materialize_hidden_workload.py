"""Materialize the complete evaluator-private model-serving workload bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.campaigns.serving_quality import (
    build_quality_requests,
    load_quality_workload,
)
from agent_collab_evals.campaigns.serving_workload import (
    HiddenWorkloadExpectations,
    materialize_hidden_workload,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--campaign",
        type=Path,
        default=Path("campaigns/model_serving_v0/campaign.toml"),
    )
    parser.add_argument(
        "--selection-seed",
        type=Path,
        default=Path("tmp/evaluator-private/model-serving-quality/selection.seed"),
    )
    parser.add_argument(
        "--quality-workload",
        type=Path,
        default=Path("tmp/evaluator-private/model-serving-quality/workload-v2.json"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("tmp/evaluator-private/model-serving-hidden-v0"),
    )
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    campaign = ModelServingCampaign.load(
        (REPOSITORY_ROOT / arguments.campaign).resolve()
    )
    quality_profile = campaign.quality_profile()
    quality_policy = campaign.quality_policy()
    quality_workload_path = (
        REPOSITORY_ROOT / arguments.quality_workload
    ).resolve()
    quality_workload = load_quality_workload(
        quality_workload_path, quality_profile
    )
    quality_policy.validate_against(quality_profile)
    if quality_workload.digest != quality_policy.quality_workload_digest:
        raise RuntimeError("private quality workload differs from the frozen policy")
    hidden_contract = campaign.hidden_contract()
    expectations = HiddenWorkloadExpectations(
        campaign_manifest_digest=campaign.manifest_digest,
        hidden_contract_digest=campaign.transitive_digests["hidden_contract"],
        quality_profile_digest=quality_profile.digest,
        quality_policy_digest=quality_policy.digest,
        quality_workload_digest=quality_workload.digest,
        public_correctness_digest=campaign.transitive_digests["public_correctness"],
        public_performance_digest=campaign.transitive_digests["public_profile"],
        required_gates=tuple(hidden_contract["required_gates"]),
    )
    selection_seed = (REPOSITORY_ROOT / arguments.selection_seed).read_bytes()
    requests = build_quality_requests(
        quality_profile,
        quality_workload,
        served_model_name=str(campaign.raw["reference"]["served_model_name"]),
    )
    bundle = materialize_hidden_workload(
        (REPOSITORY_ROOT / arguments.output_root).resolve(),
        expectations=expectations,
        public_plan=campaign.benchmark_plan(),
        selection_seed=selection_seed,
        seed_bytes=quality_profile.seed_bytes,
        quality_workload_path=quality_workload_path,
        quality_requests=requests,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "manifest": str(bundle.manifest_path),
                "manifest_digest": bundle.manifest_digest,
                "selection_seed_commitment": bundle.selection_seed_commitment,
                "resource_digests": bundle.resource_digests,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
