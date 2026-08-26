"""Artifact publication orchestration without recommendation policy."""

from __future__ import annotations

import os
import secrets
import stat
from contextlib import suppress
from pathlib import Path, PurePosixPath

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

    def snapshot_file(
        self,
        session: SessionTransport,
        relative_path: str,
        *,
        media_type: str = "application/octet-stream",
        max_bytes: int = 16 * 1024 * 1024,
    ) -> ArtifactRecord:
        """Read one regular workspace file without following symlinks."""

        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("workspace snapshot limit must be positive")
        workspace_root = self._sessions.workspace(session)
        content = self._read_workspace_file(
            workspace_root, relative_path, max_bytes=max_bytes
        )
        return self._storage.put(session, content, media_type)

    def materialize_owned_file(
        self,
        session: SessionTransport,
        artifact_ref: ArtifactRef,
        relative_path: str,
    ) -> Path:
        """Create one new workspace file without traversing symlinks."""

        workspace_root = self._sessions.workspace(session)
        content = self._storage.read_owned(session, artifact_ref)
        return self._write_workspace_file(workspace_root, relative_path, content)

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

    @staticmethod
    def _workspace_parts(relative_path: str) -> tuple[str, ...]:
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError("workspace path is required")
        path = PurePosixPath(relative_path)
        if (
            path.is_absolute()
            or str(path) != relative_path
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("workspace path must be a normalized relative path")
        return path.parts

    @classmethod
    def _open_workspace_parent(
        cls, workspace_root: Path, relative_path: str
    ) -> tuple[int, tuple[str, ...]]:
        parts = cls._workspace_parts(relative_path)
        root_descriptor = os.open(
            workspace_root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        current = root_descriptor
        try:
            for part in parts[:-1]:
                child = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current,
                )
                if current != root_descriptor:
                    os.close(current)
                current = child
            if current != root_descriptor:
                os.close(root_descriptor)
            return current, parts
        except BaseException:
            if current != root_descriptor:
                os.close(current)
            os.close(root_descriptor)
            raise

    @classmethod
    def _read_workspace_file(
        cls, workspace_root: Path, relative_path: str, *, max_bytes: int
    ) -> bytes:
        parent, parts = cls._open_workspace_parent(workspace_root, relative_path)
        try:
            descriptor = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent
            )
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError("workspace snapshot source must be a regular file")
                if metadata.st_size > max_bytes:
                    raise ValueError("workspace snapshot source exceeds its size limit")
                with os.fdopen(descriptor, "rb", closefd=False) as stream:
                    content = stream.read(max_bytes + 1)
                if len(content) > max_bytes:
                    raise ValueError("workspace snapshot source exceeds its size limit")
                return content
            finally:
                os.close(descriptor)
        finally:
            os.close(parent)

    @classmethod
    def _write_workspace_file(
        cls, workspace_root: Path, relative_path: str, content: bytes
    ) -> Path:
        parent, parts = cls._open_workspace_parent(workspace_root, relative_path)
        temporary = f".collab-materialize-{secrets.token_hex(8)}"
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.close(descriptor)
            descriptor = None
            os.link(
                temporary,
                parts[-1],
                src_dir_fd=parent,
                dst_dir_fd=parent,
                follow_symlinks=False,
            )
            os.unlink(temporary, dir_fd=parent)
            os.fsync(parent)
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=parent)
            raise
        finally:
            os.close(parent)
        return workspace_root / PurePosixPath(relative_path)
