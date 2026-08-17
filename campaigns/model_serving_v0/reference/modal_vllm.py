"""Run the pinned stock-vLLM reference privately on one Modal L4."""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import math
import os
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import modal


APP_NAME = "agent-collab-evals-model-serving-reference"
IMAGE_REF = "nvidia/cuda:12.9.0-devel-ubuntu22.04"
IMAGE_DIGEST = "sha256:0a254a86e28379f7a761c73caf4874247d5e3fbcf57bd99a44856ccf9098e092"
PINNED_IMAGE_REF = f"nvidia/cuda@{IMAGE_DIGEST}"
DEPENDENCY_LOCK = "vllm==0.21.0"

HF_CACHE_PATH = "/cache/huggingface"
VLLM_CACHE_PATH = "/cache/vllm"
SERVER_LOG_PATH = "/tmp/reference-vllm.log"
STARTUP_TIMEOUT_SECONDS = 600
FUNCTION_TIMEOUT_SECONDS = 1800
BENCHMARK_RESULT_ROOT = Path("/tmp/reference-benchmark")
EVIDENCE_VOLUME_NAME = "agent-collab-evals-evaluator-evidence-v2"
EVIDENCE_MOUNT_PATH = Path("/evaluator-evidence")
_HEX = set("0123456789abcdef")

app = modal.App(APP_NAME)
reference_image = (
    modal.Image.from_registry(PINNED_IMAGE_REF, add_python="3.12")
    .entrypoint([])
    .uv_pip_install(DEPENDENCY_LOCK)
    .env(
        {
            "HF_HOME": HF_CACHE_PATH,
            "HUGGINGFACE_HUB_CACHE": f"{HF_CACHE_PATH}/hub",
            "VLLM_CACHE_ROOT": VLLM_CACHE_PATH,
            "VLLM_NO_USAGE_STATS": "1",
        }
    )
)
model_cache = modal.Volume.from_name(
    "agent-collab-evals-huggingface-cache", create_if_missing=True
)
vllm_cache = modal.Volume.from_name(
    "agent-collab-evals-vllm-cache", create_if_missing=True
)
evidence_volume = modal.Volume.from_name(
    EVIDENCE_VOLUME_NAME, create_if_missing=True, version=2
)
huggingface_secret = modal.Secret.from_name(
    "huggingface-secret", required_keys=["HF_TOKEN"]
)


def _read_log_tail(limit: int = 8000) -> str:
    try:
        content = Path(SERVER_LOG_PATH).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    return content[-limit:]


def _wait_until_ready(process: subprocess.Popen[Any], server_port: int) -> int:
    started = time.monotonic()
    health_url = f"http://127.0.0.1:{server_port}/health"
    while time.monotonic() - started < STARTUP_TIMEOUT_SECONDS:
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(
                f"vLLM exited during startup with code {return_code}.\n"
                f"{_read_log_tail()}"
            )
        try:
            with urllib.request.urlopen(health_url, timeout=2) as response:
                if response.status == 200:
                    return round((time.monotonic() - started) * 1000)
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(1)
    raise TimeoutError(
        f"vLLM did not become healthy within {STARTUP_TIMEOUT_SECONDS}s.\n"
        f"{_read_log_tail()}"
    )


def _chat_canary(server_port: int, served_model_name: str) -> dict[str, Any]:
    started_ns = time.perf_counter_ns()
    body = json.dumps(
        {
            "model": served_model_name,
            "messages": [
                {
                    "role": "user",
                    "content": "Reply with exactly the single word READY.",
                }
            ],
            "temperature": 0,
            "max_tokens": 32,
            "chat_template_kwargs": {"enable_thinking": False},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{server_port}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.load(response)
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError("vLLM returned an invalid chat response") from error
    if not isinstance(content, str) or content.strip() != "READY":
        raise RuntimeError(f"chat canary failed with response: {content!r}")
    returned_model = payload.get("model")
    if returned_model != served_model_name:
        raise RuntimeError("vLLM returned an unexpected served model name")
    return {
        "returned_model": returned_model,
        "content": content.strip(),
        "usage": payload.get("usage", {}),
        "elapsed_us": (time.perf_counter_ns() - started_ns) // 1000,
    }


def _gpu_metadata() -> dict[str, str]:
    output = subprocess.check_output(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version,pci.bus_id,clocks.current.sm,clocks.current.memory,power.limit",
            "--format=csv,noheader,nounits",
        ],
        text=True,
    ).strip()
    (
        name,
        memory_mib,
        driver_version,
        pci_bus_id,
        sm_clock_mhz,
        memory_clock_mhz,
        power_limit_watts,
    ) = [part.strip() for part in output.split(",")]
    return {
        "name": name,
        "memory_mib": memory_mib,
        "driver_version": driver_version,
        "pci_bus_id": pci_bus_id,
        "sm_clock_mhz": sm_clock_mhz,
        "memory_clock_mhz": memory_clock_mhz,
        "power_limit_watts": power_limit_watts,
    }


def _environment_receipt() -> dict[str, Any]:
    packages = sorted(
        f"{distribution.metadata['Name'].lower()}=={distribution.version}"
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name")
    )
    content = "\n".join(packages).encode("utf-8")
    return {
        "package_count": len(packages),
        "package_set_digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
        "base_image_ref": IMAGE_REF,
        "base_image_digest": IMAGE_DIGEST,
    }


def _stop_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)


