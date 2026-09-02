"""Semantic loaders for profiles frozen before study composition."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .canonical import digest_file, digest_value, load_json


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}")


class RegisteredProfileError(ValueError):
    """A registered component profile is invalid or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class RegisteredProfile:
    path: Path
    digest: str
    profile_id: str
    schema_version: str
    document: Mapping[str, Any]

    @property
    def authority_digest(self) -> str:
        return digest_value(self.document)


def load_hidden_evaluation_profile(path: Path) -> RegisteredProfile:
    profile = _load(path, "registered-hidden-evaluation-profile/v1", "registered")
    value = profile.document
    expected = {
        "schema_version",
        "profile_id",
        "status",
        "campaign_manifest_digest",
        "hidden_workload_manifest_digest",
        "retention_receipt_file_digest",
        "phase_order",
        "correctness",
        "quality",
        "performance",
        "outer_hidden_reserved_seconds",
        "co_scheduling_policy",
        "external_gate_authorities_required",
    }
    _keys(value, expected, "hidden evaluation profile")
    _digests(
        value,
        (
            "campaign_manifest_digest",
            "hidden_workload_manifest_digest",
            "retention_receipt_file_digest",
        ),
    )
    if value["phase_order"] != ["correctness", "quality", "performance"]:
        raise RegisteredProfileError("hidden evaluation phase order differs")
    correctness = _mapping(value, "correctness")
    quality = _mapping(value, "quality")
    performance = _mapping(value, "performance")
    _keys(
        correctness,
        {
            "adapter",
            "workload_digest",
            "executions",
            "reserved_seconds_per_execution",
            "maximum_collection_seconds",
        },
        "correctness phase",
    )
    _keys(
        quality,
        {
            "adapter",
            "quality_profile_digest",
            "quality_policy_digest",
            "quality_policy_authority_digest",
            "workload_digest",
            "request_spec_digest",
            "reference_candidate_digest",
            "reference_candidate_manifest_digest",
            "repetitions",
            "roles_per_repetition",
            "reserved_seconds_per_execution",
            "role_order_by_repetition",
        },
        "quality phase",
    )
    _keys(
        performance,
        {
            "adapter",
            "workload_digest",
            "scoring_profile_digest",
            "repetitions",
            "reserved_seconds_per_execution",
            "aggregation",
            "maximum_collection_seconds",
        },
        "performance phase",
    )
    for section, names in (
        (correctness, ("workload_digest",)),
        (
            quality,
            (
                "quality_profile_digest",
                "quality_policy_digest",
                "quality_policy_authority_digest",
                "workload_digest",
                "request_spec_digest",
                "reference_candidate_digest",
                "reference_candidate_manifest_digest",
            ),
        ),
        (performance, ("workload_digest", "scoring_profile_digest")),
    ):
        _digests(section, names)
    fixed = {
        "correctness_adapter": correctness.get("adapter"),
        "correctness_executions": correctness.get("executions"),
        "quality_adapter": quality.get("adapter"),
        "quality_repetitions": quality.get("repetitions"),
        "quality_roles": quality.get("roles_per_repetition"),
        "performance_adapter": performance.get("adapter"),
        "performance_repetitions": performance.get("repetitions"),
        "performance_aggregation": performance.get("aggregation"),
        "co_scheduling_policy": value.get("co_scheduling_policy"),
    }
    required = {
        "correctness_adapter": "compute-candidate-evaluator/v0alpha1",
        "correctness_executions": 1,
        "quality_adapter": "paired-quality-series-evaluator/v0alpha1",
        "quality_repetitions": 3,
        "quality_roles": 2,
        "performance_adapter": "performance-series-evaluator/v0alpha1",
        "performance_repetitions": 3,
        "performance_aggregation": (
            "median_with_candidate_min_over_reference_max_bound"
        ),
        "co_scheduling_policy": "none_independent_receipts",
    }
    if fixed != required:
        raise RegisteredProfileError("hidden evaluation policy differs")
    orders = quality.get("role_order_by_repetition")
    if not isinstance(orders, list) or len(orders) != 3 or any(
        order not in (
            ["reference", "candidate"],
            ["candidate", "reference"],
        )
        for order in orders
    ):
        raise RegisteredProfileError("quality role order is invalid")
    allowances = (
        _positive(correctness, "reserved_seconds_per_execution")
        + _positive(quality, "repetitions")
        * _positive(quality, "roles_per_repetition")
        * _positive(quality, "reserved_seconds_per_execution")
        + _positive(performance, "repetitions")
        * _positive(performance, "reserved_seconds_per_execution")
    )
    if value.get("outer_hidden_reserved_seconds") != allowances:
        raise RegisteredProfileError("hidden phase allowances do not partition total")
    external_gates = value.get("external_gate_authorities_required")
    if external_gates != [
        "artifact_integrity",
        "cold_start",
        "api_schema",
        "stability",
        "prohibited_shortcuts",
    ]:
        raise RegisteredProfileError("external hidden gate authorities differ")
    return profile


