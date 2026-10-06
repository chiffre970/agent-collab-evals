"""Restricted, same-GPU jobs for the exploratory peer pilot only."""

import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

import modal


BASE_FILE = Path(__file__).with_name("modal_vllm.py")
PAIRED_FUNCTION_TIMEOUT_SECONDS = 3000
ALLOWED_DRIVERS = ["580.95.05", "610.57.04"]


def _base():
    name = "paired_stock_vllm_helpers"
    if name not in sys.modules:
        path = Path("/opt/evaluator/modal_vllm.py") if modal.is_local() is False else BASE_FILE
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


base = _base()
app = modal.App("agent-collab-evals-paired-serving")
image = base.reference_image.add_local_file(BASE_FILE, "/opt/evaluator/modal_vllm.py", copy=True)


def _served_once(module, candidate, spec, role):
    """Use the existing server/request primitives, with a fresh role cache."""
    requests = []
    for request in spec["requests"]:
        body = dict(request["body"])
        for name in ("temperature", "top_p", "min_p"):
            body[name] = body.pop(name + "_milli") / 1000
        requests.append({"case_id": request["case_id"], "body": body})
    gpu = module._gpu_metadata()
    executable = {**spec, "requests": requests, "expected_gpu": {
        k: gpu[k] for k in ("name", "memory_mib", "driver_version", "power_limit_watts")}}
    module._validate_quality_spec(executable)
    installed = importlib.metadata.version("vllm")
    if installed != candidate["server"]["engine_version"]:
        raise RuntimeError("installed serving engine differs")
    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    environment = module._server_environment(offline=True)
    environment["VLLM_CACHE_ROOT"] = f"/tmp/vllm-cache-{role}"
    with Path(f"/tmp/paired-{role}-server.log").open("w") as log:
        process = subprocess.Popen(module._server_command(candidate), env=environment,
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True, text=True)
        try:
            startup_ms = module._wait_until_ready(process, 8000)
            before = module._chat_canary(8000, "target-model")
            evaluated = time.monotonic()
            raw, case_receipts, error = module._run_quality_requests(requests, server_port=8000,
                request_timeout_seconds=spec["request_timeout_seconds"], max_concurrency=spec["max_concurrency"])
            evaluated_ms = round((time.monotonic() - evaluated) * 1000)
            after = module._chat_canary(8000, "target-model")
        finally:
            module._stop_process(process)
    return {"candidate_id": candidate["candidate_id"], "model_id": candidate["model"]["id"],
        "model_revision": candidate["model"]["revision"], "served_model_name": "target-model",
        "vllm_version": installed, "ok": error is None, "error": error,
        **{k: spec[k] for k in ("campaign_manifest_digest", "quality_profile_digest", "quality_workload_digest", "repetition", "attempt")},
        "started_at": started_at, "gpu_before": gpu, "gpu_after": module._gpu_metadata(),
        "environment": module._environment_receipt(), "canary_before": before, "canary_after": after,
        "case_receipts": case_receipts,
        "execution": {k: spec[k] for k in ("max_concurrency", "request_timeout_seconds")},
        "timing": {"startup_ms": startup_ms, "evaluated_cases_ms": evaluated_ms,
            "function_body_ms": round((time.monotonic() - started) * 1000)}}, raw


@app.function(
    image=image,
    **base.GPU_RESOURCES,
    volumes={base.HF_CACHE_PATH: base.model_cache.with_mount_options(read_only=True)},
    gpu="L4", max_containers=1, min_containers=0, retries=0,
    block_network=True, restrict_modal_access=True, single_use_containers=True,
    timeout=PAIRED_FUNCTION_TIMEOUT_SECONDS,
)
def paired_serving(reference, candidate, spec):
    """Run both roles in one container; stage only after candidate exit."""
    module = _base()
    if set(spec) != {"benchmark", "allowed_drivers", "expected_gpu", "order", "pair_digest"}:
        raise ValueError("paired spec fields differ")
    authority = {k: v for k, v in spec.items() if k != "pair_digest"}
    digest = "sha256:" + hashlib.sha256(module._stable_json_bytes(authority).rstrip(b"\n")).hexdigest()
    benchmark = spec["benchmark"]
    if (digest != spec["pair_digest"] or spec["allowed_drivers"] != ALLOWED_DRIVERS
        or type(benchmark["repetition"]) is not int or not 1 <= benchmark["repetition"] <= 3
        or spec["order"] != (["reference", "candidate"] if benchmark["repetition"] % 2 else ["candidate", "reference"])
        or spec["expected_gpu"] != {"name": "NVIDIA L4", "memory_mib": "23034", "power_limit_watts": "72.00"}):
        raise ValueError("paired policy differs")
    module._validate_evidence_root(benchmark["evidence_root"])
    module._server_command(reference)
    module._server_command(candidate)
    started = time.monotonic()
    before = module._gpu_metadata()
    errors = [f"gpu.{k} differs" for k, v in spec["expected_gpu"].items() if before.get(k) != v]
    if before.get("driver_version") not in ALLOWED_DRIVERS:
        errors.append("undeclared GPU driver")
    roles, raw = {}, {}
    if not errors:
        for role in spec["order"]:
            document = reference if role == "reference" else candidate
            try:
                if "invocations" in benchmark:
                    receipt, output = module._benchmark_once(document, benchmark, cache_role=role)
                else:
                    receipt, output = _served_once(module, document, benchmark, role)
            except Exception as error:
                errors.append(f"{role}: {type(error).__name__}")
                break
            roles[role] = receipt
            raw.update({f"{role}-{k}": v for k, v in output.items()})
            if receipt.get("ok") is not True:
                errors.append(f"{role} execution failed")
                break
    after = module._gpu_metadata()
    keys = ("name", "memory_mib", "driver_version", "power_limit_watts", "pci_bus_id")
    observations = [after] + [r[k] for r in roles.values() for k in ("gpu_before", "gpu_after")]
    if any(any(g.get(k) != before.get(k) for k in keys) for g in observations):
        errors.append("GPU identity changed")
    receipt = {"schema_version": "modal-paired-serving-repetition/v1", "pair_digest": spec["pair_digest"],
        "ok": not errors, "errors": errors, "order": spec["order"], "roles": roles,
        "gpu_before": before, "gpu_after": after, "environment": module._environment_receipt(),
        "reference_document_digest": "sha256:" + hashlib.sha256(module._stable_json_bytes(reference)).hexdigest(),
        "candidate_document_digest": "sha256:" + hashlib.sha256(module._stable_json_bytes(candidate)).hexdigest(),
        "timing": {"function_body_ms": round((time.monotonic() - started) * 1000)}}
    return module._stage_evaluator_evidence(benchmark["evidence_root"], receipt, raw)