def _validate_benchmark_spec(spec: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    expected = {
        "campaign_manifest_digest",
        "measurement_profile_digest",
        "scoring_profile_digest",
        "repetition",
        "attempt",
        "evidence_root",
        "point_timeout_seconds",
        "invocations",
    }
    if set(spec) != expected:
        raise ValueError("benchmark spec fields differ")
    if not isinstance(spec["repetition"], int) or spec["repetition"] < 1:
        raise ValueError("invalid benchmark repetition")
    if not isinstance(spec["attempt"], int) or spec["attempt"] < 1:
        raise ValueError("invalid benchmark attempt")
    evidence_root = spec["evidence_root"]
    if not isinstance(evidence_root, str):
        raise ValueError("invalid evidence root")
    evidence_path = PurePosixPath(evidence_root)
    if (
        evidence_path.is_absolute()
        or not evidence_path.parts
        or any(part in {"", ".", ".."} for part in evidence_path.parts)
        or any(not part.replace("-", "").replace("_", "").isalnum() for part in evidence_path.parts)
    ):
        raise ValueError("invalid evidence root")
    point_timeout = spec["point_timeout_seconds"]
    if not isinstance(point_timeout, int) or not 1 <= point_timeout <= 600:
        raise ValueError("invalid point timeout")
    for key in (
        "campaign_manifest_digest",
        "measurement_profile_digest",
        "scoring_profile_digest",
    ):
        value = spec[key]
        if not isinstance(value, str) or not value.startswith("sha256:"):
            raise ValueError(f"invalid {key}")

    values = spec["invocations"]
    if not isinstance(values, list) or len(values) != 9:
        raise ValueError("reference baseline requires exactly nine points")
    invocations: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for value in values:
        if not isinstance(value, dict) or set(value) != {
            "bucket_id",
            "request_rate",
            "result_filename",
            "argv",
        }:
            raise ValueError("invalid benchmark invocation")
        bucket_id = value["bucket_id"]
        request_rate = value["request_rate"]
        result_filename = value["result_filename"]
        argv = value["argv"]
        if (
            not isinstance(bucket_id, str)
            or not isinstance(request_rate, int)
            or request_rate < 1
            or not isinstance(result_filename, str)
            or Path(result_filename).name != result_filename
            or not result_filename.endswith(".json")
            or not isinstance(argv, list)
            or any(not isinstance(part, str) or not part for part in argv)
        ):
            raise ValueError("invalid benchmark invocation value")
        if tuple(argv[:3]) != ("vllm", "bench", "serve"):
            raise ValueError("benchmark command must be vllm bench serve")
        if (bucket_id, request_rate) in seen:
            raise ValueError("duplicate benchmark point")
        seen.add((bucket_id, request_rate))
        expected_result = str(BENCHMARK_RESULT_ROOT / result_filename)
        try:
            result_index = argv.index("--result-dir")
            filename_index = argv.index("--result-filename")
        except ValueError as error:
            raise ValueError("benchmark result arguments are required") from error
        if (
            result_index + 1 >= len(argv)
            or argv[result_index + 1] != str(BENCHMARK_RESULT_ROOT)
            or filename_index + 1 >= len(argv)
            or argv[filename_index + 1] != result_filename
            or str(BENCHMARK_RESULT_ROOT / argv[filename_index + 1])
            != expected_result
        ):
            raise ValueError("benchmark result path mismatch")
        invocations.append(value)
    return tuple(invocations)


def _run_benchmark_points(
    invocations: tuple[dict[str, Any], ...], point_timeout_seconds: int
) -> tuple[dict[str, bytes], list[dict[str, Any]], str | None]:
    BENCHMARK_RESULT_ROOT.mkdir(mode=0o700)
    raw_results: dict[str, bytes] = {}
    point_receipts: list[dict[str, Any]] = []
    for invocation in invocations:
        started_ns = time.perf_counter_ns()
        try:
            completed = subprocess.run(
                invocation["argv"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=point_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            elapsed_us = (time.perf_counter_ns() - started_ns) // 1000
            message = (
                f"benchmark point {invocation['bucket_id']}/"
                f"{invocation['request_rate']} timed out"
            )
            point_receipts.append(
                {
                    "bucket_id": invocation["bucket_id"],
                    "request_rate": invocation["request_rate"],
                    "executor_elapsed_us": elapsed_us,
                    "status": "failed",
                    "error": message,
                }
            )
            return raw_results, point_receipts, message
        elapsed_us = (time.perf_counter_ns() - started_ns) // 1000
        if completed.returncode != 0:
            output_tail = completed.stdout[-8000:]
            message = (
                f"benchmark point {invocation['bucket_id']}/"
                f"{invocation['request_rate']} failed with code "
                f"{completed.returncode}.\n{output_tail}"
            )
            point_receipts.append(
                {
                    "bucket_id": invocation["bucket_id"],
                    "request_rate": invocation["request_rate"],
                    "executor_elapsed_us": elapsed_us,
                    "status": "failed",
                    "error": message,
                }
            )
            return raw_results, point_receipts, message
        filename = invocation["result_filename"]
        result_path = BENCHMARK_RESULT_ROOT / filename
        try:
            raw_result = result_path.read_bytes()
        except FileNotFoundError:
            message = "vLLM did not write its declared result"
            point_receipts.append(
                {
                    "bucket_id": invocation["bucket_id"],
                    "request_rate": invocation["request_rate"],
                    "executor_elapsed_us": elapsed_us,
                    "status": "failed",
                    "error": message,
                }
            )
            return raw_results, point_receipts, message
        raw_results[filename] = raw_result
        point_receipts.append(
            {
                "bucket_id": invocation["bucket_id"],
                "request_rate": invocation["request_rate"],
                "executor_elapsed_us": elapsed_us,
                "status": "complete",
                "error": None,
            }
        )
    return raw_results, point_receipts, None


def _validate_quality_spec(spec: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    expected = {
        "campaign_manifest_digest",
        "quality_profile_digest",
        "quality_workload_digest",
        "repetition",
        "attempt",
        "evidence_root",
        "max_concurrency",
        "request_timeout_seconds",
        "requests",
    }
    if set(spec) != expected:
        raise ValueError("quality spec fields differ")
    for key in (
        "campaign_manifest_digest",
        "quality_profile_digest",
        "quality_workload_digest",
    ):
        value = spec[key]
        if (
            not isinstance(value, str)
            or len(value) != 71
            or not value.startswith("sha256:")
            or any(character not in _HEX for character in value[7:])
        ):
            raise ValueError(f"invalid {key}")
    if not isinstance(spec["repetition"], int) or spec["repetition"] < 1:
        raise ValueError("invalid quality repetition")
    if not isinstance(spec["attempt"], int) or spec["attempt"] < 1:
        raise ValueError("invalid quality attempt")
    _validate_evidence_root(spec["evidence_root"])
    timeout = spec["request_timeout_seconds"]
    if not isinstance(timeout, int) or not 1 <= timeout <= 300:
        raise ValueError("invalid quality request timeout")
    concurrency = spec["max_concurrency"]
    if (
        not isinstance(concurrency, int)
        or isinstance(concurrency, bool)
        or not 1 <= concurrency <= 32
    ):
        raise ValueError("invalid quality request concurrency")
    requests = spec["requests"]
    if not isinstance(requests, list) or not requests:
        raise ValueError("quality requests are invalid")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    body_fields = {
        "model",
        "messages",
        "seed",
        "stream",
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "max_tokens",
        "chat_template_kwargs",
    }
    for value in requests:
        if not isinstance(value, dict) or set(value) != {"case_id", "body"}:
            raise ValueError("quality request fields differ")
        case_id = value["case_id"]
        body = value["body"]
        if (
            not isinstance(case_id, str)
            or case_id in seen
            or not case_id.replace("-", "").replace("_", "").isalnum()
            or not isinstance(body, dict)
            or set(body) != body_fields
        ):
            raise ValueError("quality request identity is invalid")
        messages = body["messages"]
        chat_template = body["chat_template_kwargs"]
        if (
            not isinstance(body["model"], str)
            or not isinstance(messages, list)
            or len(messages) != 1
            or not isinstance(messages[0], dict)
            or set(messages[0]) != {"role", "content"}
            or messages[0]["role"] != "user"
            or not isinstance(messages[0]["content"], str)
            or not messages[0]["content"]
            or not isinstance(body["seed"], int)
            or isinstance(body["seed"], bool)
            or not 0 <= body["seed"] <= 0x7FFFFFFF
            or body["stream"] is not False
            or not isinstance(body["max_tokens"], int)
            or not 1 <= body["max_tokens"] <= 4096
            or not isinstance(chat_template, dict)
            or set(chat_template) != {"enable_thinking"}
            or not isinstance(chat_template["enable_thinking"], bool)
        ):
            raise ValueError("quality request body is invalid")
        for key in ("temperature", "top_p", "min_p"):
            if (
                not isinstance(body[key], (int, float))
                or isinstance(body[key], bool)
                or not math.isfinite(body[key])
            ):
                raise ValueError("quality sampling value is invalid")
        if not 0 <= body["temperature"] <= 2:
            raise ValueError("quality temperature is invalid")
        if not 0 < body["top_p"] <= 1 or not 0 <= body["min_p"] <= 1:
            raise ValueError("quality probability sampling value is invalid")
        if (
            not isinstance(body["top_k"], int)
            or isinstance(body["top_k"], bool)
            or not 0 <= body["top_k"] <= 10000
        ):
            raise ValueError("quality top_k is invalid")
        seen.add(case_id)
        result.append(value)
    return tuple(result)


def _validate_evidence_root(value: Any) -> None:
    if not isinstance(value, str):
        raise ValueError("invalid evidence root")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(
            not part.replace("-", "").replace("_", "").isalnum()
            for part in path.parts
        )
    ):
        raise ValueError("invalid evidence root")


def _run_quality_requests(
    requests: tuple[dict[str, Any], ...],
    *,
    server_port: int,
    request_timeout_seconds: int,
    max_concurrency: int,
) -> tuple[dict[str, bytes], list[dict[str, Any]], str | None]:
    outcomes: dict[str, tuple[bytes | None, dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        futures = {
            executor.submit(
                _run_quality_request,
                request_spec,
                server_port=server_port,
                request_timeout_seconds=request_timeout_seconds,
            ): request_spec["case_id"]
            for request_spec in requests
        }
        for future in as_completed(futures):
            case_id = futures[future]
            try:
                outcomes[case_id] = future.result()
            except Exception as error:
                outcomes[case_id] = (
                    None,
                    {
                        "case_id": case_id,
                        "elapsed_us": None,
                        "status": "failed",
                        "error": f"{type(error).__name__}: {str(error)[-2000:]}",
                    },
                )

    raw_results: dict[str, bytes] = {}
    receipts: list[dict[str, Any]] = []
    errors: list[str] = []
    for request_spec in requests:
        case_id = request_spec["case_id"]
        raw, receipt = outcomes[case_id]
        receipts.append(receipt)
        if raw is None:
            errors.append(f"{case_id}: {receipt['error']}")
        else:
            raw_results[f"{case_id}.json"] = raw
    return raw_results, receipts, errors[0] if errors else None


def _run_quality_request(
    request_spec: dict[str, Any],
    *,
    server_port: int,
    request_timeout_seconds: int,
) -> tuple[bytes | None, dict[str, Any]]:
    case_id = request_spec["case_id"]
    started_ns = time.perf_counter_ns()
    request = urllib.request.Request(
        f"http://127.0.0.1:{server_port}/v1/chat/completions",
        data=_stable_json_bytes(request_spec["body"]),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=request_timeout_seconds
        ) as response:
            raw = response.read()
        payload = json.loads(raw)
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("quality response content is not text")
    except Exception as error:
        elapsed_us = (time.perf_counter_ns() - started_ns) // 1000
        return None, {
            "case_id": case_id,
            "elapsed_us": elapsed_us,
            "status": "failed",
            "error": f"{type(error).__name__}: {str(error)[-2000:]}",
        }
    return raw, {
        "case_id": case_id,
        "elapsed_us": (time.perf_counter_ns() - started_ns) // 1000,
        "status": "complete",
        "error": None,
    }


@app.function(
    image=reference_image,
    secrets=[huggingface_secret],
    volumes={HF_CACHE_PATH: model_cache, VLLM_CACHE_PATH: vllm_cache},
    gpu="L4",
    max_containers=1,
    min_containers=0,
    retries=0,
    restrict_modal_access=True,
    single_use_containers=True,
    timeout=FUNCTION_TIMEOUT_SECONDS,
)
def smoke_reference(candidate: dict[str, Any]) -> dict[str, Any]:
    """Start the exact reference server and run one deterministic API canary."""

    server = candidate["server"]
    model = candidate["model"]
    server_port = int(server["port"])
    served_model_name = str(server["served_model_name"])
    server_command = tuple(str(part) for part in server["entrypoint"])
    installed_vllm = importlib.metadata.version("vllm")
    if installed_vllm != server["engine_version"]:
        raise RuntimeError("installed vLLM does not match the candidate manifest")

    started = time.monotonic()
    with Path(SERVER_LOG_PATH).open("w", encoding="utf-8") as server_log:
        process = subprocess.Popen(
            server_command,
            stdout=server_log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            startup_ms = _wait_until_ready(process, server_port)
            canary = _chat_canary(server_port, served_model_name)
        finally:
            _stop_process(process)

    return {
        "ok": True,
        "candidate_id": candidate["candidate_id"],
        "model_id": model["id"],
        "model_revision": model["revision"],
        "served_model_name": served_model_name,
        "vllm_version": installed_vllm,
        "startup_ms": startup_ms,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "gpu": _gpu_metadata(),
        "environment": _environment_receipt(),
        "canary": canary,
    }


@app.function(
    image=reference_image,
    secrets=[huggingface_secret],
    volumes={HF_CACHE_PATH: model_cache, EVIDENCE_MOUNT_PATH: evidence_volume},
    gpu="L4",
    max_containers=1,
    min_containers=0,
    retries=0,
    restrict_modal_access=True,
    single_use_containers=True,
    timeout=FUNCTION_TIMEOUT_SECONDS,
)
def benchmark_serving_repetition(
    candidate: dict[str, Any], benchmark_spec: dict[str, Any]
) -> dict[str, Any]:
    """Run one isolated, post-warmup serving measurement repetition."""

    invocations = _validate_benchmark_spec(benchmark_spec)
    server = candidate["server"]
    model = candidate["model"]
    server_port = int(server["port"])
    served_model_name = str(server["served_model_name"])
    server_command = tuple(str(part) for part in server["entrypoint"])
    installed_vllm = importlib.metadata.version("vllm")
    if installed_vllm != server["engine_version"]:
        raise RuntimeError("installed vLLM does not match the candidate manifest")

    repetition_started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    gpu_before = _gpu_metadata()
    with Path(SERVER_LOG_PATH).open("w", encoding="utf-8") as server_log:
        process = subprocess.Popen(
            server_command,
            stdout=server_log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            startup_ms = _wait_until_ready(process, server_port)
            canary_before = _chat_canary(server_port, served_model_name)
            measurement_started = time.monotonic()
            raw_results, point_receipts, benchmark_error = _run_benchmark_points(
                invocations, benchmark_spec["point_timeout_seconds"]
            )
            measured_ms = round((time.monotonic() - measurement_started) * 1000)
            try:
                canary_after = _chat_canary(server_port, served_model_name)
            except Exception as error:
                canary_after = {"error": f"{type(error).__name__}: {error}"}
                if benchmark_error is None:
                    benchmark_error = "post-measurement canary failed"
        finally:
            _stop_process(process)

    gpu_after = _gpu_metadata()
    remote_receipt = {
        "ok": benchmark_error is None,
        "error": benchmark_error,
        "candidate_id": candidate["candidate_id"],
        "model_id": model["id"],
        "model_revision": model["revision"],
        "served_model_name": served_model_name,
        "vllm_version": installed_vllm,
        "campaign_manifest_digest": benchmark_spec["campaign_manifest_digest"],
        "measurement_profile_digest": benchmark_spec[
            "measurement_profile_digest"
        ],
        "scoring_profile_digest": benchmark_spec["scoring_profile_digest"],
        "repetition": benchmark_spec["repetition"],
        "attempt": benchmark_spec["attempt"],
        "started_at": started_at,
        "timing": {
            "primary_source": "vllm_in_container_perf_counter",
            "lifecycle_source": "in_container_monotonic",
            "startup_ms": startup_ms,
            "measured_points_ms": measured_ms,
            "function_body_ms": round(
                (time.monotonic() - repetition_started) * 1000
            ),
        },
        "gpu_before": gpu_before,
        "gpu_after": gpu_after,
        "environment": _environment_receipt(),
        "canary_before": canary_before,
        "canary_after": canary_after,
        "point_receipts": point_receipts,
    }
    evidence = _persist_remote_evidence(
        benchmark_spec["evidence_root"], remote_receipt, raw_results
    )
    return _evidence_pointer(evidence)


@app.function(
    image=reference_image,
    secrets=[huggingface_secret],
    volumes={HF_CACHE_PATH: model_cache, EVIDENCE_MOUNT_PATH: evidence_volume},
    gpu="L4",
    max_containers=1,
    min_containers=0,
    retries=0,
    restrict_modal_access=True,
    single_use_containers=True,
    timeout=FUNCTION_TIMEOUT_SECONDS,
)
def quality_serving_repetition(
    candidate: dict[str, Any], quality_spec: dict[str, Any]
) -> dict[str, Any]:
    """Run evaluator-private generated-answer quality through the server."""

    requests = _validate_quality_spec(quality_spec)
    server = candidate["server"]
    model = candidate["model"]
    server_port = int(server["port"])
    served_model_name = str(server["served_model_name"])
    if any(value["body"]["model"] != served_model_name for value in requests):
        raise ValueError("quality request changes the served model name")
    server_command = tuple(str(part) for part in server["entrypoint"])
    installed_vllm = importlib.metadata.version("vllm")
    if installed_vllm != server["engine_version"]:
        raise RuntimeError("installed vLLM does not match the candidate manifest")

    repetition_started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    gpu_before = _gpu_metadata()
    with Path(SERVER_LOG_PATH).open("w", encoding="utf-8") as server_log:
        process = subprocess.Popen(
            server_command,
            stdout=server_log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            startup_ms = _wait_until_ready(process, server_port)
            canary_before = _chat_canary(server_port, served_model_name)
            evaluation_started = time.monotonic()
            raw_results, case_receipts, evaluation_error = _run_quality_requests(
                requests,
                server_port=server_port,
                request_timeout_seconds=quality_spec["request_timeout_seconds"],
                max_concurrency=quality_spec["max_concurrency"],
            )
            evaluated_ms = round((time.monotonic() - evaluation_started) * 1000)
            try:
                canary_after = _chat_canary(server_port, served_model_name)
            except Exception as error:
                canary_after = {"error": f"{type(error).__name__}: {error}"}
                if evaluation_error is None:
                    evaluation_error = "post-quality canary failed"
        finally:
            _stop_process(process)

    remote_receipt = {
        "ok": evaluation_error is None,
        "error": evaluation_error,
        "candidate_id": candidate["candidate_id"],
        "model_id": model["id"],
        "model_revision": model["revision"],
        "served_model_name": served_model_name,
        "vllm_version": installed_vllm,
        "campaign_manifest_digest": quality_spec["campaign_manifest_digest"],
        "quality_profile_digest": quality_spec["quality_profile_digest"],
        "quality_workload_digest": quality_spec["quality_workload_digest"],
        "repetition": quality_spec["repetition"],
        "attempt": quality_spec["attempt"],
        "started_at": started_at,
        "timing": {
            "primary_source": "served_generation_responses",
            "lifecycle_source": "in_container_monotonic",
            "startup_ms": startup_ms,
            "evaluated_cases_ms": evaluated_ms,
            "function_body_ms": round(
                (time.monotonic() - repetition_started) * 1000
            ),
        },
        "execution": {
            "max_concurrency": quality_spec["max_concurrency"],
            "request_timeout_seconds": quality_spec["request_timeout_seconds"],
        },
        "gpu_before": gpu_before,
        "gpu_after": _gpu_metadata(),
        "environment": _environment_receipt(),
        "canary_before": canary_before,
        "canary_after": canary_after,
        "case_receipts": case_receipts,
    }
    evidence = _persist_remote_evidence(
        quality_spec["evidence_root"], remote_receipt, raw_results
    )
    return _evidence_pointer(evidence)


@app.function(
    image=modal.Image.debian_slim(),
    volumes={EVIDENCE_MOUNT_PATH: evidence_volume},
    retries=0,
    restrict_modal_access=True,
    single_use_containers=True,
    timeout=120,
)
def probe_evidence_volume(probe_id: str, content: bytes) -> dict[str, str]:
    """Prove that a restricted evaluator can durably commit without API access."""

    if len(probe_id) != 32 or any(character not in _HEX for character in probe_id):
        raise ValueError("invalid evidence probe ID")
    path = EVIDENCE_MOUNT_PATH / "preflight" / probe_id / "probe.bin"
    path.parent.mkdir(parents=True)
    _write_remote_file(path, content)
    subprocess.run(
        ["sync", str(EVIDENCE_MOUNT_PATH)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
        check=True,
    )
    return {
        "path": f"preflight/{probe_id}/probe.bin",
        "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
    }


def _persist_remote_evidence(
    evidence_root: str,
    remote_receipt: dict[str, Any],
    raw_results: dict[str, bytes],
) -> dict[str, Any]:
    """Commit raw evidence to the evaluator Volume before returning metadata."""

    destination = EVIDENCE_MOUNT_PATH.joinpath(*PurePosixPath(evidence_root).parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise RuntimeError("evaluator evidence destination already exists")
    staging = destination.parent / f".{destination.name}.staging-{uuid.uuid4().hex}"
    raw_root = staging / "raw"
    raw_root.mkdir(parents=True)
    try:
        raw_digests: dict[str, str] = {}
        for filename, content in sorted(raw_results.items()):
            if Path(filename).name != filename or not filename.endswith(".json"):
                raise RuntimeError("invalid raw evidence filename")
            raw_digests[filename] = f"sha256:{hashlib.sha256(content).hexdigest()}"
            _write_remote_file(raw_root / filename, content)
        receipt_bytes = _stable_json_bytes(remote_receipt)
        receipt_digest = f"sha256:{hashlib.sha256(receipt_bytes).hexdigest()}"
        _write_remote_file(staging / "remote-receipt.json", receipt_bytes)
        manifest = {
            "schema_version": "modal-evaluator-evidence/v0alpha1",
            "volume_name": EVIDENCE_VOLUME_NAME,
            "root": evidence_root,
            "remote_receipt_digest": receipt_digest,
            "raw_digests": raw_digests,
        }
        _write_remote_file(staging / "manifest.json", _stable_json_bytes(manifest))
        os.replace(staging, destination)
        subprocess.run(
            ["sync", str(EVIDENCE_MOUNT_PATH)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
            check=True,
        )
    except BaseException:
        if staging.exists():
            import shutil

            shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest


def _write_remote_file(path: Path, content: bytes) -> None:
    with path.open("xb") as target:
        target.write(content)
        target.flush()
        os.fsync(target.fileno())


def _stable_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8") + b"\n"


def _evidence_pointer(evidence: dict[str, Any]) -> dict[str, str]:
    return {
        "schema_version": "modal-evaluator-evidence-pointer/v0alpha1",
        "root": str(evidence["root"]),
        "remote_receipt_digest": str(evidence["remote_receipt_digest"]),
    }


def _collect_remote_evidence(
    pointer: dict[str, Any], *, expected_root: str
) -> tuple[dict[str, Any], dict[str, bytes], dict[str, Any]]:
    if not isinstance(pointer, dict) or set(pointer) != {
        "schema_version",
        "root",
        "remote_receipt_digest",
    }:
        raise RuntimeError("remote evidence pointer fields differ")
    if (
        pointer["schema_version"]
        != "modal-evaluator-evidence-pointer/v0alpha1"
        or pointer["root"] != expected_root
        or not isinstance(pointer["remote_receipt_digest"], str)
        or len(pointer["remote_receipt_digest"]) != 71
        or not pointer["remote_receipt_digest"].startswith("sha256:")
        or any(
            character not in _HEX
            for character in pointer["remote_receipt_digest"][7:]
        )
    ):
        raise RuntimeError("remote evidence pointer identity differs")
    expected_keys = {
        "schema_version",
        "volume_name",
        "root",
        "remote_receipt_digest",
        "raw_digests",
    }
    stored_manifest_bytes = _read_evidence_file(f"{expected_root}/manifest.json")
    try:
        evidence = json.loads(stored_manifest_bytes)
    except json.JSONDecodeError as error:
        raise RuntimeError("stored evidence manifest is invalid") from error
    if not isinstance(evidence, dict) or set(evidence) != expected_keys:
        raise RuntimeError("stored evidence manifest fields differ")
    if (
        evidence["schema_version"] != "modal-evaluator-evidence/v0alpha1"
        or evidence["volume_name"] != EVIDENCE_VOLUME_NAME
        or evidence["root"] != expected_root
        or evidence["remote_receipt_digest"]
        != pointer["remote_receipt_digest"]
        or not isinstance(evidence["raw_digests"], dict)
    ):
        raise RuntimeError("stored evidence manifest identity differs")
    receipt_bytes = _read_evidence_file(f"{expected_root}/remote-receipt.json")
    receipt_digest = f"sha256:{hashlib.sha256(receipt_bytes).hexdigest()}"
    if receipt_digest != evidence["remote_receipt_digest"]:
        raise RuntimeError("stored remote receipt digest differs")
    try:
        remote_receipt = json.loads(receipt_bytes)
    except json.JSONDecodeError as error:
        raise RuntimeError("stored remote receipt is invalid") from error
    if not isinstance(remote_receipt, dict):
        raise RuntimeError("stored remote receipt must be an object")
    raw_results: dict[str, bytes] = {}
    for filename, expected_digest in sorted(evidence["raw_digests"].items()):
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not filename.endswith(".json")
            or not isinstance(expected_digest, str)
        ):
            raise RuntimeError("stored raw evidence identity is invalid")
        content = _read_evidence_file(f"{expected_root}/raw/{filename}")
        observed_digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if observed_digest != expected_digest:
            raise RuntimeError("stored raw evidence digest differs")
        raw_results[filename] = content
    return remote_receipt, raw_results, evidence


def _read_evidence_file(path: str) -> bytes:
    return b"".join(evidence_volume.read_file(path))


def _publish_normalized_evidence(
    evidence: dict[str, Any], normalized: dict[str, Any]
) -> str:
    content = _stable_json_bytes(normalized)
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    destination = f"{evidence['root']}/normalized.json"
    try:
        existing = _read_evidence_file(destination)
    except FileNotFoundError:
        with evidence_volume.batch_upload(force=False) as batch:
            batch.put_file(io.BytesIO(content), destination)
    else:
        if existing != content:
            raise RuntimeError(
                "durable normalized evidence already exists with different content"
            )
    return digest


@app.local_entrypoint()
def main(
    output_path: str = "tmp/calibration/model-serving-reference-smoke.json",
    baseline: bool = False,
    quality: bool = False,
    candidate_path: str = "campaigns/model_serving_v0/reference/candidate.json",
    repetition: int = 1,
    attempt: int = 1,
    baseline_output_root: str = "tmp/calibration/model-serving-reference",
    measurement_id: str = "",
    dispatch_only: bool = False,
    collect_only: bool = False,
    collect_timeout_seconds: int = 0,
    evidence_probe: bool = False,
    quality_profile_path: str = "campaigns/model_serving_v0/evaluator/quality_calibration.toml",
    quality_workload_path: str = "tmp/evaluator-private/model-serving-quality/workload-v2.json",
    quality_output_root: str = "tmp/evaluator-private/model-serving-quality/results",
    quality_role: str = "",
) -> None:
    from agent_collab_evals.canonical import load_json

    resolved_candidate_path = Path(candidate_path).resolve()
    with resolved_candidate_path.open("r", encoding="utf-8") as source:
        candidate = load_json(source)
    if not isinstance(candidate, dict):
        raise RuntimeError("candidate manifest must be an object")
    if candidate["build"]["image_ref"] != IMAGE_REF:
        raise RuntimeError("Modal image does not match candidate.json")
    if candidate["build"]["image_digest"] != IMAGE_DIGEST:
        raise RuntimeError("Modal image digest does not match candidate.json")
    if candidate["build"]["dependency_lock"] != DEPENDENCY_LOCK:
        raise RuntimeError("Modal dependency does not match candidate.json")
    if evidence_probe:
        if (
            baseline
            or quality
            or dispatch_only
            or collect_only
            or collect_timeout_seconds
            or quality_role
        ):
            raise ValueError("--evidence-probe cannot be combined with measurement flags")
        content = f"evaluator-evidence-probe:{uuid.uuid4().hex}".encode("utf-8")
        result = probe_evidence_volume.remote(uuid.uuid4().hex, content)
        stored = _read_evidence_file(result["path"])
        if stored != content or result["digest"] != (
            f"sha256:{hashlib.sha256(stored).hexdigest()}"
        ):
            raise RuntimeError("durable evaluator evidence probe differs")
        print(json.dumps({"ok": True, **result}, indent=2, sort_keys=True))
        return
    if baseline and quality:
        raise ValueError("--baseline and --quality are mutually exclusive")
    if quality:
        _run_quality_repetition(
            candidate,
            candidate_path=resolved_candidate_path,
            profile_path=Path(quality_profile_path),
            workload_path=Path(quality_workload_path),
            repetition=repetition,
            attempt=attempt,
            output_root=Path(quality_output_root),
            measurement_id_override=measurement_id,
            role_override=quality_role,
            dispatch_only=dispatch_only,
            collect_only=collect_only,
            collect_timeout_seconds=collect_timeout_seconds,
        )
        return
    if baseline:
        _run_baseline_repetition(
            candidate,
            candidate_path=resolved_candidate_path,
            repetition=repetition,
            attempt=attempt,
            output_root=Path(baseline_output_root),
            measurement_id_override=measurement_id,
            dispatch_only=dispatch_only,
            collect_only=collect_only,
            collect_timeout_seconds=collect_timeout_seconds,
        )
        return
    if dispatch_only or collect_only or collect_timeout_seconds:
        raise ValueError("dispatch and collection options require --baseline or --quality")
    if quality_role:
        raise ValueError("--quality-role requires --quality")
    result = smoke_reference.remote(candidate)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    destination = Path(output_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".reference-smoke-",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(rendered)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    print(rendered, end="")
    print(f"Receipt: {destination}")


def _run_baseline_repetition(
    candidate: dict[str, Any],
    *,
    candidate_path: Path,
    repetition: int,
    attempt: int,
    output_root: Path,
    measurement_id_override: str,
    dispatch_only: bool,
    collect_only: bool,
    collect_timeout_seconds: int,
) -> None:
    """Trusted local composition; this path performs a billable GPU run."""

    from agent_collab_evals.adapters.local_measurements import (
        LocalMeasurementBundleStore,
    )
    from agent_collab_evals.canonical import load_json
    from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
    from agent_collab_evals.campaigns.serving_benchmark import (
        build_vllm_benchmark_invocations,
    )
    from agent_collab_evals.campaigns.serving_measurement import (
        parse_vllm_benchmark_result,
        replay_vllm_goodput,
    )
    from agent_collab_evals.campaigns.serving_scoring import (
        score_repetition,
    )

    campaign_path = Path(__file__).parents[1] / "campaign.toml"
    repository_root = Path(__file__).resolve().parents[3]
    campaign = ModelServingCampaign.load(campaign_path)
    profile = campaign.measurement_profile()
    scoring = campaign.scoring_profile()
    if not 1 <= repetition <= profile.repetitions:
        raise ValueError(
            f"repetition must be between 1 and {profile.repetitions}"
        )
    if not 1 <= attempt <= profile.max_attempts:
        raise ValueError(f"attempt must be between 1 and {profile.max_attempts}")
    if dispatch_only and collect_only:
        raise ValueError("--dispatch-only and --collect-only are mutually exclusive")
    if not 0 <= collect_timeout_seconds <= 300:
        raise ValueError("collect timeout must be between 0 and 300 seconds")
    status = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=normal"],
        cwd=repository_root,
        text=True,
    ).strip()
    if status:
        raise RuntimeError("formal measurement runs require a clean Git worktree")
    collector_git_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository_root, text=True
    ).strip()
    local_modal_version = importlib.metadata.version("modal")
    if local_modal_version != profile.modal_client_version:
        raise RuntimeError("local Modal client does not match the measurement profile")
    descriptor = campaign.validate_candidate(candidate_path)
    reference_descriptor = campaign.validate_reference_candidate()
    score_role = (
        "reference"
        if descriptor.manifest_digest == reference_descriptor.manifest_digest
        else "candidate"
    )
    derived_measurement_id = (
        f"{score_role}-{descriptor.candidate_id}-"
        f"{profile.digest.removeprefix('sha256:')[:8]}-"
        f"{scoring.digest.removeprefix('sha256:')[:8]}"
    )
    measurement_id = measurement_id_override or derived_measurement_id
    store = LocalMeasurementBundleStore(output_root)
    try:
        store.load(measurement_id, repetition, attempt=attempt)
    except KeyError:
        pass
    else:
        raise RuntimeError(
            "this measurement repetition attempt is already committed; "
            "refusing another GPU allocation"
        )
    if attempt > 1:
        try:
            previous_attempt = store.load(
                measurement_id, repetition, attempt=attempt - 1
            )
        except KeyError as error:
            raise RuntimeError(
                "a retry requires the preceding attempt's committed evidence"
            ) from error
        if previous_attempt.receipt["normalized"].get("valid") is True:
            raise RuntimeError("a valid repetition cannot be retried")
    reference_environment: dict[str, Any] | None = None
    reference_gpu_identity: dict[str, Any] | None = None
    identity_keys = ("name", "memory_mib", "driver_version", "power_limit_watts")
    for previous_repetition in range(1, repetition):
        previous = None
        for previous_attempt in range(1, profile.max_attempts + 1):
            try:
                candidate_bundle = store.load(
                    measurement_id,
                    previous_repetition,
                    attempt=previous_attempt,
                )
            except KeyError:
                continue
            normalized_previous = candidate_bundle.receipt["normalized"]
            if normalized_previous.get("valid") is True:
                previous = normalized_previous
                break
        if previous is None:
            raise RuntimeError(
                f"repetition {previous_repetition} has no valid committed attempt"
            )
        previous_environment = previous["remote_receipt"]["environment"]
        if reference_environment is None:
            reference_environment = previous_environment
        elif previous_environment != reference_environment:
            raise RuntimeError("prior baseline environments are inconsistent")
        previous_gpu = previous["remote_receipt"]["gpu_before"]
        if reference_gpu_identity is None:
            reference_gpu_identity = previous_gpu
        elif any(
            previous_gpu.get(key) != reference_gpu_identity.get(key)
            for key in identity_keys
        ):
            raise RuntimeError("prior baseline GPU environments are inconsistent")
    model_id_parts = campaign.target_model_id.split("/")
    if len(model_id_parts) != 2 or any(not part for part in model_id_parts):
        raise RuntimeError("the Modal adapter requires an organisation/model ID")
    cache_repository = "--".join(model_id_parts)
    model_source = (
        f"{HF_CACHE_PATH}/hub/models--{cache_repository}/snapshots/"
        f"{campaign.target_model_revision}"
    )
    invocations = build_vllm_benchmark_invocations(
        campaign.benchmark_plan(),
        base_url=f"http://127.0.0.1:{candidate['server']['port']}",
        model_source=model_source,
        served_model_name=str(candidate["server"]["served_model_name"]),
        result_directory=BENCHMARK_RESULT_ROOT,
        warmup_requests=profile.point_warmups,
        goodput_slos_ms_by_bucket=scoring.goodput_slos_ms_by_bucket,
    )
    evidence_namespace = hashlib.sha256(measurement_id.encode("utf-8")).hexdigest()
    evidence_root = (
        f"model-serving/{evidence_namespace}/"
        f"repetition-{repetition:04d}-attempt-{attempt:02d}"
    )
    benchmark_spec = {
        "campaign_manifest_digest": campaign.manifest_digest,
        "measurement_profile_digest": profile.digest,
        "scoring_profile_digest": scoring.digest,
        "repetition": repetition,
        "attempt": attempt,
        "evidence_root": evidence_root,
        "point_timeout_seconds": profile.point_timeout_seconds,
        "invocations": [
            {
                "bucket_id": invocation.bucket_id,
                "request_rate": invocation.request_rate,
                "result_filename": invocation.result_file.name,
                "argv": list(invocation.argv),
            }
            for invocation in invocations
        ],
    }
    dispatch_path = (
        output_root
        / ".dispatch"
        / measurement_id
        / f"repetition-{repetition:04d}-attempt-{attempt:02d}.json"
    )
    dispatch_identity = {
        "schema_version": "model-serving-modal-dispatch/v0alpha1",
        "measurement_id": measurement_id,
        "campaign_manifest_digest": campaign.manifest_digest,
        "measurement_profile_digest": profile.digest,
        "scoring_profile_digest": scoring.digest,
        "candidate_manifest_digest": descriptor.manifest_digest,
        "candidate_id": descriptor.candidate_id,
        "evidence_root": evidence_root,
        "git_commit": collector_git_commit,
        "modal_client_version": local_modal_version,
        "repetition": repetition,
        "attempt": attempt,
    }
    try:
        with dispatch_path.open("r", encoding="utf-8") as source:
            dispatch_record = load_json(source)
    except FileNotFoundError:
        if collect_only:
            raise RuntimeError("no durable Modal dispatch exists for this attempt")
        function_call = benchmark_serving_repetition.spawn(candidate, benchmark_spec)
        dispatch_record = {
            **dispatch_identity,
            "function_call_id": function_call.object_id,
            "dispatched_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json_atomic(dispatch_path, dispatch_record, prefix=".dispatch-")
    else:
        if not isinstance(dispatch_record, dict):
            raise RuntimeError("Modal dispatch record must be an object")
        expected_keys = {*dispatch_identity, "function_call_id", "dispatched_at"}
        if set(dispatch_record) != expected_keys:
            raise RuntimeError("Modal dispatch record fields differ")
        for key, expected in dispatch_identity.items():
            if key == "git_commit" and collect_only:
                dispatch_git_commit = dispatch_record.get(key)
                if (
                    not isinstance(dispatch_git_commit, str)
                    or len(dispatch_git_commit) != 40
                    or any(
                        character not in "0123456789abcdef"
                        for character in dispatch_git_commit
                    )
                ):
                    raise RuntimeError("Modal dispatch record has an invalid commit")
                continue
            if dispatch_record.get(key) != expected:
                raise RuntimeError(f"Modal dispatch record {key} differs")
        function_call_id = dispatch_record.get("function_call_id")
        if not isinstance(function_call_id, str) or not function_call_id:
            raise RuntimeError("Modal dispatch record has an invalid call ID")
        if not isinstance(dispatch_record.get("dispatched_at"), str):
            raise RuntimeError("Modal dispatch record has an invalid timestamp")
        function_call = modal.FunctionCall.from_id(function_call_id)

    platform_git_commit = str(dispatch_record["git_commit"])
    function_call_id = str(dispatch_record["function_call_id"])
    print(
        json.dumps(
            {
                "status": "dispatched",
                "function_call_id": function_call_id,
                "dispatch_record": str(dispatch_path.resolve()),
            },
            indent=2,
            sort_keys=True,
        )
    )
    if dispatch_only:
        return

    client_started = time.monotonic()
    try:
        collection_timeout = (
            collect_timeout_seconds
            if collect_timeout_seconds or collect_only
            else None
        )
        remote_result = function_call.get(timeout=collection_timeout)
    except modal.exception.ConnectionError:
        print(
            json.dumps(
                {
                    "status": "collection_interrupted",
                    "function_call_id": function_call_id,
                    "dispatch_record": str(dispatch_path.resolve()),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    except (TimeoutError, modal.exception.TimeoutError) as error:
        if not isinstance(
            error,
            (
                modal.exception.FunctionTimeoutError,
                modal.exception.OutputExpiredError,
            ),
        ):
            print(
                json.dumps(
                    {
                        "status": "pending",
                        "function_call_id": function_call_id,
                        "dispatch_record": str(dispatch_path.resolve()),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return
        remote_error: Exception | None = error
    except Exception as error:
        remote_error = error
    else:
        remote_error = None

    if remote_error is not None:
        error = remote_error
        client_observed_ms = round((time.monotonic() - client_started) * 1000)
        failure = {
            "schema_version": "model-serving-measurement-repetition/v0alpha1",
            "campaign_manifest_digest": campaign.manifest_digest,
            "measurement_profile_digest": profile.digest,
            "scoring_profile_digest": scoring.digest,
            "candidate_manifest_digest": descriptor.manifest_digest,
            "candidate_id": descriptor.candidate_id,
            "platform_build": {
                "git_commit": platform_git_commit,
                "collector_git_commit": collector_git_commit,
                "modal_client_version": local_modal_version,
            },
            "modal_function_call_id": function_call_id,
            "repetition": repetition,
            "attempt": attempt,
            "valid": False,
            "failure": {
                "stage": "remote_invocation",
                "type": type(error).__name__,
                "message": str(error)[-8000:],
            },
            "parse_errors": [],
            "environment_errors": [],
            "client_observed_ms": client_observed_ms,
            "modal_timing_role": profile.modal_timing_role,
            "client_timing_role": profile.client_timing_role,
            "remote_receipt": None,
            "performance_score": None,
            "points": [],
        }
        destination = store.save(
            measurement_id,
            repetition,
            failure,
            {},
            attempt=attempt,
        )
        raise RuntimeError(
            f"serving measurement invocation failed; evidence: {destination}"
        ) from error
    client_observed_ms = round((time.monotonic() - client_started) * 1000)
    remote_result, raw_results, durable_evidence = _collect_remote_evidence(
        remote_result,
        expected_root=evidence_root,
    )
    points: list[dict[str, Any]] = []
    goodput_replays = []
    parse_errors: list[str] = []
    plan = campaign.benchmark_plan()
    for invocation in invocations:
        raw = raw_results.get(invocation.result_file.name)
        if raw is None:
            continue
        if not isinstance(raw, bytes):
            parse_errors.append(
                f"{invocation.bucket_id}/{invocation.request_rate}: invalid raw type"
            )
            continue
        try:
            point = parse_vllm_benchmark_result(
                raw,
                invocation=invocation,
                model_source=model_source,
                metric_percentiles=plan.metric_percentiles,
            )
            rule = scoring.bucket_rules[invocation.bucket_id]
            replay = replay_vllm_goodput(
                raw,
                invocation=invocation,
                model_source=model_source,
                goodput_slos_ms={
                    "ttft": rule.ttft_slo_ms,
                    "tpot": rule.tpot_slo_ms,
                },
                legacy_classification_guard_us=(
                    scoring.legacy_classification_guard_us
                ),
                aggregate_tolerance_us=scoring.legacy_aggregate_tolerance_us,
            )
            point_document = point.to_document()
            point_document["goodput"] = replay.to_document()
            points.append(point_document)
            goodput_replays.append(replay)
        except Exception as error:
            parse_errors.append(
                f"{invocation.bucket_id}/{invocation.request_rate}: "
                f"{type(error).__name__}: {error}"
            )
    environment_errors: list[str] = []
    expected_remote_identity = {
        "candidate_id": descriptor.candidate_id,
        "model_id": campaign.target_model_id,
        "model_revision": campaign.target_model_revision,
        "served_model_name": str(candidate["server"]["served_model_name"]),
        "vllm_version": str(candidate["server"]["engine_version"]),
        "campaign_manifest_digest": campaign.manifest_digest,
        "measurement_profile_digest": profile.digest,
        "scoring_profile_digest": scoring.digest,
        "repetition": repetition,
        "attempt": attempt,
    }
    for key, expected in expected_remote_identity.items():
        if remote_result.get(key) != expected:
            environment_errors.append(f"remote_receipt.{key} differs")
    current_environment = remote_result.get("environment", {})
    expected_environment = {
        "package_set_digest": profile.resolved_package_digest,
        "base_image_digest": profile.base_image_digest,
    }
    for key, expected in expected_environment.items():
        if current_environment.get(key) != expected:
            environment_errors.append(f"environment.{key} differs from profile")
    current_gpu = remote_result.get("gpu_before", {})
    gpu_after = remote_result.get("gpu_after", {})
    expected_gpu = {
        "name": f"NVIDIA {profile.gpu_type}",
        "memory_mib": str(profile.gpu_memory_mib),
        "driver_version": profile.gpu_driver_version,
        "power_limit_watts": profile.gpu_power_limit_watts,
    }
    for key, expected in expected_gpu.items():
        if current_gpu.get(key) != expected:
            environment_errors.append(f"gpu.{key} differs from profile")
        if gpu_after.get(key) != expected:
            environment_errors.append(f"gpu_after.{key} differs from profile")
    for key in identity_keys:
        if gpu_after.get(key) != current_gpu.get(key):
            environment_errors.append(f"gpu.{key} changed within repetition")
    if reference_environment is not None:
        for key in ("package_set_digest", "base_image_digest"):
            if current_environment.get(key) != reference_environment.get(key):
                environment_errors.append(f"environment.{key} changed")
        if reference_gpu_identity is None:
            raise RuntimeError("reference GPU identity is missing")
        for key in identity_keys:
            if current_gpu.get(key) != reference_gpu_identity.get(key):
                environment_errors.append(f"gpu.{key} changed")
    performance_score = None
    if len(goodput_replays) == len(invocations):
        try:
            performance_score = score_repetition(
                scoring,
                plan,
                goodput_replays,
                repetition=repetition,
                role=score_role,
            ).to_document()
        except Exception as error:
            parse_errors.append(
                f"score: {type(error).__name__}: {error}"
            )
    valid = (
        remote_result.get("ok") is True
        and not parse_errors
        and not environment_errors
        and len(points) == len(invocations)
        and all(point["valid"] for point in points)
        and performance_score is not None
        and performance_score["eligible"] is True
    )
    normalized = {
        "schema_version": "model-serving-measurement-repetition/v0alpha1",
        "campaign_manifest_digest": campaign.manifest_digest,
        "measurement_profile_digest": profile.digest,
        "scoring_profile_digest": scoring.digest,
        "candidate_manifest_digest": descriptor.manifest_digest,
        "candidate_id": descriptor.candidate_id,
        "platform_build": {
            "git_commit": platform_git_commit,
            "collector_git_commit": collector_git_commit,
            "modal_client_version": local_modal_version,
        },
        "modal_function_call_id": function_call_id,
        "durable_evidence": durable_evidence,
        "repetition": repetition,
        "attempt": attempt,
        "valid": valid,
        "failure": None,
        "parse_errors": parse_errors,
        "environment_errors": environment_errors,
        "client_observed_ms": client_observed_ms,
        "modal_timing_role": profile.modal_timing_role,
        "client_timing_role": profile.client_timing_role,
        "remote_receipt": remote_result,
        "performance_score": performance_score,
        "points": points,
    }
    normalized_evidence_digest = _publish_normalized_evidence(
        durable_evidence, normalized
    )
    normalized["durable_evidence"] = {
        **durable_evidence,
        "normalized_digest": normalized_evidence_digest,
    }
    destination = store.save(
        measurement_id,
        repetition,
        normalized,
        raw_results,
        attempt=attempt,
    )
    summary = {
        "valid": normalized["valid"],
        "repetition": repetition,
        "attempt": attempt,
        "startup_ms": remote_result["timing"]["startup_ms"],
        "measured_points_ms": remote_result["timing"]["measured_points_ms"],
        "client_observed_ms": client_observed_ms,
        "bundle": str(destination.resolve()),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not valid:
        raise RuntimeError(f"serving measurement repetition is invalid: {destination}")


def _run_quality_repetition(
    candidate: dict[str, Any],
    *,
    candidate_path: Path,
    profile_path: Path,
    workload_path: Path,
    repetition: int,
    attempt: int,
    output_root: Path,
    measurement_id_override: str,
    role_override: str,
    dispatch_only: bool,
    collect_only: bool,
    collect_timeout_seconds: int,
) -> None:
    from agent_collab_evals.adapters.local_measurements import (
        LocalMeasurementBundleStore,
    )
    from agent_collab_evals.canonical import load_json
    from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
    from agent_collab_evals.campaigns.serving_quality import (
        QualityProfile,
        build_quality_requests,
        load_quality_workload,
        score_quality_outputs,
    )

    campaign_path = Path(__file__).parents[1] / "campaign.toml"
    repository_root = Path(__file__).resolve().parents[3]
    campaign = ModelServingCampaign.load(campaign_path)
    environment_profile = campaign.measurement_profile()
    profile = QualityProfile.load(profile_path)
    workload = load_quality_workload(workload_path, profile)
    if (
        profile.target_model != campaign.target_model_id
        or profile.target_revision != campaign.target_model_revision
    ):
        raise RuntimeError("quality profile changes the target model")
    if not 1 <= repetition <= profile.repetitions:
        raise ValueError(f"repetition must be between 1 and {profile.repetitions}")
    if not 1 <= attempt <= environment_profile.max_attempts:
        raise ValueError(
            f"attempt must be between 1 and {environment_profile.max_attempts}"
        )
    if dispatch_only and collect_only:
        raise ValueError("--dispatch-only and --collect-only are mutually exclusive")
    if not 0 <= collect_timeout_seconds <= 300:
        raise ValueError("collect timeout must be between 0 and 300 seconds")
    status = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=normal"],
        cwd=repository_root,
        text=True,
    ).strip()
    if status:
        raise RuntimeError("formal quality runs require a clean Git worktree")
    collector_git_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository_root, text=True
    ).strip()
    local_modal_version = importlib.metadata.version("modal")
    if local_modal_version != environment_profile.modal_client_version:
        raise RuntimeError("local Modal client does not match the measurement profile")
    descriptor = campaign.validate_candidate(candidate_path)
    reference_descriptor = campaign.validate_reference_candidate()
    automatic_role = (
        "reference"
        if descriptor.manifest_digest == reference_descriptor.manifest_digest
        else "candidate"
    )
    role = role_override or automatic_role
    if role not in {"reference", "candidate", "clean_control"}:
        raise ValueError("quality role is invalid")
    if role == "reference" and automatic_role != "reference":
        raise ValueError("a non-reference artifact cannot use the reference role")
    if role == "clean_control" and automatic_role == "reference":
        raise ValueError("the reference artifact cannot use the clean-control role")
    derived_measurement_id = (
        f"quality-{role}-{descriptor.candidate_id}-"
        f"{profile.digest.removeprefix('sha256:')[:8]}-"
        f"{workload.digest.removeprefix('sha256:')[:8]}"
    )
    measurement_id = measurement_id_override or derived_measurement_id
    store = LocalMeasurementBundleStore(output_root)
    try:
        store.load(measurement_id, repetition, attempt=attempt)
    except KeyError:
        pass
    else:
        raise RuntimeError(
            "this quality repetition attempt is already committed; refusing another GPU allocation"
        )
    if attempt > 1:
        try:
            previous_attempt = store.load(
                measurement_id, repetition, attempt=attempt - 1
            )
        except KeyError as error:
            raise RuntimeError(
                "a retry requires the preceding attempt's committed evidence"
            ) from error
        if previous_attempt.receipt["normalized"].get("valid") is True:
            raise RuntimeError("a valid quality repetition cannot be retried")
    for previous_repetition in range(1, repetition):
        if not any(
            _stored_attempt_is_valid(store, measurement_id, previous_repetition, value)
            for value in range(1, environment_profile.max_attempts + 1)
        ):
            raise RuntimeError(
                f"quality repetition {previous_repetition} has no valid committed attempt"
            )

    requests = build_quality_requests(
        profile, workload, served_model_name=str(candidate["server"]["served_model_name"])
    )
    evidence_namespace = hashlib.sha256(measurement_id.encode("utf-8")).hexdigest()
    evidence_root = (
        f"model-serving-quality/{evidence_namespace}/"
        f"repetition-{repetition:04d}-attempt-{attempt:02d}"
    )
    quality_spec = {
        "campaign_manifest_digest": campaign.manifest_digest,
        "quality_profile_digest": profile.digest,
        "quality_workload_digest": workload.digest,
        "repetition": repetition,
        "attempt": attempt,
        "evidence_root": evidence_root,
        "max_concurrency": profile.max_concurrency,
        "request_timeout_seconds": profile.request_timeout_seconds,
        "requests": list(requests),
    }
    dispatch_path = (
        output_root
        / ".dispatch"
        / measurement_id
        / f"repetition-{repetition:04d}-attempt-{attempt:02d}.json"
    )
    dispatch_identity = {
        "schema_version": "model-serving-quality-modal-dispatch/v0alpha1",
        "measurement_id": measurement_id,
        "campaign_manifest_digest": campaign.manifest_digest,
        "quality_profile_digest": profile.digest,
        "quality_workload_digest": workload.digest,
        "candidate_manifest_digest": descriptor.manifest_digest,
        "candidate_id": descriptor.candidate_id,
        "role": role,
        "evidence_root": evidence_root,
        "git_commit": collector_git_commit,
        "modal_client_version": local_modal_version,
        "repetition": repetition,
        "attempt": attempt,
    }
    try:
        with dispatch_path.open("r", encoding="utf-8") as source:
            dispatch_record = load_json(source)
    except FileNotFoundError:
        if collect_only:
            raise RuntimeError("no durable Modal quality dispatch exists")
        function_call = quality_serving_repetition.spawn(candidate, quality_spec)
        dispatch_record = {
            **dispatch_identity,
            "function_call_id": function_call.object_id,
            "dispatched_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json_atomic(dispatch_path, dispatch_record, prefix=".dispatch-")
    else:
        if not isinstance(dispatch_record, dict):
            raise RuntimeError("Modal quality dispatch record must be an object")
        expected_keys = {*dispatch_identity, "function_call_id", "dispatched_at"}
        if set(dispatch_record) != expected_keys:
            raise RuntimeError("Modal quality dispatch record fields differ")
        for key, expected in dispatch_identity.items():
            if key == "git_commit" and collect_only:
                dispatch_git_commit = dispatch_record.get(key)
                if (
                    not isinstance(dispatch_git_commit, str)
                    or len(dispatch_git_commit) != 40
                    or any(character not in _HEX for character in dispatch_git_commit)
                ):
                    raise RuntimeError(
                        "Modal quality dispatch record has an invalid commit"
                    )
                continue
            if dispatch_record.get(key) != expected:
                raise RuntimeError(f"Modal quality dispatch record {key} differs")
        function_call_id = dispatch_record.get("function_call_id")
        if not isinstance(function_call_id, str) or not function_call_id:
            raise RuntimeError("Modal quality dispatch has an invalid call ID")
        if not isinstance(dispatch_record.get("dispatched_at"), str):
            raise RuntimeError("Modal quality dispatch has an invalid timestamp")
        function_call = modal.FunctionCall.from_id(function_call_id)
    function_call_id = str(dispatch_record["function_call_id"])
    platform_git_commit = str(dispatch_record["git_commit"])
    print(
        json.dumps(
            {
                "status": "dispatched",
                "function_call_id": function_call_id,
                "dispatch_record": str(dispatch_path.resolve()),
            },
            indent=2,
            sort_keys=True,
        )
    )
    if dispatch_only:
        return

    client_started = time.monotonic()
    try:
        collection_timeout = (
            collect_timeout_seconds
            if collect_timeout_seconds or collect_only
            else None
        )
        remote_result = function_call.get(timeout=collection_timeout)
    except modal.exception.ConnectionError:
        print(json.dumps({"status": "collection_interrupted", "function_call_id": function_call_id}))
        return
    except (TimeoutError, modal.exception.TimeoutError) as error:
        if not isinstance(
            error,
            (modal.exception.FunctionTimeoutError, modal.exception.OutputExpiredError),
        ):
            print(json.dumps({"status": "pending", "function_call_id": function_call_id}))
            return
        remote_error: Exception | None = error
    except Exception as error:
        remote_error = error
    else:
        remote_error = None
    client_observed_ms = round((time.monotonic() - client_started) * 1000)
    if remote_error is not None:
        failure = _quality_failure_document(
            campaign=campaign,
            profile=profile,
            workload=workload,
            descriptor=descriptor,
            role=role,
            platform_git_commit=platform_git_commit,
            collector_git_commit=collector_git_commit,
            modal_client_version=local_modal_version,
            function_call_id=function_call_id,
            repetition=repetition,
            attempt=attempt,
            client_observed_ms=client_observed_ms,
            error=remote_error,
        )
        destination = store.save(measurement_id, repetition, failure, {}, attempt=attempt)
        raise RuntimeError(f"quality invocation failed; evidence: {destination}") from remote_error

    remote_result, raw_results, durable_evidence = _collect_remote_evidence(
        remote_result, expected_root=evidence_root
    )
    expected_remote_identity = {
        "candidate_id": descriptor.candidate_id,
        "model_id": campaign.target_model_id,
        "model_revision": campaign.target_model_revision,
        "served_model_name": str(candidate["server"]["served_model_name"]),
        "vllm_version": str(candidate["server"]["engine_version"]),
        "campaign_manifest_digest": campaign.manifest_digest,
        "quality_profile_digest": profile.digest,
        "quality_workload_digest": workload.digest,
        "repetition": repetition,
        "attempt": attempt,
    }
    validation_errors: list[str] = []
    for key, expected in expected_remote_identity.items():
        if remote_result.get(key) != expected:
            validation_errors.append(f"remote_receipt.{key} differs")
    expected_execution = {
        "max_concurrency": profile.max_concurrency,
        "request_timeout_seconds": profile.request_timeout_seconds,
    }
    if remote_result.get("execution") != expected_execution:
        validation_errors.append("remote_receipt.execution differs")
    current_environment = remote_result.get("environment", {})
    expected_environment = {
        "package_set_digest": environment_profile.resolved_package_digest,
        "base_image_digest": environment_profile.base_image_digest,
    }
    for key, expected in expected_environment.items():
        if current_environment.get(key) != expected:
            validation_errors.append(f"environment.{key} differs")
    current_gpu = remote_result.get("gpu_before", {})
    gpu_after = remote_result.get("gpu_after", {})
    expected_gpu = {
        "name": f"NVIDIA {environment_profile.gpu_type}",
        "memory_mib": str(environment_profile.gpu_memory_mib),
        "driver_version": environment_profile.gpu_driver_version,
        "power_limit_watts": environment_profile.gpu_power_limit_watts,
    }
    for key, expected in expected_gpu.items():
        if current_gpu.get(key) != expected:
            validation_errors.append(f"gpu.{key} differs")
        if gpu_after.get(key) != expected:
            validation_errors.append(f"gpu_after.{key} differs")
        if gpu_after.get(key) != current_gpu.get(key):
            validation_errors.append(f"gpu.{key} changed within repetition")

    outputs: dict[str, str] = {}
    expected_files = {f"{case.case_id}.json" for case in workload.cases}
    if set(raw_results) != expected_files:
        validation_errors.append("quality raw result set differs")
    for case in workload.cases:
        raw = raw_results.get(f"{case.case_id}.json")
        if raw is None:
            continue
        try:
            payload = json.loads(raw)
            if payload.get("model") != str(candidate["server"]["served_model_name"]):
                raise ValueError("returned model differs")
            content = payload["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("response content is not text")
            outputs[case.case_id] = content
        except Exception as error:
            validation_errors.append(f"{case.case_id}: {type(error).__name__}: {error}")
    quality_score = None
    if len(outputs) == len(workload.cases):
        try:
            quality_score = score_quality_outputs(
                profile, workload, outputs, repetition=repetition, role=role
            )
        except Exception as error:
            validation_errors.append(f"score: {type(error).__name__}: {error}")
    valid = (
        remote_result.get("ok") is True
        and not validation_errors
        and quality_score is not None
    )
    normalized = {
        "schema_version": "model-serving-quality-repetition/v0alpha1",
        "campaign_manifest_digest": campaign.manifest_digest,
        "quality_profile_digest": profile.digest,
        "quality_workload_digest": workload.digest,
        "candidate_manifest_digest": descriptor.manifest_digest,
        "candidate_id": descriptor.candidate_id,
        "role": role,
        "platform_build": {
            "git_commit": platform_git_commit,
            "collector_git_commit": collector_git_commit,
            "modal_client_version": local_modal_version,
        },
        "modal_function_call_id": function_call_id,
        "durable_evidence": durable_evidence,
        "repetition": repetition,
        "attempt": attempt,
        "valid": valid,
        "validation_errors": validation_errors,
        "client_observed_ms": client_observed_ms,
        "remote_receipt": remote_result,
        "quality_score": quality_score,
    }
    normalized_evidence_digest = _publish_normalized_evidence(
        durable_evidence, normalized
    )
    normalized["durable_evidence"] = {
        **durable_evidence,
        "normalized_digest": normalized_evidence_digest,
    }
    destination = store.save(
        measurement_id, repetition, normalized, raw_results, attempt=attempt
    )
    print(
        json.dumps(
            {
                "valid": valid,
                "repetition": repetition,
                "attempt": attempt,
                "score_ppm": (
                    quality_score["score_ppm"] if quality_score else None
                ),
                "bundle": str(destination.resolve()),
            },
            indent=2,
            sort_keys=True,
        )
    )
    if not valid:
        raise RuntimeError(f"quality repetition is invalid: {destination}")


def _stored_attempt_is_valid(
    store: Any, measurement_id: str, repetition: int, attempt: int
) -> bool:
    try:
        bundle = store.load(measurement_id, repetition, attempt=attempt)
    except KeyError:
        return False
    return bundle.receipt["normalized"].get("valid") is True


def _quality_failure_document(
    *,
    campaign: Any,
    profile: Any,
    workload: Any,
    descriptor: Any,
    role: str,
    platform_git_commit: str,
    collector_git_commit: str,
    modal_client_version: str,
    function_call_id: str,
    repetition: int,
    attempt: int,
    client_observed_ms: int,
    error: Exception,
) -> dict[str, Any]:
    return {
        "schema_version": "model-serving-quality-repetition/v0alpha1",
        "campaign_manifest_digest": campaign.manifest_digest,
        "quality_profile_digest": profile.digest,
        "quality_workload_digest": workload.digest,
        "candidate_manifest_digest": descriptor.manifest_digest,
        "candidate_id": descriptor.candidate_id,
        "role": role,
        "platform_build": {
            "git_commit": platform_git_commit,
            "collector_git_commit": collector_git_commit,
            "modal_client_version": modal_client_version,
        },
        "modal_function_call_id": function_call_id,
        "repetition": repetition,
        "attempt": attempt,
        "valid": False,
        "validation_errors": [],
        "client_observed_ms": client_observed_ms,
        "failure": {
            "stage": "remote_invocation",
            "type": type(error).__name__,
            "message": str(error)[-8000:],
        },
        "remote_receipt": None,
        "quality_score": None,
    }


def _write_json_atomic(destination: Path, value: dict[str, Any], *, prefix: str) -> None:
    """Commit local control evidence before relying on a remote side effect."""

    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=prefix,
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(rendered)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
