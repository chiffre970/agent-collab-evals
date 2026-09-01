"""Deterministic derivation of a serving-performance policy proposal."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..adapters.local_measurements import (
    LocalMeasurementBundleStore,
    StoredMeasurementBundle,
)
from ..canonical import (
    DuplicateKeyError,
    digest_bytes,
    digest_file,
    digest_value,
    load_json,
)
from .model_serving import BenchmarkPlan, load_benchmark_plan
from .serving_benchmark import (
    BenchmarkInvocation,
    build_vllm_benchmark_invocations,
)
from .serving_measurement import parse_vllm_benchmark_result


CALIBRATION_PLAN_SCHEMA = "model-serving-performance-calibration-plan/v0alpha1"
CALIBRATION_PROPOSAL_SCHEMA = (
    "model-serving-performance-calibration-proposal/v0alpha1"
)
_REPETITION_ROOT = re.compile(r"repetition-([0-9]{4})-attempt-([0-9]{2})")


class PerformanceCalibrationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PerformanceCalibrationPlan:
    path: Path
    digest: str
    plan_id: str
    required_reference_repetitions: int
    latency_percentile: int
    slo_headroom_ppm: int
    ttft_rounding_ms: int
    tpot_rounding_ms: int
    minimum_joint_attainment_ppm: int
    selected_rate_policy: str
    reference_aggregation: str
    source_bundle_disposition: str
    study_hidden_bundle_policy: str

    @classmethod
    def load(cls, path: Path) -> "PerformanceCalibrationPlan":
        resolved = path.resolve(strict=True)
        try:
            with resolved.open("r", encoding="utf-8") as source:
                value = load_json(source)
        except (json.JSONDecodeError, DuplicateKeyError) as error:
            raise PerformanceCalibrationError(
                "performance calibration plan is not valid JSON"
            ) from error
        expected = {
            "schema_version",
            "plan_id",
            "required_reference_repetitions",
            "latency_percentile",
            "slo_headroom_ppm",
            "ttft_rounding_ms",
            "tpot_rounding_ms",
            "minimum_joint_attainment_ppm",
            "selected_rate_policy",
            "reference_aggregation",
            "source_bundle_disposition",
            "study_hidden_bundle_policy",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise PerformanceCalibrationError(
                "performance calibration plan fields differ"
            )
        if value["schema_version"] != CALIBRATION_PLAN_SCHEMA:
            raise PerformanceCalibrationError(
                "performance calibration plan schema differs"
            )
        positive = (
            "required_reference_repetitions",
            "latency_percentile",
            "slo_headroom_ppm",
            "ttft_rounding_ms",
            "tpot_rounding_ms",
            "minimum_joint_attainment_ppm",
        )
        if any(
            type(value[key]) is not int or value[key] < 1 for key in positive
        ):
            raise PerformanceCalibrationError(
                "performance calibration numeric policy is invalid"
            )
        expected_values = {
            "required_reference_repetitions": 3,
            "latency_percentile": 95,
            "selected_rate_policy": "largest_offered_rate",
            "reference_aggregation": "median_goodput_by_bucket",
            "source_bundle_disposition": (
                "calibration_only_retire_before_study"
            ),
            "study_hidden_bundle_policy": "fresh_seed_after_policy_freeze",
        }
        if any(
            value[key] != expected_value
            for key, expected_value in expected_values.items()
        ):
            raise PerformanceCalibrationError(
                "unsupported performance calibration policy"
            )
        if not 1_000_000 <= value["slo_headroom_ppm"] <= 2_000_000:
            raise PerformanceCalibrationError("SLO headroom is invalid")
        if not 1 <= value["minimum_joint_attainment_ppm"] <= 1_000_000:
            raise PerformanceCalibrationError("joint attainment is invalid")
        return cls(
            path=resolved,
            digest=digest_file(resolved),
            plan_id=str(value["plan_id"]),
            required_reference_repetitions=value[
                "required_reference_repetitions"
            ],
            latency_percentile=value["latency_percentile"],
            slo_headroom_ppm=value["slo_headroom_ppm"],
            ttft_rounding_ms=value["ttft_rounding_ms"],
            tpot_rounding_ms=value["tpot_rounding_ms"],
            minimum_joint_attainment_ppm=value[
                "minimum_joint_attainment_ppm"
            ],
            selected_rate_policy=str(value["selected_rate_policy"]),
            reference_aggregation=str(value["reference_aggregation"]),
            source_bundle_disposition=str(value["source_bundle_disposition"]),
            study_hidden_bundle_policy=str(value["study_hidden_bundle_policy"]),
        )


@dataclass(frozen=True, slots=True)
class CalibrationBundle:
    repetition: int
    receipt_digest: str
    provenance_digest: str
    candidate_manifest_digest: str
    measurement_id: str
    raw_documents: Mapping[str, bytes]


def load_calibration_bundle(
    receipt_path: Path,
    *,
    performance_profile_digest: str,
) -> CalibrationBundle:
    """Load one digest-verified local measurement bundle by receipt path."""
    receipt_path = receipt_path.resolve(strict=True)
    if receipt_path.name != "receipt.json":
        raise PerformanceCalibrationError("calibration receipt path is invalid")
    match = _REPETITION_ROOT.fullmatch(receipt_path.parent.name)
    if match is None:
        raise PerformanceCalibrationError("calibration repetition path is invalid")
    repetition, attempt = (int(value) for value in match.groups())
    measurement_id = receipt_path.parent.parent.name
    store = LocalMeasurementBundleStore(receipt_path.parents[2])
    bundle = store.load(measurement_id, repetition, attempt=attempt)
    provenance_digest, candidate_manifest_digest = _validate_bundle(
        bundle,
        repetition=repetition,
        performance_profile_digest=performance_profile_digest,
    )
    return CalibrationBundle(
        repetition=repetition,
        receipt_digest=digest_bytes(receipt_path.read_bytes()),
        provenance_digest=provenance_digest,
        candidate_manifest_digest=candidate_manifest_digest,
        measurement_id=measurement_id,
        raw_documents=bundle.raw_documents,
    )


def derive_performance_calibration(
    calibration_plan: PerformanceCalibrationPlan,
    performance_profile: Path,
    bundles: Iterable[CalibrationBundle],
    *,
    model_source: str,
    reference_candidate_manifest_digest: str,
    prior_slos_ms: Mapping[str, Mapping[str, int]],
) -> dict[str, Any]:
    """Derive a proposal from a complete reference series without mutating policy."""
    plan = load_benchmark_plan(performance_profile)
    values = tuple(bundles)
    expected_repetitions = tuple(
        range(1, calibration_plan.required_reference_repetitions + 1)
    )
    if tuple(sorted(bundle.repetition for bundle in values)) != expected_repetitions:
        raise PerformanceCalibrationError(
            "calibration requires exactly repetitions 1 through 3"
        )
    if len({bundle.receipt_digest for bundle in values}) != len(values):
        raise PerformanceCalibrationError("calibration receipts repeat")
    if any(
        bundle.candidate_manifest_digest != reference_candidate_manifest_digest
        for bundle in values
    ):
        raise PerformanceCalibrationError(
            "calibration source is not the registered reference candidate"
        )
    provenance = {bundle.provenance_digest for bundle in values}
    if len(provenance) != 1:
        raise PerformanceCalibrationError(
            "calibration reference environments differ"
        )
    _validate_prior_slos(plan, prior_slos_ms)
    invocations = _invocations(plan, model_source)
    expected_names = {invocation.result_file.name for invocation in invocations}
    parsed: dict[tuple[int, str, int], tuple[BenchmarkInvocation, bytes, Any]] = {}
    for bundle in values:
        if set(bundle.raw_documents) != expected_names:
            raise PerformanceCalibrationError(
                "calibration benchmark point set differs"
            )
        for invocation in invocations:
            raw = bundle.raw_documents[invocation.result_file.name]
            point = parse_vllm_benchmark_result(
                raw,
                invocation=invocation,
                model_source=model_source,
                metric_percentiles=plan.metric_percentiles,
            )
            if not point.valid:
                raise PerformanceCalibrationError(
                    "calibration reference contains a failed request"
                )
            _verify_direct_goodput(
                raw, invocation, prior_slos_ms[invocation.bucket_id]
            )
            key = (
                bundle.repetition,
                invocation.bucket_id,
                invocation.request_rate,
            )
            parsed[key] = (invocation, raw, point)

    bucket_rules: dict[str, dict[str, int]] = {}
    for bucket in plan.buckets:
        points = tuple(
            point
            for (repetition, bucket_id, rate), (_, raw, point) in parsed.items()
            if bucket_id == bucket.bucket_id
        )
        ttft = _derive_slo_ms(
            max(point.latency_us["p95_ttft"] for point in points),
            calibration_plan.slo_headroom_ppm,
            calibration_plan.ttft_rounding_ms,
        )
        tpot = _derive_slo_ms(
            max(point.latency_us["p95_tpot"] for point in points),
            calibration_plan.slo_headroom_ppm,
            calibration_plan.tpot_rounding_ms,
        )
        bucket_rules[bucket.bucket_id] = {
            "selected_request_rate": max(bucket.request_rates),
            "ttft_slo_ms": ttft,
            "tpot_slo_ms": tpot,
        }

    goodput: dict[tuple[int, str, int], tuple[int, int]] = {}
    for key, (invocation, raw, point) in parsed.items():
        rule = bucket_rules[invocation.bucket_id]
        good_count, goodput_micro_rps = _classify(
            raw,
            invocation,
            {"ttft": rule["ttft_slo_ms"], "tpot": rule["tpot_slo_ms"]},
        )
        attainment = _ratio_ppm(good_count, point.completed)
        if attainment < calibration_plan.minimum_joint_attainment_ppm:
            raise PerformanceCalibrationError(
                f"derived SLO does not admit {invocation.bucket_id}/"
                f"{invocation.request_rate} repetition {key[0]}"
            )
        goodput[key] = (attainment, goodput_micro_rps)

    references: dict[str, int] = {}
    for bucket in plan.buckets:
        rate = max(bucket.request_rates)
        references[bucket.bucket_id] = _median(
            tuple(
                goodput[(repetition, bucket.bucket_id, rate)][1]
                for repetition in expected_repetitions
            )
        )
        bucket_rules[bucket.bucket_id]["reference_goodput_micro_rps"] = references[
            bucket.bucket_id
        ]

    repetition_scalars = []
    for repetition in expected_repetitions:
        ratios = tuple(
            _ratio_ppm(
                goodput[
                    (repetition, bucket.bucket_id, max(bucket.request_rates))
                ][1],
                references[bucket.bucket_id],
            )
            for bucket in plan.buckets
        )
        repetition_scalars.append(_mean_integer(ratios))

    return {
        "schema_version": CALIBRATION_PROPOSAL_SCHEMA,
        "status": "calibration_proposal_not_registered",
        "calibration_plan_id": calibration_plan.plan_id,
        "calibration_plan_digest": calibration_plan.digest,
        "performance_profile_digest": digest_file(performance_profile),
        "reference_provenance_digest": next(iter(provenance)),
        "source_bundle_disposition": calibration_plan.source_bundle_disposition,
        "study_hidden_bundle_policy": calibration_plan.study_hidden_bundle_policy,
        "source_receipts": [
            {
                "repetition": bundle.repetition,
                "measurement_id": bundle.measurement_id,
                "receipt_digest": bundle.receipt_digest,
            }
            for bundle in sorted(values, key=lambda value: value.repetition)
        ],
        "derivation": {
            "latency_percentile": calibration_plan.latency_percentile,
            "slo_headroom_ppm": calibration_plan.slo_headroom_ppm,
            "ttft_rounding_ms": calibration_plan.ttft_rounding_ms,
            "tpot_rounding_ms": calibration_plan.tpot_rounding_ms,
            "minimum_joint_attainment_ppm": (
                calibration_plan.minimum_joint_attainment_ppm
            ),
            "selected_rate_policy": calibration_plan.selected_rate_policy,
            "reference_aggregation": calibration_plan.reference_aggregation,
        },
        "reference_repetition_scalar_ppm": repetition_scalars,
        "buckets": [
            {"id": bucket.bucket_id, **bucket_rules[bucket.bucket_id]}
            for bucket in plan.buckets
        ],
    }


def _validate_bundle(
    bundle: StoredMeasurementBundle,
    *,
    repetition: int,
    performance_profile_digest: str,
) -> tuple[str, str]:
    normalized = bundle.receipt["normalized"]
    if (
        normalized.get("repetition") != repetition
        or normalized.get("performance_profile_digest")
        != performance_profile_digest
        or normalized.get("failure") is not None
        or normalized.get("parse_errors") != []
        or normalized.get("environment_errors") != []
        or not isinstance(normalized.get("performance_score"), dict)
    ):
        raise PerformanceCalibrationError(
            "calibration normalized evidence is incomplete"
        )
    durable = normalized.get("durable_evidence")
    if not isinstance(durable, dict) or durable.get("raw_digests") != bundle.receipt[
        "raw_digests"
    ]:
        raise PerformanceCalibrationError("calibration durable evidence differs")
    platform = normalized.get("platform_build")
    remote = normalized.get("remote_receipt")
    if (
        not isinstance(normalized.get("campaign_manifest_digest"), str)
        or not isinstance(normalized.get("candidate_manifest_digest"), str)
        or not isinstance(platform, dict)
        or not isinstance(platform.get("git_commit"), str)
        or not isinstance(remote, dict)
    ):
        raise PerformanceCalibrationError(
            "calibration reference provenance is incomplete"
        )
    environment = remote.get("environment")
    before = remote.get("gpu_before")
    after = remote.get("gpu_after")
    if (
        not isinstance(environment, dict)
        or not isinstance(before, dict)
        or not isinstance(after, dict)
    ):
        raise PerformanceCalibrationError(
            "calibration environment provenance is incomplete"
        )
    gpu_keys = ("name", "memory_mib", "driver_version", "power_limit_watts")
    if any(before.get(key) != after.get(key) for key in gpu_keys):
        raise PerformanceCalibrationError("calibration GPU identity drifted")
    provenance = {
        "campaign_manifest_digest": normalized["campaign_manifest_digest"],
        "candidate_manifest_digest": normalized["candidate_manifest_digest"],
        "performance_profile_digest": performance_profile_digest,
        "git_commit": platform["git_commit"],
        "model_id": remote.get("model_id"),
        "model_revision": remote.get("model_revision"),
        "vllm_version": remote.get("vllm_version"),
        "environment": environment,
        "gpu": {key: before.get(key) for key in gpu_keys},
    }
    if any(
        provenance[key] is None
        for key in ("model_id", "model_revision", "vllm_version")
    ):
        raise PerformanceCalibrationError(
            "calibration model provenance is incomplete"
        )
    return digest_value(provenance), str(normalized["candidate_manifest_digest"])


def _invocations(
    plan: BenchmarkPlan, model_source: str
) -> tuple[BenchmarkInvocation, ...]:
    return build_vllm_benchmark_invocations(
        plan,
        base_url="http://127.0.0.1:8000",
        model_source=model_source,
        served_model_name="target-model",
        result_directory=Path("."),
        warmup_requests=1,
    )


def _validate_prior_slos(
    plan: BenchmarkPlan, slos: Mapping[str, Mapping[str, int]]
) -> None:
    if set(slos) != {bucket.bucket_id for bucket in plan.buckets} or any(
        set(value) != {"ttft", "tpot"}
        or any(type(limit) is not int or limit < 1 for limit in value.values())
        for value in slos.values()
    ):
        raise PerformanceCalibrationError("prior SLO policy is invalid")


def _verify_direct_goodput(
    raw: bytes,
    invocation: BenchmarkInvocation,
    slos_ms: Mapping[str, int],
) -> None:
    document = _decimal_json(raw)
    good_count, _ = _classify(raw, invocation, slos_ms)
    direct = document.get("request_goodput")
    duration = _decimal(document["duration"])
    if direct is None:
        raise PerformanceCalibrationError(
            "calibration requires direct vLLM goodput evidence"
        )
    direct_count = _decimal(direct) * duration
    rounded = int(direct_count.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))
    if (
        abs(direct_count - Decimal(rounded)) > Decimal("0.000001")
        or rounded != good_count
    ):
        raise PerformanceCalibrationError(
            "direct vLLM goodput differs from saved request detail"
        )


def _classify(
    raw: bytes,
    invocation: BenchmarkInvocation,
    slos_ms: Mapping[str, int],
) -> tuple[int, int]:
    document = _decimal_json(raw)
    errors = document["errors"]
    ttfts = document["ttfts"]
    itls = document["itls"]
    output_lens = document["output_lens"]
    ttft_limit = Decimal(slos_ms["ttft"]) / Decimal(1000)
    tpot_limit = Decimal(slos_ms["tpot"]) / Decimal(1000)
    good = 0
    for index, error in enumerate(errors):
        if error:
            continue
        ttft = _decimal(ttfts[index])
        intervals = tuple(_decimal(value) for value in itls[index])
        tpot = (
            sum(intervals, Decimal(0)) / Decimal(output_lens[index] - 1)
            if output_lens[index] > 1
            else Decimal(0)
        )
        if ttft <= ttft_limit and tpot <= tpot_limit:
            good += 1
    duration = _decimal(document["duration"])
    micro_rps = int(
        (Decimal(good) / duration * Decimal(1_000_000)).quantize(
            Decimal("1"), rounding=ROUND_HALF_EVEN
        )
    )
    return good, micro_rps


def _derive_slo_ms(worst_p95_us: int, headroom_ppm: int, step_ms: int) -> int:
    units = (
        Decimal(worst_p95_us)
        * Decimal(headroom_ppm)
        / Decimal(1_000_000)
        / Decimal(step_ms * 1000)
    ).to_integral_value(rounding=ROUND_CEILING)
    return int(units) * step_ms


def _ratio_ppm(numerator: int, denominator: int) -> int:
    if denominator < 1:
        raise PerformanceCalibrationError("calibration ratio denominator is invalid")
    return int(
        (Decimal(numerator) * Decimal(1_000_000) / Decimal(denominator)).quantize(
            Decimal("1"), rounding=ROUND_HALF_EVEN
        )
    )


def _mean_integer(values: tuple[int, ...]) -> int:
    return int(
        (Decimal(sum(values)) / Decimal(len(values))).quantize(
            Decimal("1"), rounding=ROUND_HALF_EVEN
        )
    )


def _median(values: tuple[int, ...]) -> int:
    if not values or len(values) % 2 == 0:
        raise PerformanceCalibrationError("calibration median requires an odd series")
    return sorted(values)[len(values) // 2]


def _decimal_json(raw: bytes) -> Mapping[str, Any]:
    try:
        value = json.loads(raw, parse_float=Decimal, parse_int=int)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PerformanceCalibrationError("calibration raw JSON is invalid") from error
    if not isinstance(value, dict):
        raise PerformanceCalibrationError("calibration raw result is not an object")
    return value


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise PerformanceCalibrationError("calibration decimal is invalid")
    try:
        return Decimal(value)
    except Exception as error:
        raise PerformanceCalibrationError("calibration decimal is invalid") from error
