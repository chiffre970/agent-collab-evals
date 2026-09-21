"""Explicit, one-time continuation after an aborted pilot with no model calls.

This is an operator-reviewed amendment, not automatic refund or billing logic.
The original qualification and first-attempt Modal allowances remain charged.
"""

from pathlib import Path
import re
import sqlite3
from decimal import Decimal

from .canonical import digest_file, digest_value, parse_json


def validate_retry(document, plan_digest):
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
    if not isinstance(reference, dict) or set(reference) != {"file", "digest"}:
        raise ValueError("settlement evidence reference differs")
    path = Path(reference["file"])
    if not path.is_absolute() or digest_file(path) != reference["digest"]:
        raise ValueError("settlement evidence digest differs")
    return path, parse_json(path.read_text())


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
