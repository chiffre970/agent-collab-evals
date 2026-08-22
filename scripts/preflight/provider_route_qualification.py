#!/usr/bin/env python3
"""Qualify one deterministically selected provider route with bounded spend."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_collab_evals.adapters.openrouter import OpenRouterUpstream
from agent_collab_evals.adapters.provider_receipts import OpenRouterReceiptVerifier
from agent_collab_evals.adapters.sqlite_budget import SqliteBudgetAccount
from agent_collab_evals.budget import ActorBudgetAllocation, BudgetPlan
from agent_collab_evals.canonical import (
    canonical_json_bytes,
    digest_bytes,
    digest_value,
)
from agent_collab_evals.domain import SessionHandle
from agent_collab_evals.model_gateway import ModelBudgetGateway, ModelGatewayProfile
from agent_collab_evals.provider_qualification import (
    ProviderQualificationPlan,
    QualifiedProviderRoute,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = (
    REPOSITORY_ROOT
    / "config/provider_qualification/deepseek-v4-flash-development-policy.json"
)
DEFAULT_GATEWAY_PROFILE = (
    REPOSITORY_ROOT
    / "config/gateway_profiles/openrouter-deepinfra-development-v0.json"
)
DEFAULT_SELECTION_RECORD = (
    REPOSITORY_ROOT
    / "config/provider_qualification/deepseek-v4-flash-deepinfra-development-selection.json"
)
QUALIFICATION_BUDGET_USD_NANOS = 50_000_000
MAX_PROBE_ELAPSED_MS = 60_000


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--gateway-profile", type=Path, default=DEFAULT_GATEWAY_PROFILE)
    parser.add_argument(
        "--selection-record", type=Path, default=DEFAULT_SELECTION_RECORD
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="send three live requests; without this flag the command spends nothing",
    )
    args = parser.parse_args()
    policy_path = _direct_config_member(
        args.policy,
        REPOSITORY_ROOT / "config/provider_qualification",
        "provider policy",
    )
    gateway_path = _direct_config_member(
        args.gateway_profile,
        REPOSITORY_ROOT / "config/gateway_profiles",
        "gateway profile",
    )
    record_path = _direct_config_member(
        args.selection_record,
        REPOSITORY_ROOT / "config/provider_qualification",
        "provider selection record",
    )
    plan = ProviderQualificationPlan.load(
        policy_path, repository_root=REPOSITORY_ROOT
    )
    selection = plan.select()
    profile = ModelGatewayProfile.load(
        gateway_path, repository_root=REPOSITORY_ROOT
    )
    _validate_profile_binding(plan, selection.selected_provider, profile)
    if not args.execute:
        qualified = QualifiedProviderRoute.load(
            record_path, repository_root=REPOSITORY_ROOT
        )
        if qualified.selection.resolved_digest != selection.resolved_digest:
            raise ValueError("qualified route differs from the current selection")
        print(
            json.dumps(
                {
                    "ok": True,
                    "executed": False,
                    "selection": selection.evidence(),
                    "gateway_profile_id": profile.profile_id,
                    "gateway_profile_digest": profile.resolved_digest,
                    "selection_record_digest": qualified.resolved_digest,
                    "maximum_budget_usd_nanos": QUALIFICATION_BUDGET_USD_NANOS,
                    "probe_count": 3,
                    "next": "rerun with --execute to qualify the selected route",
                },
                indent=2,
            )
        )
        return 0

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is missing")
    started = datetime.now(UTC)
    campaign_run_id = "provider-route-qualification"
    actor_id = f"{campaign_run_id}:actor:0"
    allocations = (
        ActorBudgetAllocation(
            campaign_run_id,
            actor_id,
            QUALIFICATION_BUDGET_USD_NANOS,
        ),
    )
    budget_plan = BudgetPlan.create(
        plan_id="provider-route-qualification-budget-v1",
        status="development",
        campaign_run_id=campaign_run_id,
        organisation_limit_usd_nanos=QUALIFICATION_BUDGET_USD_NANOS,
        allocations=allocations,
        rate_card_digest=digest_value(profile.rate_card),
    )
    with tempfile.TemporaryDirectory() as directory:
        account = SqliteBudgetAccount(
            Path(directory) / "budget.sqlite3",
            profile.rate_card,
            budget_plan=budget_plan,
            receipt_verifier=OpenRouterReceiptVerifier(profile),
        )
        account.open_campaign(
            campaign_run_id,
            QUALIFICATION_BUDGET_USD_NANOS,
            allocations,
        )
        gateway = ModelBudgetGateway(
            profile,
            account,
            OpenRouterUpstream.from_profile(profile, api_key),
        )
        try:
            token = gateway.issue(
                campaign_run_id=campaign_run_id,
                actor_id=actor_id,
                model_endpoint=gateway.endpoint,
            )
            gateway.activate(
                token.token_id,
                SessionHandle("provider-route-qualification-session"),
            )
            text_request = {
                "model": profile.requested_model,
                "messages": [
                    {
                        "role": "user",
                        "content": "Reply with exactly ROUTE_TEXT_OK.",
                    }
                ],
                "stream": True,
                "max_tokens": 128,
            }
            tool_request = {
                "model": profile.requested_model,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Call route_probe once with value ROUTE_TOOL_OK. "
                            "Do not answer in text."
                        ),
                    }
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "route_probe",
                            "description": "Record provider-route conformance.",
                            "parameters": {
                                "type": "object",
                                "properties": {"value": {"type": "string"}},
                                "required": ["value"],
                                "additionalProperties": False,
                            },
                        },
                    }
                ],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "route_probe"},
                },
                "stream": True,
                "max_tokens": 128,
            }
            probes = [
                _request(gateway, token.value, "text_first", text_request),
                _request(gateway, token.value, "text_repeat", text_request),
                _request(gateway, token.value, "tool_call", tool_request),
            ]
            gateway.revoke(token.token_id, "qualification complete")
            reconciliation = account.reconcile(campaign_run_id)
            snapshot = account.snapshot(campaign_run_id)
        finally:
            gateway.close()

    failures = _qualification_failures(
        profile,
        selection.selected_provider,
        probes,
        snapshot.charges,
        reconciliation.valid,
    )
    finished = datetime.now(UTC)
    evidence_root = REPOSITORY_ROOT / "tmp/provider-qualification"
    evidence_root.mkdir(parents=True, exist_ok=True)
    timestamp = finished.strftime("%Y-%m-%dT%H-%M-%SZ")
    raw_directory = evidence_root / f"provider-route-{timestamp}.receipts"
    raw_directory.mkdir(mode=0o700)
    charge_summaries = []
    for index, charge in enumerate(snapshot.charges):
        stream_path = raw_directory / f"{index:02d}.stream.sse"
        metadata_path = raw_directory / f"{index:02d}.metadata.json"
        stream_path.write_bytes(charge.usage.raw_receipt)
        metadata_path.write_bytes(charge.usage.raw_metadata_receipt)
        stream_path.chmod(0o600)
        metadata_path.chmod(0o600)
        charge_summaries.append(
            {
                "reservation_id": charge.reservation_id,
                "provider_name": charge.usage.provider_name,
                "returned_model": charge.usage.returned_model,
                "metadata_model": charge.usage.metadata_model,
                "provider_request_id": charge.usage.provider_request_id,
                "provider_generation_id": charge.usage.provider_generation_id,
                "prompt_tokens": charge.usage.prompt_tokens,
                "cached_input_tokens": charge.usage.cached_input_tokens,
                "completion_tokens": charge.usage.completion_tokens,
                "charged_usd_nanos": charge.charged_usd_nanos,
                "stream_digest": digest_bytes(charge.usage.raw_receipt),
                "metadata_digest": digest_bytes(
                    charge.usage.raw_metadata_receipt
                ),
                "stream_file": str(stream_path.relative_to(REPOSITORY_ROOT)),
                "metadata_file": str(metadata_path.relative_to(REPOSITORY_ROOT)),
            }
        )
    record = {
        "schema_version": "provider-route-qualification-record/v1",
        "status": "development",
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "selection": selection.evidence(),
        "gateway_profile_id": profile.profile_id,
        "gateway_profile_digest": profile.resolved_digest,
        "cache_policy": profile.cache_policy,
        "probe_budget_usd_nanos": QUALIFICATION_BUDGET_USD_NANOS,
        "probes": probes,
        "charges": charge_summaries,
        "total_charged_usd_nanos": snapshot.organisation_charged_usd_nanos,
        "budget_reconciliation": reconciliation.evidence(),
        "conformance_failures": failures,
        "qualified": not failures,
    }
    record_path = evidence_root / f"provider-route-{timestamp}.json"
    record_path.write_bytes(canonical_json_bytes(record) + b"\n")
    record_path.chmod(0o600)
    if failures:
        raise RuntimeError(
            "provider route qualification failed: "
            + "; ".join(failures)
            + f"; record={record_path}"
        )
    print(
        json.dumps(
            {
                "ok": True,
                "qualified": True,
                "selected_provider": selection.selected_provider,
                "probe_count": len(probes),
                "total_charged_usd_nanos": (
                    snapshot.organisation_charged_usd_nanos
                ),
                "record_path": str(record_path.relative_to(REPOSITORY_ROOT)),
            },
            indent=2,
        )
    )
    return 0


def _direct_config_member(source: Path, root: Path, label: str) -> Path:
    resolved = source.resolve()
    if resolved.parent != root.resolve() or not resolved.is_file():
        raise ValueError(f"{label} must be a file directly under {root}")
    return resolved


def _validate_profile_binding(
    plan: ProviderQualificationPlan,
    selected_provider: str,
    profile: ModelGatewayProfile,
) -> None:
    if plan.status != profile.status:
        raise ValueError("qualification and gateway profile statuses differ")
    if plan.model_id != profile.requested_model:
        raise ValueError("qualification and gateway requested models differ")
    if plan.expected_metadata_model != profile.expected_metadata_model:
        raise ValueError("qualification and gateway metadata models differ")
    if selected_provider != profile.expected_provider:
        raise ValueError("selected provider differs from the gateway route")
    if profile.cache_policy != "disabled":
        raise ValueError("development qualification requires disabled caching")


def _request(
    gateway: ModelBudgetGateway,
    token: str,
    probe_id: str,
    payload: dict[str, Any],
) -> dict[str, object]:
    host_port = gateway.endpoint.removeprefix("http://").split("/", 1)[0]
    host, port_text = host_port.rsplit(":", 1)
    connection = http.client.HTTPConnection(host, int(port_text), timeout=120)
    body = canonical_json_bytes(payload)
    started = time.monotonic_ns()
    try:
        connection.request(
            "POST",
            "/v1/chat/completions",
            body=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        raw = response.read()
        status = response.status
    finally:
        connection.close()
    elapsed_ms = (time.monotonic_ns() - started) // 1_000_000
    events = _stream_events(raw)
    content_fragments = (
        choice.get("delta", {}).get("content")
        for event in events
        for choice in event.get("choices", [])
        if isinstance(choice, dict) and isinstance(choice.get("delta"), dict)
    )
    text = "".join(
        fragment for fragment in content_fragments if isinstance(fragment, str)
    )
    tool_fragments = [
        tool
        for event in events
        for choice in event.get("choices", [])
        if isinstance(choice, dict) and isinstance(choice.get("delta"), dict)
        for tool in choice["delta"].get("tool_calls", [])
        if isinstance(tool, dict)
    ]
    return {
        "probe_id": probe_id,
        "request_digest": digest_bytes(body),
        "http_status": status,
        "elapsed_ms": elapsed_ms,
        "visible_text": text,
        "tool_fragments": tool_fragments,
        "stream_digest": digest_bytes(raw),
    }


def _stream_events(raw: bytes) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.startswith(b"data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == b"[DONE]":
            continue
        value = json.loads(payload)
        if isinstance(value, dict):
            events.append(value)
    return events


def _qualification_failures(
    profile: ModelGatewayProfile,
    selected_provider: str,
    probes: list[dict[str, object]],
    charges: tuple[object, ...],
    budget_valid: bool,
) -> list[str]:
    failures: list[str] = []
    if any(probe["http_status"] != 200 for probe in probes):
        failures.append("a probe did not return HTTP 200")
    if any(int(probe["elapsed_ms"]) > MAX_PROBE_ELAPSED_MS for probe in probes):
        failures.append("a probe exceeded the latency threshold")
    if "ROUTE_TEXT_OK" not in str(probes[0]["visible_text"]):
        failures.append("the first text probe failed")
    if "ROUTE_TEXT_OK" not in str(probes[1]["visible_text"]):
        failures.append("the repeated text probe failed")
    tool_text = json.dumps(probes[2]["tool_fragments"], sort_keys=True)
    if "route_probe" not in tool_text or "ROUTE_TOOL_OK" not in tool_text:
        failures.append("the tool-call probe failed")
    if len(charges) != len(probes):
        failures.append("probe count differs from settled charge count")
    for charge in charges:
        usage = charge.usage
        if usage.provider_name != selected_provider:
            failures.append("provider identity drifted")
        if usage.returned_model != profile.expected_returned_model:
            failures.append("stream model identity drifted")
        if usage.metadata_model != profile.expected_metadata_model:
            failures.append("metadata model identity drifted")
        if usage.cached_input_tokens != 0:
            failures.append("a disabled-cache probe reported cached input")
        if not usage.raw_receipt or not usage.raw_metadata_receipt:
            failures.append("a provider receipt is missing")
    if not budget_valid:
        failures.append("budget reconciliation failed")
    return sorted(set(failures))


if __name__ == "__main__":
    raise SystemExit(main())
