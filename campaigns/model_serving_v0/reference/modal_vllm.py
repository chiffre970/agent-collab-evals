"""Run the pinned stock-vLLM reference privately on one Modal L4."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
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
        "repetition",
        "attempt",
        "point_timeout_seconds",
        "invocations",
    }
    if set(spec) != expected:
        raise ValueError("benchmark spec fields differ")
    if not isinstance(spec["repetition"], int) or spec["repetition"] < 1:
        raise ValueError("invalid benchmark repetition")
    if not isinstance(spec["attempt"], int) or spec["attempt"] < 1:
        raise ValueError("invalid benchmark attempt")
    point_timeout = spec["point_timeout_seconds"]
    if not isinstance(point_timeout, int) or not 1 <= point_timeout <= 600:
        raise ValueError("invalid point timeout")
    for key in ("campaign_manifest_digest", "measurement_profile_digest"):
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
    volumes={HF_CACHE_PATH: model_cache},
    gpu="L4",
    max_containers=1,
    min_containers=0,
    retries=0,
    restrict_modal_access=True,
    single_use_containers=True,
    timeout=FUNCTION_TIMEOUT_SECONDS,
)
def benchmark_reference_repetition(
    candidate: dict[str, Any], benchmark_spec: dict[str, Any]
) -> dict[str, Any]:
    """Run one isolated, post-warmup reference measurement repetition."""

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
    return {
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
        "raw_results": raw_results,
    }


@app.local_entrypoint()
def main(
    output_path: str = "tmp/calibration/model-serving-reference-smoke.json",
    baseline: bool = False,
    repetition: int = 1,
    attempt: int = 1,
    baseline_output_root: str = "tmp/calibration/model-serving-reference",
) -> None:
    candidate_path = Path(__file__).with_name("candidate.json")
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    if candidate["build"]["image_ref"] != IMAGE_REF:
        raise RuntimeError("Modal image does not match candidate.json")
    if candidate["build"]["image_digest"] != IMAGE_DIGEST:
        raise RuntimeError("Modal image digest does not match candidate.json")
    if candidate["build"]["dependency_lock"] != DEPENDENCY_LOCK:
        raise RuntimeError("Modal dependency does not match candidate.json")
    if baseline:
        _run_baseline_repetition(
            candidate,
            repetition=repetition,
            attempt=attempt,
            output_root=Path(baseline_output_root),
        )
        return
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
    candidate: dict[str, Any], *, repetition: int, attempt: int, output_root: Path
) -> None:
    """Trusted local composition; this path performs a billable GPU run."""

    from agent_collab_evals.adapters.local_measurements import (
        LocalMeasurementBundleStore,
    )
    from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
    from agent_collab_evals.campaigns.serving_benchmark import (
        build_vllm_benchmark_invocations,
    )
    from agent_collab_evals.campaigns.serving_measurement import (
        parse_vllm_benchmark_result,
    )

    campaign_path = Path(__file__).parents[1] / "campaign.toml"
    repository_root = Path(__file__).resolve().parents[3]
    campaign = ModelServingCampaign.load(campaign_path)
    profile = campaign.measurement_profile()
    if not 1 <= repetition <= profile.repetitions:
        raise ValueError(
            f"repetition must be between 1 and {profile.repetitions}"
        )
    if not 1 <= attempt <= profile.max_attempts:
        raise ValueError(f"attempt must be between 1 and {profile.max_attempts}")
    status = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=normal"],
        cwd=repository_root,
        text=True,
    ).strip()
    if status:
        raise RuntimeError("formal baseline runs require a clean Git worktree")
    git_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository_root, text=True
    ).strip()
    local_modal_version = importlib.metadata.version("modal")
    if local_modal_version != profile.modal_client_version:
        raise RuntimeError("local Modal client does not match the measurement profile")
    descriptor = campaign.validate_reference_candidate()
    measurement_id = (
        f"baseline-{descriptor.candidate_id}-"
        f"{profile.digest.removeprefix('sha256:')[:12]}"
    )
    store = LocalMeasurementBundleStore(output_root)
    try:
        store.load(measurement_id, repetition, attempt=attempt)
    except KeyError:
        pass
    else:
        raise RuntimeError(
            "this baseline repetition attempt is already committed; "
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
    )
    benchmark_spec = {
        "campaign_manifest_digest": campaign.manifest_digest,
        "measurement_profile_digest": profile.digest,
        "repetition": repetition,
        "attempt": attempt,
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
    client_started = time.monotonic()
    try:
        remote_result = benchmark_reference_repetition.remote(candidate, benchmark_spec)
    except Exception as error:
        client_observed_ms = round((time.monotonic() - client_started) * 1000)
        failure = {
            "schema_version": "model-serving-reference-repetition/v0alpha1",
            "campaign_manifest_digest": campaign.manifest_digest,
            "measurement_profile_digest": profile.digest,
            "candidate_manifest_digest": descriptor.manifest_digest,
            "candidate_id": descriptor.candidate_id,
            "platform_build": {
                "git_commit": git_commit,
                "modal_client_version": local_modal_version,
            },
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
            f"reference baseline invocation failed; evidence: {destination}"
        ) from error
    client_observed_ms = round((time.monotonic() - client_started) * 1000)
    raw_results = remote_result.pop("raw_results")
    points = []
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
            points.append(
                parse_vllm_benchmark_result(
                    raw,
                    invocation=invocation,
                    model_source=model_source,
                    metric_percentiles=plan.metric_percentiles,
                )
            )
        except Exception as error:
            parse_errors.append(
                f"{invocation.bucket_id}/{invocation.request_rate}: "
                f"{type(error).__name__}: {error}"
            )
    environment_errors: list[str] = []
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
    valid = (
        remote_result.get("ok") is True
        and not parse_errors
        and not environment_errors
        and len(points) == len(invocations)
        and all(point.valid for point in points)
    )
    normalized = {
        "schema_version": "model-serving-reference-repetition/v0alpha1",
        "campaign_manifest_digest": campaign.manifest_digest,
        "measurement_profile_digest": profile.digest,
        "candidate_manifest_digest": descriptor.manifest_digest,
        "candidate_id": descriptor.candidate_id,
        "platform_build": {
            "git_commit": git_commit,
            "modal_client_version": local_modal_version,
        },
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
        "points": [point.to_document() for point in points],
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
        raise RuntimeError(f"reference baseline repetition is invalid: {destination}")
