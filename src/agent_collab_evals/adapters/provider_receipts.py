"""Independent provider receipt verification for budget reconciliation."""

from __future__ import annotations

import json
from decimal import ROUND_CEILING, Decimal
from typing import Any, Mapping

from ..budget import ProviderUsage, USD_NANOS_PER_USD
from ..canonical import digest_value
from ..model_gateway import ModelGatewayProfile


class OpenRouterReceiptVerifier:
    """Reconstruct usage and cost from raw OpenRouter stream and metadata bytes."""

    def __init__(
        self,
        profile: ModelGatewayProfile,
        *,
        require_metadata_receipt: bool = True,
    ) -> None:
        self._profile = profile
        self._require_metadata_receipt = require_metadata_receipt
        self._profile_digest = digest_value(
            {
                "adapter": "openrouter-receipt-verifier/v1",
                "gateway_profile_digest": profile.resolved_digest,
                "require_metadata_receipt": require_metadata_receipt,
            }
        )

    @property
    def profile_digest(self) -> str:
        return self._profile_digest

    def verify(
        self, raw_receipt: bytes, raw_metadata_receipt: bytes
    ) -> ProviderUsage:
        if not raw_receipt:
            raise ValueError("provider stream receipt is missing")
        usage_payload: Mapping[str, Any] | None = None
        returned_model: str | None = None
        stream_id: str | None = None
        fingerprint: str | None = None
        provider_timestamp: str | None = None
        stream_provider: str | None = None
        for line in raw_receipt.splitlines():
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if not data or data == b"[DONE]":
                continue
            payload = self._mapping(self._parse(data), "provider stream event")
            returned_model = self._optional_string(payload.get("model")) or returned_model
            stream_id = self._optional_string(payload.get("id")) or stream_id
            fingerprint = (
                self._optional_string(payload.get("system_fingerprint"))
                or fingerprint
            )
            stream_provider = (
                self._optional_string(payload.get("provider")) or stream_provider
            )
            if type(payload.get("created")) is int:
                provider_timestamp = str(payload["created"])
            if isinstance(payload.get("usage"), dict):
                usage_payload = self._mapping(payload["usage"], "stream usage")
        if usage_payload is None:
            raise ValueError("provider stream omitted its usage receipt")
        if returned_model != self._profile.expected_returned_model:
            raise ValueError("provider stream model identity differs")
        if stream_provider not in {None, self._profile.expected_provider}:
            raise ValueError("provider stream route identity differs")

        prompt_tokens = self._integer(usage_payload, "prompt_tokens")
        completion_tokens = self._integer(usage_payload, "completion_tokens")
        cached_tokens = 0
        details = usage_payload.get("prompt_tokens_details")
        if details is not None:
            cached_tokens = self._integer(
                self._mapping(details, "stream cached-token details"),
                "cached_tokens",
                default=0,
            )
        metadata_model: str | None = None
        provider_name = stream_provider or self._profile.expected_provider
        provider_request_id = stream_id
        generation_id: str | None = None
        provider_cost: int | None = None
        if raw_metadata_receipt:
            metadata = self._mapping(
                self._parse(raw_metadata_receipt), "provider metadata receipt"
            )
            data = self._mapping(metadata.get("data"), "provider metadata data")
            generation_id = self._optional_string(data.get("id"))
            if generation_id is None or generation_id != stream_id:
                raise ValueError("provider metadata does not match the stream")
            metadata_model = self._optional_string(data.get("model"))
            if metadata_model != self._profile.expected_metadata_model:
                raise ValueError("provider metadata model identity differs")
            provider_name = self._optional_string(data.get("provider_name"))
            if provider_name != self._profile.expected_provider:
                raise ValueError("provider metadata route identity differs")
            provider_request_id = (
                self._optional_string(data.get("request_id")) or provider_request_id
            )
            provider_timestamp = (
                self._optional_string(data.get("created_at")) or provider_timestamp
            )
            if data.get("streamed") is not True:
                raise ValueError("provider metadata does not identify a stream")
            prompt_tokens = self._integer(data, "native_tokens_prompt")
            completion_tokens = self._integer(data, "native_tokens_completion")
            cached_tokens = self._integer(
                data, "native_tokens_cached", default=0
            )
            provider_cost = self._usd_nanos(data.get("total_cost"))
            stream_cost = usage_payload.get("cost")
            if stream_cost is not None and self._usd_nanos(stream_cost) != provider_cost:
                raise ValueError("stream and metadata costs differ")
        elif self._require_metadata_receipt:
            raise ValueError("provider metadata receipt is missing")

        return ProviderUsage(
            requested_model=self._profile.requested_model,
            returned_model=returned_model,
            metadata_model=metadata_model,
            provider_name=provider_name,
            provider_request_id=provider_request_id,
            provider_generation_id=generation_id,
            provider_timestamp=provider_timestamp,
            system_fingerprint=fingerprint,
            provider_cost_usd_nanos=provider_cost,
            prompt_tokens=prompt_tokens,
            cached_input_tokens=cached_tokens,
            completion_tokens=completion_tokens,
            raw_receipt=raw_receipt,
            raw_metadata_receipt=raw_metadata_receipt,
        )

    @staticmethod
    def _parse(raw: bytes) -> object:
        return json.loads(
            raw.decode("utf-8"),
            parse_float=Decimal,
            object_pairs_hook=OpenRouterReceiptVerifier._unique_object,
        )

    @staticmethod
    def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"provider receipt repeats key {key}")
            value[key] = item
        return value

    @staticmethod
    def _mapping(value: object, label: str) -> Mapping[str, Any]:
        if not isinstance(value, dict):
            raise ValueError(f"{label} must be an object")
        return value

    @staticmethod
    def _integer(
        value: Mapping[str, Any], key: str, *, default: int | None = None
    ) -> int:
        item = value.get(key, default)
        if type(item) is not int or item < 0:
            raise ValueError(f"provider receipt {key} is missing or invalid")
        return item

    @staticmethod
    def _optional_string(value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _usd_nanos(value: object) -> int:
        if type(value) is int:
            decimal = Decimal(value)
        elif isinstance(value, Decimal):
            decimal = value
        else:
            raise ValueError("provider receipt cost is missing or invalid")
        if not decimal.is_finite() or decimal < 0:
            raise ValueError("provider receipt cost is missing or invalid")
        return int(
            (decimal * Decimal(USD_NANOS_PER_USD)).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
