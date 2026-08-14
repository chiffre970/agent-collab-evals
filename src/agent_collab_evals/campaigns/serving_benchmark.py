"""Pure construction of pinned vLLM benchmark invocations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from .model_serving import BenchmarkPlan, ManifestValidationError


@dataclass(frozen=True, slots=True)
class BenchmarkInvocation:
    bucket_id: str
    request_rate: int
    input_tokens: int
    output_tokens: int
    num_prompts: int
    result_file: Path
    argv: tuple[str, ...]


def build_vllm_benchmark_invocations(
    plan: BenchmarkPlan,
    *,
    base_url: str,
    model_source: str,
    served_model_name: str,
    result_directory: Path,
    warmup_requests: int,
    goodput_slos_ms: Mapping[str, int] | None = None,
) -> tuple[BenchmarkInvocation, ...]:
    """Build argv arrays only; execution belongs to a compute adapter."""

    parsed_url = urlsplit(base_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ManifestValidationError("benchmark base_url must be HTTP(S)")
    if parsed_url.username or parsed_url.password:
        raise ManifestValidationError("benchmark base_url must not contain credentials")
    if not model_source or not served_model_name:
        raise ManifestValidationError("model source and served model name are required")
    if (
        not isinstance(warmup_requests, int)
        or isinstance(warmup_requests, bool)
        or warmup_requests < 1
    ):
        raise ManifestValidationError("benchmark warmup requests must be positive")

    slos = goodput_slos_ms or {}
    if set(slos) - {"ttft", "tpot", "e2el"}:
        raise ManifestValidationError("unsupported goodput metric")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 1
        for value in slos.values()
    ):
        raise ManifestValidationError("goodput SLOs must be positive integer milliseconds")

    invocations: list[BenchmarkInvocation] = []
    for bucket in plan.buckets:
        for request_rate in bucket.request_rates:
            filename = f"{bucket.bucket_id}-{request_rate}rps.json"
            argv = [
                "vllm",
                "bench",
                "serve",
                "--backend",
                "openai-chat",
                "--base-url",
                base_url.rstrip("/"),
                "--endpoint",
                "/v1/chat/completions",
                "--model",
                model_source,
                "--served-model-name",
                served_model_name,
                "--dataset-name",
                "random",
                "--input-len",
                str(bucket.input_tokens),
                "--output-len",
                str(bucket.output_tokens),
                "--request-rate",
                str(request_rate),
                "--num-prompts",
                str(bucket.num_prompts),
                "--seed",
                str(plan.seed),
                "--temperature",
                "0",
                "--extra-body",
                '{"chat_template_kwargs":{"enable_thinking":false}}',
                "--random-range-ratio",
                "0",
                "--ignore-eos",
                "--num-warmups",
                str(warmup_requests),
                "--disable-tqdm",
                "--percentile-metrics",
                "ttft,tpot,itl,e2el",
                "--metric-percentiles",
                ",".join(str(value) for value in plan.metric_percentiles),
                "--request-id-prefix",
                f"{bucket.bucket_id}-{request_rate}rps-",
                "--save-result",
                "--save-detailed",
                "--result-dir",
                str(result_directory),
                "--result-filename",
                filename,
            ]
            if slos:
                argv.append("--goodput")
                argv.extend(f"{metric}:{slos[metric]}" for metric in sorted(slos))
            invocations.append(
                BenchmarkInvocation(
                    bucket_id=bucket.bucket_id,
                    request_rate=request_rate,
                    input_tokens=bucket.input_tokens,
                    output_tokens=bucket.output_tokens,
                    num_prompts=bucket.num_prompts,
                    result_file=result_directory / filename,
                    argv=tuple(argv),
                )
            )
    return tuple(invocations)