def load_collaboration_profile(path: Path) -> RegisteredProfile:
    profile = _load(path, "registered-collaboration-profile/v1", "registered")
    expected = {
        "schema_version",
        "profile_id",
        "status",
        "adapter",
        "peer_isolated_visibility",
        "peer_collab_visibility",
        "identity_source",
        "entry_sequence_policy",
        "notification_cursor_policy",
        "denial_audit_policy",
        "pagination_limit",
        "reset_policy",
    }
    _keys(profile.document, expected, "collaboration profile")
    required = {
        "adapter": "sqlite-collaboration-backend/v1",
        "peer_isolated_visibility": "actor_private",
        "peer_collab_visibility": "campaign_shared",
        "identity_source": "server_session_transport",
        "entry_sequence_policy": (
            "actor_local_in_private_campaign_global_in_shared"
        ),
        "notification_cursor_policy": "signed_view_local_watermark",
        "denial_audit_policy": "durable_before_propagation",
        "pagination_limit": 100,
        "reset_policy": "campaign_scoped",
    }
    if any(profile.document.get(key) != value for key, value in required.items()):
        raise RegisteredProfileError("collaboration policy differs")
    return profile


def load_research_profile(path: Path) -> RegisteredProfile:
    profile = _load(path, "registered-research-profile/v1", "registered")
    expected = {
        "schema_version",
        "profile_id",
        "status",
        "adapter",
        "network_access",
        "search_policy",
        "fetch_policy",
        "request_cap_per_actor",
        "byte_cap_per_actor",
        "cache_policy",
    }
    _keys(profile.document, expected, "research profile")
    required = {
        "adapter": "disabled-research-broker/v1",
        "network_access": "none",
        "search_policy": "deny",
        "fetch_policy": "deny",
        "request_cap_per_actor": 0,
        "byte_cap_per_actor": 0,
        "cache_policy": "none",
    }
    if any(profile.document.get(key) != value for key, value in required.items()):
        raise RegisteredProfileError("disabled research policy differs")
    return profile


def load_enforcement_requirements(path: Path) -> RegisteredProfile:
    profile = _load(
        path,
        "registered-enforcement-requirements/v1",
        "registered_requirement",
    )
    expected = {
        "schema_version",
        "profile_id",
        "status",
        "execution_authorized",
        "network",
        "filesystem",
        "process",
        "required_conformance",
        "implementation_profile",
    }
    _keys(profile.document, expected, "enforcement requirements")
    if (
        profile.document.get("execution_authorized") is not False
        or profile.document.get("implementation_profile") is not None
    ):
        raise RegisteredProfileError("enforcement requirements must fail closed")
    required = profile.document.get("required_conformance")
    if not isinstance(required, list) or len(required) != 7 or len(set(required)) != 7:
        raise RegisteredProfileError("enforcement conformance set differs")
    return profile


def _load(path: Path, schema: str, status: str) -> RegisteredProfile:
    resolved = path.resolve(strict=True)
    with resolved.open("r", encoding="utf-8") as source:
        value = load_json(source)
    if not isinstance(value, dict):
        raise RegisteredProfileError("registered profile must be an object")
    profile_id = value.get("profile_id")
    if not isinstance(profile_id, str) or not _SAFE_ID.fullmatch(profile_id):
        raise RegisteredProfileError("registered profile ID is invalid")
    if value.get("schema_version") != schema or value.get("status") != status:
        raise RegisteredProfileError("registered profile identity differs")
    return RegisteredProfile(
        path=resolved,
        digest=digest_file(resolved),
        profile_id=profile_id,
        schema_version=schema,
        document=value,
    )


def _keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise RegisteredProfileError(f"{label} fields differ")


def _mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    item = value.get(key)
    if not isinstance(item, dict):
        raise RegisteredProfileError(f"{key} must be an object")
    return item


def _digests(value: Mapping[str, Any], names: tuple[str, ...]) -> None:
    for name in names:
        item = value.get(name)
        if not isinstance(item, str) or not _DIGEST.fullmatch(item):
            raise RegisteredProfileError(f"{name} must be SHA-256")


def _positive(value: Mapping[str, Any], name: str) -> int:
    item = value.get(name)
    if type(item) is not int or item < 1:
        raise RegisteredProfileError(f"{name} must be a positive integer")
    return item
