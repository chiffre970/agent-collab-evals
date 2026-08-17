"""Materialize the evaluator-private Qwen quality workload once."""

from __future__ import annotations

import argparse
import json
import os
import secrets
from pathlib import Path

from agent_collab_evals.canonical import digest_bytes
from agent_collab_evals.campaigns.serving_quality import (
    QualityProfile,
    materialize_quality_workload,
    write_private_workload,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path(
            "campaigns/model_serving_v0/evaluator/quality_calibration.toml"
        ),
    )
    parser.add_argument(
        "--sources",
        type=Path,
        default=Path("campaigns/model_serving_v0/evaluator/quality_sources.toml"),
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path("tmp/evaluator-private/model-serving-quality/sources"),
    )
    parser.add_argument(
        "--seed-path",
        type=Path,
        default=Path("tmp/evaluator-private/model-serving-quality/selection.seed"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tmp/evaluator-private/model-serving-quality/workload.json"),
    )
    arguments = parser.parse_args()

    profile = QualityProfile.load(arguments.profile)
    seed = _load_or_create_seed(arguments.seed_path, profile.seed_bytes)
    document = materialize_quality_workload(
        profile,
        arguments.sources,
        arguments.source_root,
        seed,
    )
    destination = write_private_workload(arguments.output, document)
    receipt = {
        "ok": True,
        "profile_digest": profile.digest,
        "selection_seed_commitment": digest_bytes(seed),
        "workload_path": str(destination),
        "workload_digest": digest_bytes(destination.read_bytes()),
        "case_count": document["case_count"],
    }
    print(json.dumps(receipt, indent=2, sort_keys=True))


def _load_or_create_seed(path: Path, length: int) -> bytes:
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        seed = destination.read_bytes()
    else:
        seed = secrets.token_bytes(length)
        try:
            os.write(descriptor, seed)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    if len(seed) != length:
        raise RuntimeError("private quality selection seed has the wrong length")
    return seed


if __name__ == "__main__":
    main()
