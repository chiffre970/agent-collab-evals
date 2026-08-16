"""Frozen measurement policy and normalization of pinned vLLM results."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Mapping

from ..canonical import digest_bytes, digest_file
from .model_serving import BenchmarkPlan
from .serving_benchmark import BenchmarkInvocation


MEASUREMENT_SCHEMA = "model-serving-measurement/v0alpha1"
_LATENCY_METRICS = ("ttft", "tpot", "itl", "e2el")


class MeasurementValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MeasurementPoint:
    repetition: int
    position: int
    bucket_id: str
    request_rate: int


@dataclass(frozen=True, slots=True)
class MeasurementProfile:
    path: Path
    digest: str
    phase: str
    repetitions: int
    point_order: str
    container_reset: str
    server_reset: str
    model_weight_cache: str
    engine_cache: str
    primary_scope: str
    cold_start_scope: str
    server_canary_requests: int
    point_warmups: int
    primary_timing_source: str
    lifecycle_timing_source: str
    modal_timing_role: str
    client_timing_role: str
    base_image_digest: str
    gpu_type: str
    gpu_memory_mib: int
    gpu_driver_version: str
    gpu_power_limit_watts: str
    modal_client_version: str
    resolved_package_digest: str
    resolved_package_policy: str
    driver_policy: str
    clocks_and_power_policy: str
    server_startup_timeout_seconds: int
    point_timeout_seconds: int
    repetition_timeout_seconds: int
    request_failure: str
    point_failure: str
    retry: str
    max_attempts: int
    canary_bracketing: str
    canary_drift_policy: str
    raw_persistence: str
    normalized_persistence: str
    release_policy: str

    @classmethod
    def load(cls, path: Path) -> "MeasurementProfile":
        resolved = path.resolve()
        try:
            with resolved.open("rb") as source:
                raw = tomllib.load(source)
        except tomllib.TOMLDecodeError as error:
            raise MeasurementValidationError(
                "measurement profile is not valid TOML"
            ) from error
        _validate_profile(raw)
        lifecycle = _mapping(raw, "lifecycle")
        warmup = _mapping(raw, "warmup")
        timing = _mapping(raw, "timing")
        environment = _mapping(raw, "environment")
        timeouts = _mapping(raw, "timeouts")
        failure = _mapping(raw, "failure")
        canary = _mapping(raw, "canary")
        persistence = _mapping(raw, "persistence")
        return cls(
            path=resolved,
            digest=digest_file(resolved),
            phase=str(raw["phase"]),
            repetitions=_positive_int(raw, "repetitions"),
            point_order=str(raw["point_order"]),
            container_reset=str(lifecycle["container_reset"]),
            server_reset=str(lifecycle["server_reset"]),
            model_weight_cache=str(lifecycle["model_weight_cache"]),
            engine_cache=str(lifecycle["engine_cache"]),
            primary_scope=str(lifecycle["primary_scope"]),
            cold_start_scope=str(lifecycle["cold_start_scope"]),
            server_canary_requests=_positive_int(warmup, "server_canary_requests"),
            point_warmups=_positive_int(warmup, "point_requests"),
            primary_timing_source=str(timing["primary_source"]),
            lifecycle_timing_source=str(timing["lifecycle_source"]),
            modal_timing_role=str(timing["modal_source"]),
            client_timing_role=str(timing["client_source"]),
            base_image_digest=str(environment["base_image_digest"]),
            gpu_type=str(environment["gpu_type"]),
            gpu_memory_mib=_positive_int(environment, "gpu_memory_mib"),
            gpu_driver_version=str(environment["gpu_driver_version"]),
            gpu_power_limit_watts=str(environment["gpu_power_limit_watts"]),
            modal_client_version=str(environment["modal_client_version"]),
            resolved_package_digest=str(environment["resolved_package_digest"]),
            resolved_package_policy=str(environment["resolved_package_policy"]),
            driver_policy=str(environment["driver_policy"]),
            clocks_and_power_policy=str(environment["clocks_and_power_policy"]),
            server_startup_timeout_seconds=_positive_int(
                timeouts, "server_startup_seconds"
            ),
            point_timeout_seconds=_positive_int(timeouts, "point_seconds"),
            repetition_timeout_seconds=_positive_int(
                timeouts, "repetition_seconds"
            ),
            request_failure=str(failure["request_failure"]),
            point_failure=str(failure["point_failure"]),
            retry=str(failure["retry"]),
            max_attempts=_positive_int(failure, "max_attempts"),
            canary_bracketing=str(canary["bracketing"]),
            canary_drift_policy=str(canary["drift_policy"]),
            raw_persistence=str(persistence["raw"]),
            normalized_persistence=str(persistence["normalized"]),
            release_policy=str(persistence["release"]),
        )

    def schedule(self, plan: BenchmarkPlan) -> tuple[MeasurementPoint, ...]:
        """Expand the frozen canonical order without random runtime decisions."""

        points: list[MeasurementPoint] = []
        position = 0
        for repetition in range(1, self.repetitions + 1):
            for bucket in plan.buckets:
                for request_rate in bucket.request_rates:
                    position += 1
                    points.append(
                        MeasurementPoint(
                            repetition=repetition,
                            position=position,
                            bucket_id=bucket.bucket_id,
                            request_rate=request_rate,
                        )
                    )
        return tuple(points)


@dataclass(frozen=True, slots=True)
class VllmBenchmarkPointResult:
    bucket_id: str
    request_rate: int
    expected_prompts: int
    completed: int
    failed: int
    valid: bool
    duration_us: int
    total_input_tokens: int
    total_output_tokens: int
    request_throughput_micro_rps: int
    request_goodput_micro_rps: int | None
    output_throughput_micro_tps: int
    total_token_throughput_micro_tps: int
    latency_us: Mapping[str, int]
    raw_digest: str

    def to_document(self) -> dict[str, Any]:
        return {
            "bucket_id": self.bucket_id,
            "request_rate": self.request_rate,
            "expected_prompts": self.expected_prompts,
            "completed": self.completed,
            "failed": self.failed,
            "valid": self.valid,
            "duration_us": self.duration_us,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "request_throughput_micro_rps": self.request_throughput_micro_rps,
            "request_goodput_micro_rps": self.request_goodput_micro_rps,
            "output_throughput_micro_tps": self.output_throughput_micro_tps,
            "total_token_throughput_micro_tps": (
                self.total_token_throughput_micro_tps
            ),
            "latency_us": dict(self.latency_us),
            "raw_digest": self.raw_digest,
        }


@dataclass(frozen=True, slots=True)
class GoodputReplay:
    bucket_id: str
    request_rate: int
    expected_prompts: int
    completed: int
    failed: int
    good_completed: int
    joint_attainment_ppm: int
    goodput_micro_rps: int
    source: str
    minimum_slo_margin_us: int
    aggregate_reconstruction_error_us: int
    raw_digest: str

    def to_document(self) -> dict[str, Any]:
        return {
            "bucket_id": self.bucket_id,
            "request_rate": self.request_rate,
            "expected_prompts": self.expected_prompts,
            "completed": self.completed,
            "failed": self.failed,
            "good_completed": self.good_completed,
            "joint_attainment_ppm": self.joint_attainment_ppm,
            "goodput_micro_rps": self.goodput_micro_rps,
            "source": self.source,
            "minimum_slo_margin_us": self.minimum_slo_margin_us,
            "aggregate_reconstruction_error_us": (
                self.aggregate_reconstruction_error_us
            ),
            "raw_digest": self.raw_digest,
        }


def parse_vllm_benchmark_result(
    raw_bytes: bytes,
    *,
    invocation: BenchmarkInvocation,
    model_source: str,
    metric_percentiles: tuple[int, ...],
) -> VllmBenchmarkPointResult:
    """Validate vLLM 0.21 output and normalize floats into integer units."""

    document = _load_decimal_json(raw_bytes)
    if not isinstance(document, dict):
        raise MeasurementValidationError("vLLM result must be a JSON object")

    expected_scalars = {
        "backend": "openai-chat",
        "endpoint_type": "openai-chat",
        "model_id": model_source,
        "num_prompts": invocation.num_prompts,
    }
    for key, expected in expected_scalars.items():
        if document.get(key) != expected:
            raise MeasurementValidationError(f"vLLM result {key} mismatch")
    if _decimal(document, "request_rate") != Decimal(invocation.request_rate):
        raise MeasurementValidationError("vLLM result request_rate mismatch")

    completed = _nonnegative_int(document, "completed")
    failed = _nonnegative_int(document, "failed")
    if completed + failed != invocation.num_prompts:
        raise MeasurementValidationError(
            "vLLM completed and failed counts do not match num_prompts"
        )

    input_lens = _integer_list(document, "input_lens", invocation.num_prompts)
    output_lens = _integer_list(document, "output_lens", invocation.num_prompts)
    for key in ("ttfts", "itls", "start_times", "generated_texts", "errors"):
        value = document.get(key)
        if not isinstance(value, list) or len(value) != invocation.num_prompts:
            raise MeasurementValidationError(
                f"vLLM detailed field {key} must match num_prompts"
            )

    ttfts = _decimal_list(document, "ttfts", invocation.num_prompts)
    _decimal_list(document, "start_times", invocation.num_prompts)
    _nested_decimal_list(document, "itls", invocation.num_prompts)
    generated_texts = document["generated_texts"]
    if any(not isinstance(value, str) for value in generated_texts):
        raise MeasurementValidationError("vLLM generated_texts must be strings")
    total_input = _nonnegative_int(document, "total_input_tokens")
    total_output = _nonnegative_int(document, "total_output_tokens")
    errors = document["errors"]
    if any(not isinstance(error, str) for error in errors):
        raise MeasurementValidationError("vLLM errors must be strings")
    successful = tuple(index for index, error in enumerate(errors) if not error)
    if len(successful) != completed:
        raise MeasurementValidationError("vLLM error details do not match counts")
    if total_input != sum(input_lens[index] for index in successful) or total_output != sum(
        output_lens[index] for index in successful
    ):
        raise MeasurementValidationError("vLLM token totals do not match details")
    successful_input_lengths = {input_lens[index] for index in successful}
    if (
        len(successful_input_lengths) != 1
        or next(iter(successful_input_lengths)) < invocation.input_tokens
        or any(
            output_lens[index] != invocation.output_tokens or ttfts[index] <= 0
            for index in successful
        )
    ):
        raise MeasurementValidationError(
            "vLLM successful request lengths or TTFT differ from fixed workload"
        )
    if any(output_lens[index] != 0 for index, error in enumerate(errors) if error):
        raise MeasurementValidationError(
            "vLLM failed requests must have zero output length"
        )

    duration = _positive_decimal(document, "duration")
    request_throughput = _nonnegative_decimal(document, "request_throughput")
    output_throughput = _nonnegative_decimal(document, "output_throughput")
    total_token_throughput = _nonnegative_decimal(
        document, "total_token_throughput"
    )
    _require_close(request_throughput, Decimal(completed) / duration, "request")
    _require_close(output_throughput, Decimal(total_output) / duration, "output")
    _require_close(
        total_token_throughput,
        Decimal(total_input + total_output) / duration,
        "total token",
    )

    goodput_value = document.get("request_goodput")
    request_goodput = (
        None
        if goodput_value is None
        else _nonnegative_decimal(document, "request_goodput")
    )

    latency_us: dict[str, int] = {}
    for metric in _LATENCY_METRICS:
        for statistic in ("mean", "median", "std"):
            key = f"{statistic}_{metric}_ms"
            latency_us[f"{statistic}_{metric}"] = _milliseconds_to_microseconds(
                _nonnegative_decimal(document, key)
            )
        for percentile in metric_percentiles:
            key = f"p{percentile}_{metric}_ms"
            latency_us[f"p{percentile}_{metric}"] = (
                _milliseconds_to_microseconds(_nonnegative_decimal(document, key))
            )

    return VllmBenchmarkPointResult(
        bucket_id=invocation.bucket_id,
        request_rate=invocation.request_rate,
        expected_prompts=invocation.num_prompts,
        completed=completed,
        failed=failed,
        valid=failed == 0 and completed == invocation.num_prompts,
        duration_us=_to_integer_units(duration, Decimal("1000000")),
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        request_throughput_micro_rps=_to_integer_units(
            request_throughput, Decimal("1000000")
        ),
        request_goodput_micro_rps=(
            None
            if request_goodput is None
            else _to_integer_units(request_goodput, Decimal("1000000"))
        ),
        output_throughput_micro_tps=_to_integer_units(
            output_throughput, Decimal("1000000")
        ),
        total_token_throughput_micro_tps=_to_integer_units(
            total_token_throughput, Decimal("1000000")
        ),
        latency_us=latency_us,
        raw_digest=digest_bytes(raw_bytes),
    )


def replay_vllm_goodput(
    raw_bytes: bytes,
    *,
    invocation: BenchmarkInvocation,
    model_source: str,
    goodput_slos_ms: Mapping[str, int],
    legacy_classification_guard_us: int,
    aggregate_tolerance_us: int,
) -> GoodputReplay:
    """Replay TTFT/TPOT goodput from detailed vLLM 0.21 evidence.

    New measurements must ask vLLM to calculate goodput directly while its
    per-request latency is still in memory. Older calibration JSON omitted
    latency, so this function can reconstruct it as ``ttft + sum(itls)`` only
    when every request is safely outside a declared ambiguity guard.
    """

    if set(goodput_slos_ms) != {"ttft", "tpot"}:
        raise MeasurementValidationError(
            "V0 goodput replay requires exactly TTFT and TPOT SLOs"
        )
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 1
        for value in goodput_slos_ms.values()
    ):
        raise MeasurementValidationError(
            "goodput SLOs must be positive integer milliseconds"
        )
    for name, value in {
        "legacy classification guard": legacy_classification_guard_us,
        "aggregate tolerance": aggregate_tolerance_us,
    }.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise MeasurementValidationError(f"{name} must be positive microseconds")

    document = _load_decimal_json(raw_bytes)
    if not isinstance(document, dict):
        raise MeasurementValidationError("vLLM result must be a JSON object")
    if document.get("model_id") != model_source:
        raise MeasurementValidationError("vLLM result model_id mismatch")
    if (
        document.get("backend") != "openai-chat"
        or document.get("endpoint_type") != "openai-chat"
    ):
        raise MeasurementValidationError("vLLM result backend mismatch")
    if document.get("num_prompts") != invocation.num_prompts:
        raise MeasurementValidationError("vLLM result num_prompts mismatch")
    if _decimal(document, "request_rate") != Decimal(invocation.request_rate):
        raise MeasurementValidationError("vLLM result request_rate mismatch")

    completed = _nonnegative_int(document, "completed")
    failed = _nonnegative_int(document, "failed")
    if completed + failed != invocation.num_prompts:
        raise MeasurementValidationError(
            "vLLM completed and failed counts do not match num_prompts"
        )
    errors = document.get("errors")
    if (
        not isinstance(errors, list)
        or len(errors) != invocation.num_prompts
        or any(not isinstance(error, str) for error in errors)
    ):
        raise MeasurementValidationError(
            "vLLM errors must contain one string per prompt"
        )
    successful = tuple(index for index, error in enumerate(errors) if not error)
    if len(successful) != completed:
        raise MeasurementValidationError("vLLM error details do not match counts")

    output_lens = _integer_list(document, "output_lens", invocation.num_prompts)
    ttfts = _decimal_list(document, "ttfts", invocation.num_prompts)
    itls = _nested_decimal_list(document, "itls", invocation.num_prompts)
    duration = _positive_decimal(document, "duration")
    ttft_limit = Decimal(goodput_slos_ms["ttft"]) / Decimal(1000)
    tpot_limit = Decimal(goodput_slos_ms["tpot"]) / Decimal(1000)

    reconstructed_good = 0
    minimum_margin: Decimal | None = None
    successful_ttfts: list[Decimal] = []
    successful_tpots: list[Decimal] = []
    successful_e2els: list[Decimal] = []
    for index in successful:
        ttft = ttfts[index]
        output_len = output_lens[index]
        decode_latency = sum(itls[index], Decimal(0))
        tpot = (
            decode_latency / Decimal(output_len - 1)
            if output_len > 1
            else Decimal(0)
        )
        e2el = ttft + decode_latency
        successful_ttfts.append(ttft)
        successful_tpots.append(tpot)
        successful_e2els.append(e2el)
        margins = (abs(ttft - ttft_limit), abs(tpot - tpot_limit))
        request_margin = min(margins)
        minimum_margin = (
            request_margin
            if minimum_margin is None
            else min(minimum_margin, request_margin)
        )
        if ttft <= ttft_limit and tpot <= tpot_limit:
            reconstructed_good += 1

    if completed == 0 or minimum_margin is None:
        raise MeasurementValidationError("goodput replay requires a completed request")

    aggregate_errors = (
        abs(
            _mean(successful_ttfts) * Decimal(1000)
            - _nonnegative_decimal(document, "mean_ttft_ms")
        ),
        abs(
            _mean(successful_tpots) * Decimal(1000)
            - _nonnegative_decimal(document, "mean_tpot_ms")
        ),
        abs(
            _mean(successful_e2els) * Decimal(1000)
            - _nonnegative_decimal(document, "mean_e2el_ms")
        ),
    )
    maximum_aggregate_error = max(aggregate_errors)
    maximum_aggregate_error_us = _milliseconds_to_microseconds(
        maximum_aggregate_error
    )
    if maximum_aggregate_error > Decimal(aggregate_tolerance_us) / Decimal(1000):
        raise MeasurementValidationError(
            "saved vLLM details do not reconstruct aggregate latency"
        )

    direct_value = document.get("request_goodput")
    if direct_value is None:
        minimum_margin_us = _to_integer_units(
            minimum_margin, Decimal("1000000")
        )
        if (
            minimum_margin * Decimal(1000000)
            <= Decimal(legacy_classification_guard_us)
        ):
            raise MeasurementValidationError(
                "legacy goodput replay is ambiguous at an SLO boundary"
            )
        good_completed = reconstructed_good
        source = "guarded_saved_detail_replay"
    else:
        request_goodput = _nonnegative_decimal(document, "request_goodput")
        direct_count = request_goodput * duration
        good_completed = int(
            direct_count.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
        )
        if abs(direct_count - Decimal(good_completed)) > Decimal("0.000001"):
            raise MeasurementValidationError(
                "direct vLLM goodput does not resolve to a request count"
            )
        if not 0 <= good_completed <= completed:
            raise MeasurementValidationError("direct vLLM goodput count is invalid")
        minimum_margin_us = _to_integer_units(
            minimum_margin, Decimal("1000000")
        )
        if (
            good_completed != reconstructed_good
            and minimum_margin_us > legacy_classification_guard_us
        ):
            raise MeasurementValidationError(
                "direct and reconstructed vLLM goodput disagree"
            )
        source = "vllm_direct"

    return GoodputReplay(
        bucket_id=invocation.bucket_id,
        request_rate=invocation.request_rate,
        expected_prompts=invocation.num_prompts,
        completed=completed,
        failed=failed,
        good_completed=good_completed,
        joint_attainment_ppm=_to_integer_units(
            Decimal(good_completed) / Decimal(completed), Decimal("1000000")
        ),
        goodput_micro_rps=_to_integer_units(
            Decimal(good_completed) / duration, Decimal("1000000")
        ),
        source=source,
        minimum_slo_margin_us=minimum_margin_us,
        aggregate_reconstruction_error_us=maximum_aggregate_error_us,
        raw_digest=digest_bytes(raw_bytes),
    )


def _validate_profile(raw: Mapping[str, Any]) -> None:
    _exact_keys(
        raw,
        {
            "schema_version",
            "phase",
            "repetitions",
            "point_order",
            "lifecycle",
            "warmup",
            "timing",
            "environment",
            "timeouts",
            "failure",
            "canary",
            "persistence",
        },
        "measurement profile",
    )
    expected_top = {
        "schema_version": MEASUREMENT_SCHEMA,
        "phase": "calibration",
        "point_order": "canonical_per_repetition",
    }
    for key, expected in expected_top.items():
        if raw.get(key) != expected:
            raise MeasurementValidationError(f"unsupported measurement {key}")
    _positive_int(raw, "repetitions")

    expected_sections = {
        "lifecycle": {
            "container_reset": "fresh_single_use_per_repetition",
            "server_reset": "new_process_per_repetition",
            "model_weight_cache": "prepopulated_pinned_revision",
            "engine_cache": "fresh_ephemeral_per_repetition",
            "primary_scope": "post_warmup_steady_state",
            "cold_start_scope": "process_spawn_to_health_secondary",
        },
        "timing": {
            "primary_source": "vllm_in_container_perf_counter",
            "lifecycle_source": "in_container_monotonic",
            "modal_source": "observational_only",
            "client_source": "observational_only",
        },
        "environment": {
            "base_image_digest": "sha256:0a254a86e28379f7a761c73caf4874247d5e3fbcf57bd99a44856ccf9098e092",
            "gpu_type": "L4",
            "gpu_memory_mib": 23034,
            "gpu_driver_version": "580.95.05",
            "gpu_power_limit_watts": "72.00",
            "modal_client_version": "1.5.4",
            "resolved_package_digest": "sha256:4455c0b21d306127bf6f61ddc5319f4898aab49732bc6dbf6a1da2658cd5111b",
            "resolved_package_policy": "require_exact_digest",
            "driver_policy": "require_exact_version",
            "clocks_and_power_policy": (
                "provider_managed_record_observable"
            ),
        },
        "failure": {
            "request_failure": "invalidate_repetition",
            "point_failure": "abort_repetition",
            "retry": "one_whole_repetition_for_infrastructure_only",
        },
        "canary": {
            "bracketing": "reference_before_and_after_candidate_batch",
            "drift_policy": "record_only_until_calibration",
        },
        "persistence": {
            "raw": "verbatim_evaluator_private",
            "normalized": "integer_units_atomic_bundle",
            "release": "after_registered_boundary",
        },
    }
    for section_name, expected in expected_sections.items():
        section = _mapping(raw, section_name)
        extra_keys = {"max_attempts"} if section_name == "failure" else set()
        _exact_keys(section, set(expected) | extra_keys, section_name)
        for key, value in expected.items():
            if section.get(key) != value:
                raise MeasurementValidationError(
                    f"unsupported measurement {section_name}.{key}"
                )
    if _positive_int(_mapping(raw, "failure"), "max_attempts") != 2:
        raise MeasurementValidationError(
            "one whole-repetition retry requires exactly two attempts"
        )

    warmup = _mapping(raw, "warmup")
    _exact_keys(warmup, {"server_canary_requests", "point_requests"}, "warmup")
    if _positive_int(warmup, "server_canary_requests") != 1:
        raise MeasurementValidationError(
            "the calibration executor supports exactly one bracketing canary"
        )
    _positive_int(warmup, "point_requests")

    timeouts = _mapping(raw, "timeouts")
    _exact_keys(
        timeouts,
        {"server_startup_seconds", "point_seconds", "repetition_seconds"},
        "timeouts",
    )
    for key in timeouts:
        _positive_int(timeouts, key)
    if timeouts["server_startup_seconds"] != 600:
        raise MeasurementValidationError(
            "server startup timeout must match the pinned executor"
        )
    if timeouts["repetition_seconds"] != 1800:
        raise MeasurementValidationError(
            "repetition timeout must match the pinned executor"
        )
    if timeouts["point_seconds"] > timeouts["repetition_seconds"]:
        raise MeasurementValidationError(
            "point timeout cannot exceed repetition timeout"
        )


def _load_decimal_json(raw_bytes: bytes) -> Any:
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MeasurementValidationError("vLLM result must be UTF-8") from error

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise MeasurementValidationError(
                    f"duplicate vLLM result key: {key}"
                )
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise MeasurementValidationError(f"non-finite JSON number: {value}")

    try:
        return json.loads(
            text,
            parse_float=Decimal,
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except (json.JSONDecodeError, InvalidOperation) as error:
        raise MeasurementValidationError(
            "vLLM result is not strict JSON"
        ) from error


def _mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    item = value.get(key)
    if not isinstance(item, dict):
        raise MeasurementValidationError(f"{key} must be a table")
    return item


def _exact_keys(
    value: Mapping[str, Any], expected: set[str], location: str
) -> None:
    if set(value) != expected:
        raise MeasurementValidationError(f"{location} keys differ")


def _positive_int(value: Mapping[str, Any], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool) or item < 1:
        raise MeasurementValidationError(f"{key} must be a positive integer")
    return item


def _nonnegative_int(value: Mapping[str, Any], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool) or item < 0:
        raise MeasurementValidationError(f"{key} must be a non-negative integer")
    return item


def _integer_list(
    value: Mapping[str, Any], key: str, expected_length: int
) -> tuple[int, ...]:
    item = value.get(key)
    if (
        not isinstance(item, list)
        or len(item) != expected_length
        or any(
            not isinstance(entry, int) or isinstance(entry, bool) or entry < 0
            for entry in item
        )
    ):
        raise MeasurementValidationError(
            f"{key} must contain one non-negative integer per prompt"
        )
    return tuple(item)


def _decimal_list(
    value: Mapping[str, Any], key: str, expected_length: int
) -> tuple[Decimal, ...]:
    item = value.get(key)
    if not isinstance(item, list) or len(item) != expected_length:
        raise MeasurementValidationError(
            f"{key} must contain one finite non-negative number per prompt"
        )
    result: list[Decimal] = []
    for entry in item:
        if isinstance(entry, bool) or not isinstance(entry, (int, Decimal)):
            raise MeasurementValidationError(
                f"{key} must contain one finite non-negative number per prompt"
            )
        number = Decimal(entry)
        if not number.is_finite() or number < 0:
            raise MeasurementValidationError(
                f"{key} must contain one finite non-negative number per prompt"
            )
        result.append(number)
    return tuple(result)


def _nested_decimal_list(
    value: Mapping[str, Any], key: str, expected_length: int
) -> tuple[tuple[Decimal, ...], ...]:
    item = value.get(key)
    if not isinstance(item, list) or len(item) != expected_length:
        raise MeasurementValidationError(
            f"{key} must contain one number list per prompt"
        )
    result: list[tuple[Decimal, ...]] = []
    for entries in item:
        if not isinstance(entries, list):
            raise MeasurementValidationError(
                f"{key} must contain one number list per prompt"
            )
        row: list[Decimal] = []
        for entry in entries:
            if isinstance(entry, bool) or not isinstance(entry, (int, Decimal)):
                raise MeasurementValidationError(
                    f"{key} entries must be finite non-negative numbers"
                )
            number = Decimal(entry)
            if not number.is_finite() or number < 0:
                raise MeasurementValidationError(
                    f"{key} entries must be finite non-negative numbers"
                )
            row.append(number)
        result.append(tuple(row))
    return tuple(result)


def _decimal(value: Mapping[str, Any], key: str) -> Decimal:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, (int, Decimal)):
        raise MeasurementValidationError(f"{key} must be a finite number")
    result = Decimal(item)
    if not result.is_finite():
        raise MeasurementValidationError(f"{key} must be finite")
    return result


def _positive_decimal(value: Mapping[str, Any], key: str) -> Decimal:
    result = _decimal(value, key)
    if result <= 0:
        raise MeasurementValidationError(f"{key} must be positive")
    return result


def _nonnegative_decimal(value: Mapping[str, Any], key: str) -> Decimal:
    result = _decimal(value, key)
    if result < 0:
        raise MeasurementValidationError(f"{key} must be non-negative")
    return result


def _require_close(actual: Decimal, expected: Decimal, metric: str) -> None:
    tolerance = max(Decimal("0.000000001"), abs(expected) * Decimal("0.000000001"))
    if abs(actual - expected) > tolerance:
        raise MeasurementValidationError(
            f"vLLM {metric} throughput is inconsistent with raw totals"
        )


def _to_integer_units(value: Decimal, scale: Decimal) -> int:
    return int((value * scale).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def _milliseconds_to_microseconds(value: Decimal) -> int:
    return _to_integer_units(value, Decimal("1000"))


def _mean(values: list[Decimal]) -> Decimal:
    if not values:
        raise MeasurementValidationError("cannot average an empty measurement")
    return sum(values, Decimal(0)) / Decimal(len(values))
