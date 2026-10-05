"""No-refund extension of the existing journal for three matched GPU pairs."""

import re
from pathlib import Path

from .canonical import digest_value
from .compute_backend import ComputeExecutionRequest


def validate_performance_extension(document, plan_digest):
    from .pilot_retry import _resolve, validate_retry
    from .solo_evaluation_spend import evaluation_admissions
    fields = {"schema_version", "previous_amendment", "prior_plan_digest", "prior_stop",
        "prior_receipts_digest", "followup_manifest", "run_id", "provider_limits_usd_nanos",
        "total_limit_usd_nanos"}
    if (not isinstance(document, dict) or set(document) != fields
        or document["schema_version"] != "exploratory-solo-retry/v12"
        or document["prior_plan_digest"] != plan_digest
        or document["provider_limits_usd_nanos"] != {"modal": 20_000_000_000, "openrouter": 3_100_000_000}
        or document["total_limit_usd_nanos"] != 23_100_000_000
        or not isinstance(document["run_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", document["run_id"])):
        raise ValueError("performance-only extension fields or caps differ")
    _, previous = _resolve(document["previous_amendment"])
    if previous.get("schema_version") != "exploratory-solo-retry/v11":
        raise ValueError("performance extension requires the reviewed V11 amendment")
    original, releases = validate_retry(previous, plan_digest)
    _, stop = _resolve(document["prior_stop"])
    prior_manifest_path, prior_manifest = _resolve(previous["continuation_manifest"])
    if (stop.get("schema_version") != "evaluation-continuation-stop/v1"
        or stop.get("status") != "stopped" or stop.get("scoreable") is not False
        or stop.get("manifest_digest") != previous["continuation_manifest"]["digest"]
        or stop.get("spend_admission", {}).get("retry_amendment_digest") != digest_value(previous)):
        raise ValueError("performance extension prior stop differs")
    receipts = stop["spend_admission"]["receipts"]
    old = {r["operation_key"]: r for r in original}
    all_receipts = {r["operation_key"]: r for r in receipts}
    expected = evaluation_admissions(previous)
    if (len(all_receipts) != len(receipts) or len(receipts) != len(original) + 7
        or set(all_receipts) - set(old) != set(expected)
        or any(all_receipts.get(k) != v for k, v in old.items())
        or digest_value(receipts) != document["prior_receipts_digest"]):
        raise ValueError("performance extension must preserve every prior admission")
    for key, value in expected.items():
        r = all_receipts[key]
        if (r["provider"], r["purpose"], r["maximum_usd_nanos"]) != value:
            raise ValueError("performance extension prior allowance differs")
    _, manifest = _resolve(document["followup_manifest"])
    binding = manifest.get("binding", {})
    if (manifest.get("schema_version") != "solo-paired-performance-followup/v1"
        or manifest.get("scoreable") is not False or manifest.get("new_gpu_calls") != 3
        or manifest.get("agent_reruns") != 0 or manifest.get("model_calls") != 0
        or manifest.get("run_id") != document["run_id"] or document["run_id"] == previous["run_id"]
        or manifest.get("estimate", {}).get("per_execution_allowance_usd_nanos") != 1_137_984_000
        or manifest["estimate"].get("shared_overhead_allowance_usd_nanos") != 1_000_000_000
        or manifest["estimate"].get("function_timeout_seconds") != 3000
        or len(binding.get("jobs", [])) != 3):
        raise ValueError("performance extension may fund only three reviewed matched pairs")
    _, retained = _resolve(binding["retained_inputs"])
    if retained.get("previous_manifest") != {"file": str(prior_manifest_path), "digest": previous["continuation_manifest"]["digest"]}:
        raise ValueError("performance extension source continuation differs")
    for index, job in enumerate(binding["jobs"], 1):
        request = ComputeExecutionRequest.from_document(job["request"])
        spec = job["spec"]
        if (request.campaign_run_id != document["run_id"] or request.maximum_seconds != 3000
            or request.scope.value != "hidden" or request.candidate_digest != prior_manifest["selected_candidate_digest"]
            or spec.get("allowed_drivers") != ["580.95.05", "610.57.04"]
            or spec["benchmark"]["repetition"] != index or spec["benchmark"]["attempt"] != 1
            or spec["pair_digest"] != digest_value({k: v for k, v in spec.items() if k != "pair_digest"})):
            raise ValueError("performance extension request differs")
    # No additional release: all older uncertain calls and unused allowances
    # remain reserved. The new work must fit the existing remaining balance.
    return receipts, releases


def performance_admissions(amendment):
    from .pilot_retry import _resolve
    _, manifest = _resolve(amendment["followup_manifest"])
    return {"pilot:retry-" + digest_value(amendment)[7:] + ":modal:base": ("modal", "overhead", 1_000_000_000),
        **{f"pilot:{amendment['run_id']}:compute:{digest_value(j['request'])[7:]}": ("modal", "pilot", 1_137_984_000)
           for j in manifest["binding"]["jobs"]}}
