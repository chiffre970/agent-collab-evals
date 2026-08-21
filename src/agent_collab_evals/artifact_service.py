"""Artifact publication orchestration without recommendation policy."""

from __future__ import annotations

from contextlib import suppress

from .artifacts import (
    ArtifactMaterialization,
    ArtifactRecord,
    ArtifactRef,
    PublicationAudience,
    PublicationId,
    TrustedServiceTransport,
)
from .collaboration import (
    CollaborationEntry,
    CollaborationScope,
    CollaborationVisibility,
    SessionTransport,
)
from .ports import CollaborationBackend, PublicationRegistry, StorageBackend
from .session_identity import SessionIdentityRegistry


class ArtifactService:
    """Joins owner-only bytes, publication state, and collaboration entries."""

    def __init__(
        self,
        sessions: SessionIdentityRegistry,
        storage: StorageBackend,
        publications: PublicationRegistry,
        collaboration: CollaborationBackend,
        service_transport: TrustedServiceTransport,
    ) -> None:
        self._sessions = sessions
        self._storage = storage
        self._publications = publications
        self._collaboration = collaboration
        self._service = service_transport

    def snapshot_bytes(
        self,
        session: SessionTransport,
        content: bytes,
        media_type: str = "application/octet-stream",
    ) -> ArtifactRecord:
        return self._storage.put(session, content, media_type)

    def publish(
        self,
        session: SessionTransport,
        scope: CollaborationScope,
        idempotency_key: str,
        body: str,
        artifact_refs: tuple[ArtifactRef, ...] = (),
        reply_to: str | None = None,
        audience: PublicationAudience = PublicationAudience.ACTOR_PRIVATE,
    ) -> CollaborationEntry:
        context = self._sessions.resolve(session)
        self._validate_scope(context.campaign_run_id, scope)
        if (
            audience is PublicationAudience.ORGANISATION_SHARED
            and scope.visibility is not CollaborationVisibility.ORGANISATION_SHARED
        ):
            raise PermissionError(
                "organisation publication requires a shared collaboration scope"
            )
        publication_ids: list[PublicationId] = []
        for index, artifact_ref in enumerate(artifact_refs):
            record = self._storage.describe_owned(session, artifact_ref)
            publication_ids.append(
                self._publications.prepare(
                    self._service,
                    f"{context.actor_id}:{idempotency_key}:artifact:{index}",
                    context.campaign_run_id,
                    record.owner_actor_id,
                    artifact_ref,
                    audience,
                )
            )
        try:
            entry = self._collaboration.publish(
                scope,
                session,
                idempotency_key,
                body,
                reply_to,
                tuple(item.value for item in publication_ids),
            )
        except Exception:
            for publication_id in publication_ids:
                with suppress(RuntimeError):
                    self._publications.abort(
                        self._service,
                        publication_id,
                        "collaboration write failed",
                    )
            raise
        for publication_id in publication_ids:
            self._publications.bind(
                self._service, publication_id, entry.entry_id
            )
        return entry

    def materialize(
        self,
        session: SessionTransport,
        scope: CollaborationScope,
        publication_id: PublicationId,
    ) -> ArtifactMaterialization:
        context = self._sessions.resolve(session)
        self._validate_scope(context.campaign_run_id, scope)
        record = self._publications.resolve(
            self._service, context.campaign_run_id, publication_id
        )
        if record.audience is PublicationAudience.ACTOR_PRIVATE:
            if record.owner_actor_id != context.actor_id:
                raise PermissionError("publication is private to its owner")
        elif scope.visibility is not CollaborationVisibility.ORGANISATION_SHARED:
            raise PermissionError("publication is not visible in this scope")
        assert record.entry_id is not None
        thread = self._collaboration.get_thread(scope, session, record.entry_id)
        if not any(
            entry.entry_id == record.entry_id
            and publication_id.value in entry.publication_ids
            for entry in thread
        ):
            raise PermissionError(
                "publication is not attached to its bound collaboration entry"
            )
        authorization = self._storage.authorize_read(
            self._service,
            context.campaign_run_id,
            record.artifact_ref,
            "publication_materialization",
        )
        artifact, content = self._storage.trusted_read(
            self._service,
            authorization,
            "publication_materialization",
        )
        return ArtifactMaterialization(publication_id, artifact, content)

    @staticmethod
    def _validate_scope(
        campaign_run_id: str, scope: CollaborationScope
    ) -> None:
        if scope.campaign_run_id != campaign_run_id:
            raise PermissionError("collaboration scope belongs to another campaign")
