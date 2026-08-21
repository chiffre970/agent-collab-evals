"""Artifact and publication values independent of storage implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    value: str


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    ref: ArtifactRef
    campaign_run_id: str
    owner_actor_id: str
    digest: str
    media_type: str
    size_bytes: int


class PublicationAudience(str, Enum):
    ACTOR_PRIVATE = "actor_private"
    ORGANISATION_SHARED = "organisation_shared"


class PublicationStatus(str, Enum):
    PREPARED = "prepared"
    BOUND = "bound"
    ABORTED = "aborted"


@dataclass(frozen=True, slots=True)
class PublicationId:
    value: str


@dataclass(frozen=True, slots=True)
class PublicationRecord:
    publication_id: PublicationId
    publication_key: str
    campaign_run_id: str
    owner_actor_id: str
    artifact_ref: ArtifactRef
    audience: PublicationAudience
    status: PublicationStatus
    entry_id: str | None
    abort_reason: str | None


@dataclass(frozen=True, slots=True)
class PublicationSnapshot:
    campaign_run_id: str
    records: tuple[PublicationRecord, ...]
    audit_events: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, slots=True, eq=False)
class TrustedServiceTransport:
    """Server-held identity for a pinned internal service."""

    _identity: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True, eq=False)
class ArtifactReadAuthorization:
    """One-use server-held authority for one artifact and purpose."""

    _identity: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ArtifactMaterialization:
    publication_id: PublicationId
    artifact: ArtifactRecord
    content: bytes = field(repr=False)
