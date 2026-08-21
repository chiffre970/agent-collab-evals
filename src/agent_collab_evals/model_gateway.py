"""Session-scoped OpenAI-compatible model budget gateway."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol
from urllib.parse import urlsplit

from .budget import (
    BillingRateCard,
    BudgetRejected,
    GatewayAccessToken,
    ModelCallContext,
    ProviderUsage,
    USD_NANOS_PER_USD,
)
from .canonical import (
    canonical_json_bytes,
    digest_bytes,
    digest_file,
    digest_value,
    load_json,
    parse_json,
)
from .domain import SessionHandle
from .ports import BudgetAccount


@dataclass(frozen=True, slots=True)
class ModelGatewayProfile:
    profile_id: str
    status: str
    requested_model: str
    expected_returned_model: str
    expected_metadata_model: str
    expected_provider: str
    upstream_endpoint: str
    client_app_title: str
    provider_request: Mapping[str, object]
    inference_request: Mapping[str, object]
    max_body_bytes: int
    max_response_bytes: int
    input_token_overhead: int
    default_max_output_tokens: int
    cache_policy: str
    rate_card: BillingRateCard
    source_digest: str
    model_profile_digest: str
    resolved_digest: str

    @classmethod
    def load(
        cls, source: Path, *, repository_root: Path
    ) -> "ModelGatewayProfile":
        with source.open("r", encoding="utf-8") as stream:
            payload = load_json(stream)
        cls._require_keys(
            payload,
            {
                "schema_version",
                "profile_id",
                "status",
                "model_profile",
                "request_limits",
                "billing",
                "cache_policy",
            },
            "gateway profile",
        )
        if payload["schema_version"] != "model-gateway-profile/v1":
            raise ValueError("unsupported model gateway profile schema")
        if payload["status"] not in {"conformance_only", "development", "registered"}:
            raise ValueError("unsupported model gateway profile status")
        model_path = repository_root / str(payload["model_profile"])
        if not model_path.is_file():
            raise ValueError("model gateway profile references a missing model profile")
        with model_path.open("r", encoding="utf-8") as stream:
            model = load_json(stream)
        cls._require_keys(
            model,
            {
                "schema_version",
                "profile_id",
                "status",
                "transport",
                "endpoint",
                "requested_model",
                "expected_stream_model",
                "expected_metadata_model",
                "provider",
                "inference",
                "preflight",
                "client",
            },
            "model profile",
        )
        provider = cls._mapping(model["provider"], "model provider")
        cls._require_keys(
            provider,
            {
                "order",
                "only",
                "expected",
                "allow_fallbacks",
                "data_collection",
                "require_parameters",
                "zdr",
            },
            "model provider",
        )
        inference = cls._mapping(model["inference"], "model inference")
        client = cls._mapping(model["client"], "model client")
        cls._require_keys(client, {"app_title"}, "model client")
        endpoint = urlsplit(str(model["endpoint"]))
        if (
            model["transport"] != "openrouter"
            or endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("model profile must use a credential-free HTTPS OpenRouter endpoint")
        if not isinstance(client["app_title"], str) or not client["app_title"]:
            raise ValueError("model client application title must be nonempty")
        limits = cls._mapping(payload["request_limits"], "request limits")
        cls._require_keys(
            limits,
            {
                "max_body_bytes",
                "max_response_bytes",
                "input_token_overhead",
                "default_max_output_tokens",
            },
            "request limits",
        )
        billing = cls._mapping(payload["billing"], "billing")
        cls._require_keys(
            billing,
            {
                "catalog_id",
                "schedule_version",
                "effective_tier",
                "uncached_input_usd_nanos_per_million",
                "cached_input_usd_nanos_per_million",
                "output_usd_nanos_per_million",
            },
            "billing",
        )
        for name in (
            "max_body_bytes",
            "max_response_bytes",
            "input_token_overhead",
            "default_max_output_tokens",
        ):
            if type(limits[name]) is not int or int(limits[name]) < 1:
                raise ValueError(f"request_limits.{name} must be a positive integer")
        if payload["cache_policy"] not in {
            "disabled",
            "actor_run_scoped",
            "provider_managed_observed",
        }:
            raise ValueError("unsupported provider cache policy")
        billing_digest = digest_value(billing)
        rate_card = BillingRateCard(
            catalog_id=str(billing["catalog_id"]),
            catalog_digest=billing_digest,
            schedule_version=str(billing["schedule_version"]),
            effective_tier=str(billing["effective_tier"]),
            uncached_input_usd_nanos_per_million=billing[
                "uncached_input_usd_nanos_per_million"
            ],
            cached_input_usd_nanos_per_million=billing[
                "cached_input_usd_nanos_per_million"
            ],
            output_usd_nanos_per_million=billing[
                "output_usd_nanos_per_million"
            ],
        )
        source_digest = digest_file(source)
        model_digest = digest_file(model_path)
        resolved_digest = digest_value(
            {
                "gateway_profile": payload,
                "gateway_profile_digest": source_digest,
                "model_profile_digest": model_digest,
            }
        )
        return cls(
            profile_id=str(payload["profile_id"]),
            status=str(payload["status"]),
            requested_model=str(model["requested_model"]),
            expected_returned_model=str(model["expected_stream_model"]),
            expected_metadata_model=str(model["expected_metadata_model"]),
            expected_provider=str(provider["expected"]),
            upstream_endpoint=str(model["endpoint"]),
            client_app_title=str(client["app_title"]),
            provider_request={
                key: value for key, value in provider.items() if key != "expected"
            },
            inference_request=dict(inference),
            max_body_bytes=int(limits["max_body_bytes"]),
            max_response_bytes=int(limits["max_response_bytes"]),
            input_token_overhead=int(limits["input_token_overhead"]),
            default_max_output_tokens=int(limits["default_max_output_tokens"]),
            cache_policy=str(payload["cache_policy"]),
            rate_card=rate_card,
            source_digest=source_digest,
            model_profile_digest=model_digest,
            resolved_digest=resolved_digest,
        )

    @staticmethod
    def _mapping(value: object, label: str) -> Mapping[str, Any]:
        if not isinstance(value, dict):
            raise ValueError(f"{label} must be an object")
        return value

    @classmethod
    def _require_keys(
        cls, value: object, expected: set[str], label: str
    ) -> None:
        mapping = cls._mapping(value, label)
        if set(mapping) != expected:
            missing = sorted(expected - set(mapping))
            unknown = sorted(set(mapping) - expected)
            raise ValueError(
                f"{label} keys differ; missing={missing}, unknown={unknown}"
            )


@dataclass(frozen=True, slots=True)
class UpstreamStream:
    status: int
    headers: Mapping[str, str]
    chunks: Iterable[bytes]
    provider_name: str | None = None
    metadata_receipt: Callable[[], bytes] | None = None


class ModelUpstream(Protocol):
    def stream(self, request: bytes) -> UpstreamStream: ...


class UpstreamRequestRejected(RuntimeError):
    """An upstream returned a response before a model stream was established."""

    def __init__(
        self,
        status: int,
        raw_response: bytes,
        *,
        definitely_unstarted: bool,
    ) -> None:
        super().__init__(f"model upstream rejected the request with HTTP {status}")
        self.status = status
        self.raw_response = raw_response
        self.definitely_unstarted = definitely_unstarted


def _unique_request_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate model request key: {key}")
        result[key] = value
    return result


def _parse_request(value: bytes) -> object:
    return json.loads(
        value.decode("utf-8"),
        object_pairs_hook=_unique_request_object,
        parse_float=Decimal,
    )


def _request_json(value: object) -> str:
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("model request object keys must be strings")
        return "{" + ",".join(
            f"{json.dumps(key, ensure_ascii=False)}:{_request_json(value[key])}"
            for key in sorted(value)
        ) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_request_json(item) for item in value) + "]"
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("model request numbers must be finite")
        return str(value)
    if value is None or isinstance(value, (str, int, bool)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    raise ValueError(f"unsupported model request value: {type(value).__name__}")


def _request_json_bytes(value: object) -> bytes:
    return _request_json(value).encode("utf-8")


@dataclass(slots=True)
class _GatewayAccessState:
    token_id: str
    campaign_run_id: str
    actor_id: str
    session: SessionHandle | None = None


class ModelBudgetGateway:
    """Authenticate OpenCode sessions, reserve spend, and proxy model streams."""

    def __init__(
        self,
        profile: ModelGatewayProfile,
        account: BudgetAccount,
        upstream: ModelUpstream,
        *,
        serve_http: bool = True,
    ) -> None:
        if account.rate_card_digest != digest_value(profile.rate_card):
            raise ValueError("budget account and gateway rate cards differ")
        self._profile = profile
        self._account = account
        self._upstream = upstream
        self._lock = threading.RLock()
        self._tokens: dict[str, _GatewayAccessState] = {}
        self._token_ids: dict[str, str] = {}
        self._sessions: dict[str, str] = {}
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
                gateway._handle(self)

            def log_message(self, format: str, *args: object) -> None:
                return

        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        if serve_http:
            self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            self._server.daemon_threads = True
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name="model-budget-gateway",
                daemon=True,
            )
            self._thread.start()

    @property
    def endpoint(self) -> str:
        if self._server is None:
            return "http://model-gateway.invalid/v1"
        host, port = self._server.server_address
        return f"http://{host}:{port}/v1"

    @property
    def profile_digest(self) -> str:
        return self._profile.resolved_digest

    def issue(
        self,
        *,
        campaign_run_id: str,
        actor_id: str,
        model_endpoint: str,
    ) -> GatewayAccessToken:
        if model_endpoint != self.endpoint:
            raise ValueError("OpenCode model endpoint is not this budget gateway")
        if not self._account.has_actor(campaign_run_id, actor_id):
            raise PermissionError("model budget actor is not provisioned")
        token_id = f"model-token-{secrets.token_hex(12)}"
        token = secrets.token_urlsafe(32)
        token_digest = self._token_digest(token)
        with self._lock:
            self._tokens[token_digest] = _GatewayAccessState(
                token_id, campaign_run_id, actor_id
            )
            self._token_ids[token_id] = token_digest
        return GatewayAccessToken(token_id, token)

    def activate(self, token_id: str, session: SessionHandle) -> None:
        with self._lock:
            token_digest = self._token_ids.get(token_id)
            if token_digest is None:
                raise PermissionError("model gateway token is unknown")
            state = self._tokens[token_digest]
            if state.session is not None:
                raise ValueError("model gateway token is already active")
            if session.value in self._sessions:
                raise ValueError("OpenCode session already has a model gateway token")
            state.session = session
            self._sessions[session.value] = token_id

    def revoke(self, token_id: str, reason: str) -> None:
        if not reason:
            raise ValueError("model gateway revocation reason must be nonempty")
        with self._lock:
            token_digest = self._token_ids.pop(token_id, None)
            if token_digest is None:
                return
            state = self._tokens.pop(token_digest)
            if state.session is not None:
                self._sessions.pop(state.session.value, None)

    def close(self) -> None:
        with self._lock:
            self._tokens.clear()
            self._token_ids.clear()
            self._sessions.clear()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        if handler.path != "/v1/chat/completions":
            self._send_json(handler, 404, {"error": "unknown model gateway endpoint"})
            return
        state = self._authorize(handler.headers.get("Authorization", ""))
        if state is None:
            self._send_json(handler, 403, {"error": "model gateway access denied"})
            return
        length = self._content_length(handler)
        if length is None:
            self._send_json(handler, 400, {"error": "invalid request length"})
            return
        try:
            raw_request = handler.rfile.read(length)
            effective_request, output_upper = self._effective_request(raw_request)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            self._send_json(handler, 400, {"error": str(error)})
            return
        call_id = f"call-{secrets.token_hex(16)}"
        context = ModelCallContext(
            call_id,
            digest_bytes(effective_request),
            self._profile.requested_model,
            len(effective_request) + self._profile.input_token_overhead,
            output_upper,
        )
        reservation = self._account.reserve(
            state.campaign_run_id, state.actor_id, context
        )
        if isinstance(reservation, BudgetRejected):
            self._send_json(
                handler,
                402,
                {
                    "error": "model budget exhausted",
                    "reason": reservation.reason,
                    "required_usd_nanos": reservation.required_usd_nanos,
                    "actor_remaining_usd_nanos": reservation.actor_remaining_usd_nanos,
                },
            )
            return
        try:
            upstream = self._upstream.stream(effective_request)
        except UpstreamRequestRejected as error:
            if error.definitely_unstarted:
                self._account.release(
                    reservation.reservation_id,
                    f"upstream rejected request with HTTP {error.status}",
                )
            else:
                self._account.forfeit(
                    reservation.reservation_id,
                    f"ambiguous upstream HTTP {error.status}",
                    error.raw_response or str(error).encode("utf-8"),
                )
            self._send_json(
                handler,
                error.status if 400 <= error.status < 500 else 502,
                {"error": "model upstream rejected the request"},
            )
            return
        except Exception as error:
            self._account.forfeit(
                reservation.reservation_id,
                "upstream outcome unknown",
                str(error).encode("utf-8") or b"unknown upstream error",
            )
            self._send_json(handler, 502, {"error": "model upstream failed"})
            return

        self._send_stream_headers(handler, upstream)
        response = bytearray()
        try:
            for chunk in upstream.chunks:
                if not isinstance(chunk, bytes):
                    raise TypeError("model upstream emitted a non-byte chunk")
                if len(chunk) > self._profile.max_response_bytes - len(response):
                    raise ValueError("model upstream exceeded its response bound")
                response.extend(chunk)
                handler.wfile.write(chunk)
                handler.wfile.flush()
        except Exception as error:
            response.extend(f"\n[gateway-stream-error:{type(error).__name__}]".encode())
            self._account.forfeit(
                reservation.reservation_id,
                "upstream stream incomplete",
                bytes(response),
            )
            return

        try:
            usage = self._provider_usage(bytes(response), upstream)
        except Exception as error:
            self._account.forfeit(
                reservation.reservation_id,
                f"provider usage missing or invalid: {str(error)[:256]}",
                bytes(response) or b"empty upstream response",
            )
            return
        try:
            self._account.settle(reservation.reservation_id, usage)
        except RuntimeError:
            # The account has durably recorded the reservation overrun. The
            # caller cannot be retroactively unserved, but the run must fail.
            return

    def _effective_request(self, raw_request: bytes) -> tuple[bytes, int]:
        if not 1 <= len(raw_request) <= self._profile.max_body_bytes:
            raise ValueError("model request body exceeds its configured bound")
        payload = _parse_request(raw_request)
        if not isinstance(payload, dict):
            raise ValueError("model request must be an object")
        if payload.get("model") != self._profile.requested_model:
            raise ValueError("requested model differs from the gateway profile")
        if payload.get("stream") is not True:
            raise ValueError("V0 model gateway requires streaming responses")
        values = [
            payload.get("max_tokens"),
            payload.get("max_completion_tokens"),
        ]
        specified = [value for value in values if value is not None]
        if len(specified) > 1:
            raise ValueError("model request supplies two output-token limits")
        output_upper = (
            specified[0]
            if specified
            else self._profile.default_max_output_tokens
        )
        if (
            type(output_upper) is not int
            or output_upper < 1
            or output_upper > self._profile.default_max_output_tokens
        ):
            raise ValueError("model output-token limit exceeds the gateway profile")
        payload["model"] = self._profile.requested_model
        payload["provider"] = dict(self._profile.provider_request)
        payload.update(self._profile.inference_request)
        return _request_json_bytes(payload), output_upper

    def _provider_usage(
        self, raw_response: bytes, upstream: UpstreamStream
    ) -> ProviderUsage:
        receipt: Mapping[str, Any] | None = None
        returned_model: str | None = None
        request_id: str | None = None
        fingerprint: str | None = None
        provider_timestamp: str | None = None
        for line in raw_response.splitlines():
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if not data or data == b"[DONE]":
                continue
            payload = parse_json(data.decode("utf-8"))
            if not isinstance(payload, dict):
                continue
            returned_model = self._optional_string(payload.get("model")) or returned_model
            request_id = self._optional_string(payload.get("id")) or request_id
            fingerprint = (
                self._optional_string(payload.get("system_fingerprint")) or fingerprint
            )
            created = payload.get("created")
            if type(created) is int:
                provider_timestamp = str(created)
            if isinstance(payload.get("usage"), dict):
                receipt = payload
        if receipt is None:
            raise ValueError("provider stream omitted its usage receipt")
        if returned_model != self._profile.expected_returned_model:
            raise ValueError("provider returned an unexpected model identity")
        usage = receipt["usage"]
        assert isinstance(usage, dict)
        prompt_tokens = self._usage_integer(usage, "prompt_tokens")
        completion_tokens = self._usage_integer(usage, "completion_tokens")
        details = usage.get("prompt_tokens_details")
        cached_tokens = 0
        if details is not None:
            if not isinstance(details, dict):
                raise ValueError("provider cached-token details are invalid")
            cached_tokens = self._usage_integer(details, "cached_tokens", default=0)
        metadata_model: str | None = None
        generation_id: str | None = None
        raw_metadata = b""
        provider_cost_usd_nanos: int | None = None
        provider_name = upstream.provider_name
        if upstream.metadata_receipt is not None:
            raw_metadata = upstream.metadata_receipt()
            metadata = _parse_request(raw_metadata)
            if (
                not isinstance(metadata, dict)
                or not isinstance(metadata.get("data"), dict)
            ):
                raise ValueError("provider generation metadata is invalid")
            data = metadata["data"]
            assert isinstance(data, dict)
            generation_id = self._optional_string(data.get("id"))
            if generation_id is None or generation_id != request_id:
                raise ValueError(
                    "provider generation metadata does not match the stream"
                )
            metadata_model = self._optional_string(data.get("model"))
            if metadata_model != self._profile.expected_metadata_model:
                raise ValueError(
                    "provider metadata returned an unexpected model identity"
                )
            provider_name = self._optional_string(data.get("provider_name"))
            provider_request_id = self._optional_string(data.get("request_id"))
            if provider_request_id is not None:
                request_id = provider_request_id
            provider_timestamp = (
                self._optional_string(data.get("created_at")) or provider_timestamp
            )
            prompt_tokens = self._usage_integer(data, "native_tokens_prompt")
            completion_tokens = self._usage_integer(
                data, "native_tokens_completion"
            )
            cached_tokens = self._usage_integer(
                data, "native_tokens_cached", default=0
            )
            if data.get("streamed") is not True:
                raise ValueError(
                    "provider metadata does not identify a streamed request"
                )
            provider_cost_usd_nanos = self._usd_nanos(data.get("total_cost"))
        if provider_name != self._profile.expected_provider:
            raise ValueError("provider returned an unexpected route identity")
        return ProviderUsage(
            requested_model=self._profile.requested_model,
            returned_model=returned_model,
            metadata_model=metadata_model,
            provider_name=provider_name,
            provider_request_id=request_id,
            provider_generation_id=generation_id,
            provider_timestamp=provider_timestamp,
            system_fingerprint=fingerprint,
            provider_cost_usd_nanos=provider_cost_usd_nanos,
            prompt_tokens=prompt_tokens,
            cached_input_tokens=cached_tokens,
            completion_tokens=completion_tokens,
            raw_receipt=raw_response,
            raw_metadata_receipt=raw_metadata,
        )

    def _authorize(self, authorization: str) -> _GatewayAccessState | None:
        if not authorization.startswith("Bearer "):
            return None
        token_digest = self._token_digest(authorization[7:])
        with self._lock:
            state = self._tokens.get(token_digest)
            if state is None or state.session is None:
                return None
            return _GatewayAccessState(
                state.token_id,
                state.campaign_run_id,
                state.actor_id,
                state.session,
            )

    def _content_length(self, handler: BaseHTTPRequestHandler) -> int | None:
        try:
            length = int(handler.headers.get("Content-Length", ""))
        except ValueError:
            return None
        if not 1 <= length <= self._profile.max_body_bytes:
            return None
        return length

    @staticmethod
    def _usage_integer(
        value: Mapping[str, Any], key: str, *, default: int | None = None
    ) -> int:
        item = value.get(key, default)
        if type(item) is not int or item < 0:
            raise ValueError(f"provider usage {key} is missing or invalid")
        return item

    @staticmethod
    def _usd_nanos(value: object) -> int:
        if type(value) is int:
            decimal = Decimal(value)
        elif isinstance(value, Decimal):
            decimal = value
        else:
            raise ValueError("provider total cost is missing or invalid")
        if not decimal.is_finite() or decimal < 0:
            raise ValueError("provider total cost is missing or invalid")
        return int(
            (decimal * Decimal(USD_NANOS_PER_USD)).to_integral_value(
                rounding=ROUND_CEILING
            )
        )

    @staticmethod
    def _optional_string(value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _token_digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _send_json(
        handler: BaseHTTPRequestHandler,
        status: int,
        payload: Mapping[str, object],
    ) -> None:
        content = canonical_json_bytes(payload)
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(content)))
        handler.end_headers()
        handler.wfile.write(content)

    @staticmethod
    def _send_stream_headers(
        handler: BaseHTTPRequestHandler, upstream: UpstreamStream
    ) -> None:
        handler.send_response(upstream.status)
        handler.send_header(
            "Content-Type", upstream.headers.get("Content-Type", "text/event-stream")
        )
        handler.send_header("Cache-Control", "no-cache")
        handler.end_headers()
