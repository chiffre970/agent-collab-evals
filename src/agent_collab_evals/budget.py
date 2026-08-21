"""Provider-neutral fixed-point model-budget values."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .canonical import digest_bytes, digest_value


TOKENS_PER_MILLION = 1_000_000
USD_NANOS_PER_USD = 1_000_000_000


def _positive_integer(value: object, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _nonnegative_integer(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


@dataclass(frozen=True, slots=True)
class GatewayAccessToken:
    """Opaque, revocable credential scoped to one top-level model session."""

    token_id: str
    value: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class BillingRateCard:
    """One immutable USD price tier expressed as nanodollars per million tokens."""

    catalog_id: str
    catalog_digest: str
    schedule_version: str
    effective_tier: str
    uncached_input_usd_nanos_per_million: int
    cached_input_usd_nanos_per_million: int
    output_usd_nanos_per_million: int

    def __post_init__(self) -> None:
        for label in (
            "catalog_id",
            "catalog_digest",
            "schedule_version",
            "effective_tier",
        ):
            if not getattr(self, label):
                raise ValueError(f"{label} must be nonempty")
        for label in (
            "uncached_input_usd_nanos_per_million",
            "cached_input_usd_nanos_per_million",
            "output_usd_nanos_per_million",
        ):
            _nonnegative_integer(getattr(self, label), label)

    def reserve_maximum(self, input_token_upper: int, output_token_upper: int) -> int:
        input_tokens = _nonnegative_integer(input_token_upper, "input token upper bound")
        output_tokens = _positive_integer(output_token_upper, "output token upper bound")
        input_rate = max(
            self.uncached_input_usd_nanos_per_million,
            self.cached_input_usd_nanos_per_million,
        )
        return max(
            1,
            _ceil_div(
                input_tokens * input_rate
                + output_tokens * self.output_usd_nanos_per_million,
                TOKENS_PER_MILLION,
            ),
        )

    def charge(self, usage: "ProviderUsage") -> int:
        if usage.provider_cost_usd_nanos is not None:
            return usage.provider_cost_usd_nanos
        uncached_tokens = usage.prompt_tokens - usage.cached_input_tokens
        return max(
            1,
            _ceil_div(
                uncached_tokens * self.uncached_input_usd_nanos_per_million
                + usage.cached_input_tokens
                * self.cached_input_usd_nanos_per_million
                + usage.completion_tokens * self.output_usd_nanos_per_million,
                TOKENS_PER_MILLION,
            ),
        )


@dataclass(frozen=True, slots=True)
class ActorBudgetAllocation:
    campaign_run_id: str
    actor_id: str
    limit_usd_nanos: int

    def __post_init__(self) -> None:
        if not self.campaign_run_id or not self.actor_id:
            raise ValueError("budget allocation identity must be nonempty")
        _positive_integer(self.limit_usd_nanos, "actor budget limit")


@dataclass(frozen=True, slots=True)
class ModelCallContext:
    call_id: str
    request_digest: str
    requested_model: str
    input_token_upper: int
    output_token_upper: int

    def __post_init__(self) -> None:
        if not self.call_id or not self.request_digest or not self.requested_model:
            raise ValueError("model call context strings must be nonempty")
        _nonnegative_integer(self.input_token_upper, "input token upper bound")
        _positive_integer(self.output_token_upper, "output token upper bound")


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    requested_model: str
    returned_model: str | None
    metadata_model: str | None
    provider_name: str | None
    provider_request_id: str | None
    provider_generation_id: str | None
    provider_timestamp: str | None
    system_fingerprint: str | None
    provider_cost_usd_nanos: int | None
    prompt_tokens: int
    cached_input_tokens: int
    completion_tokens: int
    raw_receipt: bytes = field(repr=False)
    raw_metadata_receipt: bytes = field(default=b"", repr=False)

    def __post_init__(self) -> None:
        if not self.requested_model:
            raise ValueError("requested model must be nonempty")
        _nonnegative_integer(self.prompt_tokens, "prompt tokens")
        _nonnegative_integer(self.cached_input_tokens, "cached input tokens")
        _nonnegative_integer(self.completion_tokens, "completion tokens")
        if self.provider_cost_usd_nanos is not None:
            _nonnegative_integer(
                self.provider_cost_usd_nanos,
                "provider cost in USD nanodollars",
            )
        if self.cached_input_tokens > self.prompt_tokens:
            raise ValueError("cached input tokens cannot exceed prompt tokens")
        if not self.raw_receipt:
            raise ValueError("raw provider receipt must be retained")

    @property
    def receipt_digest(self) -> str:
        return digest_bytes(self.raw_receipt)

    @property
    def metadata_receipt_digest(self) -> str | None:
        if not self.raw_metadata_receipt:
            return None
        return digest_bytes(self.raw_metadata_receipt)

    @property
    def usage_digest(self) -> str:
        return digest_value(
            {
                "requested_model": self.requested_model,
                "returned_model": self.returned_model,
                "metadata_model": self.metadata_model,
                "provider_name": self.provider_name,
                "provider_request_id": self.provider_request_id,
                "provider_generation_id": self.provider_generation_id,
                "provider_timestamp": self.provider_timestamp,
                "system_fingerprint": self.system_fingerprint,
                "provider_cost_usd_nanos": self.provider_cost_usd_nanos,
                "prompt_tokens": self.prompt_tokens,
                "cached_input_tokens": self.cached_input_tokens,
                "completion_tokens": self.completion_tokens,
                "receipt_digest": self.receipt_digest,
                "metadata_receipt_digest": self.metadata_receipt_digest,
            }
        )


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    reservation_id: str
    campaign_run_id: str
    actor_id: str
    call_id: str
    maximum_usd_nanos: int


@dataclass(frozen=True, slots=True)
class BudgetRejected:
    campaign_run_id: str
    actor_id: str
    call_id: str
    reason: str
    required_usd_nanos: int
    actor_remaining_usd_nanos: int
    organisation_remaining_usd_nanos: int


@dataclass(frozen=True, slots=True)
class BudgetCharge:
    reservation_id: str
    campaign_run_id: str
    actor_id: str
    call_id: str
    charged_usd_nanos: int
    usage: ProviderUsage
    rate_card_digest: str


@dataclass(frozen=True, slots=True)
class ActorBudgetSnapshot:
    actor_id: str
    limit_usd_nanos: int
    reserved_usd_nanos: int
    charged_usd_nanos: int

    @property
    def remaining_usd_nanos(self) -> int:
        return self.limit_usd_nanos - self.reserved_usd_nanos - self.charged_usd_nanos


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    campaign_run_id: str
    organisation_limit_usd_nanos: int
    organisation_reserved_usd_nanos: int
    organisation_charged_usd_nanos: int
    actors: tuple[ActorBudgetSnapshot, ...]
    charges: tuple[BudgetCharge, ...]
    audit_events: tuple[Mapping[str, object], ...]

    @property
    def organisation_remaining_usd_nanos(self) -> int:
        return (
            self.organisation_limit_usd_nanos
            - self.organisation_reserved_usd_nanos
            - self.organisation_charged_usd_nanos
        )
