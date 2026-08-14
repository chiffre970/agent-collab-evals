"""Atomic local persistence for campaign snapshots used in development."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Mapping

from ..canonical import DuplicateKeyError, canonical_json_bytes, load_json
from ..domain import (
    AgentIdentity,
    CampaignSnapshot,
    CampaignStatus,
    CoordinationCondition,
    HarnessOrganisation,
    HarnessSnapshot,
    SessionHandle,
    top_level_actor_count,
)


SNAPSHOT_SCHEMA = "campaign-snapshot/v0alpha1"
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}")


class SnapshotValidationError(ValueError):
    pass


class LocalCampaignSnapshotStore:
    """Stores one replaceable snapshot per run without partial-file exposure."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._lock = threading.Lock()

    def save(self, snapshot: CampaignSnapshot) -> None:
        self._validate_id(snapshot.campaign_run_id)
        destination = self._path(snapshot.campaign_run_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = canonical_json_bytes(_to_document(snapshot)) + b"\n"

        with self._lock:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".snapshot-",
                suffix=".tmp",
                dir=destination.parent,
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as target:
                    target.write(content)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, destination)
                directory_fd = os.open(destination.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise

    def load(self, campaign_run_id: str) -> CampaignSnapshot:
        self._validate_id(campaign_run_id)
        path = self._path(campaign_run_id)
        try:
            with path.open("r", encoding="utf-8") as source:
                document = load_json(source)
        except FileNotFoundError as error:
            raise KeyError(f"no snapshot for campaign: {campaign_run_id}") from error
        except (json.JSONDecodeError, DuplicateKeyError) as error:
            raise SnapshotValidationError("snapshot is not unambiguous JSON") from error
        snapshot = _from_document(document)
        if snapshot.campaign_run_id != campaign_run_id:
            raise SnapshotValidationError("snapshot campaign identifier mismatch")
        return snapshot

    def _path(self, campaign_run_id: str) -> Path:
        return self._root / campaign_run_id / "snapshot.json"

    @staticmethod
    def _validate_id(campaign_run_id: str) -> None:
        if not _SAFE_ID.fullmatch(campaign_run_id):
            raise SnapshotValidationError("invalid campaign_run_id")


def _to_document(snapshot: CampaignSnapshot) -> dict[str, Any]:
    return {
        "schema_version": SNAPSHOT_SCHEMA,
        "campaign_run_id": snapshot.campaign_run_id,
        "condition": snapshot.condition.value,
        "organisation_size": snapshot.organisation_size,
        "workspace_root": snapshot.workspace_root,
        "model_endpoint": snapshot.model_endpoint,
        "status": snapshot.status.value,
        "organisation_id": snapshot.organisation.value,
        "actors": [
            {
                "campaign_run_id": actor.campaign_run_id,
                "ordinal": actor.ordinal,
            }
            for actor in snapshot.actors
        ],
        "sessions": [session.value for session in snapshot.sessions],
        "delivered_job_ids": list(snapshot.delivered_job_ids),
        "harness": {
            "organisation_id": snapshot.harness.organisation_id,
            "payload": snapshot.harness.payload,
        },
    }


def _from_document(value: Any) -> CampaignSnapshot:
    document = _mapping(value, "snapshot")
    expected = {
        "schema_version",
        "campaign_run_id",
        "condition",
        "organisation_size",
        "workspace_root",
        "model_endpoint",
        "status",
        "organisation_id",
        "actors",
        "sessions",
        "delivered_job_ids",
        "harness",
    }
    if set(document) != expected:
        raise SnapshotValidationError("snapshot fields do not match the schema")
    if document["schema_version"] != SNAPSHOT_SCHEMA:
        raise SnapshotValidationError("unsupported snapshot schema")

    campaign_run_id = _string(document, "campaign_run_id")
    organisation_size = _positive_int(document, "organisation_size")
    organisation_id = _string(document, "organisation_id")
    try:
        condition = CoordinationCondition(_string(document, "condition"))
        status = CampaignStatus(_string(document, "status"))
    except ValueError as error:
        raise SnapshotValidationError("invalid snapshot enum value") from error

    actors_value = document["actors"]
    if not isinstance(actors_value, list) or not actors_value:
        raise SnapshotValidationError("actors must be a non-empty list")
    actors: list[AgentIdentity] = []
    ordinals: set[int] = set()
    for value in actors_value:
        actor = _mapping(value, "actor")
        if set(actor) != {"campaign_run_id", "ordinal"}:
            raise SnapshotValidationError("actor fields do not match the schema")
        actor_campaign = _string(actor, "campaign_run_id")
        ordinal = actor.get("ordinal")
        if actor_campaign != campaign_run_id:
            raise SnapshotValidationError("actor campaign identifier mismatch")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0:
            raise SnapshotValidationError("actor ordinal must be non-negative")
        if ordinal in ordinals:
            raise SnapshotValidationError("actor ordinals must be unique")
        ordinals.add(ordinal)
        actors.append(AgentIdentity(actor_campaign, ordinal))
    expected_actor_count = top_level_actor_count(condition, organisation_size)
    if len(actors) != expected_actor_count or ordinals != set(
        range(expected_actor_count)
    ):
        raise SnapshotValidationError("actors do not match the condition topology")

    sessions_value = document["sessions"]
    if not isinstance(sessions_value, list) or len(sessions_value) != len(actors):
        raise SnapshotValidationError("sessions must match the actor count")
    sessions = tuple(SessionHandle(_nonempty_string(item, "session")) for item in sessions_value)
    if len({session.value for session in sessions}) != len(sessions):
        raise SnapshotValidationError("session identifiers must be unique")

    job_ids_value = document["delivered_job_ids"]
    if not isinstance(job_ids_value, list):
        raise SnapshotValidationError("delivered_job_ids must be a list")
    job_ids = tuple(_nonempty_string(item, "job id") for item in job_ids_value)
    if len(set(job_ids)) != len(job_ids):
        raise SnapshotValidationError("delivered job identifiers must be unique")

    harness_value = _mapping(document["harness"], "harness")
    if set(harness_value) != {"organisation_id", "payload"}:
        raise SnapshotValidationError("harness fields do not match the schema")
    harness_organisation_id = _string(harness_value, "organisation_id")
    if harness_organisation_id != organisation_id:
        raise SnapshotValidationError("harness organisation identifier mismatch")
    harness_payload = _mapping(harness_value["payload"], "harness payload")

    return CampaignSnapshot(
        campaign_run_id=campaign_run_id,
        condition=condition,
        organisation_size=organisation_size,
        workspace_root=_string(document, "workspace_root"),
        model_endpoint=_string(document, "model_endpoint"),
        status=status,
        organisation=HarnessOrganisation(organisation_id),
        actors=tuple(actors),
        sessions=sessions,
        delivered_job_ids=job_ids,
        harness=HarnessSnapshot(harness_organisation_id, harness_payload),
    )


def _mapping(value: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise SnapshotValidationError(f"{location} must be an object")
    return value


def _string(value: Mapping[str, Any], key: str) -> str:
    return _nonempty_string(value.get(key), key)


def _nonempty_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise SnapshotValidationError(f"{location} must be a non-empty string")
    return value


def _positive_int(value: Mapping[str, Any], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool) or item < 1:
        raise SnapshotValidationError(f"{key} must be a positive integer")
    return item
