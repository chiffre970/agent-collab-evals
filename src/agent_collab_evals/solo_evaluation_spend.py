"""Narrow settlement and dollar admission for six evaluation-only jobs.

Settlement is an operator-reviewed amendment, never an automatic refund. This
module preserves older unresolved reserves and allows no agent/model spending.
"""

from pathlib import Path
import re

from .canonical import digest_value


SOURCE_AUDIT_DIGEST = "sha256:be6dd8d3e8f223baa9f316f414b059943c80bf09ad1772b4359eb47e83b92e32"


def validate_evaluation_settlement(document, plan_digest):
    """Verify the original run and all retained billing before releasing capacity."""
    from .pilot_retry import _resolve, _billing_nanos, _reconcile_prior_model, validate_retry
    from .compute_backend import ComputeExecutionRequest

    fields = {"schema_version", "previous_amendment", "prior_plan_digest", "prior_receipts_digest",
        "prior_audit", "prior_run_config", "prior_budget_database", "prior_budget_plan",
        "recovery_plan", "continuation_manifest", "terminal_observation", "billing_report",
        "run_id", "provider_limits_usd_nanos", "total_limit_usd_nanos", "billing_buffer_usd_nanos"}
    if (not isinstance(document, dict) or set(document) != fields
        or document["schema_version"] != "exploratory-solo-retry/v11"
        or document["prior_plan_digest"] != plan_digest
        or document["provider_limits_usd_nanos"] != {"modal": 20_000_000_000, "openrouter": 3_100_000_000}
        or document["total_limit_usd_nanos"] != 23_100_000_000
        or type(document["billing_buffer_usd_nanos"]) is not int
        or document["billing_buffer_usd_nanos"] != 100_000_000
        or not isinstance(document["run_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", document["run_id"])):
        raise ValueError("evaluation settlement fields or ceilings differ")
    _, previous = _resolve(document["previous_amendment"])
    if previous.get("schema_version") != "exploratory-solo-retry/v10":
        raise ValueError("evaluation settlement requires the reviewed V10 amendment")
    prior, releases = validate_retry(previous, plan_digest)
    audit_path, audit = _resolve(document["prior_audit"])
    config_path, config = _resolve(document["prior_run_config"])
    if (document["prior_audit"]["digest"] != SOURCE_AUDIT_DIGEST
        or audit.get("run_id") != previous["run_id"]
        or audit.get("status") != "aborted" or audit.get("scoreable") is not False
        or audit.get("budget_reconciliation", {}).get("valid") is not True
        or document["run_id"] == audit["run_id"]
        or config_path != audit_path.parent / "run-config.json"
        or document["prior_run_config"]["digest"] != audit.get("run_config_digest")
        or Path(document["prior_budget_database"]["file"]) != audit_path.parent / "budget.sqlite3"
        or Path(document["prior_budget_plan"]["file"]) != audit_path.parent / "budget-plan.json"):
        raise ValueError("evaluation settlement source run differs")
    recovery_path, recovery = _resolve(document["recovery_plan"])
    _, continuation = _resolve(document["continuation_manifest"])
    if (recovery.get("schema_version") != "solo-evaluation-recovery-plan/v1"
        or recovery.get("source_audit_digest") != SOURCE_AUDIT_DIGEST
        or recovery.get("source_root") != str(audit_path.parent)
        or recovery.get("summary") != {"reuse_verified": 6, "replace_environment_rejected": 1, "execute_missing": 5}
        or recovery.get("execution_authorized") is not False
        or continuation.get("schema_version") != "solo-evaluation-continuation/v1"
        or continuation.get("execution_mode") != "live"
        or continuation.get("run_id") != document["run_id"]
        or continuation.get("scoreable") is not False
        or continuation.get("agent_reruns") != 0 or continuation.get("model_calls") != 0
        or continuation.get("source_recovery_plan") != {"file": str(recovery_path), "digest": document["recovery_plan"]["digest"]}):
        raise ValueError("evaluation settlement continuation binding differs")
    jobs = continuation.get("jobs", [])
    entries = recovery["entries"]
    if (len(jobs) != 12 or len(entries) != 12
        or {digest_value(j["source"]) for j in jobs} != {digest_value(e) for e in entries}):
        raise ValueError("evaluation settlement source request inventory differs")
    outstanding = [job for job in jobs if job["replacement_request"] is not None]
    if [job["slot"] for job in outstanding] != ["quality-2-reference", "quality-3-reference", "quality-3-candidate",
            "performance-1", "performance-2", "performance-3"]:
        raise ValueError("evaluation settlement may authorize only six hidden jobs")
    for job in jobs:
        original = ComputeExecutionRequest.from_document(job["source"]["request"])
        if job["replacement_request"] is None:
            if job["source"]["action"] != "reuse_verified":
                raise ValueError("evaluation settlement reused source is not verified")
            continue
        request = ComputeExecutionRequest.from_document(job["replacement_request"])
        if (request.campaign_run_id != document["run_id"] or request.scope.value != "hidden"
            or request.maximum_seconds != original.maximum_seconds or request.maximum_seconds != 1800
            or request.candidate_digest != original.candidate_digest
            or request.candidate_manifest_digest != original.candidate_manifest_digest):
            raise ValueError("evaluation settlement replacement request differs")
    receipts = audit["spend_admission"]["receipts"]
    by_key = {r["operation_key"]: r for r in receipts}
    old_keys = {r["operation_key"] for r in prior}
    prefix = "pilot:retry-" + digest_value(previous)[7:] + ":"
    expected = {prefix + "modal:base": ("modal", "overhead", 1_000_000_000),
        prefix + "openrouter:base": ("openrouter", "pilot", 2_900_000_000),
        **{f"pilot:{audit['run_id']}:compute:{e['request_digest'][7:]}": ("modal", "pilot", 766_080_000)
            for e in entries}}
    if (len(receipts) != len(prior) + 14 or len(by_key) != len(receipts)
        or set(by_key) - old_keys != set(expected)
        or any(by_key.get(r["operation_key"]) != r for r in prior)
        or digest_value(receipts) != document["prior_receipts_digest"]
        or audit["spend_admission"].get("retry_amendment_digest") != digest_value(previous)):
        raise ValueError("evaluation settlement prior dollar admissions differ")
    for key, values in expected.items():
        item = by_key[key]
        if (item["provider"], item["purpose"], item["maximum_usd_nanos"]) != values:
            raise ValueError("evaluation settlement prior allowance differs")
    _, terminal = _resolve(document["terminal_observation"])
    _, billing = _resolve(document["billing_report"])
    calls = terminal.get("calls", [])
    call_ids = {entry["source_call_id"] for entry in entries if entry["source_call_id"]}
    apps = {call["provider_metadata"]["app_id"] for call in calls}
    if (terminal.get("schema_version") != "devbound-terminal-observation/v1"
        or terminal.get("actor_containers_active") is not False
        or terminal.get("new_model_calls") != 0 or terminal.get("new_scored_dispatches") != 0
        or len(calls) != 7 or len(call_ids) != 7 or len(apps) != 7
        or {call["function_call_id"] for call in calls} != call_ids
        or any(call.get("status") != "terminal_result" or
            call["provider_metadata"].get("function_call_id") != call["function_call_id"] for call in calls)
        or any(app.get("state") != "stopped" for app in terminal.get("apps", []))
        or billing.get("schema_version") != "devbound-billing-snapshot/v1"
        or set(billing.get("attributed_app_ids", [])) != apps
        or billing.get("command") != ["billing", "report", "--start", "2026-10-03", "--end", "2026-10-05",
            "-r", "h", "--json", "--show-resources"]):
        raise ValueError("evaluation settlement provider terminal or billing evidence differs")
    rows = [row for row in billing["rows"] if row.get("object_id") in apps]
    if (any(row.get("resource") not in {"CPU", "Memory", "L4"} for row in rows)
        or len({(row["object_id"], row["interval_start"], row["resource"]) for row in rows}) != len(rows)
        or any({row["resource"] for row in rows if row["object_id"] == app} != {"CPU", "Memory", "L4"} for app in apps)):
        raise ValueError("evaluation settlement billed resource inventory differs")
    modal_cost = sum(_billing_nanos(row) for row in rows)
    if modal_cost != 1_205_089_550:
        raise ValueError("evaluation settlement reviewed billing amount differs")
    model_cost = _reconcile_prior_model(document, config, audit, expected_cost=4_291_740, expected_calls=5)
    releases["modal"] += 10_192_960_000 - modal_cost - document["billing_buffer_usd_nanos"]
    releases["openrouter"] += 2_900_000_000 - model_cost
    return receipts, releases


def evaluation_admissions(amendment):
    """Return the only dollar operations permitted by the reviewed continuation."""
    from .pilot_retry import _resolve
    _, manifest = _resolve(amendment["continuation_manifest"])
    from .compute_backend import ComputeExecutionRequest
    requests = [ComputeExecutionRequest.from_document(job["replacement_request"])
        for job in manifest["jobs"] if job["replacement_request"] is not None]
    prefix = "pilot:retry-" + digest_value(amendment)[7:] + ":"
    return {prefix + "modal:base": ("modal", "overhead", 1_000_000_000),
        **{f"pilot:{amendment['run_id']}:compute:{r.request_digest[7:]}": ("modal", "pilot", 766_080_000) for r in requests}}
