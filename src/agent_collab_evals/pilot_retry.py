"""Explicit, evidence-bound continuations after aborted exploratory pilots.

This is an operator-reviewed amendment, not automatic refund or billing logic.
The original qualification and first-attempt Modal allowances remain charged.
"""

from pathlib import Path
import re
import sqlite3
import tempfile
from decimal import Decimal

from .canonical import canonical_json_bytes, digest_bytes, digest_file, digest_value, parse_json


_REFERENCE_ABORT_AUDIT_DIGEST = "sha256:1b055e27b062de5a6f07b90a5f62dc13e365864fb7748398e79532037188c109"
_REFERENCE_ABORT_SOURCE_COMMIT = "f5ea74212535a29dce73c412b7d807d834219d80"
_ENVIRONMENT_ABORT_AUDIT_DIGEST = "sha256:2b6502eca0e1d1b6b975cc2dc86724f49357c314e0a5eb10dbec32ae4a0bc79f"


def validate_retry(document, plan_digest):
    if isinstance(document, dict) and document.get("schema_version") == "exploratory-solo-retry/v11":
        from .solo_evaluation_spend import validate_evaluation_settlement
        return validate_evaluation_settlement(document, plan_digest)
    if isinstance(document, dict) and document.get("schema_version") == "exploratory-solo-retry/v10":
        return _validate_environment_recovery(document, plan_digest)
    if isinstance(document, dict) and document.get("schema_version") == "exploratory-solo-retry/v9":
        return _validate_staging_bundle_settlement(document, plan_digest)
    if isinstance(document, dict) and document.get("schema_version") == "exploratory-solo-retry/v8":
        return _validate_reference_probe_settlement(document, plan_digest)
    if isinstance(document, dict) and document.get("schema_version") == "exploratory-solo-retry/v7":
        return _validate_connected_quality_settlement(document, plan_digest)
    if isinstance(document, dict) and document.get("schema_version") == "exploratory-solo-retry/v6":
        return _validate_series_failure_settlement(document, plan_digest)
    if isinstance(document, dict) and document.get("schema_version") == "exploratory-solo-retry/v5":
        return _validate_collector_failure_settlement(document, plan_digest)
    if isinstance(document, dict) and document.get("schema_version") == "exploratory-solo-retry/v4":
        return _validate_feedback_failure_settlement(document, plan_digest)
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


