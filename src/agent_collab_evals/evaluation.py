"""Provider-neutral candidate evaluation and selection values."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from .artifacts import ArtifactRef


class EvaluationScope(str, Enum):
    VISIBLE = "visible"
    HIDDEN = "hidden"


class EvaluationReservationStatus(str, Enum):
    RESERVED = "reserved"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ActorComputeAllocation:
    campaign_run_id: str
    actor_id: str
    limit_seconds: int

    def __post_init__(self) -> None:
        if not self.campaign_run_id or not self.actor_id:
            raise ValueError("compute allocation identity must be nonempty")
        if type(self.limit_seconds) is not int or self.limit_seconds < 1:
            raise ValueError("compute allocation must be a positive integer")


@dataclass(frozen=True, slots=True)
class ComputePlan:
    plan_id: str
    campaign_run_id: str
    organisation_limit_seconds: int
    actor_allocations: tuple[ActorComputeAllocation, ...]
    hidden_evaluator_limit_seconds: int
    source_digest: str

    def __post_init__(self) -> None:
        if not self.plan_id or not self.campaign_run_id:
            raise ValueError("compute plan identity must be nonempty")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.source_digest):
            raise ValueError("compute plan source digest must be SHA-256")
        if type(self.organisation_limit_seconds) is not int or (
            self.organisation_limit_seconds < 1
        ):
            raise ValueError("organisation compute limit must be positive")
        if type(self.hidden_evaluator_limit_seconds) is not int or (
            self.hidden_evaluator_limit_seconds < 1
        ):
            raise ValueError("hidden evaluator compute limit must be positive")
        actor_ids = [allocation.actor_id for allocation in self.actor_allocations]
        if not actor_ids or len(set(actor_ids)) != len(actor_ids):
            raise ValueError("compute plan actors must be nonempty and unique")
        if any(
            allocation.campaign_run_id != self.campaign_run_id
            for allocation in self.actor_allocations
        ):
            raise ValueError("compute allocation belongs to another campaign")
        if (
            sum(item.limit_seconds for item in self.actor_allocations)
            != self.organisation_limit_seconds
        ):
            raise ValueError("actor compute allocations must partition the limit")

    @property
    def actor_limits(self) -> Mapping[str, int]:
        return {
            allocation.actor_id: allocation.limit_seconds
            for allocation in self.actor_allocations
        }


@dataclass(frozen=True, slots=True)
class EvaluationReservation:
    reservation_id: str
    reservation_key: str
    campaign_run_id: str
    actor_id: str | None
    artifact_ref: ArtifactRef
    scope: EvaluationScope
    reserved_seconds: int
    status: EvaluationReservationStatus

    def __post_init__(self) -> None:
        if not re.fullmatch(r"evaluation-[0-9a-f]{32}", self.reservation_id):
            raise ValueError("evaluation reservation ID is invalid")
        if not self.reservation_key or not self.campaign_run_id:
            raise ValueError("evaluation reservation identity must be nonempty")
        if type(self.reserved_seconds) is not int or self.reserved_seconds < 1:
            raise ValueError("evaluation reservation duration must be positive")
        if self.scope is EvaluationScope.VISIBLE and not self.actor_id:
            raise ValueError("visible evaluation reservation requires an actor")
        if self.scope is EvaluationScope.HIDDEN and self.actor_id is not None:
            raise ValueError("hidden evaluation reservation cannot name an actor")


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    eligible: bool
    criterion_units: int
    failures: tuple[str, ...]
    evidence_digest: str
    diagnostics: Mapping[str, object]

    def __post_init__(self) -> None:
        if type(self.eligible) is not bool:
            raise ValueError("evaluation eligibility must be boolean")
        if type(self.criterion_units) is not int:
            raise ValueError("evaluation criterion must use integer units")
        if any(not isinstance(item, str) or not item for item in self.failures):
            raise ValueError("evaluation failures must be nonempty strings")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.evidence_digest):
            raise ValueError("evaluation evidence digest must be SHA-256")


@dataclass(frozen=True, slots=True)
class EvaluationReceipt:
    """Opaque reference to evidence held by the evaluator."""

    value: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"evalreceipt-[0-9a-f]{32}", self.value):
            raise ValueError("evaluation receipt is invalid")


@dataclass(frozen=True, slots=True)
class CandidateReceipt:
    value: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"candidate-[0-9a-f]{32}", self.value):
            raise ValueError("candidate receipt is invalid")


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    receipt: CandidateReceipt
    idempotency_key: str
    campaign_run_id: str
    job_id: str
    owner_actor_id: str
    artifact_ref: ArtifactRef
    artifact_digest: str
    reservation_id: str
    evaluation_receipt: EvaluationReceipt | None
    visible_result: EvaluationResult | None
    evaluation_failure: str | None


@dataclass(frozen=True, slots=True)
class SubmissionPolicy:
    per_actor_candidate_limit: int
    visible_evaluation_seconds: int
    selection_rule: str = "highest_eligible_criterion_then_artifact_digest"

    def __post_init__(self) -> None:
        if type(self.per_actor_candidate_limit) is not int or (
            self.per_actor_candidate_limit < 1
        ):
            raise ValueError("candidate limit must be positive")
        if type(self.visible_evaluation_seconds) is not int or (
            self.visible_evaluation_seconds < 1
        ):
            raise ValueError("visible evaluation duration must be positive")
        if self.selection_rule != "highest_eligible_criterion_then_artifact_digest":
            raise ValueError("unsupported candidate selection rule")


@dataclass(frozen=True, slots=True)
class SubmissionSet:
    campaign_run_id: str
    job_id: str
    candidates: tuple[CandidateRecord, ...]
    default_artifact_ref: ArtifactRef
    default_evaluation_receipt: EvaluationReceipt
    default_result: EvaluationResult


@dataclass(frozen=True, slots=True)
class SelectionReceipt:
    """Opaque reference to a persisted, authoritative selection."""

    value: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"selection-[0-9a-f]{32}", self.value):
            raise ValueError("selection receipt is invalid")


@dataclass(frozen=True, slots=True)
class SelectionResult:
    receipt: SelectionReceipt
    campaign_run_id: str
    job_id: str
    selected_receipt: CandidateReceipt | None
    selected_artifact_ref: ArtifactRef | None
    result: EvaluationResult
    used_default: bool
    selection_digest: str


@dataclass(frozen=True, slots=True)
class ComputeSnapshot:
    campaign_run_id: str
    organisation_limit_seconds: int
    actor_reserved_seconds: Mapping[str, int]
    actor_used_seconds: Mapping[str, int]
    hidden_reserved_seconds: int
    hidden_used_seconds: int
    released_actor_ids: tuple[str, ...]
    reservations: tuple[EvaluationReservation, ...]
    audit_events: tuple[Mapping[str, object], ...]
