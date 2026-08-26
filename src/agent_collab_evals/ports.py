"""Narrow ports exercised by the first fake vertical slice."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from .artifacts import (
    ArtifactReadAuthorization,
    ArtifactRecord,
    ArtifactRef,
    PublicationAudience,
    PublicationId,
    PublicationRecord,
    PublicationSnapshot,
    StorageSeal,
    TrustedServiceTransport,
)
from .budget import (
    ActorBudgetAllocation,
    BudgetCharge,
    BudgetRejected,
    BudgetReconciliation,
    BudgetReservation,
    BudgetSnapshot,
    ModelCallContext,
    ProviderUsage,
)
from .domain import (
    AgentIdentity,
    CampaignSnapshot,
    HarnessOrganisation,
    HarnessSnapshot,
    Job,
    MaterializedJobs,
    OrganisationSpec,
    SessionHandle,
)
from .evaluation import (
    CandidateReceipt,
    ComputeSnapshot,
    EvaluationReceipt,
    EvaluationReservation,
    EvaluationResult,
    EvaluationScope,
    SelectionReceipt,
    SelectionResult,
    SubmissionPolicy,
    SubmissionSet,
)
from .collaboration import (
    CollaborationEntry,
    CollaborationScope,
    CollaborationSnapshot,
    CollaborationVisibility,
    Notification,
    Page,
    SessionTransport,
)


class HarnessRuntime(Protocol):
    def capabilities(self) -> Mapping[str, Any]: ...

    def start_organisation(self, spec: OrganisationSpec) -> HarnessOrganisation: ...

    def create_primary(
        self, organisation: HarnessOrganisation, actor: AgentIdentity
    ) -> SessionHandle: ...

    def deliver(self, session: SessionHandle, job: Job) -> None:
        """Deliver idempotently by job identifier and materials digest."""
        ...

    def events(
        self, organisation: HarnessOrganisation
    ) -> tuple[Mapping[str, Any], ...]: ...

    def snapshot(self, organisation: HarnessOrganisation) -> HarnessSnapshot: ...

    def resume(self, snapshot: HarnessSnapshot) -> HarnessOrganisation: ...

    def stop(
        self, organisation: HarnessOrganisation, reason: str
    ) -> HarnessSnapshot: ...


class ProcessSandbox(Protocol):
    @property
    def profile_id(self) -> str: ...

    @property
    def profile_digest(self) -> str: ...

    def validate_model_endpoint(self, endpoint: str) -> None: ...

    def wrap(self, command: tuple[str, ...]) -> tuple[str, ...]: ...

    def evidence(self) -> Mapping[str, object]: ...


class EventSink(Protocol):
    def append(
        self, campaign_run_id: str, kind: str, payload: Mapping[str, Any]
    ) -> int: ...


class CampaignSnapshotStore(Protocol):
    def save(self, snapshot: CampaignSnapshot) -> None: ...

    def load(self, campaign_run_id: str) -> CampaignSnapshot: ...


class CampaignDefinition(Protocol):
    @property
    def manifest_digest(self) -> str: ...

    def materialize(self, task_seed: int) -> MaterializedJobs: ...


class CollaborationBackend(Protocol):
    def provision(
        self, campaign_run_id: str, visibility: CollaborationVisibility
    ) -> CollaborationScope: ...

    def publish(
        self,
        scope: CollaborationScope,
        session: SessionTransport,
        idempotency_key: str,
        body: str,
        reply_to: str | None = None,
        publication_ids: tuple[str, ...] = (),
    ) -> CollaborationEntry: ...

    def list_recent(
        self,
        scope: CollaborationScope,
        session: SessionTransport,
        cursor: str | None = None,
        limit: int = 50,
    ) -> Page[CollaborationEntry]: ...

    def get_thread(
        self,
        scope: CollaborationScope,
        session: SessionTransport,
        entry_id: str,
    ) -> tuple[CollaborationEntry, ...]: ...

    def search(
        self,
        scope: CollaborationScope,
        session: SessionTransport,
        query: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> Page[CollaborationEntry]: ...

    def notifications(
        self,
        scope: CollaborationScope,
        session: SessionTransport,
        cursor: str | None = None,
        limit: int = 50,
    ) -> Page[Notification]: ...

    def export(self, scope: CollaborationScope) -> CollaborationSnapshot: ...

    def reset(self, scope: CollaborationScope) -> None: ...


class BudgetAccount(Protocol):
    @property
    def rate_card_digest(self) -> str: ...

    def open_campaign(
        self,
        campaign_run_id: str,
        organisation_limit_usd_nanos: int,
        allocations: tuple[ActorBudgetAllocation, ...],
    ) -> None: ...

    def has_actor(self, campaign_run_id: str, actor_id: str) -> bool: ...

    def reserve(
        self,
        campaign_run_id: str,
        actor_id: str,
        context: ModelCallContext,
    ) -> BudgetReservation | BudgetRejected: ...

    def settle(
        self, reservation_id: str, usage: ProviderUsage
    ) -> BudgetCharge: ...

    def forfeit(
        self, reservation_id: str, reason: str, raw_receipt: bytes
    ) -> None: ...

    def release(self, reservation_id: str, reason: str) -> None: ...

    def snapshot(self, campaign_run_id: str) -> BudgetSnapshot: ...

    def reconcile(self, campaign_run_id: str) -> BudgetReconciliation: ...


class BudgetReconciliationGate(Protocol):
    def reconcile(self, campaign_run_id: str) -> BudgetReconciliation: ...


class StorageBackend(Protocol):
    def open_campaign(
        self, campaign_run_id: str, actor_ids: tuple[str, ...]
    ) -> None: ...

    def put(
        self,
        session: SessionTransport,
        content: bytes,
        media_type: str = "application/octet-stream",
    ) -> ArtifactRecord: ...

    def describe_owned(
        self, session: SessionTransport, ref: ArtifactRef
    ) -> ArtifactRecord: ...

    def read_owned(self, session: SessionTransport, ref: ArtifactRef) -> bytes: ...

    def authorize_read(
        self,
        service: TrustedServiceTransport,
        campaign_run_id: str,
        ref: ArtifactRef,
        purpose: str,
    ) -> ArtifactReadAuthorization: ...

    def trusted_read(
        self,
        service: TrustedServiceTransport,
        authorization: ArtifactReadAuthorization,
        purpose: str,
    ) -> tuple[ArtifactRecord, bytes]: ...

    def seal(
        self, campaign_run_id: str, final_manifest: Mapping[str, object]
    ) -> StorageSeal: ...


class PublicationRegistry(Protocol):
    def prepare(
        self,
        service: TrustedServiceTransport,
        publication_key: str,
        campaign_run_id: str,
        owner_actor_id: str,
        artifact_ref: ArtifactRef,
        audience: PublicationAudience,
    ) -> PublicationId: ...

    def bind(
        self,
        service: TrustedServiceTransport,
        publication_id: PublicationId,
        entry_id: str,
    ) -> None: ...

    def abort(
        self,
        service: TrustedServiceTransport,
        publication_id: PublicationId,
        reason: str,
    ) -> None: ...

    def resolve(
        self,
        service: TrustedServiceTransport,
        campaign_run_id: str,
        publication_id: PublicationId,
    ) -> PublicationRecord: ...

    def export(self, campaign_run_id: str) -> PublicationSnapshot: ...

    def reset(self, campaign_run_id: str) -> None: ...


class ComputeBroker(Protocol):
    def reserve_visible_evaluation(
        self,
        session: SessionTransport,
        reservation_key: str,
        artifact_ref: ArtifactRef,
        seconds: int,
    ) -> EvaluationReservation: ...

    def reserve_hidden_evaluation(
        self,
        service: TrustedServiceTransport,
        reservation_key: str,
        campaign_run_id: str,
        artifact_ref: ArtifactRef,
        seconds: int,
    ) -> EvaluationReservation: ...

    def complete(
        self, reservation_id: str, used_seconds: int
    ) -> EvaluationReservation: ...

    def fail(self, reservation_id: str, reason: str) -> None: ...

    def cancel(self, reservation_id: str, reason: str) -> None: ...

    def release_visible_results(
        self, campaign_run_id: str, actor_id: str
    ) -> None: ...

    def is_visible_result_released(
        self, campaign_run_id: str, actor_id: str
    ) -> bool: ...

    def snapshot(self, campaign_run_id: str) -> ComputeSnapshot: ...


class CandidateEvaluator(Protocol):
    @property
    def profile_digest(self) -> str: ...

    @property
    def visible_used_seconds(self) -> int: ...

    @property
    def hidden_used_seconds(self) -> int: ...

    def visible_evaluate(
        self,
        candidate: bytes,
        reservation: EvaluationReservation | None,
        evaluation_key: str,
    ) -> EvaluationReceipt: ...

    def hidden_evaluate(
        self,
        candidate: bytes,
        reservation: EvaluationReservation,
        evaluation_key: str,
    ) -> EvaluationReceipt: ...

    def resolve(
        self,
        receipt: EvaluationReceipt,
        candidate: bytes,
        reservation: EvaluationReservation | None,
        scope: EvaluationScope,
    ) -> EvaluationResult: ...


class SubmissionRegistry(Protocol):
    def initialize(
        self,
        campaign_run_id: str,
        job_id: str,
        actor_ids: tuple[str, ...],
        policy: SubmissionPolicy,
        default_artifact_ref: ArtifactRef,
        default_evaluation_receipt: EvaluationReceipt,
    ) -> None: ...

    def submit(
        self,
        session: SessionTransport,
        job_id: str,
        artifact_ref: ArtifactRef,
        idempotency_key: str,
    ) -> CandidateReceipt: ...

    def evaluate_visible(self, receipt: CandidateReceipt) -> None: ...

    def visible_result(
        self, session: SessionTransport, receipt: CandidateReceipt
    ) -> EvaluationResult | None: ...

    def close(self, campaign_run_id: str, job_id: str) -> SubmissionSet: ...

    def select(self, submissions: SubmissionSet) -> SelectionResult: ...

    def evaluate_hidden(
        self, selection_receipt: SelectionReceipt, *, reserved_seconds: int
    ) -> EvaluationResult: ...