def _validate_environment_recovery(document, plan_digest):
    """Settle two terminal reference aborts after full collector conformance."""
    from .adapters.local_measurements import LocalMeasurementBundleStore

    fields = {"schema_version", "previous_amendment", "prior_plan_digest",
        "prior_receipts_digest", "prior_audit", "prior_run_config", "prior_inventory",
        "prior_dispatch", "conformance", "terminal_observation", "billing_report",
        "run_id", "provider_limits_usd_nanos", "total_limit_usd_nanos",
        "billing_buffer_per_run_usd_nanos"}
    if (not isinstance(document, dict) or set(document) != fields
        or document["schema_version"] != "exploratory-solo-retry/v10"
        or document["prior_plan_digest"] != plan_digest
        or document["run_id"] != "solo-devbound-1003"
        or document["provider_limits_usd_nanos"] !=
            {"modal": 20_000_000_000, "openrouter": 3_100_000_000}
        or document["total_limit_usd_nanos"] != 23_100_000_000
        or document["billing_buffer_per_run_usd_nanos"] != 100_000_000):
        raise ValueError("environment recovery fields or limits differ")
    _, previous = _resolve(document["previous_amendment"])
    if previous.get("schema_version") != "exploratory-solo-retry/v9":
        raise ValueError("environment recovery requires the reviewed V9 amendment")
    older_receipts, releases = validate_retry(previous, plan_digest)
    _, reference_amendment = _resolve(previous["previous_amendment"])
    _, older_audit = _resolve(reference_amendment["prior_audit"])
    audit_path, audit = _resolve(document["prior_audit"])
    config_path, config = _resolve(document["prior_run_config"])
    if (audit_path.parent != config_path.parent
        or document["prior_audit"]["digest"] != _ENVIRONMENT_ABORT_AUDIT_DIGEST
        or audit.get("run_id") != previous["run_id"]
        or audit.get("status") != "aborted" or audit.get("scoreable") is not False
        or audit.get("failure") != {"stage": "reference", "type": "RuntimeError"}
        or audit.get("budget_reconciliation") is not None
        or audit.get("run_config_digest") != document["prior_run_config"]["digest"]
        or config.get("git_commit") != "316c19bf1334310daf43a6d9c825b2b00b2ec2f2"
        or config.get("git_dirty") is not False
        or any((audit_path.parent / name).exists() for name in
            ("budget.sqlite3", "budget-plan.json", "candidate-services", "reference-result.json"))):
        raise ValueError("environment recovery does not prove a pre-model abort")
    cleanup = audit.get("remote_cleanup", [])
    if (len(cleanup) != 1 or cleanup[0].get("status") != "cancellation_requested"
        or cleanup[0].get("terminal_confirmed") is not False
        or cleanup[0].get("function_call_id") != "fc-01M40PAXQ6E8RD3GEYG3TQYTSG"):
        raise ValueError("environment recovery prior cleanup differs")
    receipts = audit["spend_admission"]["receipts"]
    by_key = {item["operation_key"]: item for item in receipts}
    old_keys = {item["operation_key"] for item in older_receipts}
    prefix = f"pilot:retry-{digest_value(previous)[7:]}:"
    expected = {prefix + "openrouter:base": ("openrouter", "pilot", 2_900_000_000),
        prefix + "modal:base": ("modal", "overhead", 1_000_000_000),
        f"pilot:{previous['run_id']}:compute:{cleanup[0]['request_digest'][7:]}":
            ("modal", "pilot", 766_080_000)}
    if (len(receipts) != len(older_receipts) + 3 or len(by_key) != len(receipts)
        or set(by_key) - old_keys != set(expected)
        or any(by_key.get(item["operation_key"]) != item for item in older_receipts)
        or digest_value(receipts) != document["prior_receipts_digest"]
        or audit["spend_admission"].get("retry_amendment_digest") != digest_value(previous)):
        raise ValueError("environment recovery admissions differ")
    for key, values in expected.items():
        item = by_key[key]
        if (item["provider"], item["purpose"], item["maximum_usd_nanos"]) != values:
            raise ValueError("environment recovery allowance differs")
    inventory = _resolve_path(document["prior_inventory"])
    dispatch_path, dispatch = _resolve(document["prior_dispatch"])
    if (inventory != audit_path.parent / "evaluation/compute/inventory.sqlite3"
        or not dispatch_path.is_relative_to(inventory.parent / "routes")
        or dispatch.get("function_call_id") != cleanup[0]["function_call_id"]
        or dispatch.get("measurement_id") != "exec-" + cleanup[0]["request_digest"][7:]):
        raise ValueError("environment recovery dispatch identity differs")
    connection = sqlite3.connect(inventory.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        requests = connection.execute("SELECT request_digest FROM route_requests").fetchall()
    finally:
        connection.close()
    if requests != [(cleanup[0]["request_digest"],)]:
        raise ValueError("environment recovery inventory differs")
    _, terminal = _resolve(document["terminal_observation"])
    old_terminal = terminal.get("older_reference", {})
    latest = terminal.get("latest_reference", {})
    pointer = latest.get("pointer", {})
    if (terminal.get("schema_version") != "dev-environment-terminal-observation/v1"
        or terminal.get("new_scored_dispatches") != 0
        or terminal.get("actor_containers_active") is not False
        or not isinstance(terminal.get("apps"), list)
        or any(app.get("state") != "stopped" for app in terminal["apps"])
        or old_terminal != {"function_call_id": "fc-01M3XVBN06YC79HA1EACHJJTVA",
            "status": "terminal_cancelled", "error_type": "RemoteError",
            "message": "Function call was cancelled by user or a failure."}
        or older_audit["remote_cleanup"][0]["function_call_id"] != old_terminal["function_call_id"]
        or latest.get("function_call_id") != dispatch["function_call_id"]
        or latest.get("status") != "terminal_result"
        or pointer.get("schema_version") != "modal-evaluator-staging-bundle-pointer/v0alpha1"
        or pointer.get("root") != dispatch.get("evidence_root")
        or pointer.get("volume_name") != "agent-collab-evals-evaluator-staging-v2"):
        raise ValueError("environment recovery terminal evidence differs")
    _, conformance = _resolve(document["conformance"])
    receipt_path, receipt = _resolve(conformance.get("measurement_receipt"))
    store = LocalMeasurementBundleStore(receipt_path.parents[2])
    bundle = store.load(dispatch["measurement_id"], 1, attempt=1)
    normalized = bundle.receipt["normalized"]
    if (conformance.get("schema_version") != "dev-collector-persistence-conformance/v1"
        or conformance.get("controller_commit") != "396c98473ec53d52c3f08b52b0f5102b75e4fee2"
        or conformance.get("source_run") != audit["run_id"]
        or conformance.get("source_audit_digest") != document["prior_audit"]["digest"]
        or conformance.get("source_dispatch_digest") != document["prior_dispatch"]["digest"]
        or conformance.get("function_call_id") != dispatch["function_call_id"]
        or conformance.get("modal_environment") != "dev"
        or conformance.get("normalized_valid") is not True
        or conformance.get("raw_documents_verified") != 9
        or conformance.get("new_scored_dispatches") != 0
        or conformance.get("new_model_calls") != 0
        or conformance.get("original_audit_or_ledgers_modified") is not False
        or normalized.get("valid") is not True
        or normalized.get("modal_function_call_id") != dispatch["function_call_id"]
        or len(receipt.get("raw_digests", {})) != 9):
        raise ValueError("environment recovery full persistence evidence differs")
    _, billing = _resolve(document["billing_report"])
    if (billing.get("schema_version") != "dev-environment-billing-snapshot/v1"
        or billing.get("command") != ["billing", "report", "--start", "2026-10-02",
            "--end", "2026-10-04", "-r", "h", "--json", "--show-resources"]
        or not isinstance(billing.get("rows"), list)):
        raise ValueError("environment recovery billing snapshot differs")
    total_billed = 0
    for app, hours, count, expected_cost in (
        ("ap-81EHOHnmxdm2Z2FCD4JE7J", {"2026-10-02T08:00:00"}, 3, 86_281_100),
        ("ap-eqjg9YDDJ21f7rvfDlxNs7", {"2026-10-03T10:00:00", "2026-10-03T11:00:00"},
            6, 229_151_290),
    ):
        rows = [row for row in billing["rows"] if row.get("object_id") == app]
        if (len(rows) != count or {row.get("interval_start") for row in rows} != hours
            or len({(row.get("interval_start"), row.get("resource")) for row in rows}) != count
            or any(row.get("resource") not in {"CPU", "Memory", "L4"} for row in rows)
            or sum(_billing_nanos(row) for row in rows) != expected_cost):
            raise ValueError("environment recovery complete billed intervals differ")
        total_billed += expected_cost
    # V8 deliberately retained the earlier $1 overhead and $0.76608 dispatch.
    # Release each terminal attempt once, retaining a separate $0.10 buffer.
    releases["modal"] += (2 * 1_766_080_000 - total_billed
        - 2 * document["billing_buffer_per_run_usd_nanos"])
    releases["openrouter"] += 2_900_000_000
    return receipts, releases


def _validate_staging_bundle_settlement(document, plan_digest):
    """Settle the V8 abort only after terminal, billing, and model evidence.

    The older unresolved reference dispatch remains reserved by the V8 chain.
    This amendment does not make the aborted run scoreable.
    """
    fields = {"schema_version", "previous_amendment", "prior_plan_digest",
        "prior_receipts_digest", "prior_audit", "prior_run_config", "prior_budget_plan",
        "prior_budget_database", "prior_compute_inventory", "prior_compute_route",
        "terminal_observation", "billing_report", "staging_diagnostic",
        "staging_approval", "staging_result", "run_id", "provider_limits_usd_nanos",
        "total_limit_usd_nanos", "billing_buffer_usd_nanos", "model_buffer_usd_nanos"}
    if (not isinstance(document, dict) or set(document) != fields
        or document["schema_version"] != "exploratory-solo-retry/v9"
        or document["prior_plan_digest"] != plan_digest
        or document["run_id"] != "solo-stagingfix-1003"
        or document["provider_limits_usd_nanos"] !=
            {"modal": 20_000_000_000, "openrouter": 3_100_000_000}
        or document["total_limit_usd_nanos"] != 23_100_000_000
        or document["billing_buffer_usd_nanos"] != 100_000_000
        or document["model_buffer_usd_nanos"] != 10_000_000):
        raise ValueError("staging-bundle settlement fields or limits differ")
    _, previous = _resolve(document["previous_amendment"])
    if previous.get("schema_version") != "exploratory-solo-retry/v8":
        raise ValueError("staging-bundle settlement requires the reviewed V8 amendment")
    older_receipts, releases = validate_retry(previous, plan_digest)
    audit_path, audit = _resolve(document["prior_audit"])
    config_path, run_config = _resolve(document["prior_run_config"])
    if (audit_path.parent != config_path.parent
        or document["prior_audit"]["digest"] !=
            "sha256:3030449ace799cb20f2e9a9a00425c11aa872e28c2e4e4d63a23859fa48c2fdf"
        or audit.get("run_id") != previous["run_id"]
        or audit.get("status") != "aborted" or audit.get("scoreable") is not False
        or audit.get("failure") != {"stage": "hidden_evaluation", "type": "RuntimeError"}
        or audit.get("budget_reconciliation", {}).get("valid") is not True
        or audit.get("run_config_digest") != document["prior_run_config"]["digest"]
        or run_config.get("git_commit") != "176d3912c4d30f7b4435aa6021c46badd79bf822"
        or run_config.get("git_dirty") is not False
        or audit_path.parent.joinpath("hidden-result.json").exists()
        or audit_path.parent.joinpath("budget.sqlite3-wal").stat().st_size != 0):
        raise ValueError("prior staging-bundle attempt differs")
    cleanup = audit.get("remote_cleanup", [])
    ambiguous = [item for item in cleanup if item.get("status") == "cancellation_requested"]
    if (len(cleanup) != 12 or len(ambiguous) != 1
        or sum(item.get("status") == "terminal_evidence_verified" and
            item.get("terminal_confirmed") is True for item in cleanup) != 5
        or sum(item.get("status") == "not_dispatched" for item in cleanup) != 6
        or ambiguous[0].get("terminal_confirmed") is not False
        or ambiguous[0].get("function_call_id") !=
            "fc-01M3YQZ7V6AQA075F29G3FQP5G"):
        raise ValueError("prior remote cleanup differs")
    receipts = audit["spend_admission"]["receipts"]
    old_by_key = {item["operation_key"]: item for item in older_receipts}
    by_key = {item["operation_key"]: item for item in receipts}
    prefix = f"pilot:retry-{digest_value(previous)[7:]}:"
    expected_new = {
        prefix + "modal:base": ("modal", "overhead", 1_000_000_000),
        prefix + "openrouter:base": ("openrouter", "pilot", 2_900_000_000),
        **{f"pilot:{previous['run_id']}:compute:{item['request_digest'][7:]}":
            ("modal", "pilot", 766_080_000) for item in cleanup},
    }
    if (len(receipts) != len(older_receipts) + 14
        or len(by_key) != len(receipts)
        or any(by_key.get(key) != value for key, value in old_by_key.items())
        or set(by_key) - set(old_by_key) != set(expected_new)
        or audit["spend_admission"].get("retry_amendment_digest") != digest_value(previous)):
        raise ValueError("prior staging-bundle admissions differ")
    for key, expected in expected_new.items():
        receipt = by_key[key]
        if (receipt["provider"], receipt["purpose"], receipt["maximum_usd_nanos"]) != expected:
            raise ValueError("prior staging-bundle allowance differs")
    inventory = _resolve_path(document["prior_compute_inventory"])
    route = _resolve_path(document["prior_compute_route"])
    compute_root = audit_path.parent / "evaluation/compute"
    if (inventory != compute_root / "inventory.sqlite3"
        or not route.is_relative_to(compute_root / "routes")
        or route.name != "executions.sqlite3"):
        raise ValueError("prior compute evidence path differs")
    with sqlite3.connect(inventory.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        campaigns = connection.execute("SELECT campaign_run_id FROM route_inventory").fetchall()
        requests = {row[0]: row[1] for row in connection.execute(
            "SELECT request_digest, route_id FROM route_requests")}
    if (campaigns != [(previous["run_id"],)]
        or set(requests) != {item["request_digest"] for item in cleanup}
        or route.parent.name != requests[ambiguous[0]["request_digest"]]):
        raise ValueError("prior compute inventory differs")
    with sqlite3.connect(route.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        dispatch = connection.execute(
            "SELECT status, external_call_id, dispatch_evidence_digest "
            "FROM compute_executions WHERE request_digest = ?",
            (ambiguous[0]["request_digest"],)).fetchone()
    if (dispatch is None or dispatch[0] != "dispatched"
        or dispatch[1] != ambiguous[0]["function_call_id"]
        or not isinstance(dispatch[2], str) or not dispatch[2].startswith("sha256:")):
        raise ValueError("prior ambiguous dispatch identity differs")
    _, terminal = _resolve(document["terminal_observation"])
    pointer = terminal.get("pointer", {})
    if (terminal.get("schema_version") != "statusprobe-terminal-observation/v1"
        or terminal.get("function_call_id") != dispatch[1]
        or terminal.get("new_scored_dispatches") != 0
        or terminal.get("pointer_digest") != digest_value(pointer)
        or pointer.get("schema_version") != "modal-evaluator-staging-pointer/v0alpha1"
        or pointer.get("volume_name") != "agent-collab-evals-evaluator-staging-v2"
        or not isinstance(pointer.get("root"), str)
        or not pointer["root"].startswith("model-serving-quality/")
        or not isinstance(pointer.get("remote_receipt_digest"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", pointer["remote_receipt_digest"])):
        raise ValueError("later terminal Modal observation differs")
    _, billing = _resolve(document["billing_report"])
    if (billing.get("schema_version") != "statusprobe-modal-billing-snapshot/v1"
        or billing.get("command") != ["billing", "report", "--start", "2026-10-02",
            "--end", "2026-10-04", "-r", "h", "--json", "--show-resources"]
        or not isinstance(billing.get("rows"), list)):
        raise ValueError("prior Modal billing snapshot differs")
    rows = [row for row in billing["rows"] if row.get("interval_start") in
        {"2026-10-02T15:00:00", "2026-10-02T16:00:00"}]
    app_ids = {"ap-05VPFb2F1SBtNkqDTjJFUF", "ap-EL5e2oLrLo2hNkoSPpkQBi",
        "ap-FNeMo9MkDlPF77D4xuTcJ2", "ap-IS497w4DK6ckCzm8k9pnAr",
        "ap-RNAZpbjJEqgZPPdxVPUJWR", "ap-SHWpUo3xAR8SAMA2BqcU9V",
        "ap-USJBmygF5dsUdzQxdAqEgt", "ap-UzTUQwjUwNIWVyqGFx3FEJ",
        "ap-WhTQwGGGhL2Cmwfci32u38", "ap-YTiSJoUpeZzHo9H8DpYOdC",
        "ap-aXRv3HOeUekDEQyvuDTGcc", "ap-mR1XqV6fraaU6BmXZx5qs1",
        "ap-uUOA4rz8W2YhKJXyidzxf1"}
    if (len(rows) != 35 or {row.get("object_id") for row in rows} != app_ids
        or any(row.get("resource") not in {"L4", "CPU", "Memory"} for row in rows)
        or any(row.get("object_id") in app_ids for row in billing["rows"] if row not in rows)):
        raise ValueError("prior Modal billed application set differs")
    billed = sum(_billing_nanos(row) for row in rows)
    if billed != 993_867_050:
        raise ValueError("prior Modal billed amount differs")
    _, diagnostic = _resolve(document["staging_diagnostic"])
    _, approval = _resolve(document["staging_approval"])
    _, result = _resolve(document["staging_result"])
    if (diagnostic.get("schema_version") != "cpu-staging-bundle-diagnostic/v1"
        or diagnostic.get("source_commit") != "cc1832319b0ea0b1b4d81e88c9c4e21e14f49f38"
        or diagnostic.get("approval_digest") != document["staging_approval"]["digest"]
        or diagnostic.get("result_digest") != document["staging_result"]["digest"]
        or diagnostic.get("staging_pointer_schema") !=
            "modal-evaluator-staging-bundle-pointer/v0alpha1"
        or diagnostic.get("raw_documents_verified") != 1
        or diagnostic.get("new_scored_dispatches") != 0
        or diagnostic.get("model_calls") != 0
        or diagnostic.get("maximum_modal_usd_nanos") != 100_000_000
        or approval.get("schema_version") != "cpu-staging-bundle-diagnostic-approval/v1"
        or approval.get("operator_approved_modal_usd_nanos") != 100_000_000
        or result.get("schema_version") != "modal-staging-conformance/v0alpha1"
        or result.get("ok") is not True):
        raise ValueError("CPU-only staging diagnostic differs")
    diagnostic_receipt = dict(operation_key=prefix + "staging-bundle-1003",
        provider="modal", purpose="qualification", request_digest=digest_value(approval),
        maximum_usd_nanos=100_000_000, plan_digest=plan_digest)
    if diagnostic["admission_digest"] != digest_value(diagnostic_receipt):
        raise ValueError("CPU diagnostic admission differs")
    complete_receipts = sorted((*receipts, diagnostic_receipt),
        key=lambda item: digest_value(item["operation_key"])[7:])
    if digest_value(complete_receipts) != document["prior_receipts_digest"]:
        raise ValueError("staging-bundle prior receipts differ")
    model_cost = _reconcile_prior_model(document, run_config, audit,
        expected_cost=3_114_720, expected_calls=5)
    if (billed + document["billing_buffer_usd_nanos"] >= 10_192_960_000
        or model_cost + document["model_buffer_usd_nanos"] >= 2_900_000_000):
        raise ValueError("staging-bundle release bounds differ")
    releases["modal"] += 10_192_960_000 - billed - document["billing_buffer_usd_nanos"]
    releases["openrouter"] += (2_900_000_000 - model_cost
        - document["model_buffer_usd_nanos"])
    return complete_receipts, releases


def _validate_reference_probe_settlement(document, plan_digest):
    """Release only unused model allowance after the October 2 reference abort.

    Both unresolved GPU allowances and all Modal overhead remain reserved.
    The approved cap increase is an admission ceiling, not a billing claim.
    """
    fields = {"schema_version", "previous_amendment", "prior_plan_digest",
        "prior_receipts_digest", "prior_audit", "prior_run_config",
        "diagnostic_result", "run_id", "provider_limits_usd_nanos",
        "total_limit_usd_nanos", "retained_modal_release_usd_nanos"}
    if (not isinstance(document, dict) or set(document) != fields
        or document["prior_plan_digest"] != plan_digest
        or document["schema_version"] != "exploratory-solo-retry/v8"
        or document["run_id"] != "solo-statusprobe-1003"
        or document["provider_limits_usd_nanos"] !=
            {"modal": 20_000_000_000, "openrouter": 3_100_000_000}
        or document["total_limit_usd_nanos"] != 23_100_000_000
        or document["retained_modal_release_usd_nanos"] != 0):
        raise ValueError("reference-probe settlement fields or limits differ")
    _, previous = _resolve(document["previous_amendment"])
    if previous.get("schema_version") != "exploratory-solo-retry/v7":
        raise ValueError("reference-probe settlement requires the reviewed V7 amendment")
    older_receipts, releases = validate_retry(previous, plan_digest)
    audit_path, audit = _resolve(document["prior_audit"])
    config_path, run_config = _resolve(document["prior_run_config"])
    if (audit_path.parent != config_path.parent
        or document["prior_audit"]["digest"] != _REFERENCE_ABORT_AUDIT_DIGEST
        or audit.get("run_id") != previous["run_id"]
        or audit.get("status") != "aborted" or audit.get("scoreable") is not False
        or audit.get("failure") != {"stage": "reference", "type": "RuntimeError"}
        or audit.get("budget_reconciliation") is not None
        or audit.get("run_config_digest") != document["prior_run_config"]["digest"]
        or run_config.get("git_commit") != _REFERENCE_ABORT_SOURCE_COMMIT
        or run_config.get("git_dirty") is not False
        or audit_path.parent.joinpath("reference-result.json").exists()
        or audit_path.parent.joinpath("budget.sqlite3").exists()
        or audit_path.parent.joinpath("budget-plan.json").exists()
        or audit_path.parent.joinpath("candidate-services").exists()):
        raise ValueError("prior attempt does not prove a pre-model reference abort")
    cleanup = audit.get("remote_cleanup")
    if (not isinstance(cleanup, list) or len(cleanup) != 1
        or cleanup[0].get("function_call_id") != "fc-01M3XVBN06YC79HA1EACHJJTVA"
        or cleanup[0].get("terminal_confirmed") is not False
        or cleanup[0].get("status") != "cancellation_requested"):
        raise ValueError("the unresolved Modal call must remain fully reserved")
    receipts = audit["spend_admission"]["receipts"]
    old_by_key = {item["operation_key"]: item for item in older_receipts}
    by_key = {item["operation_key"]: item for item in receipts}
    prefix = f"pilot:retry-{digest_value(previous)[7:]}:"
    expected_new = {
        prefix + "openrouter:base": ("openrouter", "pilot", 2_900_000_000),
        prefix + "modal:base": ("modal", "overhead", 1_000_000_000),
        "pilot:solo-connected-1002:compute:d0b948032089035fcab6d071c3205c593186c7d407aecc81757750a8a70c4316":
            ("modal", "pilot", 766_080_000),
    }
    if (len(receipts) != len(older_receipts) + len(expected_new)
        or len(by_key) != len(receipts)
        or any(by_key.get(key) != value for key, value in old_by_key.items())
        or set(by_key) - set(old_by_key) != set(expected_new)
        or audit["spend_admission"].get("retry_amendment_digest") != digest_value(previous)):
        raise ValueError("prior reference-run admissions differ")
    for key, (provider, purpose, amount) in expected_new.items():
        item = by_key[key]
        if (item["provider"], item["purpose"], item["maximum_usd_nanos"]) != (provider, purpose, amount):
            raise ValueError("prior reference-run allowance differs")
    _, diagnostic = _resolve(document["diagnostic_result"])
    diagnostic_receipt = diagnostic.get("admission")
    diagnostic_key = prefix + "modal:cpu-pending-probe-v1"
    if (diagnostic.get("schema_version") != "modal-pending-probe-result/v1"
        or diagnostic.get("operation_key") != diagnostic_key
        or diagnostic.get("pending_before_completion") is not True
        or diagnostic.get("terminal_after_completion") is not False
        or diagnostic.get("sentinel_verified") is not True
        or diagnostic.get("error_type") is not None
        or not isinstance(diagnostic_receipt, dict)
        or diagnostic_receipt.get("operation_key") != diagnostic_key
        or diagnostic_receipt.get("provider") != "modal"
        or diagnostic_receipt.get("purpose") != "qualification"
        or diagnostic_receipt.get("maximum_usd_nanos") != 100_000_000):
        raise ValueError("CPU-only status-probe admission differs")
    complete_receipts = sorted((*receipts, diagnostic_receipt),
        key=lambda item: digest_value(item["operation_key"])[7:])
    if digest_value(complete_receipts) != document["prior_receipts_digest"]:
        raise ValueError("reference-probe prior receipts differ")
    releases["openrouter"] += 2_900_000_000
    return complete_receipts, releases


def _reconcile_prior_model(document, run_config, audit, *, expected_cost=1_351_620,
                           expected_calls=3):
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
        from contextlib import closing
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(Path(temporary) / "ledger.sqlite3")) as copy:
                source.backup(copy)
        account = SqliteBudgetAccount(Path(temporary) / "ledger.sqlite3", gateway.rate_card,
            budget_plan=plan, receipt_verifier=OpenRouterReceiptVerifier(gateway))
        reconciliation = account.reconcile(run_id)
        snapshot = account.snapshot(run_id)
    if (not reconciliation.valid or reconciliation.evidence() != audit.get("budget_reconciliation")
        or snapshot.organisation_reserved_usd_nanos != 0
        or len(snapshot.charges) != expected_calls
        or snapshot.organisation_charged_usd_nanos != expected_cost):
        raise ValueError("prior model receipts do not reconcile")
    return snapshot.organisation_charged_usd_nanos


def _validate_connected_quality_settlement(document, plan_digest):
    """Settle one aborted quality series while retaining earlier ambiguity."""
    from .adapters.local_measurements import LocalMeasurementBundleStore

    fields = {"schema_version", "previous_amendment", "prior_plan_digest",
        "prior_receipts_digest", "prior_audit", "prior_run_config", "prior_budget_plan",
        "prior_budget_database", "prior_compute_inventory", "model_reconciliation",
        "billing_report", "conformance_receipt", "conformance_bundle_receipt",
        "cleanup_observation", "run_id", "provider_limits_usd_nanos",
        "total_limit_usd_nanos", "billing_buffer_usd_nanos",
        "model_buffer_usd_nanos", "older_unresolved_reserve_usd_nanos"}
    if (not isinstance(document, dict) or set(document) != fields
        or document["prior_plan_digest"] != plan_digest):
        raise ValueError("connected settlement fields differ")
    _, previous = _resolve(document["previous_amendment"])
    if previous.get("schema_version") != "exploratory-solo-retry/v6":
        raise ValueError("connected settlement requires the reviewed sixth amendment")
    previous_receipts, releases = validate_retry(previous, plan_digest)
    audit_path, audit = _resolve(document["prior_audit"])
    _, run_config = _resolve(document["prior_run_config"])
    if (document["provider_limits_usd_nanos"] != {"modal": 17_000_000_000,
            "openrouter": 3_100_000_000}
        or document["total_limit_usd_nanos"] != 20_100_000_000
        or document["run_id"] != "solo-connected-1002"
        or audit.get("run_id") != previous["run_id"]
        or audit.get("status") != "aborted" or audit.get("scoreable") is not False
        or audit.get("failure") != {"stage": "hidden_evaluation", "type": "RuntimeError"}
        or document["prior_audit"]["digest"] !=
            "sha256:a0f7dbdcd1b7d55c2a215e32ed91fadc4f04e1a012812d0a650928553cc820a1"
        or audit.get("run_config_digest") != document["prior_run_config"]["digest"]
        or run_config.get("git_commit") != "9711db8e6532a645063ff2dcf55189a0dcb1f3e8"
        or run_config.get("git_dirty") is not False
        or audit_path.parent.joinpath("hidden-result.json").exists()
        or document["billing_buffer_usd_nanos"] != 100_000_000
        or document["model_buffer_usd_nanos"] != 10_000_000
        or document["older_unresolved_reserve_usd_nanos"] != 766_080_000):
        raise ValueError("connected settlement prior run or limits differ")
    receipts = audit["spend_admission"]["receipts"]
    by_key = {item["operation_key"]: item for item in receipts}
    if (len(receipts) != 47 or len(by_key) != len(receipts)
        or any(by_key.get(item["operation_key"]) != item for item in previous_receipts)
        or digest_value(receipts) != document["prior_receipts_digest"]
        or audit["spend_admission"].get("retry_amendment_digest") != digest_value(previous)):
        raise ValueError("connected settlement admissions differ")
    cleanup = audit.get("remote_cleanup", [])
    if (len(cleanup) != 12
        or sum(item.get("status") == "terminal_evidence_verified" and
            item.get("terminal_confirmed") is True for item in cleanup) != 6
        or sum(item.get("status") == "cancellation_requested" and
            item.get("terminal_confirmed") is False for item in cleanup) != 1
        or sum(item.get("status") == "not_dispatched" for item in cleanup) != 5):
        raise ValueError("connected settlement cleanup differs")
    inventory_path = _resolve_path(document["prior_compute_inventory"])
    if not inventory_path.is_relative_to(audit_path.parent):
        raise ValueError("connected settlement compute inventory path differs")
    with sqlite3.connect(inventory_path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        inventory = connection.execute("SELECT campaign_run_id FROM route_inventory").fetchall()
        requests = {row[0] for row in connection.execute("SELECT request_digest FROM route_requests")}
    if inventory != [(previous["run_id"],)] or requests != {item["request_digest"] for item in cleanup}:
        raise ValueError("connected settlement compute inventory differs")
    model_cost = _reconcile_prior_model(document, run_config, audit,
        expected_cost=4_800_840, expected_calls=5)
    _, model = _resolve(document["model_reconciliation"])
    if (model.get("run_id") != previous["run_id"]
        or model.get("audit_digest") != document["prior_audit"]["digest"]
        or model.get("plan_and_raw_provider_receipts_verified") is not True
        or model.get("new_provider_calls") is not False
        or model.get("settled_calls") != 5 or model.get("charged_usd_nanos") != model_cost
        or model.get("reserved_usd_nanos") != 0
        or model.get("reconciliation") != audit["budget_reconciliation"]):
        raise ValueError("connected settlement model evidence differs")
    _, observed = _resolve(document["cleanup_observation"])
    if (observed.get("run_name") != previous["run_id"]
        or observed.get("audit_status") != "aborted"
        or observed.get("modal_apps_active") is not False
        or observed.get("actor_containers_active") is not False
        or observed.get("original_audit_or_ledgers_modified") is not False):
        raise ValueError("connected settlement cleanup observation differs")
    _, report = _resolve(document["billing_report"])
    if (not isinstance(report, dict) or set(report) !=
        {"schema_version", "retrieved_at", "periods"}
        or report["schema_version"] != "modal-billing-settlement-snapshot/v1"
        or not report["retrieved_at"].startswith("2026-10-01T")
        or not isinstance(report["periods"], list) or len(report["periods"]) != 2):
        raise ValueError("connected settlement billing snapshot differs")
    periods = report["periods"]
    if ([(item.get("start"), item.get("end")) for item in periods] !=
            [("2026-09-25", "2026-10-01"), ("2026-10-01", "2026-10-03")]
        or any(set(item) != {"start", "end", "rows"} for item in periods)):
        raise ValueError("connected settlement billing periods differ")
    september, october = periods[0]["rows"], periods[1]["rows"]
    september_ids = {"ap-0LmHGBgSLm5C4gzwgtbawu", "ap-8U5frjmeCOkwiNfucoDOI2",
        "ap-DSVT8kRSzdSVkPrG3fv7Fw", "ap-TS6o0DLpAMZIvodOA45Jzd",
        "ap-VsRxyNZt1YQLsrj3Ilf6Zi", "ap-ZAWd23iRXKmAexFALOCI0X",
        "ap-a5aMzFWP7ojx7JMRQMP3xE", "ap-jeXSlFSqJa5JYKdtBie1it",
        "ap-mIOYDgatrRcNchhaj6jFDF", "ap-moyJxR7ZdYzDYN3JBCYBoQ",
        "ap-rLyfcaHTKjOEB9EA6pPtkF", "ap-tJVLvSIdhT4DWSvOL1GFcS"}
    if (len(september) != 12 or len(october) != 1
        or {row.get("object_id") for row in september} != september_ids
        or october[0].get("object_id") != "ap-qUzNZ83MHjgkuoN0mDgXlq"
        or any(row.get("interval_start") != "2026-09-25T00:00:00" for row in september)
        or october[0].get("interval_start") != "2026-10-01T00:00:00"):
        raise ValueError("connected settlement billed application set differs")
    source_modal = sum(_billing_nanos(row) for row in september)
    conformance_modal = _billing_nanos(october[0])
    if source_modal != 1_166_175_520 or conformance_modal != 47_600:
        raise ValueError("connected settlement billing amount differs")
    _, conformance = _resolve(document["conformance_receipt"])
    bundle_path, bundle_receipt = _resolve(document["conformance_bundle_receipt"])
    if (conformance.get("schema_version") != "cpu-collector-conformance/v1"
        or conformance.get("source_run") != previous["run_id"]
        or conformance.get("source_audit_sha256") != document["prior_audit"]["digest"]
        or conformance.get("source_audit_status") != "aborted"
        or conformance.get("source_scoreable") is not False
        or conformance.get("diagnostic_only") is not True
        or conformance.get("collection_mode") != "connected_collect_only"
        or conformance.get("new_scored_dispatches") != 0
        or conformance.get("openrouter_calls") != 0
        or conformance.get("validated") is not True
        or conformance.get("raw_documents_verified") != 64
        or conformance.get("private_measurement_receipt_sha256") != document["conformance_bundle_receipt"]["digest"]
        or conformance.get("modal_app_id") != october[0]["object_id"]
        or conformance.get("modal_app_status") != "stopped"
        or conformance.get("modal_billing_snapshot_usd") != october[0]["cost"]
        or conformance.get("modal_billing_snapshot_resources") != ["CPU", "Memory"]
        or bundle_receipt.get("measurement_id") !=
            "exec-306042480f794b37c4a132e98b18c98a2798133385fb737999624393c139b701"
        or bundle_receipt.get("repetition") != 2 or bundle_receipt.get("attempt") != 1
        or len(bundle_receipt.get("raw_digests", {})) != 64):
        raise ValueError("connected settlement conformance evidence differs")
    store = LocalMeasurementBundleStore(bundle_path.parents[2])
    store.load(bundle_receipt["measurement_id"], 2, attempt=1)
    source_allowance = 10_192_960_000
    if (source_modal + conformance_modal + document["billing_buffer_usd_nanos"] >= source_allowance
        or model_cost + document["model_buffer_usd_nanos"] >= 2_900_000_000):
        raise ValueError("connected settlement release bounds differ")
    # The earlier ambiguous dispatch remains held by the validated V6 chain.
    releases["modal"] += (source_allowance - source_modal - conformance_modal
        - document["billing_buffer_usd_nanos"])
    releases["openrouter"] += 2_900_000_000 - model_cost - document["model_buffer_usd_nanos"]
    return receipts, releases


def _validate_series_failure_settlement(document, plan_digest):
    """Review the September 24 abort; retain its ambiguous dispatch in full."""
    fields = {"schema_version", "previous_amendment", "prior_plan_digest",
        "prior_receipts_digest", "prior_audit", "prior_run_config", "prior_budget_plan",
        "prior_budget_database", "prior_compute_inventory", "prior_ambiguous_execution_database",
        "cleanup_observation", "billing_report", "run_id", "provider_limits_usd_nanos",
        "total_limit_usd_nanos", "billing_buffer_usd_nanos", "model_buffer_usd_nanos",
        "unresolved_reserve_usd_nanos"}
    if not isinstance(document, dict) or set(document) != fields or document["prior_plan_digest"] != plan_digest:
        raise ValueError("series settlement fields differ")
    _, previous = _resolve(document["previous_amendment"])
    if previous.get("schema_version") != "exploratory-solo-retry/v5":
        raise ValueError("series settlement requires the reviewed fifth amendment")
    previous_receipts, releases = validate_retry(previous, plan_digest)
    audit_path, audit = _resolve(document["prior_audit"])
    _, run_config = _resolve(document["prior_run_config"])
    if (document["provider_limits_usd_nanos"] != {"modal": 16_000_000_000, "openrouter": 3_000_000_000}
        or document["total_limit_usd_nanos"] != 19_000_000_000
        or document["run_id"] != "solo-seriesfix-0925"
        or audit.get("run_id") != previous["run_id"]
        or audit.get("status") != "aborted" or audit.get("scoreable") is not False
        or audit.get("failure") != {"stage": "hidden_evaluation", "type": "RuntimeError"}
        or audit.get("run_config_digest") != document["prior_run_config"]["digest"]
        or run_config.get("git_commit") != "d3fe34504dd805cd87e669dcad86ef1f8f3e6988"
        or run_config.get("git_dirty") is not False
        or audit_path.parent.joinpath("hidden-result.json").exists()
        or document["billing_buffer_usd_nanos"] != 100_000_000
        or document["model_buffer_usd_nanos"] != 10_000_000
        or document["unresolved_reserve_usd_nanos"] != 766_080_000):
        raise ValueError("series settlement prior run or limits differ")
    receipts = audit["spend_admission"]["receipts"]
    if (digest_value(receipts) != document["prior_receipts_digest"]
        or audit["spend_admission"].get("retry_amendment_digest") != digest_value(previous)):
        raise ValueError("series settlement admissions differ")
    by_key = {item["operation_key"]: item for item in receipts}
    prior_keys = {item["operation_key"] for item in previous_receipts}
    prefix = "pilot:retry-" + digest_value(previous)[7:]
    model_key, overhead_key = prefix + ":openrouter:base", prefix + ":modal:base"
    cleanup = audit.get("remote_cleanup", [])
    if (len(cleanup) != 12 or len(by_key) != len(receipts)
        or len({item.get("request_digest") for item in cleanup}) != 12
        or any(by_key.get(item["operation_key"]) != item for item in previous_receipts)):
        raise ValueError("series settlement prior receipts or request inventory differ")
    compute_keys = {f"pilot:{previous['run_id']}:compute:{item['request_digest'][7:]}" for item in cleanup}
    if (set(by_key) - prior_keys != {model_key, overhead_key, *compute_keys}
        or by_key[model_key]["provider"] != "openrouter"
        or by_key[model_key]["maximum_usd_nanos"] != 2_900_000_000
        or by_key[overhead_key]["provider"] != "modal"
        or by_key[overhead_key]["maximum_usd_nanos"] != 1_000_000_000
        or any(by_key[key]["provider"] != "modal"
            or by_key[key]["maximum_usd_nanos"] != 766_080_000 for key in compute_keys)):
        raise ValueError("series settlement execution allowances differ")
    ambiguous = [item for item in cleanup if item.get("status") == "unresolved_dispatch"]
    if (len(ambiguous) != 1 or ambiguous[0].get("terminal_confirmed") is not False
        or ambiguous[0].get("request_digest") !=
            "sha256:514f79cf23eba7f06ffcff0e61fb8a555ff692f986b2ce7673c9439a991afc85"
        or sum(item.get("status") == "terminal_evidence_verified" and
            item.get("terminal_confirmed") is True for item in cleanup) != 5
        or sum(item.get("status") == "not_dispatched" for item in cleanup) != 6):
        raise ValueError("series settlement cleanup is not the reviewed partial run")
    inventory_path = _resolve_path(document["prior_compute_inventory"])
    ambiguous_path = _resolve_path(document["prior_ambiguous_execution_database"])
    if (not inventory_path.is_relative_to(audit_path.parent)
        or not ambiguous_path.is_relative_to(inventory_path.parent)
        or ambiguous_path.parent.name != "8920b1289957a5c233c8a50ec4af61e43e2b094abcd041bd4dad42d98fcdc427"):
        raise ValueError("series settlement compute evidence path differs")
    with sqlite3.connect(inventory_path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        inventory = connection.execute("SELECT campaign_run_id, seal_digest FROM route_inventory").fetchall()
        requests = {row[0] for row in connection.execute("SELECT request_digest FROM route_requests")}
    with sqlite3.connect(ambiguous_path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        execution = connection.execute(
            "SELECT status, request_digest, external_call_id, evidence_locator, evidence_digest "
            "FROM compute_executions WHERE request_digest = ?",
            (ambiguous[0]["request_digest"],)).fetchall()
    if (inventory != [(previous["run_id"],
            "sha256:25b2f26a25b359ee91257423bad232e979dcf3f5181106ddac0390da1b720e74")]
        or requests != {item["request_digest"] for item in cleanup}
        or execution != [("ambiguous", ambiguous[0]["request_digest"], None, None, None)]):
        raise ValueError("series settlement ambiguous dispatch is not retained")
    model_cost = _reconcile_prior_model(document, run_config, audit,
        expected_cost=5_109_960, expected_calls=7)
    _, observed = _resolve(document["cleanup_observation"])
    if (observed.get("run_id") != previous["run_id"]
        or observed.get("active_modal_apps") != []
        or observed.get("actor_containers") != []):
        raise ValueError("series settlement cleanup differs")
    _, report = _resolve(document["billing_report"])
    _, prior_report = _resolve(previous["billing_report"])
    if (not isinstance(report, dict) or set(report) !=
        {"schema_version", "retrieved_at", "start", "end", "rows"}
        or report["schema_version"] != "modal-billing-snapshot/v1"
        or report["start"] != "2026-09-01" or report["end"] != "2026-09-26"
        or not report["retrieved_at"].startswith("2026-09-25T")
        or not isinstance(report["rows"], list)):
        raise ValueError("series settlement billing snapshot differs")
    rows = {row["object_id"]: row for row in report["rows"]}
    prior_rows = {row["object_id"]: row for row in prior_report["rows"]}
    current_ids = {"ap-KyfqkzSe1eSDQGx2j2ICWY", "ap-OOlvLSiTlPEMd21mH9niVN",
        "ap-OwZzXZg94XUClFKzbyUZaa", "ap-Qzb74IEDSntm5VmFoWBywa",
        "ap-XrxaJ5u98D8TnDJllXjbxc", "ap-aFfl1ReHPiLMFm6Rywlrq8",
        "ap-aUEWUwkbQV6CPxxTQf9P2v", "ap-aoTiuhsgJmUoS6ZIKK48FJ",
        "ap-daothuvl1Znn0JeeRffjga", "ap-tgiJJqWknZlIGmadWnkj65",
        "ap-wxUsqWw4yzVpj4E89bEfNW"}
    if (len(rows) != len(report["rows"])
        or any(rows.get(key) != value for key, value in prior_rows.items())
        or set(rows) != set(prior_rows) | current_ids
        or {key for key, row in rows.items() if row["interval_start"] == "2026-09-24T00:00:00"}
            != current_ids | {"ap-WVjBgH1M2Js1XxxAtHTuB7"}
        or any(row["interval_start"] == "2026-09-25T00:00:00" for row in rows.values())):
        raise ValueError("series settlement billing scope differs")
    current_cost = sum(_billing_nanos(rows[key]) for key in current_ids)
    if (current_cost != 887_086_540
        or current_cost + document["billing_buffer_usd_nanos"]
            + document["unresolved_reserve_usd_nanos"] >= 10_192_960_000
        or model_cost + document["model_buffer_usd_nanos"] >= 2_900_000_000):
        raise ValueError("series settlement release bounds differ")
    releases["modal"] += (10_192_960_000 - current_cost
        - document["billing_buffer_usd_nanos"] - document["unresolved_reserve_usd_nanos"])
    releases["openrouter"] += 2_900_000_000 - model_cost - document["model_buffer_usd_nanos"]
    return receipts, releases


def _validate_collector_failure_settlement(document, plan_digest):
    """Settle one aborted reference collection without treating it as a score."""
    from .adapters.local_measurements import LocalMeasurementBundleStore

    fields = {"schema_version", "previous_amendment", "prior_plan_digest",
        "prior_receipts_digest", "prior_audit", "prior_run_config",
        "prior_compute_database", "prior_dispatch_record", "call_pointer",
        "staged_manifest", "staged_remote_receipt", "conformance_receipt",
        "cleanup_observation", "billing_report", "reference_app_id",
        "conformance_app_id", "run_id", "provider_limits_usd_nanos",
        "total_limit_usd_nanos", "billing_buffer_usd_nanos",
        "conformance_buffer_usd_nanos", "model_buffer_usd_nanos",
        "qualification_release_usd_nanos"}
    if (not isinstance(document, dict) or set(document) != fields
        or document["prior_plan_digest"] != plan_digest):
        raise ValueError("collector settlement fields differ")
    _, previous = _resolve(document["previous_amendment"])
    if previous.get("schema_version") != "exploratory-solo-retry/v4":
        raise ValueError("collector settlement requires the reviewed fourth amendment")
    prior_receipts, releases = validate_retry(previous, plan_digest)
    audit_path, audit = _resolve(document["prior_audit"])
    _, run_config = _resolve(document["prior_run_config"])
    if (document["provider_limits_usd_nanos"] != previous["provider_limits_usd_nanos"]
        or document["total_limit_usd_nanos"] != previous["total_limit_usd_nanos"]
        or not isinstance(document["run_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", document["run_id"])
        or document["run_id"] in {previous["run_id"], "solo-next-0923", "solo-final-0921"}
        or audit.get("run_id") != previous["run_id"]
        or audit.get("status") != "aborted" or audit.get("scoreable") is not False
        or audit.get("failure") != {"stage": "reference", "type": "RuntimeError"}
        or audit.get("run_config_digest") != document["prior_run_config"]["digest"]
        or run_config.get("git_commit") != "9312b743d256dfbae87fd4e117e68bca31abb75c"
        or run_config.get("git_dirty") is not False
        or run_config.get("config", {}).get("sandbox_profile") !=
            "config/enforcement_profiles/oci-opencode-podman-development-v2.json"
        or audit_path.parent.joinpath("task.json").exists()
        or audit_path.parent.joinpath("budget.sqlite3").exists()
        or audit_path.parent.joinpath("hidden-result.json").exists()):
        raise ValueError("collector settlement prior run differs")
    terminal = audit.get("remote_cleanup", [])
    if (len(terminal) != 1 or terminal[0].get("status") != "cancellation_requested"
        or terminal[0].get("terminal_confirmed") is not False
        or terminal[0].get("function_call_id") != "fc-01M36RFHNVSH1NPN1NR2YSMBW4"):
        raise ValueError("collector settlement prior dispatch differs")
    receipts = audit["spend_admission"]["receipts"]
    if (digest_value(receipts) != document["prior_receipts_digest"]
        or audit["spend_admission"].get("retry_amendment_digest") != digest_value(previous)):
        raise ValueError("collector settlement admissions differ")
    by_key = {item["operation_key"]: item for item in receipts}
    prior_keys = {item["operation_key"] for item in prior_receipts}
    prefix = "pilot:retry-" + digest_value(previous)[7:]
    model_key, overhead_key = prefix + ":openrouter:base", prefix + ":modal:base"
    request_digest = terminal[0].get("request_digest")
    if not isinstance(request_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", request_digest):
        raise ValueError("collector settlement request identity differs")
    compute_key = f"pilot:{previous['run_id']}:compute:{request_digest[7:]}"
    if (len(receipts) != len(by_key)
        or any(by_key.get(item["operation_key"]) != item for item in prior_receipts)
        or set(by_key) - prior_keys != {model_key, overhead_key, compute_key}
        or by_key[model_key]["provider"] != "openrouter"
        or by_key[model_key]["maximum_usd_nanos"] != 2_900_000_000
        or by_key[overhead_key]["provider"] != "modal"
        or by_key[overhead_key]["maximum_usd_nanos"] != 1_000_000_000
        or by_key[compute_key]["provider"] != "modal"
        or by_key[compute_key]["maximum_usd_nanos"] != 766_080_000):
        raise ValueError("collector settlement execution admissions differ")
    dispatch_path, dispatch = _resolve(document["prior_dispatch_record"])
    compute_path = _resolve_path(document["prior_compute_database"])
    if (not dispatch_path.is_relative_to(audit_path.parent)
        or not compute_path.is_relative_to(audit_path.parent)
        or dispatch.get("function_call_id") != terminal[0]["function_call_id"]
        or dispatch.get("evidence_root") !=
            "model-serving/d8b2ac9486364026f3975d15598bc86e52e7a1fe784889530275f93e0a00647d/repetition-0001-attempt-01"
        or digest_value(dispatch) != terminal[0].get("dispatch_digest")):
        raise ValueError("collector settlement durable dispatch differs")
    connection = sqlite3.connect(compute_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT status, request_digest, external_call_id, "
            "dispatch_evidence_digest, evidence_locator, evidence_digest "
            "FROM compute_executions").fetchall()
    finally:
        connection.close()
    if rows != [("dispatched", request_digest, terminal[0]["function_call_id"],
                terminal[0]["dispatch_digest"], None, None)]:
        raise ValueError("collector settlement compute ledger differs")
    _, pointer = _resolve(document["call_pointer"])
    manifest_path, manifest = _resolve(document["staged_manifest"])
    receipt_path, remote_receipt = _resolve(document["staged_remote_receipt"])
    if (pointer.get("function_call_id") != terminal[0]["function_call_id"]
        or pointer.get("result", {}).get("schema_version") != "modal-evaluator-staging-pointer/v0alpha1"
        or pointer["result"].get("volume_name") != "agent-collab-evals-evaluator-staging-v2"
        or pointer["result"].get("root") != dispatch["evidence_root"]
        or manifest.get("schema_version") != "modal-evaluator-evidence/v0alpha1"
        or manifest.get("volume_name") != "agent-collab-evals-evaluator-staging-v2"
        or manifest.get("root") != dispatch["evidence_root"]
        or manifest.get("remote_receipt_digest") != pointer["result"].get("remote_receipt_digest")
        or manifest["remote_receipt_digest"] != digest_file(receipt_path)
        or receipt_path.parent != manifest_path.parent
        or remote_receipt.get("ok") is not True
        or remote_receipt.get("candidate_id") != "stock-vllm-0.21.0"
        or remote_receipt.get("campaign_manifest_digest") !=
            dispatch.get("campaign_manifest_digest")
        or not isinstance(manifest.get("raw_digests"), dict)
        or len(manifest["raw_digests"]) != 9):
        raise ValueError("collector settlement remote evidence differs")
    for name, expected in manifest["raw_digests"].items():
        if (not isinstance(name, str) or Path(name).name != name
            or not name.endswith(".json") or not isinstance(expected, str)
            or digest_file(manifest_path.parent / "raw" / name) != expected):
            raise ValueError("collector settlement raw evidence differs")
    conformance_path = _resolve_path(document["conformance_receipt"])
    measurement_id = dispatch["measurement_id"]
    bundle = LocalMeasurementBundleStore(conformance_path.parents[2]).load(
        measurement_id, 1, attempt=1)
    normalized = bundle.receipt["normalized"]
    if (conformance_path.name != "receipt.json"
        or conformance_path.parent.name != "repetition-0001-attempt-01"
        or conformance_path.parent.parent.name != measurement_id
        or bundle.receipt["raw_digests"] != manifest["raw_digests"]
        or normalized.get("valid") is not True
        or normalized.get("modal_function_call_id") != terminal[0]["function_call_id"]
        or normalized.get("remote_receipt") != remote_receipt
        or normalized.get("performance_score", {}).get("eligible") is not True
        or normalized.get("durable_evidence", {}).get("volume_name") !=
            "agent-collab-evals-evaluator-evidence-v2"
        or normalized["durable_evidence"].get("remote_receipt_digest") !=
            manifest["remote_receipt_digest"]
        or normalized.get("platform_build", {}).get("git_commit") !=
            run_config["git_commit"]
        or normalized["platform_build"].get("collector_git_commit") !=
            "bfca53e06cdb5f787c076cda2b5242326ee8a328"):
        raise ValueError("collector settlement conformance evidence differs")
    _, cleanup = _resolve(document["cleanup_observation"])
    if (cleanup.get("run_id") != previous["run_id"]
        or cleanup.get("active_modal_apps") != []
        or cleanup.get("actor_containers") != []):
        raise ValueError("collector settlement cleanup differs")
    _, report = _resolve(document["billing_report"])
    _, prior_report = _resolve(previous["billing_report"])
    if (not isinstance(report, dict) or set(report) !=
        {"schema_version", "retrieved_at", "start", "end", "rows"}
        or report["schema_version"] != "modal-billing-snapshot/v1"
        or report["start"] != "2026-09-01" or report["end"] != "2026-09-25"
        or not isinstance(report["retrieved_at"], str)
        or not report["retrieved_at"].startswith("2026-09-24T")
        or not isinstance(report["rows"], list)):
        raise ValueError("collector settlement billing snapshot differs")
    rows = {row["object_id"]: row for row in report["rows"]}
    old_rows = {row["object_id"]: row for row in prior_report["rows"]}
    current_ids = {row["object_id"] for row in report["rows"]
        if row["interval_start"] == "2026-09-23T00:00:00"}
    allowed = {*old_rows, document["reference_app_id"], document["conformance_app_id"]}
    if (document["reference_app_id"] != "ap-1Wao2Jn6Msdl2fGA3XyMJ4"
        or document["conformance_app_id"] != "ap-WVjBgH1M2Js1XxxAtHTuB7"
        or len(rows) != len(report["rows"])
        or any(rows.get(key) != value for key, value in old_rows.items())
        or not set(rows).issubset(allowed)
        or current_ids != {"ap-vWm2nqnesAmOq740ajOMHs",
            "ap-U1QlcwmrZURytic8pXj78g", "ap-s5Ta3RtUBuCCak6J5Qaqya",
            "ap-LNPq0tjXccxhwJhmuPCjG2", document["reference_app_id"]}
        or rows[document["reference_app_id"]]["interval_start"] !=
            "2026-09-23T00:00:00"
        or any(row["interval_start"] == "2026-09-14T00:00:00" for row in report["rows"])
        or document["billing_buffer_usd_nanos"] != 100_000_000
        or document["conformance_buffer_usd_nanos"] != 10_000_000
        or document["model_buffer_usd_nanos"] != 10_000_000
        or document["qualification_release_usd_nanos"] != 500_000_000):
        raise ValueError("collector settlement billing scope differs")
    reference_cost = _billing_nanos(rows[document["reference_app_id"]])
    conformance_row = rows.get(document["conformance_app_id"])
    if (reference_cost != 209_026_580
        or conformance_row is not None and (
            conformance_row["interval_start"] != "2026-09-24T00:00:00"
            or _billing_nanos(conformance_row) > document["conformance_buffer_usd_nanos"])
        or reference_cost + document["billing_buffer_usd_nanos"]
            + document["conformance_buffer_usd_nanos"] >= 1_766_080_000):
        raise ValueError("collector settlement release bounds differ")
    releases["modal"] += (1_766_080_000 - reference_cost
        - document["billing_buffer_usd_nanos"]
        - document["conformance_buffer_usd_nanos"]
        + document["qualification_release_usd_nanos"])
    releases["openrouter"] += 2_900_000_000 - document["model_buffer_usd_nanos"]
    return receipts, releases


def _validate_feedback_failure_settlement(document, plan_digest):
    """Release only reviewed unused allowances from the September 23 abort."""
    fields = {"schema_version", "previous_amendment", "prior_plan_digest",
        "prior_receipts_digest", "prior_audit", "prior_run_config", "prior_budget_plan",
        "prior_budget_database", "cleanup_observation", "billing_report",
        "reference_app_id", "candidate_app_id", "helper_app_ids", "run_id",
        "provider_limits_usd_nanos", "total_limit_usd_nanos",
        "billing_buffer_usd_nanos", "model_buffer_usd_nanos",
        "qualification_release_usd_nanos"}
    if not isinstance(document, dict) or set(document) != fields or document["prior_plan_digest"] != plan_digest:
        raise ValueError("feedback settlement fields differ")
    _, previous = _resolve(document["previous_amendment"])
    if previous.get("schema_version") != "exploratory-solo-retry/v3":
        raise ValueError("feedback settlement requires the reviewed third amendment")
    previous_receipts, releases = validate_retry(previous, plan_digest)
    audit_path, audit = _resolve(document["prior_audit"])
    _, run_config = _resolve(document["prior_run_config"])
    if (document["provider_limits_usd_nanos"] != previous["provider_limits_usd_nanos"]
        or document["total_limit_usd_nanos"] != previous["total_limit_usd_nanos"]
        or not isinstance(document["run_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", document["run_id"])
        or document["run_id"] in {previous["run_id"], "first-solo", "solo-retry-0921", "solo-final-0921"}
        or audit.get("run_id") != previous["run_id"]
        or audit.get("status") != "aborted" or audit.get("scoreable") is not False
        or audit.get("failure") != {"stage": "public_feedback", "type": "RuntimeError"}
        or audit.get("cleanup_failure") != "ExceptionGroup"
        or audit.get("run_config_digest") != document["prior_run_config"]["digest"]
        or run_config.get("git_commit") != "ad75c700dfbdc0986950cd6c4e43834eb952f705"
        or run_config.get("git_dirty") is not False
        or run_config.get("config", {}).get("sandbox_profile") !=
            "config/enforcement_profiles/oci-opencode-podman-development-v1.json"
        or audit.get("budget_reconciliation", {}).get("valid") is not True
        or audit_path.parent.joinpath("hidden-result.json").exists()):
        raise ValueError("feedback settlement prior run differs")
    compute = audit.get("partial_compute_snapshot", {})
    terminal = audit.get("remote_cleanup", [])
    if (compute.get("hidden_reserved_seconds") != 0
        or compute.get("hidden_used_seconds") != 0
        or len(compute.get("reservations", [])) != 1
        or compute["reservations"][0].get("status") != "complete"
        or compute["reservations"][0].get("scope") != "visible"
        or len(terminal) != 2
        or any(item.get("terminal_confirmed") is not True for item in terminal)
        or len({item.get("request_digest") for item in terminal}) != 2):
        raise ValueError("feedback settlement compute is not terminal and public-only")
    receipts = audit["spend_admission"]["receipts"]
    if (digest_value(receipts) != document["prior_receipts_digest"]
        or audit["spend_admission"].get("retry_amendment_digest") != digest_value(previous)):
        raise ValueError("feedback settlement admissions differ")
    by_key = {item["operation_key"]: item for item in receipts}
    prior_keys = {item["operation_key"] for item in previous_receipts}
    prefix = "pilot:retry-" + digest_value(previous)[7:]
    model_key, overhead_key = prefix + ":openrouter:base", prefix + ":modal:base"
    compute_keys = {f"pilot:{previous['run_id']}:compute:" + item["request_digest"][7:] for item in terminal}
    if (len(receipts) != len(by_key)
        or any(by_key.get(item["operation_key"]) != item for item in previous_receipts)
        or set(by_key) - prior_keys != {model_key, overhead_key, *compute_keys}
        or by_key[model_key]["maximum_usd_nanos"] != 2_900_000_000
        or by_key[model_key]["provider"] != "openrouter"
        or by_key[overhead_key]["maximum_usd_nanos"] != 1_000_000_000
        or by_key[overhead_key]["provider"] != "modal"
        or any(by_key[key]["maximum_usd_nanos"] != 766_080_000
               or by_key[key]["provider"] != "modal" for key in compute_keys)):
        raise ValueError("feedback settlement execution admissions differ")
    model_cost = _reconcile_prior_model(document, run_config, audit, expected_cost=3_081_720)
    _, cleanup = _resolve(document["cleanup_observation"])
    if (cleanup.get("run_id") != previous["run_id"]
        or cleanup.get("active_modal_apps") != []
        or cleanup.get("actor_containers") != []):
        raise ValueError("feedback settlement cleanup differs")
    _, report = _resolve(document["billing_report"])
    if (not isinstance(report, dict) or set(report) !=
        {"schema_version", "retrieved_at", "start", "end", "rows"}
        or report["schema_version"] != "modal-billing-snapshot/v1"
        or report["start"] != "2026-09-01" or report["end"] != "2026-09-24"
        or not isinstance(report["retrieved_at"], str)
        or not report["retrieved_at"].startswith("2026-09-23T")
        or not isinstance(report["rows"], list)):
        raise ValueError("feedback settlement billing snapshot differs")
    rows = {row["object_id"]: row for row in report["rows"]}
    current_ids = {document["reference_app_id"], document["candidate_app_id"], *document["helper_app_ids"]}
    if (document["reference_app_id"] != "ap-vWm2nqnesAmOq740ajOMHs"
        or document["candidate_app_id"] != "ap-U1QlcwmrZURytic8pXj78g"
        or set(document["helper_app_ids"]) != {"ap-s5Ta3RtUBuCCak6J5Qaqya", "ap-LNPq0tjXccxhwJhmuPCjG2"}
        or len(report["rows"]) != len(rows) or len(current_ids) != 4
        or {row["object_id"] for row in report["rows"]
            if row["interval_start"] == "2026-09-23T00:00:00"} != current_ids
        or any(row["interval_start"] == "2026-09-14T00:00:00" for row in report["rows"])):
        raise ValueError("feedback settlement billing scope differs")
    current_cost = sum(_billing_nanos(rows[item]) for item in current_ids)
    qualification = by_key["qualification:modal-access-v1"]
    if (current_cost != 449_142_790
        or qualification["maximum_usd_nanos"] != 1_532_160_000
        or qualification["provider"] != "modal"
        or document["billing_buffer_usd_nanos"] != 100_000_000
        or document["model_buffer_usd_nanos"] != 10_000_000
        or document["qualification_release_usd_nanos"] != 500_000_000
        or current_cost + document["billing_buffer_usd_nanos"] >= 2_532_160_000
        or model_cost + document["model_buffer_usd_nanos"] >= 2_900_000_000):
        raise ValueError("feedback settlement release bounds differ")
    releases["modal"] += (2_532_160_000 - current_cost - document["billing_buffer_usd_nanos"]
        + document["qualification_release_usd_nanos"])
    releases["openrouter"] += (2_900_000_000 - model_cost - document["model_buffer_usd_nanos"])
    return receipts, releases


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
