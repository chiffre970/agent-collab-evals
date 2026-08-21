from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_collab_evals.adapters.local_events import LocalEventSink
from agent_collab_evals.adapters.opencode_harness import (
    OpenCodeHarnessRuntime,
    OpenCodeRuntimeProfile,
)
from agent_collab_evals.adapters.sqlite_budget import SqliteBudgetAccount
from agent_collab_evals.budget import ActorBudgetAllocation
from agent_collab_evals.controller import CampaignController
from agent_collab_evals.domain import CoordinationCondition, Job, OrganisationSpec
from agent_collab_evals.model_gateway import (
    ModelBudgetGateway,
    ModelGatewayProfile,
    UpstreamStream,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GATEWAY_PROFILE_PATH = (
    REPOSITORY_ROOT
    / "config/gateway_profiles/openrouter-deepinfra-local-conformance-v0.json"
)
RUNTIME_PROFILE_PATH = (
    REPOSITORY_ROOT
    / "config/runtime_profiles/opencode-deepseek-v4-flash-development.json"
)


class _OpenCodeUpstream:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def stream(self, request: bytes) -> UpstreamStream:
        body = json.loads(request)
        self.requests.append(body)
        request_id = f"gateway-integration-{len(self.requests)}"
        chunks = [
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": 1_787_300_000,
                "model": "deepseek/deepseek-v4-flash-0731",
                "system_fingerprint": "local-conformance-fingerprint",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "content": "MODEL_GATEWAY_OK",
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": 1_787_300_000,
                "model": "deepseek/deepseek-v4-flash-0731",
                "system_fingerprint": "local-conformance-fingerprint",
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                    "prompt_tokens_details": {"cached_tokens": 0},
                },
            },
        ]
        stream = "".join(
            f"data: {json.dumps(chunk)}\n\n" for chunk in chunks
        ) + "data: [DONE]\n\n"
        return UpstreamStream(
            200,
            {"Content-Type": "text/event-stream"},
            (stream.encode(),),
            provider_name="DeepInfra",
        )


@unittest.skipUnless(
    os.environ.get("RUN_MODEL_GATEWAY_INTEGRATION") == "1",
    "set RUN_MODEL_GATEWAY_INTEGRATION=1 to run OpenCode through the gateway",
)
class ModelGatewayIntegrationTests(unittest.TestCase):
    def test_stock_opencode_routes_and_settles_through_budget_gateway(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gateway_profile = ModelGatewayProfile.load(
                GATEWAY_PROFILE_PATH, repository_root=REPOSITORY_ROOT
            )
            account = SqliteBudgetAccount(
                root / "budget.sqlite3", gateway_profile.rate_card
            )
            campaign_run_id = "opencode-budget-gateway"
            actor_id = f"{campaign_run_id}:actor:0"
            account.open_campaign(
                campaign_run_id,
                250_000_000,
                (
                    ActorBudgetAllocation(
                        campaign_run_id, actor_id, 250_000_000
                    ),
                ),
            )
            upstream = _OpenCodeUpstream()
            gateway = ModelBudgetGateway(gateway_profile, account, upstream)
            try:
                runtime = OpenCodeHarnessRuntime(
                    OpenCodeRuntimeProfile.load(RUNTIME_PROFILE_PATH),
                    root / "runtime-state",
                    gateway,
                    timeout_seconds=30,
                )
                controller = CampaignController(
                    runtime, LocalEventSink(root / "events")
                )
                handle = controller.start(
                    OrganisationSpec(
                        campaign_run_id=campaign_run_id,
                        condition=CoordinationCondition.SOLO,
                        organisation_size=1,
                        workspace_root=root / "workspaces",
                        model_endpoint=gateway.endpoint,
                    )
                )
                controller.deliver(
                    handle,
                    Job(
                        "gateway-job",
                        "Return a short completion.",
                        "sha256:gateway-job",
                        {},
                    ),
                )
                result = controller.close(handle, "complete")

                self.assertEqual(result.delivered_job_ids, ("gateway-job",))
                session = result.final_harness_snapshot.payload["sessions"][0]
                errors = [
                    event
                    for event in session["events"]
                    if "error" in json.dumps(event).lower()
                ]
                self.assertEqual(
                    len(upstream.requests),
                    1,
                    json.dumps(errors[-5:], sort_keys=True)[:8_000],
                )
                self.assertEqual(
                    upstream.requests[0]["provider"]["only"], ["DeepInfra"]
                )
                snapshot = account.snapshot(campaign_run_id)
                self.assertEqual(snapshot.organisation_reserved_usd_nanos, 0)
                self.assertGreater(snapshot.organisation_charged_usd_nanos, 0)
                self.assertIn(
                    "reservation.settled",
                    [event["kind"] for event in snapshot.audit_events],
                )
            finally:
                gateway.close()


if __name__ == "__main__":
    unittest.main()
