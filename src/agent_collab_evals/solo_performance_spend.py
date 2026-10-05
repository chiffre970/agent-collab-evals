"""Reviewed admission and settlement for three matched GPU pairs."""

from contextlib import closing
import re
from pathlib import Path
import sqlite3

from .canonical import canonical_json_bytes, digest_bytes, digest_value, parse_json
from .compute_backend import ComputeExecutionRequest, FrozenComputeRunManifest


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


def validate_performance_settlement(document, plan_digest):
    """Replace the cancelled first pair only after independent terminal evidence.

    The two unissued requests are checked against both durable databases. All
    older reserves stay unchanged; the cancelled app retains its observed cost
    plus the same $0.10 provisional-billing buffer used by the prior settlement.
    """
    from .pilot_retry import _resolve, _resolve_path, _billing_nanos, validate_retry
    from .adapters.modal_paired_performance import profiles
    from .adapters.sqlite_execution_backend import SqliteComputeBackend
    fields = {"schema_version", "previous_amendment", "prior_plan_digest", "prior_stop",
        "prior_receipts_digest", "prior_compute_manifest", "prior_spend_database", "prior_execution_database",
        "prior_dispatch", "provider_observation", "followup_manifest", "run_id", "provider_limits_usd_nanos",
        "total_limit_usd_nanos", "billing_buffer_usd_nanos"}
    if (not isinstance(document, dict) or set(document) != fields
        or document["schema_version"] != "exploratory-solo-retry/v13"
        or document["prior_plan_digest"] != plan_digest
        or document["provider_limits_usd_nanos"] != {"modal": 20_000_000_000, "openrouter": 3_100_000_000}
        or document["total_limit_usd_nanos"] != 23_100_000_000
        or type(document["billing_buffer_usd_nanos"]) is not int or document["billing_buffer_usd_nanos"] != 100_000_000
        or not isinstance(document["run_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", document["run_id"])):
        raise ValueError("paired settlement fields or ceilings differ")
    _, previous = _resolve(document["previous_amendment"])
    if previous.get("schema_version") != "exploratory-solo-retry/v12":
        raise ValueError("paired settlement requires the reviewed V12 amendment")
    original, releases = validate_retry(previous, plan_digest)
    old_path, old_manifest = _resolve(previous["followup_manifest"])
    old_binding, old_root = old_manifest["binding"], old_path.parent
    stop_path, stop = _resolve(document["prior_stop"])
    if (stop_path.parent != old_root / "stops" or stop.get("schema_version") != "solo-paired-performance-stop/v1"
        or stop.get("status") != "stopped" or stop.get("scoreable") is not False
        or stop.get("manifest_digest") != previous["followup_manifest"]["digest"]
        or stop.get("spend_admission", {}).get("retry_amendment_digest") != digest_value(previous)):
        raise ValueError("paired settlement prior stop differs")
    receipts = stop["spend_admission"]["receipts"]
    old = {r["operation_key"]: r for r in original}
    all_receipts = {r["operation_key"]: r for r in receipts}
    expected = performance_admissions(previous)
    if (len(all_receipts) != len(receipts) or len(receipts) != len(original) + 4
        or set(all_receipts) - set(old) != set(expected)
        or any(all_receipts.get(k) != v for k, v in old.items())
        or digest_value(receipts) != document["prior_receipts_digest"]):
        raise ValueError("paired settlement must preserve every prior admission")
    requests = tuple(ComputeExecutionRequest.from_document(j["request"]) for j in old_binding["jobs"])
    for key, values in expected.items():
        r = all_receipts[key]
        expected_digest = (previous["followup_manifest"]["digest"] if values[1] == "overhead"
            else next(request.request_digest for request in requests if key.endswith(request.request_digest[7:])))
        if ((r["provider"], r["purpose"], r["maximum_usd_nanos"]) != values
            or r["plan_digest"] != plan_digest or r["request_digest"] != expected_digest):
            raise ValueError("paired settlement prior allowance differs")
    for field, filename in (("prior_compute_manifest", "compute-manifest.json"),
            ("prior_spend_database", "spend.sqlite3"), ("prior_execution_database", "executions.sqlite3")):
        if _resolve_path(document[field]) != old_root / filename:
            raise ValueError("paired settlement durable state path differs")
    authority = FrozenComputeRunManifest.load(old_root / "compute-manifest.json",
        expected_digest=old_manifest["compute_manifest_digest"])
    transport_digest, evidence_digest = profiles(old_binding)
    authority.assert_backend_profiles(SqliteComputeBackend.profile_digest_for(transport_digest, evidence_digest), transport_digest)
    if authority.requests() != requests:
        raise ValueError("paired settlement frozen requests differ")
    with closing(sqlite3.connect((old_root / "spend.sqlite3").as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        issued = [dict(row) for row in connection.execute("SELECT * FROM compute_spend_authorizations")]
    with closing(sqlite3.connect((old_root / "executions.sqlite3").as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        executions = [dict(row) for row in connection.execute("SELECT * FROM compute_executions")]
    first = requests[0]
    first_allowance = all_receipts[f"pilot:{previous['run_id']}:compute:{first.request_digest[7:]}"]
    expected_authorization = {"authorization_id": "spend-" + digest_value({
        "run_manifest_digest": authority.manifest_digest, "request_digest": first.request_digest,
        "transport_profile_digest": transport_digest})[7:39], "campaign_run_id": previous["run_id"],
        "request_digest": first.request_digest, "transport_profile_digest": transport_digest,
        "run_manifest_digest": authority.manifest_digest,
        "approval_digest": digest_value({"approval_reference": "pilot-admission:" + digest_value(first_allowance)}),
        "status": "consumed"}
    if issued != [expected_authorization] or len(executions) != 1:
        raise ValueError("paired settlement must prove only the first request was consumed or dispatched")
    dispatch_path, dispatch = _resolve(document["prior_dispatch"])
    call = dispatch.get("function_call_id")
    if (dispatch_path != old_root / "dispatch" / (first.request_digest[7:] + ".json")
        or dispatch != {"schema_version": "modal-paired-dispatch/v1", "request_digest": first.request_digest,
            "function_call_id": call, "binding_digest": digest_value(old_binding),
            "evidence_root": old_binding["jobs"][0]["spec"]["benchmark"]["evidence_root"], "git_commit": old_binding["git_commit"]}
        or not isinstance(call, str) or not re.fullmatch(r"fc-[A-Za-z0-9]+", call)):
        raise ValueError("paired settlement dispatch differs")
    row = executions[0]
    expected_execution = {"execution_id": "execution-" + first.request_digest[7:39],
        "execution_key": first.execution_key, "campaign_run_id": previous["run_id"],
        "request_digest": first.request_digest, "request_json": canonical_json_bytes(first.document).decode(),
        "candidate_digest": first.candidate_digest, "transport_profile_digest": transport_digest,
        "evidence_profile_digest": evidence_digest, "run_manifest_digest": authority.manifest_digest,
        "status": "dispatched", "external_call_id": call,
        "dispatch_evidence_digest": digest_bytes(canonical_json_bytes(dispatch))}
    if any(row.get(k) != v for k, v in expected_execution.items()):
        raise ValueError("paired settlement execution identity differs")
    _, observation = _resolve(document["provider_observation"])
    metadata = observation.get("provider_metadata", {})
    app = metadata.get("app_id")
    apps = observation.get("apps_snapshot", [])
    if (observation.get("schema_version") != "paired-performance-settlement-observation/v1"
        or observation.get("function_call_id") != call or metadata.get("function_call_id") != call
        or not isinstance(app, str) or not re.fullmatch(r"ap-[A-Za-z0-9]+", app)
        or observation.get("status_values") != [3] or observation.get("actor_containers_active") is not False
        or len([entry for entry in apps if entry.get("app_id") == app and entry.get("state") == "stopped"
            and str(entry.get("tasks")) == "0"]) != 1
        or any(entry.get("state") != "stopped" for entry in apps)
        or observation.get("billing_finality") != "current_provider_snapshot_not_final_invoice"):
        raise ValueError("paired settlement provider terminal evidence differs")
    rows = [r for r in observation["billing_snapshot"] if r.get("object_id") == app]
    if (not rows or {r.get("resource") for r in rows} != {"CPU", "Memory", "L4"}
        or len({(r["object_id"], r["interval_start"], r["resource"]) for r in rows}) != len(rows)):
        raise ValueError("paired settlement billed resource inventory differs")
    cost = sum(_billing_nanos(r) for r in rows)
    if cost != observation.get("cost_snapshot_usd_nanos"):
        raise ValueError("paired settlement reconstructed billing differs")
    _, manifest = _resolve(document["followup_manifest"])
    binding = manifest.get("binding", {})
    if (manifest.get("schema_version") != "solo-paired-performance-followup/v1"
        or manifest.get("run_id") != document["run_id"] or document["run_id"] == previous["run_id"]
        or manifest.get("scoreable") is not False or manifest.get("new_gpu_calls") != 3
        or manifest.get("agent_reruns") != 0 or manifest.get("model_calls") != 0
        or manifest.get("estimate", {}).get("per_execution_allowance_usd_nanos") != 1_137_984_000
        or manifest["estimate"].get("shared_overhead_allowance_usd_nanos") != 1_000_000_000
        or manifest["estimate"].get("function_timeout_seconds") != 3000 or len(binding.get("jobs", [])) != 3
        or binding.get("retained_inputs") != old_binding["retained_inputs"]
        or any(binding.get(k, {}).get("digest") != old_binding[k]["digest"]
            for k in ("candidate", "reference", "campaign_manifest", "performance_profile", "scoring_profile"))):
        raise ValueError("paired settlement replacement changes the experiment")
    for index, job in enumerate(binding["jobs"], 1):
        request = ComputeExecutionRequest.from_document(job["request"])
        spec, prior_spec = job["spec"], old_binding["jobs"][index - 1]["spec"]
        comparable = {k: v for k, v in spec.items() if k not in {"benchmark", "pair_digest"}}
        previous_comparable = {k: v for k, v in prior_spec.items() if k not in {"benchmark", "pair_digest"}}
        if (request.campaign_run_id != document["run_id"] or request.maximum_seconds != 3000
            or request.scope.value != "hidden" or request.candidate_digest != first.candidate_digest
            or request.candidate_manifest_digest != first.candidate_manifest_digest
            or request.evaluator_profile_digest != digest_value({"paired_spec": spec, "retained_inputs": binding["retained_inputs"]["digest"]})
            or comparable != previous_comparable
            or {k: v for k, v in spec["benchmark"].items() if k != "evidence_root"}
                != {k: v for k, v in prior_spec["benchmark"].items() if k != "evidence_root"}
            or spec["benchmark"]["evidence_root"] == prior_spec["benchmark"]["evidence_root"]
            or spec["pair_digest"] != digest_value({k: v for k, v in spec.items() if k != "pair_digest"})):
            raise ValueError("paired settlement replacement request differs")
    admitted = sum(values[2] for values in expected.values())
    retained_charge = cost + document["billing_buffer_usd_nanos"]
    if retained_charge >= admitted:
        raise ValueError("paired settlement has no unused allowance to release")
    releases["modal"] += admitted - retained_charge
    return receipts, releases
