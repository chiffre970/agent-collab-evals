"""Real private-quality fixtures shared by evaluator integration tests."""

from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.campaigns.serving_quality import (
    QualityPolicy,
    build_quality_requests,
    load_quality_workload,
    materialize_quality_workload,
    write_private_workload,
)
from agent_collab_evals.campaigns.serving_workload import (
    HiddenWorkloadBundle,
    HiddenWorkloadExpectations,
    materialize_hidden_workload,
)
from agent_collab_evals.canonical import digest_file


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def real_hidden_quality_bundle(
    root: Path,
) -> tuple[ModelServingCampaign, HiddenWorkloadBundle, QualityPolicy]:
    """Materialize a valid deterministic hidden bundle from local test sources."""

    root.mkdir(parents=True, exist_ok=True)
    campaign = ModelServingCampaign.load(
        REPOSITORY_ROOT / "campaigns/model_serving_v0/campaign.toml"
    )
    profile = campaign.quality_profile()
    source_profile = _write_sources(root)
    workload_document = materialize_quality_workload(
        profile, source_profile, root, bytes(range(32))
    )
    workload_path = write_private_workload(
        root / "private-quality-workload.json", workload_document
    )
    workload = load_quality_workload(workload_path, profile)
    policy = replace(
        campaign.quality_policy(),
        quality_workload_digest=workload.digest,
        bootstrap_resamples=100,
    )
    campaign.validate_reference_candidate()
    requests = build_quality_requests(
        profile,
        workload,
        served_model_name=str(campaign.raw["reference"]["served_model_name"]),
    )
    expectations = HiddenWorkloadExpectations(
        campaign_manifest_digest=campaign.manifest_digest,
        hidden_contract_digest=campaign.transitive_digests["hidden_contract"],
        quality_profile_digest=profile.digest,
        quality_policy_digest=policy.digest,
        quality_workload_digest=workload.digest,
        public_correctness_digest=campaign.transitive_digests["public_correctness"],
        public_performance_digest=campaign.transitive_digests["public_profile"],
        required_gates=tuple(campaign.hidden_contract()["required_gates"]),
    )
    bundle = materialize_hidden_workload(
        root / "hidden-bundle",
        expectations=expectations,
        public_plan=campaign.benchmark_plan(),
        selection_seed=bytes(reversed(range(32))),
        seed_bytes=32,
        quality_workload_path=workload_path,
        quality_requests=requests,
    )
    return campaign, bundle, policy


def _write_sources(root: Path) -> Path:
    mmlu = root / "mmlu.csv"
    with mmlu.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("", "Question", "A", "B", "C", "D", "Answer", "Subject"),
        )
        writer.writeheader()
        for index in range(16):
            writer.writerow(
                {
                    "": index,
                    "Question": f"Which value is {index}?",
                    "A": str(index),
                    "B": str(index + 1),
                    "C": str(index + 2),
                    "D": str(index + 3),
                    "Answer": "A",
                    "Subject": f"subject-{index}",
                }
            )
    gsm8k = root / "gsm8k-test.jsonl"
    gsm8k.write_text(
        "".join(
            json.dumps(
                {
                    "question": f"What is {index} plus one?",
                    "answer": f"Calculation.\n#### {index + 1}",
                }
            )
            + "\n"
            for index in range(16)
        ),
        encoding="utf-8",
    )
    bbh_date = root / "bbh-date-understanding.json"
    bbh_logic = root / "bbh-logical-deduction.json"
    for path, prefix in ((bbh_date, "date"), (bbh_logic, "logic")):
        path.write_text(
            json.dumps(
                {
                    "canary": "private",
                    "examples": [
                        {
                            "input": f"{prefix} question {index}\n(A) yes\n(B) no",
                            "target": "(A)",
                        }
                        for index in range(8)
                    ],
                }
            ),
            encoding="utf-8",
        )
    source_profile = root / "sources.toml"
    lines = ['schema_version = "model-serving-quality-sources/v0alpha1"', ""]
    for source_id, source_format, path, revision in (
        ("mmlu", "openai_mmlu_csv", mmlu, "mmlu-revision"),
        ("gsm8k", "gsm8k_jsonl", gsm8k, "gsm-revision"),
        ("bbh_date_understanding", "bbh_json", bbh_date, "bbh-revision"),
        ("bbh_logical_deduction", "bbh_json", bbh_logic, "bbh-revision"),
    ):
        lines.extend(
            [
                "[[sources]]",
                f'id = "{source_id}"',
                f'format = "{source_format}"',
                f'filename = "{path.name}"',
                f'url = "https://example.invalid/{path.name}"',
                f'revision = "{revision}"',
                f'sha256 = "{digest_file(path).removeprefix("sha256:")}"',
                "",
            ]
        )
    source_profile.write_text("\n".join(lines), encoding="utf-8")
    return source_profile
