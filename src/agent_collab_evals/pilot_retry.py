"""Explicit, one-time continuation after an aborted pilot with no model calls.

This is an operator-reviewed amendment, not automatic refund or billing logic.
The original qualification and first-attempt Modal allowances remain charged.
"""

from pathlib import Path
import re
import sqlite3
import tempfile
from decimal import Decimal

from .canonical import canonical_json_bytes, digest_bytes, digest_file, digest_value, parse_json


def validate_retry(document, plan_digest):
    if isinstance(document, dict) and document.get("schema_version") == "exploratory-solo-retry/v3":
        return _validate_public_failure_settlement(document, plan_digest)
    if isinstance(document, dict) and document.get("schema_version") == "exploratory-solo-retry/v2":
        return _validate_reference_failure_settlement(document, plan_digest)
    fields = {"schema_version", "prior_plan_digest", "prior_receipts_digest",
        "prior_audit", "prior_budget", "run_id", "provider_limits_usd_nanos",
        "total_limit_usd_nanos"}
    if (not isinstance(document, dict) or set(document) != fields
        or document["schema_version"] != "exploratory-solo-retry/v1"
        or document["prior_plan_digest"] != plan_digest
        or not isinstance(document["run_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", document["run_id"])):
        raise ValueError("retry amendment fields differ")
    # Deliberately scoped to the approved ceiling, not a general limit editor.
    if (document["provider_limits_usd_nanos"] != {"modal": 14_000_000_000, "openrouter": 3_000_000_000}
        or type(document["total_limit_usd_nanos"]) is not int
        or document["total_limit_usd_nanos"] != 17_000_000_000):
        raise ValueError("retry limits differ from the approved ceiling")
    paths = {}
    for field in ("prior_audit", "prior_budget"):
        ref = document[field]
        if not isinstance(ref, dict) or set(ref) != {"file", "digest"}:
            raise ValueError("retry evidence reference differs")
        path = Path(ref["file"])
        if not path.is_absolute() or digest_file(path) != ref["digest"]:
            raise ValueError("retry evidence digest differs")
        paths[field] = path
    audit = parse_json(paths["prior_audit"].read_text())
    receipts = audit["spend_admission"]["receipts"]
    if (audit.get("status") != "aborted" or audit.get("scoreable") is not False
        or audit["run_id"] == document["run_id"]
        or audit["budget_reconciliation"].get("valid") is not True
        or digest_value(receipts) != document["prior_receipts_digest"]
        or not audit.get("remote_cleanup")
        or any(item.get("terminal_confirmed") is not True for item in audit["remote_cleanup"])):
        raise ValueError("prior attempt is not a reconciled aborted run")
    connection = sqlite3.connect(paths["prior_budget"].resolve().as_uri() + "?mode=ro", uri=True)
    try:
        campaigns = connection.execute(
            "SELECT campaign_run_id, charged_usd_nanos FROM budget_campaigns").fetchall()
        count = connection.execute("SELECT count(*) FROM budget_reservations").fetchone()[0]
        if count != 0 or campaigns != [(audit["run_id"], 0)]:
            raise ValueError("prior model allowance is not entirely unused")
    finally:
        connection.close()
    model = [item for item in receipts
        if item["operation_key"] == "pilot:single-attempt:openrouter:base"]
    if (len(model) != 1 or model[0]["provider"] != "openrouter"
        or model[0]["maximum_usd_nanos"] != 2_900_000_000):
        raise ValueError("prior model admission differs")
    return receipts, {"modal": 0, "openrouter": model[0]["maximum_usd_nanos"]}


def _resolve(reference):
    path = _resolve_path(reference)
    return path, parse_json(path.read_text())


def _resolve_path(reference):
    if not isinstance(reference, dict) or set(reference) != {"file", "digest"}:
        raise ValueError("settlement evidence reference differs")
    path = Path(reference["file"])
    if not path.is_absolute() or digest_file(path) != reference["digest"]:
        raise ValueError("settlement evidence digest differs")
    return path


def _billing_nanos(row):
    if not isinstance(row, dict) or row.get("environment") != "dev":
        raise ValueError("settlement billing environment differs")
    amount = Decimal(row["cost"]) * 1_000_000_000
    if not amount.is_finite() or amount <= 0 or amount != amount.to_integral_value():
        raise ValueError("settlement billing amount is invalid")
    return int(amount)


def _reconcile_prior_model(document, run_config, audit):
    """Verify the frozen model plan against raw provider receipts on a copy."""
    from .adapters.provider_receipts import OpenRouterReceiptVerifier
    from .adapters.sqlite_budget import SqliteBudgetAccount
    from .budget import ActorBudgetAllocation, BudgetPlan
    from .model_gateway import ModelGatewayProfile

    database = _resolve_path(document["prior_budget_database"])
    _, raw_plan = _resolve(document["prior_budget_plan"])
    run_id = audit["run_id"]
    if (raw_plan.get("campaign_run_id") != run_id
        or raw_plan.get("status") != "development"
        or raw_plan.get("organisation_limit_usd_nanos") != 2_900_000_000
        or len(raw_plan.get("allocations", [])) != 1):
        raise ValueError("prior model budget plan differs")
    allocations = tuple(ActorBudgetAllocation(run_id, item["actor_id"], item["limit_usd_nanos"])
        for item in raw_plan["allocations"])
    plan = BudgetPlan.create(plan_id=raw_plan["plan_id"], status=raw_plan["status"],
        campaign_run_id=run_id, organisation_limit_usd_nanos=raw_plan["organisation_limit_usd_nanos"],
        allocations=allocations, rate_card_digest=raw_plan["rate_card_digest"])
    if plan.source_digest != raw_plan.get("source_digest"):
        raise ValueError("prior model budget plan digest differs")
    previous_path = _resolve_path(document["previous_amendment"])
    repository = previous_path.parents[2]
    gateway_path = (repository / run_config["config"]["gateway_profile"]).resolve(strict=True)
    gateway_path.relative_to(repository)
    gateway = ModelGatewayProfile.load(gateway_path, repository_root=repository)
    if plan.rate_card_digest != digest_value(gateway.rate_card):
        raise ValueError("prior model rate card differs")
    with tempfile.TemporaryDirectory() as temporary:
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(Path(temporary) / "ledger.sqlite3") as copy:
                source.backup(copy)
        account = SqliteBudgetAccount(Path(temporary) / "ledger.sqlite3", gateway.rate_card,
            budget_plan=plan, receipt_verifier=OpenRouterReceiptVerifier(gateway))
        reconciliation = account.reconcile(run_id)
        snapshot = account.snapshot(run_id)
    if (not reconciliation.valid or reconciliation.evidence() != audit.get("budget_reconciliation")
        or snapshot.organisation_reserved_usd_nanos != 0
        or len(snapshot.charges) != 3
        or snapshot.organisation_charged_usd_nanos != 1_351_620):
        raise ValueError("prior model receipts do not reconcile")
    return snapshot.organisation_charged_usd_nanos


def _validate_public_failure_settlement(document, plan_digest):
    """Settle only the retained September 21 public-collection failure."""
    fields = {"schema_version", "previous_amendment", "prior_plan_digest",
        "prior_receipts_digest", "prior_audit", "prior_run_config", "prior_budget_plan",
        "prior_budget_database", "recovery_output", "recovery_manifest", "recovery_receipt",
        "recovery_score", "cleanup_observation", "billing_report", "first_reference_app_id",
        "current_app_ids", "run_id", "provider_limits_usd_nanos", "total_limit_usd_nanos"}
    if not isinstance(document, dict) or set(document) != fields or document["prior_plan_digest"] != plan_digest:
        raise ValueError("public settlement fields differ")
    previous_path, previous = _resolve(document["previous_amendment"])
    if previous["schema_version"] != "exploratory-solo-retry/v2":
        raise ValueError("public settlement requires the reviewed second amendment")
    previous_receipts, releases = validate_retry(previous, plan_digest)
    audit_path, audit = _resolve(document["prior_audit"])
    _, run_config = _resolve(document["prior_run_config"])
    if (document["provider_limits_usd_nanos"] != previous["provider_limits_usd_nanos"]
        or document["total_limit_usd_nanos"] != previous["total_limit_usd_nanos"]
        or document["run_id"] in {previous["run_id"], "first-solo", "solo-retry-0921"}
        or not isinstance(document["run_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", document["run_id"])
        or audit.get("run_id") != previous["run_id"]
        or audit.get("status") != "aborted" or audit.get("scoreable") is not False
        or audit.get("failure", {}).get("stage") != "public_evaluation"
        or audit.get("run_config_digest") != document["prior_run_config"]["digest"]
        or run_config.get("git_commit") != "90746ce881d053b7c2d0ec11ee1ce0abd8a20cbd"
        or run_config.get("git_dirty") is not False
        or audit.get("budget_reconciliation", {}).get("valid") is not True
        or audit_path.parent.joinpath("hidden-result.json").exists()):
        raise ValueError("public settlement prior run differs")
    receipts = audit["spend_admission"]["receipts"]
    if (digest_value(receipts) != document["prior_receipts_digest"]
        or audit["spend_admission"].get("retry_amendment_digest") != digest_value(previous)):
        raise ValueError("public settlement admissions differ")
    by_key = {item["operation_key"]: item for item in receipts}
    prior_keys = {item["operation_key"] for item in previous_receipts}
    if any(by_key.get(item["operation_key"]) != item for item in previous_receipts):
        raise ValueError("public settlement lost earlier admissions")
    prefix = "pilot:retry-" + digest_value(previous)[7:]
    model_key, overhead_key = prefix + ":openrouter:base", prefix + ":modal:base"
    compute_keys = [key for key in by_key if key.startswith(f"pilot:{previous['run_id']}:compute:")]
    if (len(receipts) != len(by_key) or len(compute_keys) != 2
        or set(by_key) - prior_keys != {model_key, overhead_key, *compute_keys}
        or by_key[model_key]["maximum_usd_nanos"] != 2_900_000_000
        or by_key[overhead_key]["maximum_usd_nanos"] != 1_000_000_000
        or any(by_key[key]["maximum_usd_nanos"] != 766_080_000 for key in compute_keys)):
        raise ValueError("public settlement execution admissions differ")
    prior_model_cost = _reconcile_prior_model(document, run_config, audit)
    _, recovered = _resolve(document["recovery_output"])
    _, manifest = _resolve(document["recovery_manifest"])
    _, remote_receipt = _resolve(document["recovery_receipt"])
    _, score = _resolve(document["recovery_score"])
    _, cleanup = _resolve(document["cleanup_observation"])
    terminal = audit.get("remote_cleanup", [])
    if (len(terminal) != 2 or len([item for item in terminal if item.get("terminal_confirmed") is False]) != 1
        or recovered.get("output_status") != "recovered" or recovered.get("new_compute_dispatched") is not False
        or recovered.get("call_id") != next(item["function_call_id"] for item in terminal if item.get("terminal_confirmed") is False)
        or recovered.get("result", {}).get("root") != manifest.get("root")
        or recovered["result"].get("remote_receipt_digest") != manifest.get("remote_receipt_digest")
        or manifest.get("remote_receipt_digest") != digest_bytes(canonical_json_bytes(remote_receipt) + b"\n")
        or remote_receipt.get("ok") is not True
        or score.get("campaign_status") != "aborted" or score.get("diagnostic_only") is not True
        or score.get("remote_ok") is not True or score.get("all_points_valid") is not True
        or score.get("hidden_quality_evaluated") is not False or score.get("new_compute_dispatched") is not False
        or score.get("identity_errors") != [] or len(score.get("points", [])) != 9
        or cleanup.get("run_id") != previous["run_id"]
        or cleanup.get("active_modal_apps") != [] or cleanup.get("actor_containers") != []):
        raise ValueError("public settlement recovery or cleanup differs")
    _, billing = _resolve(document["billing_report"])
    first_id = document["first_reference_app_id"]
    current_ids = document["current_app_ids"]
    if (first_id != "ap-prLVu6B1U3GH3DTD9D8CzI"
        or not isinstance(current_ids, list) or len(current_ids) != 3
        or len(set(current_ids)) != 3 or previous["reference_app_id"] in current_ids
        or any(not isinstance(row, dict) or row.get("description") != "agent-collab-evals-model-serving-reference"
            for row in billing)):
        raise ValueError("public settlement billing scope differs")
    rows = {row["object_id"]: row for row in billing}
    if (len(rows) != len(billing) or set(row["object_id"] for row in billing
        if row["interval_start"] == "2026-09-21T00:00:00") != set(current_ids) | {previous["reference_app_id"]}
        or first_id not in rows or rows[first_id]["interval_start"] != "2026-09-15T00:00:00"
        or any(rows[item]["interval_start"] != "2026-09-21T00:00:00" for item in current_ids)):
        raise ValueError("public settlement billing rows differ")
    first_cost = _billing_nanos(rows[first_id])
    current_cost = sum(_billing_nanos(rows[item]) for item in current_ids)
    first_key = [key for key in by_key if key.startswith("pilot:first-solo:compute:")]
    if (len(first_key) != 1 or by_key[first_key[0]]["maximum_usd_nanos"] != 766_080_000
        or first_cost + 100_000_000 > 766_080_000
        or current_cost + 100_000_000 > 532_160_000):
        raise ValueError("public settlement retained Modal allowances are insufficient")
    releases["modal"] += (766_080_000 - first_cost - 100_000_000) + 2_000_000_000
    releases["openrouter"] += 2_900_000_000 - prior_model_cost - 10_000_000
    return receipts, releases


def _validate_reference_failure_settlement(document, plan_digest):
    """Review one terminal reference-only failure, preserving earlier reserves."""
    fields = {"schema_version", "previous_amendment", "prior_plan_digest", "prior_receipts_digest",
        "prior_audit", "prior_run_config", "billing_report", "reference_app_id", "run_id",
        "provider_limits_usd_nanos", "total_limit_usd_nanos", "billing_buffer_usd_nanos"}
    if set(document) != fields or document["prior_plan_digest"] != plan_digest:
        raise ValueError("settlement amendment fields differ")
    _, previous = _resolve(document["previous_amendment"])
    if previous["schema_version"] != "exploratory-solo-retry/v1":
        raise ValueError("settlement requires the single preceding reviewed retry")
    earlier_receipts, releases = validate_retry(previous, plan_digest)
    if (document["provider_limits_usd_nanos"] != previous["provider_limits_usd_nanos"]
        or document["total_limit_usd_nanos"] != previous["total_limit_usd_nanos"]
        or type(document["billing_buffer_usd_nanos"]) is not int
        or document["billing_buffer_usd_nanos"] != 100_000_000
        or not isinstance(document["run_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", document["run_id"])
        or document["run_id"] == previous["run_id"]):
        raise ValueError("settlement limits or new run identity differ")
    audit_path, audit = _resolve(document["prior_audit"])
    _, config = _resolve(document["prior_run_config"])
    _, billing = _resolve(document["billing_report"])
    if (audit.get("status") != "aborted" or audit.get("scoreable") is not False
        or audit.get("failure", {}).get("stage") != "reference"
        or audit.get("run_id") != previous["run_id"]
        or audit.get("cleanup_failure") is not None
        or audit.get("run_config_digest") != document["prior_run_config"]["digest"]
        or config.get("git_commit") != "c116488efd2bdd3af6abe766320781104c793420"
        or config.get("git_dirty") is not False
        or len(audit.get("remote_cleanup", [])) != 1
        or audit["remote_cleanup"][0].get("terminal_confirmed") is not True
        or any((audit_path.parent / name).exists() for name in ("budget.sqlite3", "task.json", "runtime"))):
        raise ValueError("settlement is not the retained pre-agent reference failure")
    receipts = audit["spend_admission"]["receipts"]
    if (digest_value(receipts) != document["prior_receipts_digest"]
        or audit["spend_admission"].get("retry_amendment_digest") != digest_value(previous)):
        raise ValueError("settlement prior admissions differ")
    by_key = {item["operation_key"]: item for item in receipts}
    if any(by_key.get(item["operation_key"]) != item for item in earlier_receipts):
        raise ValueError("settlement lost earlier admissions")
    prefix = "pilot:retry-" + digest_value(previous)[7:]
    model_key, overhead_key = prefix + ":openrouter:base", prefix + ":modal:base"
    compute_key = f"pilot:{previous['run_id']}:compute:" + audit["remote_cleanup"][0]["request_digest"][7:]
    earlier_keys = {item["operation_key"] for item in earlier_receipts}
    if set(by_key) - earlier_keys != {model_key, overhead_key, compute_key}:
        raise ValueError("settlement contains additional work")
    if (by_key[model_key]["maximum_usd_nanos"] != 2_900_000_000
        or by_key[overhead_key]["maximum_usd_nanos"] != 1_000_000_000
        or by_key[model_key]["provider"] != "openrouter"
        or any(by_key[key]["provider"] != "modal" for key in (overhead_key, compute_key))):
        raise ValueError("settlement reservation amounts differ")
    rows = [row for row in billing if row.get("object_id") == document["reference_app_id"]]
    if len(rows) != 1 or rows[0].get("environment") != "dev":
        raise ValueError("settlement requires the reviewed app billing row")
    cost = Decimal(rows[0]["cost"]) * 1_000_000_000
    if not cost.is_finite() or cost <= 0 or cost != cost.to_integral_value():
        raise ValueError("settlement billing amount is invalid")
    retained = int(cost) + document["billing_buffer_usd_nanos"]
    if retained >= by_key[compute_key]["maximum_usd_nanos"]:
        raise ValueError("settlement has no unused compute allowance")
    releases["openrouter"] += by_key[model_key]["maximum_usd_nanos"]
    releases["modal"] += by_key[overhead_key]["maximum_usd_nanos"] + by_key[compute_key]["maximum_usd_nanos"] - retained
    return receipts, releases
