"""Explicit, one-time continuation after an aborted pilot with no model calls.

This is an operator-reviewed amendment, not automatic refund or billing logic.
All previous Modal allowances remain charged to the cumulative envelope.
"""

from pathlib import Path
import re
import sqlite3

from .canonical import digest_file, digest_value, parse_json


def validate_retry(document, plan_digest):
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
    return receipts, model[0]["maximum_usd_nanos"]
