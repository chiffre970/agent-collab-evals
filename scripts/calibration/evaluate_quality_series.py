#!/usr/bin/env python3
"""Evaluate three paired served-generation quality repetitions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from agent_collab_evals.canonical import (
    DuplicateKeyError,
    canonical_json_bytes,
    digest_file,
    load_json,
)
from agent_collab_evals.campaigns.serving_quality import (
    QualityPolicy,
    QualityValidationError,
    evaluate_quality_series,
)


DEFAULT_POLICY = Path("campaigns/model_serving_v0/evaluator/quality_policy.toml")
DEFAULT_RESULTS = Path("tmp/evaluator-private/model-serving-quality/results")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--reference-measurement-id")
    parser.add_argument("--candidate-measurement-id")
    args = parser.parse_args()

    policy = QualityPolicy.load(args.policy)
    reference_id = args.reference_measurement_id or policy.reference_measurement_id
    candidate_id = (
        args.candidate_measurement_id or policy.clean_control_measurement_id
    )
    references, reference_digests = _load_series(
        args.results_root, reference_id, policy.repetitions
    )
    candidates, candidate_digests = _load_series(
        args.results_root, candidate_id, policy.repetitions
    )
    if reference_id == policy.reference_measurement_id and (
        reference_digests != policy.reference_receipt_digests
    ):
        raise QualityValidationError("reference calibration receipt digests differ")
    if candidate_id == policy.clean_control_measurement_id and (
        candidate_digests != policy.clean_control_receipt_digests
    ):
        raise QualityValidationError("clean-control calibration receipt digests differ")

    decision = evaluate_quality_series(policy, references, candidates)
    print(canonical_json_bytes(decision).decode("utf-8"))
    return 0 if decision["eligible"] else 2


def _load_series(
    results_root: Path, measurement_id: str, repetitions: int
) -> tuple[tuple[Mapping[str, Any], ...], tuple[str, ...]]:
    if not measurement_id or Path(measurement_id).name != measurement_id:
        raise QualityValidationError("quality measurement ID is invalid")
    series_root = results_root.resolve() / measurement_id
    runs: list[Mapping[str, Any]] = []
    receipt_digests: list[str] = []
    for repetition in range(1, repetitions + 1):
        candidates = sorted(
            series_root.glob(f"repetition-{repetition:04d}-attempt-*/receipt.json")
        )
        valid: list[tuple[Path, Mapping[str, Any]]] = []
        for path in candidates:
            try:
                with path.open("r", encoding="utf-8") as source:
                    envelope = load_json(source)
            except (json.JSONDecodeError, DuplicateKeyError) as error:
                raise QualityValidationError(
                    f"quality receipt is not unambiguous JSON: {path}"
                ) from error
            if not isinstance(envelope, dict) or set(envelope) != {
                "schema_version",
                "measurement_id",
                "repetition",
                "attempt",
                "normalized",
                "raw_digests",
            }:
                raise QualityValidationError(f"quality receipt fields differ: {path}")
            normalized = envelope["normalized"]
            if (
                envelope["measurement_id"] != measurement_id
                or envelope["repetition"] != repetition
                or not isinstance(normalized, dict)
            ):
                raise QualityValidationError(f"quality receipt identity differs: {path}")
            if normalized.get("valid") is True:
                valid.append((path, normalized))
        if len(valid) != 1:
            raise QualityValidationError(
                f"quality repetition {repetition} requires exactly one valid receipt"
            )
        path, normalized = valid[0]
        score = normalized.get("quality_score")
        if not isinstance(score, dict):
            raise QualityValidationError("valid quality receipt has no score")
        runs.append(score)
        receipt_digests.append(digest_file(path))
    return tuple(runs), tuple(receipt_digests)


if __name__ == "__main__":
    raise SystemExit(main())
