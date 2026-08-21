"""Deterministic harness adapter for lifecycle and conformance tests."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..domain import (
    AgentIdentity,
    HarnessOrganisation,
    HarnessSnapshot,
    Job,
    OrganisationSpec,
    SessionHandle,
)


@dataclass(slots=True)
class _FakeSession:
    handle: SessionHandle
    actor: AgentIdentity
    delivered_jobs: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class _FakeOrganisation:
    handle: HarnessOrganisation
    spec: OrganisationSpec
    sessions: dict[str, _FakeSession] = field(default_factory=dict)
    stopped: bool = False


class FakeHarnessRuntime:
    """A fake that preserves session identity and delivered work across resume."""

    def __init__(self) -> None:
        self._organisations: dict[str, _FakeOrganisation] = {}
        self._session_to_org: dict[str, str] = {}

    def capabilities(self) -> dict[str, Any]:
        return {
            "capability_version": "fake-harness/v1",
            "durable_sessions": True,
            "native_handoffs": False,
            "observational_events": False,
        }

    def start_organisation(self, spec: OrganisationSpec) -> HarnessOrganisation:
        organisation_id = f"fake-org:{spec.campaign_run_id}"
        if organisation_id in self._organisations:
            raise ValueError(f"organisation already exists: {organisation_id}")
        handle = HarnessOrganisation(organisation_id)
        self._organisations[organisation_id] = _FakeOrganisation(handle, spec)
        return handle

    def create_primary(
        self, organisation: HarnessOrganisation, actor: AgentIdentity
    ) -> SessionHandle:
        state = self._active_organisation(organisation)
        session_id = f"{organisation.value}:session:{actor.ordinal}"
        if session_id in self._session_to_org:
            raise ValueError(f"session already exists: {session_id}")
        handle = SessionHandle(session_id)
        state.sessions[session_id] = _FakeSession(handle, actor)
        self._session_to_org[session_id] = organisation.value
        return handle

    def deliver(self, session: SessionHandle, job: Job) -> None:
        state = self._session(session)
        previous_digest = state.delivered_jobs.get(job.job_id)
        if previous_digest is not None:
            if previous_digest != job.materials_digest:
                raise ValueError(
                    f"job identifier reused with different materials: {job.job_id}"
                )
            return
        state.delivered_jobs[job.job_id] = job.materials_digest

    def events(
        self, organisation: HarnessOrganisation
    ) -> tuple[dict[str, Any], ...]:
        self._organisation(organisation)
        return ()

    def snapshot(self, organisation: HarnessOrganisation) -> HarnessSnapshot:
        state = self._organisation(organisation)
        payload = {
            "spec": {
                "campaign_run_id": state.spec.campaign_run_id,
                "condition": state.spec.condition.value,
                "organisation_size": state.spec.organisation_size,
                "workspace_root": str(state.spec.workspace_root),
                "model_endpoint": state.spec.model_endpoint,
            },
            "sessions": [
                {
                    "session_id": session.handle.value,
                    "actor_ordinal": session.actor.ordinal,
                    "delivered_jobs": [
                        {
                            "job_id": job_id,
                            "materials_digest": materials_digest,
                        }
                        for job_id, materials_digest in session.delivered_jobs.items()
                    ],
                }
                for session in state.sessions.values()
            ],
            "stopped": state.stopped,
        }
        return HarnessSnapshot(organisation.value, deepcopy(payload))

    def resume(self, snapshot: HarnessSnapshot) -> HarnessOrganisation:
        from ..domain import CoordinationCondition

        if snapshot.organisation_id in self._organisations:
            raise ValueError(
                f"organisation already exists: {snapshot.organisation_id}"
            )
        spec_payload = snapshot.payload["spec"]
        spec = OrganisationSpec(
            campaign_run_id=str(spec_payload["campaign_run_id"]),
            condition=CoordinationCondition(str(spec_payload["condition"])),
            organisation_size=int(spec_payload["organisation_size"]),
            workspace_root=Path(str(spec_payload["workspace_root"])),
            model_endpoint=str(spec_payload["model_endpoint"]),
        )
        handle = HarnessOrganisation(snapshot.organisation_id)
        restored = _FakeOrganisation(handle, spec)
        for item in snapshot.payload["sessions"]:
            actor = AgentIdentity(spec.campaign_run_id, int(item["actor_ordinal"]))
            session = SessionHandle(str(item["session_id"]))
            if session.value in self._session_to_org:
                raise ValueError(f"session already exists: {session.value}")
            delivered_jobs = {
                str(delivered["job_id"]): str(delivered["materials_digest"])
                for delivered in item["delivered_jobs"]
            }
            restored.sessions[session.value] = _FakeSession(
                session, actor, delivered_jobs
            )
            self._session_to_org[session.value] = handle.value
        restored.stopped = bool(snapshot.payload["stopped"])
        if restored.stopped:
            raise ValueError("cannot resume a stopped harness organisation")
        self._organisations[handle.value] = restored
        return handle

    def stop(
        self, organisation: HarnessOrganisation, reason: str
    ) -> HarnessSnapshot:
        state = self._active_organisation(organisation)
        state.stopped = True
        snapshot = self.snapshot(organisation)
        payload = dict(snapshot.payload)
        payload["stop_reason"] = reason
        return HarnessSnapshot(snapshot.organisation_id, payload)

    def delivered_jobs(self, session: SessionHandle) -> tuple[str, ...]:
        return tuple(self._session(session).delivered_jobs)

    def _organisation(self, handle: HarnessOrganisation) -> _FakeOrganisation:
        try:
            return self._organisations[handle.value]
        except KeyError as error:
            raise KeyError(f"unknown organisation: {handle.value}") from error

    def _active_organisation(self, handle: HarnessOrganisation) -> _FakeOrganisation:
        state = self._organisation(handle)
        if state.stopped:
            raise RuntimeError("harness organisation is stopped")
        return state

    def _session(self, handle: SessionHandle) -> _FakeSession:
        try:
            organisation_id = self._session_to_org[handle.value]
            return self._organisations[organisation_id].sessions[handle.value]
        except KeyError as error:
            raise KeyError(f"unknown session: {handle.value}") from error
