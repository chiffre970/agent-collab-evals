"""Verify Modal auth, the Hugging Face secret, and optionally an L4 allocation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
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
    cpu=(4.0, 4.0),
    memory=(16384, 16384),
    startup_timeout=60,
    timeout=120,
    retries=0,
    single_use_containers=True,
    block_network=True,
    restrict_modal_access=True,
)
def verify_l4(hold_seconds: int = 0) -> dict[str, str]:
    """Allocate one L4 briefly and return non-sensitive device metadata."""

    if type(hold_seconds) is not int or not 0 <= hold_seconds <= 60:
        raise ValueError("hold duration must be between 0 and 60 seconds")
    output = subprocess.check_output(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ],
        text=True,
    ).strip()
    name, memory_mib, driver_version = [part.strip() for part in output.split(",")]
    time.sleep(hold_seconds)
    return {
        "name": name,
        "memoryMiB": memory_mib,
        "driverVersion": driver_version,
    }


def qualify_gpu(function, envelope, evidence_root: Path, repository: Path, *,
                monotonic=time.monotonic, sleep=time.sleep) -> dict:
    """Admit two calls once, retain IDs, and observe exact-call cancellation.

    Function statistics are observational. Quiescence does not certify final
    billing, termination of every provider resource, or the scored boundary.
    """
    from agent_collab_evals.canonical import digest_bytes, digest_value
    from agent_collab_evals.modal_pilot_cost import modal_pilot_cost
    from agent_collab_evals.pilot_evidence import retain_document

    estimate = modal_pilot_cost(
        repository / "campaigns/model_serving_v0/reference/modal_vllm.py",
        repository / "config/compute/modal-pilot-cost-v1.json",
    )
    request = {
        "script_digest": digest_bytes(Path(__file__).read_bytes()),
        "calls": [{"hold_seconds": 0}, {"hold_seconds": 60}],
        "cost_estimate": estimate,
    }
    admission = envelope.reserve(
        operation_key="qualification:modal-access-v1", provider="modal",
        purpose="qualification", request_digest=digest_value(request),
        maximum_usd_nanos=2 * estimate["per_execution_allowance_usd_nanos"],
        allow_existing=False,
    )
    retain_document(evidence_root / "admission.json", {"request": request, "admission": admission})

    def observe(expected_running: bool) -> dict:
        deadline = monotonic() + 45
        while True:
            stats = function.get_current_stats()
            observed = {name: getattr(stats, name) for name in (
                "backlog", "num_total_runners", "num_running_inputs")}
            matches = (observed["num_running_inputs"] == 1 if expected_running
                else all(value == 0 for value in observed.values()))
            if matches:
                return observed
            if monotonic() >= deadline:
                raise TimeoutError("Modal resource observation did not reach the expected state")
            sleep(1)

    for index, hold in enumerate((0, 60)):
        call = None
        try:
            # Retain intent before dispatch. A missing ID after this point is an
            # unresolved submission, never permission to retry automatically.
            retain_document(evidence_root / f"call-{index}-intent.json", {"hold_seconds": hold})
            call = function.spawn(hold_seconds=hold)
            retain_document(evidence_root / f"call-{index}-dispatch.json", {"call_id": call.object_id})
            if hold:
                result = {"running_observation": observe(True)}
            else:
                result = call.get(timeout=180)
                if "L4" not in str(result.get("name", "")):
                    raise RuntimeError("Modal returned an unexpected GPU")
            retain_document(evidence_root / f"call-{index}-result.json", result)
        except BaseException as error:
            retain_document(evidence_root / f"call-{index}-failure.json", {
                "error_type": type(error).__name__,
                "dispatch_status": "unknown" if call is None else "known",
            })
            raise
        finally:
            if call is not None:
                try:
                    call.cancel(terminate_containers=True)
                    retain_document(evidence_root / f"call-{index}-cancellation.json", {
                        "call_id": call.object_id, "status": "cancellation_requested",
                    })
                    retain_document(evidence_root / f"call-{index}-quiescence.json", observe(False))
                except BaseException as error:
                    retain_document(evidence_root / f"call-{index}-cleanup-failure.json", {
                        "call_id": call.object_id, "error_type": type(error).__name__,
                    })
                    raise
    outcome = {"schema_version": "modal-access-qualification/v1",
        "status": "device_and_cancellation_observed", "call_count": 2,
        "provider_billing_reconciled": False, "scored_boundary_qualified": False,
        "admission_digest": digest_value(admission)}
    retain_document(evidence_root / "outcome.json", outcome)
    return outcome


@app.local_entrypoint()
def main(gpu: bool = False, spend_envelope: str = "") -> None:
    if gpu:
        if not spend_envelope:
            raise ValueError("--gpu requires the pilot's shared --spend-envelope")
        from agent_collab_evals.canonical import parse_json
        from agent_collab_evals.pilot_spend import PilotSpendEnvelope

        repository = Path(__file__).resolve().parents[2]
        root = Path(spend_envelope).resolve()
        plan = parse_json((repository / "config/pilots/solo-spend-envelope-v1.json").read_text())
        envelope = PilotSpendEnvelope(root, plan)
        print(json.dumps(qualify_gpu(verify_l4, envelope,
            root / "modal-access-v1", repository), indent=2, sort_keys=True))
        return
    print("Checking Modal execution and the Hugging Face secret...")
    print(json.dumps(verify_huggingface_secret.remote(), indent=2, sort_keys=True))

    print("GPU qualification skipped. It requires --gpu and --spend-envelope.")
