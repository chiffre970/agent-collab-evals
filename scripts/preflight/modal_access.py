"""Verify Modal auth, the Hugging Face secret, and optionally an L4 allocation."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request

import modal


APP_NAME = "agent-collab-evals-preflight"
HUGGINGFACE_SECRET_NAME = os.environ.get(
    "MODAL_HF_SECRET_NAME", "huggingface-secret"
)

app = modal.App(APP_NAME)
base_image = modal.Image.debian_slim(python_version="3.12")
huggingface_secret = modal.Secret.from_name(
    HUGGINGFACE_SECRET_NAME,
    required_keys=["HF_TOKEN"],
)


@app.function(
    image=base_image,
    secrets=[huggingface_secret],
    cpu=0.125,
    memory=128,
    timeout=60,
)
def verify_huggingface_secret() -> dict[str, object]:
    """Validate the injected token without returning or logging the token."""

    token = os.environ["HF_TOKEN"]
    request = urllib.request.Request(
        "https://huggingface.co/api/whoami-v2",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "agent-collab-evals-preflight/0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"Hugging Face rejected the Modal secret with HTTP {error.code}."
        ) from error

    return {
        "authenticated": True,
        "account": payload.get("name"),
        "tokenType": payload.get("auth", {}).get("type"),
    }


@app.function(
    image=base_image,
    gpu="L4",
    max_containers=1,
    min_containers=0,
    timeout=120,
)
def verify_l4() -> dict[str, str]:
    """Allocate one L4 briefly and return non-sensitive device metadata."""

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
        "memoryMiB": memory_mib,
        "driverVersion": driver_version,
    }


@app.local_entrypoint()
def main(gpu: bool = False) -> None:
    print("Checking Modal execution and the Hugging Face secret...")
    print(json.dumps(verify_huggingface_secret.remote(), indent=2, sort_keys=True))

    if gpu:
        print("Allocating one L4 for a short device check...")
        print(json.dumps(verify_l4.remote(), indent=2, sort_keys=True))
    else:
        print(
            "L4 check skipped. Re-run with --gpu to perform the billable GPU "
            "smoke test."
        )
