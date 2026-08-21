"""Core types with no dependency on a harness, cloud, or campaign implementation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class CoordinationCondition(str, Enum):
    SOLO = "solo"
    NATIVE_MULTIAGENT = "native_multiagent"
    PEER_ISOLATED = "peer_isolated"
    PEER_COLLAB = "peer_collab"


class CampaignStatus(str, Enum):
    ACTIVE = "active"
    CLOSED = "closed"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    campaign_run_id: str
    ordinal: int

    @property
    def actor_id(self) -> str:
        return f"{self.campaign_run_id}:actor:{self.ordinal}"


@dataclass(frozen=True, slots=True)
class SessionHandle:
    value: str


@dataclass(frozen=True, slots=True)
class HarnessOrganisation:
    value: str


@dataclass(frozen=True, slots=True)
class Job:
    job_id: str
    mission: str
    materials_digest: str
    public_materials: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class MaterializedJobs:
    jobs: tuple[Job, ...]
    material_digest: str


@dataclass(frozen=True, slots=True)
class OrganisationSpec:
    campaign_run_id: str
    condition: CoordinationCondition
    organisation_size: int
    workspace_root: Path
    model_endpoint: str


@dataclass(frozen=True, slots=True)
class HarnessSnapshot:
    organisation_id: str
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class CampaignSnapshot:
    campaign_run_id: str
    condition: CoordinationCondition
    organisation_size: int
    workspace_root: str
    model_endpoint: str
    status: CampaignStatus
    organisation: HarnessOrganisation
    actors: tuple[AgentIdentity, ...]
    sessions: tuple[SessionHandle, ...]
    delivered_job_ids: tuple[str, ...]
    harness: HarnessSnapshot


@dataclass(frozen=True, slots=True)
class CampaignResult:
    campaign_run_id: str
    delivered_job_ids: tuple[str, ...]
    final_harness_snapshot: HarnessSnapshot


def top_level_actor_count(
    condition: CoordinationCondition, organisation_size: int
) -> int:
    if organisation_size < 1:
        raise ValueError("organisation_size must be at least one")
    if condition in {
        CoordinationCondition.PEER_ISOLATED,
        CoordinationCondition.PEER_COLLAB,
    }:
        return organisation_size
    return 1
