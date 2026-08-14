"""Durable campaign lifecycle orchestration, independent of runtime adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .domain import (
    AgentIdentity,
    CampaignResult,
    CampaignSnapshot,
    CampaignStatus,
    HarnessOrganisation,
    Job,
    OrganisationSpec,
    SessionHandle,
    top_level_actor_count,
)
from .ports import EventSink, HarnessRuntime


@dataclass(slots=True)
class CampaignHandle:
    spec: OrganisationSpec
    organisation: HarnessOrganisation
    actors: tuple[AgentIdentity, ...]
    sessions: tuple[SessionHandle, ...]
    delivered_job_ids: list[str] = field(default_factory=list)
    status: CampaignStatus = CampaignStatus.ACTIVE


class CampaignController:
    """Owns lifecycle only; it does not plan, delegate, merge, or score work."""

    def __init__(self, harness: HarnessRuntime, events: EventSink) -> None:
        self._harness = harness
        self._events = events

    def start(self, spec: OrganisationSpec) -> CampaignHandle:
        actor_count = top_level_actor_count(spec.condition, spec.organisation_size)
        organisation = self._harness.start_organisation(spec)
        if not organisation.value:
            raise RuntimeError("harness returned an empty organisation identifier")
        actors = tuple(
            AgentIdentity(spec.campaign_run_id, ordinal)
            for ordinal in range(actor_count)
        )
        sessions = tuple(
            self._harness.create_primary(organisation, actor) for actor in actors
        )
        if any(not session.value for session in sessions) or len(
            {session.value for session in sessions}
        ) != len(sessions):
            raise RuntimeError("harness returned invalid primary session identifiers")
        handle = CampaignHandle(spec, organisation, actors, sessions)
        self._events.append(
            spec.campaign_run_id,
            "campaign.started",
            {
                "condition": spec.condition.value,
                "organisation_size": spec.organisation_size,
                "top_level_actor_count": actor_count,
            },
        )
        return handle

    def deliver(self, handle: CampaignHandle, job: Job) -> None:
        self._require_active(handle)
        if job.job_id in handle.delivered_job_ids:
            raise ValueError(f"job already delivered: {job.job_id}")
        for session in handle.sessions:
            self._harness.deliver(session, job)
        handle.delivered_job_ids.append(job.job_id)
        self._events.append(
            handle.spec.campaign_run_id,
            "job.delivered",
            {
                "job_id": job.job_id,
                "materials_digest": job.materials_digest,
                "recipient_count": len(handle.sessions),
            },
        )

    def snapshot(self, handle: CampaignHandle) -> CampaignSnapshot:
        self._require_active(handle)
        snapshot = CampaignSnapshot(
            campaign_run_id=handle.spec.campaign_run_id,
            condition=handle.spec.condition,
            organisation_size=handle.spec.organisation_size,
            workspace_root=str(handle.spec.workspace_root),
            model_endpoint=handle.spec.model_endpoint,
            status=handle.status,
            organisation=handle.organisation,
            actors=handle.actors,
            sessions=handle.sessions,
            delivered_job_ids=tuple(handle.delivered_job_ids),
            harness=self._harness.snapshot(handle.organisation),
        )
        self._events.append(
            handle.spec.campaign_run_id,
            "campaign.snapshotted",
            {"delivered_job_count": len(handle.delivered_job_ids)},
        )
        return snapshot

    def resume(self, snapshot: CampaignSnapshot) -> CampaignHandle:
        if snapshot.status is not CampaignStatus.ACTIVE:
            raise ValueError("only active campaigns can be resumed")
        spec = OrganisationSpec(
            campaign_run_id=snapshot.campaign_run_id,
            condition=snapshot.condition,
            organisation_size=snapshot.organisation_size,
            workspace_root=Path(snapshot.workspace_root),
            model_endpoint=snapshot.model_endpoint,
        )
        organisation = self._harness.resume(snapshot.harness)
        if organisation != snapshot.organisation:
            raise RuntimeError("harness resumed a different organisation")
        handle = CampaignHandle(
            spec=spec,
            organisation=organisation,
            actors=snapshot.actors,
            sessions=snapshot.sessions,
            delivered_job_ids=list(snapshot.delivered_job_ids),
        )
        self._events.append(
            spec.campaign_run_id,
            "campaign.resumed",
            {"delivered_job_count": len(handle.delivered_job_ids)},
        )
        return handle

    def close(self, handle: CampaignHandle, reason: str) -> CampaignResult:
        self._require_active(handle)
        final_snapshot = self._harness.stop(handle.organisation, reason)
        handle.status = CampaignStatus.CLOSED
        self._events.append(
            handle.spec.campaign_run_id,
            "campaign.closed",
            {"reason": reason, "delivered_job_count": len(handle.delivered_job_ids)},
        )
        return CampaignResult(
            campaign_run_id=handle.spec.campaign_run_id,
            delivered_job_ids=tuple(handle.delivered_job_ids),
            final_harness_snapshot=final_snapshot,
        )

    @staticmethod
    def _require_active(handle: CampaignHandle) -> None:
        if handle.status is not CampaignStatus.ACTIVE:
            raise RuntimeError("campaign is not active")
