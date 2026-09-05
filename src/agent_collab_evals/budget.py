"""Provider-neutral fixed-point model-budget values."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol

from .canonical import digest_bytes, digest_file, digest_value, load_json


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
    broker_socket: Path | None = None


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
class BudgetPlan:
    """Immutable campaign budget authority supplied outside the ledger."""

    plan_id: str
    status: str
    campaign_run_id: str
    organisation_limit_usd_nanos: int
    allocations: tuple[ActorBudgetAllocation, ...]
    rate_card_digest: str
    source_digest: str

    def __post_init__(self) -> None:
        if not all(
            (
                self.plan_id,
                self.campaign_run_id,
                self.rate_card_digest,
                self.source_digest,
            )
        ):
            raise ValueError("budget plan identity must be nonempty")
        if self.status not in {"conformance_only", "development", "registered"}:
            raise ValueError("budget plan status is unsupported")
        _positive_integer(
            self.organisation_limit_usd_nanos, "organisation budget limit"
        )
        if not self.allocations:
            raise ValueError("budget plan must allocate at least one actor")
        actor_ids = [allocation.actor_id for allocation in self.allocations]
        if len(set(actor_ids)) != len(actor_ids):
            raise ValueError("budget plan actor allocations must be unique")
        if any(
            allocation.campaign_run_id != self.campaign_run_id
            for allocation in self.allocations
        ):
            raise ValueError("budget plan allocation belongs to another campaign")
        if (
            sum(allocation.limit_usd_nanos for allocation in self.allocations)
            != self.organisation_limit_usd_nanos
        ):
            raise ValueError("budget plan allocations must partition the limit")

    @classmethod
    def create(
        cls,
        *,
        plan_id: str,
        status: str,
        campaign_run_id: str,
        organisation_limit_usd_nanos: int,
        allocations: tuple[ActorBudgetAllocation, ...],
        rate_card_digest: str,
    ) -> "BudgetPlan":
        payload = {
            "plan_id": plan_id,
            "status": status,
            "campaign_run_id": campaign_run_id,
            "organisation_limit_usd_nanos": organisation_limit_usd_nanos,
            "allocations": [
                {
                    "actor_id": allocation.actor_id,
                    "limit_usd_nanos": allocation.limit_usd_nanos,
                }
                for allocation in allocations
            ],
            "rate_card_digest": rate_card_digest,
        }
        return cls(
            plan_id,
            status,
            campaign_run_id,
            organisation_limit_usd_nanos,
            allocations,
            rate_card_digest,
            digest_value(payload),
        )

    @classmethod
    def load(
        cls, source: Path, *, expected_digest: str | None = None
    ) -> "BudgetPlan":
        """Load a pinned plan from a run manifest component."""

        with source.open("r", encoding="utf-8") as stream:
            payload = load_json(stream)
        expected = {
            "schema_version",
            "plan_id",
            "status",
            "campaign_run_id",
            "organisation_limit_usd_nanos",
            "allocations",
            "rate_card_digest",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("budget plan keys differ")
        if payload["schema_version"] != "budget-plan/v1":
            raise ValueError("unsupported budget plan schema")
        allocation_values = payload["allocations"]
        if not isinstance(allocation_values, list):
            raise ValueError("budget plan allocations must be a list")
        allocations: list[ActorBudgetAllocation] = []
        for value in allocation_values:
            if not isinstance(value, dict) or set(value) != {
                "actor_id",
                "limit_usd_nanos",
            }:
                raise ValueError("budget plan allocation keys differ")
            allocations.append(
                ActorBudgetAllocation(
                    str(payload["campaign_run_id"]),
                    str(value["actor_id"]),
                    value["limit_usd_nanos"],
                )
            )
        source_digest = digest_file(source)
        if expected_digest is not None and source_digest != expected_digest:
            raise ValueError("budget plan digest differs from the run manifest")
        if payload["status"] == "registered" and expected_digest is None:
            raise ValueError("registered budget plan requires a manifest digest")
        return cls(
            str(payload["plan_id"]),
            str(payload["status"]),
            str(payload["campaign_run_id"]),
            payload["organisation_limit_usd_nanos"],
            tuple(allocations),
            str(payload["rate_card_digest"]),
            source_digest,
        )

    @property
    def allocation_limits(self) -> dict[str, int]:
        return {
            allocation.actor_id: allocation.limit_usd_nanos
            for allocation in self.allocations
        }


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


class ProviderReceiptVerifier(Protocol):
    """Reconstruct authoritative usage from raw provider evidence."""

    @property
    def profile_digest(self) -> str: ...

    def verify(
        self, raw_receipt: bytes, raw_metadata_receipt: bytes
    ) -> ProviderUsage: ...


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


@dataclass(frozen=True, slots=True)
class BudgetReconciliation:
    """Close-time validity evidence for one campaign budget ledger."""

    campaign_run_id: str
    accounting_mode: str
    budget_plan_digest: str | None = None
    receipt_verifier_digest: str | None = None
    active_reservation_ids: tuple[str, ...] = ()
    forfeited_reservation_ids: tuple[str, ...] = ()
    overrun_reservation_ids: tuple[str, ...] = ()
    missing_receipt_reservation_ids: tuple[str, ...] = ()
    ledger_errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.campaign_run_id or not self.accounting_mode:
            raise ValueError("budget reconciliation identity must be nonempty")

    @property
    def valid(self) -> bool:
        return not any(
            (
                self.active_reservation_ids,
                self.forfeited_reservation_ids,
                self.overrun_reservation_ids,
                self.missing_receipt_reservation_ids,
                self.ledger_errors,
            )
        )

    def evidence(self) -> dict[str, object]:
        return {
            "campaign_run_id": self.campaign_run_id,
            "accounting_mode": self.accounting_mode,
            "budget_plan_digest": self.budget_plan_digest,
            "receipt_verifier_digest": self.receipt_verifier_digest,
            "valid": self.valid,
            "active_reservation_ids": list(self.active_reservation_ids),
            "forfeited_reservation_ids": list(self.forfeited_reservation_ids),
            "overrun_reservation_ids": list(self.overrun_reservation_ids),
            "missing_receipt_reservation_ids": list(
                self.missing_receipt_reservation_ids
            ),
            "ledger_errors": list(self.ledger_errors),
        }
