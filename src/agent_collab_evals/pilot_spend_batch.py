"""Exact, append-only batch admissions on the existing pilot currency journal.

Each externally pinned approval freezes the preceding snapshot and admits only
its listed operations. It can explicitly increase ceilings, but never releases
an old reservation or creates a replacement journal.
"""

from pathlib import Path
import re

from .canonical import canonical_json_bytes, digest_value
from .pilot_evidence import retain_document


def validate_batch(approval, snapshot, validate_receipt):
    fields = {"schema_version", "batch_id", "prior_snapshot_digest",
        "provider_limits_usd_nanos", "total_limit_usd_nanos", "admissions"}
    if (not isinstance(approval, dict) or set(approval) != fields
        or approval["schema_version"] != "pilot-spend-batch/v1"
        or not isinstance(approval["batch_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", approval["batch_id"])
        or approval["prior_snapshot_digest"] != digest_value(snapshot)):
        raise PermissionError("batch approval differs from the preceding journal snapshot")
    limits = approval["provider_limits_usd_nanos"]
    total = approval["total_limit_usd_nanos"]
    if (not isinstance(limits, dict) or set(limits) != {"modal", "openrouter"}
        or any(type(v) is not int or v < snapshot["provider_limits_usd_nanos"][k] for k, v in limits.items())
        or type(total) is not int or total < sum(limits.values())):
        raise PermissionError("batch ceilings must preserve the preceding provider limits")
    admissions = approval["admissions"]
    if not isinstance(admissions, list) or not admissions:
        raise ValueError("batch requires an exact nonempty admission inventory")
    keys = {r["operation_key"] for r in snapshot["receipts"]}
    for record in admissions:
        validate_receipt(record)
        if (record["operation_key"] in keys
            or not record["operation_key"].startswith(f"pilot:batch-{approval['batch_id']}:")):
            raise PermissionError("batch admission is duplicate or outside its approved scope")
        keys.add(record["operation_key"])
    proposed = dict(snapshot["reserved_usd_nanos"])
    for record in admissions:
        proposed[record["provider"]] += record["maximum_usd_nanos"]
    if any(proposed[k] > limits[k] for k in limits) or sum(proposed.values()) > total:
        raise PermissionError("pilot spending envelope exhausted before batch admission")


def batch_paths(root, approval):
    directory = Path(root) / "batches" / digest_value(approval)[7:]
    return directory / "approval.json", directory / "admission.json"


def admission_document(approval):
    return {"schema_version": "pilot-spend-batch-admission/v1",
        "approval_digest": digest_value(approval), "receipts": approval["admissions"]}


def apply_batches(root, snapshot, approvals, validate_receipt):
    """Validate supplied pins, retained approvals, and atomic batch receipts."""
    expected_files = {path for approval in approvals for path in batch_paths(root, approval)}
    actual_files = {path for path in (Path(root) / "batches").rglob("*") if path.is_file()}
    if not actual_files <= expected_files:
        raise PermissionError("journal requires all independently pinned batch approvals")
    identifiers = set()
    for index, approval in enumerate(approvals):
        validate_batch(approval, snapshot, validate_receipt)
        if approval["batch_id"] in identifiers:
            raise PermissionError("batch ID is already used")
        identifiers.add(approval["batch_id"])
        approval_path, receipt_path = batch_paths(root, approval)
        if approval_path.exists() and approval_path.read_bytes() != canonical_json_bytes(approval):
            raise RuntimeError("retained batch approval differs")
        if receipt_path.exists():
            if not approval_path.exists() or receipt_path.read_bytes() != canonical_json_bytes(admission_document(approval)):
                raise RuntimeError("retained batch admission differs")
        elif index != len(approvals) - 1:
            raise RuntimeError("preceding batch admission is incomplete")
        receipts = list(snapshot["receipts"])
        totals = dict(snapshot["reserved_usd_nanos"])
        if receipt_path.exists():
            for record in approval["admissions"]:
                receipts.append(record)
                totals[record["provider"]] += record["maximum_usd_nanos"]
        limits = dict(approval["provider_limits_usd_nanos"])
        snapshot = {**snapshot, "receipts": receipts, "reserved_usd_nanos": totals,
            "provider_limits_usd_nanos": limits,
            "remaining_usd_nanos": {k: limits[k] - totals[k] for k in limits},
            "batch_approval_digests": [*snapshot.get("batch_approval_digests", []), digest_value(approval)]}
    return snapshot


def retain_batch(root, approval):
    """Called under the envelope lock after the entire batch passes validation."""
    approval_path, receipt_path = batch_paths(root, approval)
    retain_document(approval_path, approval)
    retain_document(receipt_path, admission_document(approval))
    return admission_document(approval)
