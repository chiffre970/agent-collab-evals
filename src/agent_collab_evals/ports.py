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
    TrustedServiceTransport,
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


class StorageBackend(Protocol):
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
