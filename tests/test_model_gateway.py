from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from agent_collab_evals.adapters.sqlite_budget import SqliteBudgetAccount
from agent_collab_evals.budget import ActorBudgetAllocation
from agent_collab_evals.domain import SessionHandle
from agent_collab_evals.model_gateway import (
    ModelBudgetGateway,
    ModelGatewayProfile,
    UpstreamStream,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = (
    REPOSITORY_ROOT
    / "config/gateway_profiles/openrouter-deepinfra-local-conformance-v0.json"
)
DEVELOPMENT_PROFILE_PATH = (
    REPOSITORY_ROOT
    / "config/gateway_profiles/openrouter-deepinfra-development-v0.json"
)


class _Upstream:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.provider_name = "DeepInfra"

    def stream(self, request: bytes) -> UpstreamStream:
        self.requests.append(json.loads(request))
        first = {
            "id": f"provider-{len(self.requests)}",
            "object": "chat.completion.chunk",
            "created": 1_787_300_000,
            "model": "deepseek/deepseek-v4-flash-0731",
            "system_fingerprint": "local-conformance-fingerprint",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "GATEWAY_OK"},
                    "finish_reason": None,
                }
            ],
        }
        final = {
            "id": f"provider-{len(self.requests)}",
            "object": "chat.completion.chunk",
            "created": 1_787_300_000,
            "model": "deepseek/deepseek-v4-flash-0731",
            "system_fingerprint": "local-conformance-fingerprint",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
                "prompt_tokens_details": {"cached_tokens": 25},
            },
        }
        payload = (
            f"data: {json.dumps(first)}\n\n"
            f"data: {json.dumps(final)}\n\n"
            "data: [DONE]\n\n"
        ).encode()
        midpoint = len(payload) // 2
        return UpstreamStream(
            200,
            {"Content-Type": "text/event-stream"},
            (payload[:midpoint], payload[midpoint:]),
            provider_name=self.provider_name,
        )


