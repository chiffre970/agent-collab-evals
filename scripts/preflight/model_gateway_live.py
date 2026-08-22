#!/usr/bin/env python3
"""Run one explicitly enabled, budget-bounded live model-gateway canary."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

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


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE = (
    REPOSITORY_ROOT
    / "config/gateway_profiles/openrouter-deepinfra-development-v0.json"
)
CANARY_BUDGET_USD_NANOS = 10_000_000


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="send one live request; without this flag the command spends nothing",
    )
    args = parser.parse_args()
    profile_path = args.profile.resolve()
    profile_root = (REPOSITORY_ROOT / "config/gateway_profiles").resolve()
    if profile_path.parent != profile_root:
        raise ValueError("profile must be a file directly under config/gateway_profiles")
    profile = ModelGatewayProfile.load(
        profile_path, repository_root=REPOSITORY_ROOT
    )
    if profile.status != "development":
        raise ValueError("live canary requires a development gateway profile")
    if not args.execute:
        print(
            json.dumps(
                {
                    "ok": True,
                    "executed": False,
                    "profile_id": profile.profile_id,
                    "profile_digest": profile.resolved_digest,
                    "maximum_budget_usd_nanos": CANARY_BUDGET_USD_NANOS,
                    "next": "rerun with --execute to send one live request",
                },
                indent=2,
            )
        )
        return 0

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is missing")

    started = datetime.now(UTC)
    campaign_run_id = "model-gateway-live-canary"
    actor_id = f"{campaign_run_id}:actor:0"
    allocations = (
        ActorBudgetAllocation(
            campaign_run_id, actor_id, CANARY_BUDGET_USD_NANOS
        ),
    )
    budget_plan = BudgetPlan.create(
        plan_id="model-gateway-live-canary-budget-v1",
        status="development",
        campaign_run_id=campaign_run_id,
        organisation_limit_usd_nanos=CANARY_BUDGET_USD_NANOS,
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
            CANARY_BUDGET_USD_NANOS,
            allocations,
        )
        upstream = OpenRouterUpstream.from_profile(profile, api_key)
        gateway = ModelBudgetGateway(profile, account, upstream)
        try:
            token = gateway.issue(
                campaign_run_id=campaign_run_id,
                actor_id=actor_id,
                model_endpoint=gateway.endpoint,
            )
            gateway.activate(
                token.token_id, SessionHandle("model-gateway-live-canary-session")
            )
            status, raw_stream = _request(gateway, token.value, profile.requested_model)
            snapshot = account.snapshot(campaign_run_id)
            reconciliation = account.reconcile(campaign_run_id)
        finally:
            gateway.close()

    if status != 200:
        raise RuntimeError(f"model gateway canary failed with HTTP {status}")
    if b"GATEWAY_LIVE_OK" not in raw_stream:
        raise RuntimeError("model gateway canary response failed its content check")
    if len(snapshot.charges) != 1:
        raise RuntimeError("model gateway canary did not produce exactly one charge")
    if not reconciliation.valid:
        raise RuntimeError("model gateway canary failed budget reconciliation")
    charge = snapshot.charges[0]
    usage = charge.usage
    if not usage.raw_metadata_receipt:
        raise RuntimeError("model gateway canary omitted generation metadata")

    finished = datetime.now(UTC)
    receipt_dir = REPOSITORY_ROOT / "tmp/preflight"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    timestamp = finished.strftime("%Y-%m-%dT%H-%M-%SZ")
    receipt_stem = f"model-gateway-live-{timestamp}"
    stream_path = receipt_dir / f"{receipt_stem}.stream.sse"
    metadata_path = receipt_dir / f"{receipt_stem}.metadata.json"
    stream_path.write_bytes(raw_stream)
    metadata_path.write_bytes(usage.raw_metadata_receipt)
    stream_path.chmod(0o600)
    metadata_path.chmod(0o600)
    receipt = {
        "schema_version": "model-gateway-live-canary/v1",
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "profile_id": profile.profile_id,
        "profile_digest": profile.resolved_digest,
        "model_profile_digest": profile.model_profile_digest,
        "billing_catalog_digest": profile.billing_catalog_digest,
        "requested_model": usage.requested_model,
        "returned_model": usage.returned_model,
        "metadata_model": usage.metadata_model,
        "provider_name": usage.provider_name,
        "provider_request_id": usage.provider_request_id,
        "provider_generation_id": usage.provider_generation_id,
        "provider_timestamp": usage.provider_timestamp,
        "system_fingerprint": usage.system_fingerprint,
        "prompt_tokens": usage.prompt_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
        "completion_tokens": usage.completion_tokens,
        "charged_usd_nanos": charge.charged_usd_nanos,
        "stream_digest": digest_bytes(raw_stream),
        "stream_path": str(stream_path.relative_to(REPOSITORY_ROOT)),
        "metadata_receipt_digest": usage.metadata_receipt_digest,
        "metadata_receipt_path": str(
            metadata_path.relative_to(REPOSITORY_ROOT)
        ),
    }
    receipt_path = receipt_dir / f"{receipt_stem}.json"
    receipt_path.write_bytes(canonical_json_bytes(receipt) + b"\n")
    receipt_path.chmod(0o600)
    print(
        json.dumps(
            {
                "ok": True,
                "executed": True,
                "provider": usage.provider_name,
                "returned_model": usage.returned_model,
                "metadata_model": usage.metadata_model,
                "charged_usd_nanos": charge.charged_usd_nanos,
                "receipt_path": str(receipt_path.relative_to(REPOSITORY_ROOT)),
            },
            indent=2,
        )
    )
    return 0


def _request(
    gateway: ModelBudgetGateway, token: str, model: str
) -> tuple[int, bytes]:
    authority = gateway.endpoint.removeprefix("http://").split("/", 1)[0]
    host, port_text = authority.split(":", 1)
    connection = http.client.HTTPConnection(host, int(port_text), timeout=150)
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": "Return exactly GATEWAY_LIVE_OK and nothing else.",
                }
            ],
            "stream": True,
            "max_tokens": 64,
        },
        separators=(",", ":"),
    ).encode()
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
        return response.status, response.read()
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
