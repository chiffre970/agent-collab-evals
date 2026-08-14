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
from pathlib import Path
from typing import Any

import modal


APP_NAME = "agent-collab-evals-model-serving-reference"
IMAGE_REF = "nvidia/cuda:12.9.0-devel-ubuntu22.04"
DEPENDENCY_LOCK = "vllm==0.21.0"

HF_CACHE_PATH = "/cache/huggingface"
VLLM_CACHE_PATH = "/cache/vllm"
SERVER_LOG_PATH = "/tmp/reference-vllm.log"
STARTUP_TIMEOUT_SECONDS = 600
FUNCTION_TIMEOUT_SECONDS = 1800

app = modal.App(APP_NAME)
reference_image = (
    modal.Image.from_registry(IMAGE_REF, add_python="3.12")
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
    return {
        "returned_model": payload.get("model"),
        "content": content.strip(),
        "usage": payload.get("usage", {}),
    }


def _gpu_metadata() -> dict[str, str]:
    output = subprocess.check_output(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ],
        text=True,
    ).strip()
    name, memory_mib, driver_version = [part.strip() for part in output.split(",")]
    return {
        "name": name,
        "memory_mib": memory_mib,
        "driver_version": driver_version,
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

    if canary["returned_model"] != served_model_name:
        raise RuntimeError("vLLM returned an unexpected served model name")
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


@app.local_entrypoint()
def main(
    output_path: str = "tmp/calibration/model-serving-reference-smoke.json",
) -> None:
    candidate_path = Path(__file__).with_name("candidate.json")
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    if candidate["build"]["image_ref"] != IMAGE_REF:
        raise RuntimeError("Modal image does not match candidate.json")
    if candidate["build"]["dependency_lock"] != DEPENDENCY_LOCK:
        raise RuntimeError("Modal dependency does not match candidate.json")
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
