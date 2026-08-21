from __future__ import annotations

import json
import tempfile
import unittest
from collections import deque
from pathlib import Path

from agent_collab_evals.adapters.openrouter import OpenRouterUpstream
from agent_collab_evals.adapters.sqlite_budget import SqliteBudgetAccount
from agent_collab_evals.model_gateway import (
    ModelBudgetGateway,
    ModelGatewayProfile,
    UpstreamRequestRejected,
    UpstreamStream,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = (
    REPOSITORY_ROOT
    / "config/gateway_profiles/openrouter-deepinfra-local-conformance-v0.json"
)


class _Response:
    def __init__(
        self,
        status: int,
        body: bytes,
        headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.status = status
        self._body = body
        self._offset = 0
        self._headers = headers
        self.closed = False

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self._headers)

    def getheader(self, name: str, default: str | None = None) -> str | None:
        for key, value in self._headers:
            if key.lower() == name.lower():
                return value
        return default

    def read(self, amount: int | None = None) -> bytes:
        if amount is None:
            amount = len(self._body) - self._offset
        result = self._body[self._offset : self._offset + amount]
        self._offset += len(result)
        return result

    def read1(self, amount: int = -1) -> bytes:
        return self.read(None if amount < 0 else amount)

    def close(self) -> None:
        self.closed = True


class _Connection:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.requests: list[tuple[str, str, bytes | None, dict[str, str]]] = []
        self.closed = False

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.requests.append((method, url, body, dict(headers or {})))

    def getresponse(self) -> _Response:
        return self.response

    def close(self) -> None:
        self.closed = True


class _ConnectionFactory:
    def __init__(self, responses: tuple[_Response, ...]) -> None:
        self._responses = deque(responses)
        self.connections: list[_Connection] = []

    def __call__(self, host: str, port: int, timeout: float) -> _Connection:
        if (host, port, timeout) != ("openrouter.ai", 443, 5):
            raise AssertionError("unexpected OpenRouter connection target")
        connection = _Connection(self._responses.popleft())
        self.connections.append(connection)
        return connection


def _stream_bytes() -> bytes:
    first = {
        "id": "gen-test",
        "created": 1_787_300_000,
        "model": "deepseek/deepseek-v4-flash-0731",
        "choices": [{"index": 0, "delta": {"content": "ok"}}],
    }
    final = {
        "id": "gen-test",
        "created": 1_787_300_000,
        "model": "deepseek/deepseek-v4-flash-0731",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 99,
            "completion_tokens": 88,
            "prompt_tokens_details": {"cached_tokens": 0},
        },
    }
    return (
        f"data: {json.dumps(first)}\n\n"
        f"data: {json.dumps(final)}\n\n"
        "data: [DONE]\n\n"
    ).encode()


def _metadata_bytes() -> bytes:
    return json.dumps(
        {
            "data": {
                "id": "gen-test",
                "request_id": "req-test",
                "created_at": "2026-08-21T10:00:00Z",
                "model": "deepseek/deepseek-v4-flash-20260731",
                "provider_name": "DeepInfra",
                "streamed": True,
                "native_tokens_prompt": 11,
                "native_tokens_cached": 3,
                "native_tokens_completion": 2,
                "total_cost": 0.00000124,
            }
        },
        separators=(",", ":"),
    ).encode()


class OpenRouterUpstreamTests(unittest.TestCase):
    def test_streams_bytes_and_retries_generation_receipt(self) -> None:
        factory = _ConnectionFactory(
            (
                _Response(
                    200,
                    _stream_bytes(),
                    (
                        ("Content-Type", "text/event-stream"),
                        ("X-Generation-Id", "gen-test"),
                    ),
                ),
                _Response(404, b'{"error":"not ready"}'),
                _Response(200, _metadata_bytes()),
            )
        )
        upstream = OpenRouterUpstream(
            "https://openrouter.ai/api/v1",
            "secret-test-key",
            "agent-collab-evals",
            timeout_seconds=5,
            metadata_retry_seconds=(0, 0),
            connection_factory=factory,
            sleep=lambda _: None,
        )

        stream = upstream.stream(b'{"stream":true}')
        self.assertEqual(b"".join(stream.chunks), _stream_bytes())
        self.assertIsNotNone(stream.metadata_receipt)
        assert stream.metadata_receipt is not None
        self.assertEqual(stream.metadata_receipt(), _metadata_bytes())

        post = factory.connections[0].requests[0]
        self.assertEqual(post[:3], ("POST", "/api/v1/chat/completions", b'{"stream":true}'))
        self.assertEqual(post[3]["Authorization"], "Bearer secret-test-key")
        self.assertEqual(post[3]["X-Title"], "agent-collab-evals")
        self.assertEqual(
            factory.connections[2].requests[0][1],
            "/api/v1/generation?id=gen-test",
        )

    def test_rejects_nonstream_response_before_returning_upstream(self) -> None:
        factory = _ConnectionFactory((_Response(429, b'{"error":"limited"}'),))
        upstream = OpenRouterUpstream(
            "https://openrouter.ai/api/v1",
            "secret-test-key",
            "agent-collab-evals",
            timeout_seconds=5,
            connection_factory=factory,
        )
        with self.assertRaises(UpstreamRequestRejected) as caught:
            upstream.stream(b"{}")
        self.assertTrue(caught.exception.definitely_unstarted)
        self.assertEqual(caught.exception.raw_response, b'{"error":"limited"}')

    def test_gateway_uses_metadata_identity_and_native_usage(self) -> None:
        profile = ModelGatewayProfile.load(
            PROFILE_PATH, repository_root=REPOSITORY_ROOT
        )
        self.assertEqual(profile.upstream_endpoint, "https://openrouter.ai/api/v1")
        self.assertEqual(profile.client_app_title, "agent-collab-evals")
        with tempfile.TemporaryDirectory() as directory:
            account = SqliteBudgetAccount(
                Path(directory) / "budget.sqlite3", profile.rate_card
            )
            gateway = ModelBudgetGateway(
                profile, account, object(), serve_http=False
            )
            stream = UpstreamStream(
                200, {}, (), metadata_receipt=_metadata_bytes
            )

            usage = gateway._provider_usage(_stream_bytes(), stream)

        self.assertEqual(usage.returned_model, profile.expected_returned_model)
        self.assertEqual(usage.metadata_model, profile.expected_metadata_model)
        self.assertEqual(usage.provider_name, "DeepInfra")
        self.assertEqual(usage.provider_request_id, "req-test")
        self.assertEqual(usage.provider_generation_id, "gen-test")
        self.assertEqual(usage.provider_cost_usd_nanos, 1_240)
        self.assertEqual(
            (usage.prompt_tokens, usage.cached_input_tokens, usage.completion_tokens),
            (11, 3, 2),
        )
        self.assertEqual(usage.raw_metadata_receipt, _metadata_bytes())


if __name__ == "__main__":
    unittest.main()