class ModelBudgetGatewayTests(unittest.TestCase):
    def test_revocation_waits_for_authenticated_requests(self) -> None:
        profile = ModelGatewayProfile.load(
            PROFILE_PATH, repository_root=REPOSITORY_ROOT
        )
        with tempfile.TemporaryDirectory() as directory:
            account = SqliteBudgetAccount(
                Path(directory) / "budget.sqlite3", profile.rate_card
            )
            account.open_campaign(
                "revocation-run",
                1_000_000,
                (
                    ActorBudgetAllocation(
                        "revocation-run", "revocation-run:actor:0", 1_000_000
                    ),
                ),
            )
            gateway = ModelBudgetGateway(
                profile, account, _Upstream(), serve_http=False
            )
            token = gateway.issue(
                campaign_run_id="revocation-run",
                actor_id="revocation-run:actor:0",
                model_endpoint=gateway.endpoint,
            )
            gateway.activate(token.token_id, SessionHandle("revocation-session"))
            authorization = gateway._authorize(f"Bearer {token.value}")
            self.assertIsNotNone(authorization)
            assert authorization is not None

            revoker = threading.Thread(
                target=gateway.revoke,
                args=(token.token_id, "session stopped"),
            )
            revoker.start()
            with gateway._request_condition:
                token_digest = gateway._token_ids[token.token_id]
                state = gateway._tokens[token_digest]
                self.assertTrue(
                    gateway._request_condition.wait_for(
                        lambda: state.revoked, timeout=1
                    )
                )
            self.assertTrue(revoker.is_alive())

            gateway._release_authorization(authorization.token_id)
            revoker.join(timeout=1)

            self.assertFalse(revoker.is_alive())
            self.assertIsNone(gateway._authorize(f"Bearer {token.value}"))
            gateway.close()

    def test_development_profile_pins_exact_public_billing_snapshot(self) -> None:
        profile = ModelGatewayProfile.load(
            DEVELOPMENT_PROFILE_PATH, repository_root=REPOSITORY_ROOT
        )
        self.assertEqual(profile.status, "development")
        self.assertEqual(
            profile.rate_card.uncached_input_usd_nanos_per_million,
            80_000_000,
        )
        self.assertEqual(
            profile.rate_card.cached_input_usd_nanos_per_million,
            16_000_000,
        )
        self.assertEqual(
            profile.rate_card.output_usd_nanos_per_million,
            180_000_000,
        )
        self.assertTrue(profile.billing_catalog_digest.startswith("sha256:"))

    def test_profile_transitively_pins_model_route_and_synthetic_rates(self) -> None:
        profile = ModelGatewayProfile.load(
            PROFILE_PATH, repository_root=REPOSITORY_ROOT
        )
        self.assertEqual(profile.status, "conformance_only")
        self.assertEqual(
            profile.requested_model, "deepseek/deepseek-v4-flash-0731"
        )
        self.assertEqual(profile.expected_provider, "DeepInfra")
        self.assertNotIn("expected", profile.provider_request)
        self.assertTrue(profile.resolved_digest.startswith("sha256:"))
        self.assertTrue(profile.model_profile_digest.startswith("sha256:"))

    def test_profile_rejects_unknown_fields(self) -> None:
        payload = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        payload["unexpected"] = True
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "gateway-profile.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"unknown=\['unexpected'\]"):
                ModelGatewayProfile.load(
                    source, repository_root=REPOSITORY_ROOT
                )

    @unittest.skipUnless(
        os.environ.get("RUN_MODEL_GATEWAY_INTEGRATION") == "1",
        "set RUN_MODEL_GATEWAY_INTEGRATION=1 to run the loopback gateway",
    )
    def test_session_lifecycle_routing_settlement_and_cutoff(self) -> None:
        profile = ModelGatewayProfile.load(
            PROFILE_PATH, repository_root=REPOSITORY_ROOT
        )
        with tempfile.TemporaryDirectory() as directory:
            account = SqliteBudgetAccount(
                Path(directory) / "budget.sqlite3", profile.rate_card
            )
            account.open_campaign(
                "gateway-run",
                10_000_000,
                (
                    ActorBudgetAllocation(
                        "gateway-run", "gateway-run:actor:0", 5_000_000
                    ),
                    ActorBudgetAllocation(
                        "gateway-run", "gateway-run:actor:1", 5_000_000
                    ),
                ),
            )
            upstream = _Upstream()
            gateway = ModelBudgetGateway(profile, account, upstream)
            try:
                token = gateway.issue(
                    campaign_run_id="gateway-run",
                    actor_id="gateway-run:actor:0",
                    model_endpoint=gateway.endpoint,
                )
                pending_status, _ = self._request(gateway, token.value)
                self.assertEqual(pending_status, 403)
                gateway.activate(token.token_id, SessionHandle("gateway-session"))
                status, response = self._request(gateway, token.value)
                self.assertEqual(status, 200)
                self.assertIn(b"GATEWAY_OK", response)
                self.assertEqual(len(upstream.requests), 1)
                effective = upstream.requests[0]
                self.assertEqual(effective["model"], profile.requested_model)
                self.assertEqual(
                    effective["provider"],
                    {
                        "allow_fallbacks": False,
                        "data_collection": "deny",
                        "only": ["DeepInfra"],
                        "order": ["DeepInfra"],
                        "require_parameters": True,
                        "zdr": True,
                    },
                )
                self.assertEqual(effective["reasoning_effort"], "low")
                snapshot = account.snapshot("gateway-run")
                self.assertEqual(snapshot.organisation_reserved_usd_nanos, 0)
                self.assertEqual(snapshot.organisation_charged_usd_nanos, 107_500)
                self.assertEqual(snapshot.actors[0].charged_usd_nanos, 107_500)
                self.assertEqual(snapshot.actors[1].charged_usd_nanos, 0)
                self.assertEqual(len(snapshot.charges), 1)
                self.assertEqual(
                    snapshot.charges[0].usage.returned_model,
                    profile.expected_returned_model,
                )
                self.assertEqual(
                    snapshot.charges[0].usage.provider_name,
                    profile.expected_provider,
                )

                gateway.revoke(token.token_id, "session complete")
                revoked_status, _ = self._request(gateway, token.value)
                self.assertEqual(revoked_status, 403)
                self.assertEqual(len(upstream.requests), 1)

                account.open_campaign(
                    "cutoff-run",
                    1_000_000,
                    (
                        ActorBudgetAllocation(
                            "cutoff-run", "cutoff-run:actor:0", 1_000_000
                        ),
                    ),
                )
                cutoff = gateway.issue(
                    campaign_run_id="cutoff-run",
                    actor_id="cutoff-run:actor:0",
                    model_endpoint=gateway.endpoint,
                )
                gateway.activate(cutoff.token_id, SessionHandle("cutoff-session"))
                cutoff_status, cutoff_body = self._request(gateway, cutoff.value)
                self.assertEqual(cutoff_status, 402)
                self.assertIn(b"model budget exhausted", cutoff_body)
                self.assertEqual(len(upstream.requests), 1)

                account.open_campaign(
                    "drift-run",
                    100_000_000,
                    (
                        ActorBudgetAllocation(
                            "drift-run", "drift-run:actor:0", 100_000_000
                        ),
                    ),
                )
                drift = gateway.issue(
                    campaign_run_id="drift-run",
                    actor_id="drift-run:actor:0",
                    model_endpoint=gateway.endpoint,
                )
                gateway.activate(drift.token_id, SessionHandle("drift-session"))
                upstream.provider_name = "Unexpected Provider"
                drift_status, _ = self._request(gateway, drift.value)
                self.assertEqual(drift_status, 200)
                drift_snapshot = account.snapshot("drift-run")
                self.assertEqual(len(drift_snapshot.charges), 0)
                forfeits = [
                    event
                    for event in drift_snapshot.audit_events
                    if event["kind"] == "reservation.forfeited"
                ]
                self.assertEqual(len(forfeits), 1)
                self.assertIn("route identity", forfeits[0]["details"]["reason"])
            finally:
                gateway.close()

    @staticmethod
    def _request(
        gateway: ModelBudgetGateway, token: str
    ) -> tuple[int, bytes]:
        host, port_text = gateway.endpoint.removeprefix("http://").split("/")[0].split(":")
        connection = http.client.HTTPConnection(host, int(port_text), timeout=10)
        body = json.dumps(
            {
                "model": "deepseek/deepseek-v4-flash-0731",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
                "max_tokens": 50,
                "provider": {"only": ["Spoofed Provider"]},
                "reasoning_effort": "spoofed",
            }
        ).encode()
        connection.request(
            "POST",
            "/v1/chat/completions",
            body=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-Actor-ID": "spoofed-actor",
            },
        )
        response = connection.getresponse()
        content = response.read()
        status = response.status
        connection.close()
        return status, content


if __name__ == "__main__":
    unittest.main()
