"""Architecture-neutral goodput scoring for the first serving campaign."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..canonical import digest_file
from .model_serving import BenchmarkPlan
from .serving_measurement import GoodputReplay


SCORING_SCHEMA = "model-serving-scoring/v0alpha1"
_SHA256 = "sha256:"


class ScoringValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BucketScoringRule:
    bucket_id: str
    selected_request_rate: int
    ttft_slo_ms: int
    tpot_slo_ms: int
    reference_goodput_micro_rps: int


@dataclass(frozen=True, slots=True)
class ScoringProfile:
    path: Path
    digest: str
    phase: str
    candidate_repetitions: int
    minimum_joint_attainment_ppm: int
    candidate_goodput_source: str
    legacy_classification_guard_us: int
    legacy_aggregate_tolerance_us: int
    reference_measurement_id: str
    reference_measurement_profile_digest: str
    reference_receipt_digests: tuple[str, ...]
    reference_repetition_scalar_ppm: tuple[int, ...]
    bucket_rules: Mapping[str, BucketScoringRule]

    @classmethod
    def load(cls, path: Path) -> "ScoringProfile":
        resolved = path.resolve()
        try:
            with resolved.open("rb") as source:
                raw = tomllib.load(source)
        except tomllib.TOMLDecodeError as error:
            raise ScoringValidationError("scoring profile is not valid TOML") from error
        _validate_profile(raw)
        legacy = _mapping(raw, "legacy_replay")
        reference = _mapping(raw, "reference")
        rules: dict[str, BucketScoringRule] = {}
        for value in raw["buckets"]:
            rule = BucketScoringRule(
                bucket_id=str(value["id"]),
                selected_request_rate=_positive_int(value, "selected_request_rate"),
                ttft_slo_ms=_positive_int(value, "ttft_slo_ms"),
                tpot_slo_ms=_positive_int(value, "tpot_slo_ms"),
                reference_goodput_micro_rps=_positive_int(
                    value, "reference_goodput_micro_rps"
                ),
            )
            rules[rule.bucket_id] = rule
        return cls(
            path=resolved,
            digest=digest_file(resolved),
            phase=str(raw["phase"]),
            candidate_repetitions=_positive_int(raw, "candidate_repetitions"),
            minimum_joint_attainment_ppm=_positive_int(
                raw, "minimum_joint_attainment_ppm"
            ),
            candidate_goodput_source=str(raw["candidate_goodput_source"]),
            legacy_classification_guard_us=_positive_int(
                legacy, "classification_guard_us"
            ),
            legacy_aggregate_tolerance_us=_positive_int(
                legacy, "aggregate_tolerance_us"
            ),
            reference_measurement_id=str(reference["measurement_id"]),
            reference_measurement_profile_digest=str(
                reference["measurement_profile_digest"]
            ),
            reference_receipt_digests=tuple(reference["receipt_digests"]),
            reference_repetition_scalar_ppm=tuple(
                reference["repetition_scalar_ppm"]
            ),
            bucket_rules=rules,
        )

    @property
    def goodput_slos_ms_by_bucket(self) -> Mapping[str, Mapping[str, int]]:
        return {
            bucket_id: {
                "ttft": rule.ttft_slo_ms,
                "tpot": rule.tpot_slo_ms,
            }
            for bucket_id, rule in self.bucket_rules.items()
        }

    def validate_against(
        self,
        plan: BenchmarkPlan,
        *,
        measurement_profile_digest: str,
        measurement_repetitions: int,
    ) -> None:
        plan_buckets = {bucket.bucket_id: bucket for bucket in plan.buckets}
        if set(plan_buckets) != set(self.bucket_rules):
            raise ScoringValidationError(
                "scoring buckets do not match the benchmark plan"
            )
        for bucket_id, bucket in plan_buckets.items():
            if self.bucket_rules[bucket_id].selected_request_rate != max(
                bucket.request_rates
            ):
                raise ScoringValidationError(
                    "scoring point must be the largest offered rate"
                )
        if self.candidate_repetitions != measurement_repetitions:
            raise ScoringValidationError(
                "scoring and measurement repetition counts differ"
            )
        if self.reference_measurement_profile_digest != measurement_profile_digest:
            raise ScoringValidationError(
                "scoring reference names a different measurement profile"
            )


@dataclass(frozen=True, slots=True)
class RepetitionScore:
    repetition: int
    eligible: bool
    scalar_ppm: int
    bucket_ratio_ppm: Mapping[str, int]
    selected_goodput_micro_rps: Mapping[str, int]
    failures: tuple[str, ...]

    def to_document(self) -> dict[str, Any]:
        return {
            "repetition": self.repetition,
            "eligible": self.eligible,
            "scalar_ppm": self.scalar_ppm,
            "bucket_ratio_ppm": dict(self.bucket_ratio_ppm),
            "selected_goodput_micro_rps": dict(
                self.selected_goodput_micro_rps
            ),
            "failures": list(self.failures),
        }


@dataclass(frozen=True, slots=True)
class CandidateScore:
    eligible: bool
    primary_scalar_ppm: int
    conservative_improvement_lower_bound_ppm: int
    reference_max_scalar_ppm: int
    repetition_scalar_ppm: tuple[int, ...]
    failures: tuple[str, ...]

    @property
    def clears_improvement_bound(self) -> bool:
        return self.eligible and self.conservative_improvement_lower_bound_ppm > 0

    def to_document(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "primary_scalar_ppm": self.primary_scalar_ppm,
            "conservative_improvement_lower_bound_ppm": (
                self.conservative_improvement_lower_bound_ppm
            ),
            "reference_max_scalar_ppm": self.reference_max_scalar_ppm,
            "repetition_scalar_ppm": list(self.repetition_scalar_ppm),
            "clears_improvement_bound": self.clears_improvement_bound,
            "failures": list(self.failures),
        }


def score_repetition(
    profile: ScoringProfile,
    plan: BenchmarkPlan,
    replays: Iterable[GoodputReplay],
    *,
    repetition: int,
    role: str,
) -> RepetitionScore:
    """Score one complete nine-point repetition with no architecture checks."""

    if not isinstance(repetition, int) or isinstance(repetition, bool) or repetition < 1:
        raise ScoringValidationError("repetition must be a positive integer")
    if role not in {"reference", "candidate"}:
        raise ScoringValidationError("score role must be reference or candidate")
    values: dict[tuple[str, int], GoodputReplay] = {}
    for replay in replays:
        key = (replay.bucket_id, replay.request_rate)
        if key in values:
            raise ScoringValidationError("duplicate scored benchmark point")
        values[key] = replay
    expected = {
        (bucket.bucket_id, rate)
        for bucket in plan.buckets
        for rate in bucket.request_rates
    }
    if set(values) != expected:
        raise ScoringValidationError("scored benchmark point set differs")

    failures: list[str] = []
    plan_buckets = {bucket.bucket_id: bucket for bucket in plan.buckets}
    for key in sorted(expected):
        replay = values[key]
        if replay.expected_prompts != plan_buckets[key[0]].num_prompts:
            failures.append(f"{key[0]}/{key[1]} prompt count differs from plan")
        if replay.failed != 0 or replay.completed != replay.expected_prompts:
            failures.append(f"{key[0]}/{key[1]} has request failures")
        if replay.joint_attainment_ppm < profile.minimum_joint_attainment_ppm:
            failures.append(f"{key[0]}/{key[1]} misses joint SLO attainment")
        if role == "candidate" and replay.source != profile.candidate_goodput_source:
            failures.append(f"{key[0]}/{key[1]} lacks direct vLLM goodput")

    ratios: dict[str, int] = {}
    selected: dict[str, int] = {}
    for bucket_id, rule in profile.bucket_rules.items():
        replay = values[(bucket_id, rule.selected_request_rate)]
        selected[bucket_id] = replay.goodput_micro_rps
        ratios[bucket_id] = _round_ratio_ppm(
            replay.goodput_micro_rps, rule.reference_goodput_micro_rps
        )
    scalar = _round_mean(tuple(ratios.values()))
    return RepetitionScore(
        repetition=repetition,
        eligible=not failures,
        scalar_ppm=scalar if not failures else 0,
        bucket_ratio_ppm=ratios,
        selected_goodput_micro_rps=selected,
        failures=tuple(failures),
    )


def summarize_candidate(
    profile: ScoringProfile, repetitions: Iterable[RepetitionScore]
) -> CandidateScore:
    values = tuple(repetitions)
    if len(values) != profile.candidate_repetitions:
        raise ScoringValidationError("candidate repetition count differs")
    if len({value.repetition for value in values}) != len(values):
        raise ScoringValidationError("candidate repetition numbers repeat")
    reported_failures = tuple(
        f"repetition {value.repetition}: {failure}"
        for value in values
        for failure in value.failures
    )
    eligibility_failures = tuple(
        f"repetition {value.repetition}: repetition is ineligible"
        for value in values
        if not value.eligible and not value.failures
    )
    failures = reported_failures + eligibility_failures
    reference_max = max(profile.reference_repetition_scalar_ppm)
    if failures:
        return CandidateScore(
            eligible=False,
            primary_scalar_ppm=0,
            conservative_improvement_lower_bound_ppm=-1_000_000,
            reference_max_scalar_ppm=reference_max,
            repetition_scalar_ppm=tuple(value.scalar_ppm for value in values),
            failures=failures,
        )
    scalars = tuple(value.scalar_ppm for value in values)
    candidate_min = min(scalars)
    lower_bound = int(
        (
            Decimal(candidate_min) * Decimal(1_000_000) / Decimal(reference_max)
        ).to_integral_value(rounding=ROUND_FLOOR)
    ) - 1_000_000
    return CandidateScore(
        eligible=True,
        primary_scalar_ppm=sorted(scalars)[len(scalars) // 2],
        conservative_improvement_lower_bound_ppm=lower_bound,
        reference_max_scalar_ppm=reference_max,
        repetition_scalar_ppm=scalars,
        failures=(),
    )


def _validate_profile(raw: Mapping[str, Any]) -> None:
    _exact_keys(
        raw,
        {
            "schema_version",
            "phase",
            "primary_metric",
            "point_selection",
            "bucket_aggregation",
            "repetition_aggregation",
            "candidate_repetitions",
            "minimum_joint_attainment_ppm",
            "request_failure_policy",
            "candidate_goodput_source",
            "improvement_bound",
            "legacy_replay",
            "reference",
            "buckets",
        },
        "scoring profile",
    )
    expected = {
        "schema_version": SCORING_SCHEMA,
        "phase": "calibration_candidate_sensitivity",
        "primary_metric": "goodput_requests_per_second",
        "point_selection": "largest_offered_rate_per_bucket",
        "bucket_aggregation": "equal_weight_mean_of_reference_ratios",
        "repetition_aggregation": "median",
        "request_failure_policy": "zero_failures_at_every_point",
        "candidate_goodput_source": "vllm_direct",
        "improvement_bound": "candidate_min_over_reference_max",
    }
    if any(raw.get(key) != value for key, value in expected.items()):
        raise ScoringValidationError("unsupported scoring profile")
    if _positive_int(raw, "candidate_repetitions") != 3:
        raise ScoringValidationError("V0 scoring requires three repetitions")
    attainment = _positive_int(raw, "minimum_joint_attainment_ppm")
    if attainment > 1_000_000:
        raise ScoringValidationError("joint attainment cannot exceed one million ppm")

    legacy = _mapping(raw, "legacy_replay")
    _exact_keys(
        legacy,
        {
            "authority",
            "latency_reconstruction",
            "classification_guard_us",
            "aggregate_tolerance_us",
        },
        "legacy replay",
    )
    if (
        legacy.get("authority") != "reference_calibration_only"
        or legacy.get("latency_reconstruction") != "ttft_plus_sum_itls"
    ):
        raise ScoringValidationError("unsupported legacy replay policy")
    _positive_int(legacy, "classification_guard_us")
    _positive_int(legacy, "aggregate_tolerance_us")

    reference = _mapping(raw, "reference")
    _exact_keys(
        reference,
        {
            "measurement_id",
            "measurement_profile_digest",
            "aggregation",
            "receipt_digests",
            "repetition_scalar_ppm",
        },
        "scoring reference",
    )
    if (
        not isinstance(reference.get("measurement_id"), str)
        or not reference["measurement_id"]
        or not _is_digest(reference.get("measurement_profile_digest"))
        or reference.get("aggregation") != "median_bucket_goodput"
    ):
        raise ScoringValidationError("invalid scoring reference")
    receipts = reference.get("receipt_digests")
    scalars = reference.get("repetition_scalar_ppm")
    if (
        not isinstance(receipts, list)
        or len(receipts) != 3
        or any(not _is_digest(value) for value in receipts)
        or not isinstance(scalars, list)
        or len(scalars) != 3
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 1
            for value in scalars
        )
    ):
        raise ScoringValidationError("invalid scoring reference repetitions")

    buckets = raw.get("buckets")
    if not isinstance(buckets, list) or not buckets:
        raise ScoringValidationError("scoring buckets are required")
    identifiers: list[str] = []
    for bucket in buckets:
        if not isinstance(bucket, dict):
            raise ScoringValidationError("scoring bucket must be a table")
        _exact_keys(
            bucket,
            {
                "id",
                "selected_request_rate",
                "ttft_slo_ms",
                "tpot_slo_ms",
                "reference_goodput_micro_rps",
            },
            "scoring bucket",
        )
        identifier = bucket.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ScoringValidationError("scoring bucket id is required")
        identifiers.append(identifier)
        for key in (
            "selected_request_rate",
            "ttft_slo_ms",
            "tpot_slo_ms",
            "reference_goodput_micro_rps",
        ):
            _positive_int(bucket, key)
    if len(set(identifiers)) != len(identifiers):
        raise ScoringValidationError("scoring bucket ids repeat")


def _mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    item = value.get(key)
    if not isinstance(item, dict):
        raise ScoringValidationError(f"{key} must be a table")
    return item


def _exact_keys(value: Mapping[str, Any], expected: set[str], location: str) -> None:
    if set(value) != expected:
        raise ScoringValidationError(f"{location} keys differ")


def _positive_int(value: Mapping[str, Any], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool) or item < 1:
        raise ScoringValidationError(f"{key} must be a positive integer")
    return item


def _is_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(_SHA256)
        and len(value) == len(_SHA256) + 64
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _round_ratio_ppm(numerator: int, denominator: int) -> int:
    return int(
        (
            Decimal(numerator) * Decimal(1_000_000) / Decimal(denominator)
        ).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    )


def _round_mean(values: tuple[int, ...]) -> int:
    if not values:
        raise ScoringValidationError("cannot score an empty bucket set")
    return int(
        (Decimal(sum(values)) / Decimal(len(values))).quantize(
            Decimal("1"), rounding=ROUND_HALF_EVEN
        )
    )
