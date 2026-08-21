"""Narrow ports exercised by the first fake vertical slice."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

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
