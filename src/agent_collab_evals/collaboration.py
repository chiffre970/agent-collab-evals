"""Collaboration-domain values independent of any service implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, Mapping, TypeVar


class CollaborationVisibility(str, Enum):
    NONE = "none"
    ACTOR_PRIVATE = "actor_private"
    ORGANISATION_SHARED = "organisation_shared"


@dataclass(frozen=True, slots=True)
class CollaborationScope:
    scope_id: str
    campaign_run_id: str
    visibility: CollaborationVisibility


@dataclass(frozen=True, slots=True, eq=False)
class SessionTransport:
    """Server-held local stand-in for a non-exportable authenticated transport."""

    _identity: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class SessionContext:
    campaign_run_id: str
    actor_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class CollaborationEntry:
    entry_id: str
    sequence: int
    actor_id: str
    body: str
    reply_to: str | None
    thread_root: str
    publication_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Notification:
    sequence: int
    entry_id: str
    actor_id: str
    kind: str


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class CollaborationSnapshot:
    scope: CollaborationScope
    entries: tuple[CollaborationEntry, ...]
    audit_events: tuple[Mapping[str, object], ...]
