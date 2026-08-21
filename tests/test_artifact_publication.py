from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_collab_evals.adapters.local_artifact_storage import (
    LocalArtifactStorage,
)
from agent_collab_evals.adapters.sqlite_collaboration import (
    SqliteCollaborationBackend,
)
from agent_collab_evals.adapters.sqlite_publications import (
    SqlitePublicationRegistry,
)
from agent_collab_evals.artifact_service import ArtifactService
from agent_collab_evals.artifacts import (
    ArtifactReadAuthorization,
    ArtifactRef,
    PublicationAudience,
    PublicationId,
    PublicationStatus,
    TrustedServiceTransport,
)
from agent_collab_evals.collaboration import CollaborationVisibility
from agent_collab_evals.domain import AgentIdentity, SessionHandle
from agent_collab_evals.service_identity import ServiceIdentityRegistry
from agent_collab_evals.session_identity import SessionIdentityRegistry


class ArtifactPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        root = Path(self._temporary.name)
        self.sessions = SessionIdentityRegistry()
        self.services = ServiceIdentityRegistry()
        self.service_transport = self.services.bind("artifact_service")
        self.storage = LocalArtifactStorage(
            root / "storage", self.sessions, self.services
        )
        self.collaboration = SqliteCollaborationBackend(
            root / "collaboration.sqlite3", self.sessions
        )
        self.publications = SqlitePublicationRegistry(
            root / "publications.sqlite3", self.services
        )
        self.service = ArtifactService(
            self.sessions,
            self.storage,
            self.publications,
            self.collaboration,
            self.service_transport,
        )
        self.actors = (
            AgentIdentity("campaign", 0),
            AgentIdentity("campaign", 1),
        )
        self.transports = tuple(
            self.sessions.bind(actor, SessionHandle(f"session-{index}"))
            for index, actor in enumerate(self.actors)
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_owner_storage_is_private_and_trusted_reads_are_one_use(self) -> None:
        artifact = self.service.snapshot_bytes(
            self.transports[0], b"immutable candidate", "text/plain"
        )
        self.assertEqual(
            self.storage.read_owned(self.transports[0], artifact.ref),
            b"immutable candidate",
        )
        with self.assertRaisesRegex(PermissionError, "owned by another"):
            self.storage.read_owned(self.transports[1], artifact.ref)
        with self.assertRaisesRegex(KeyError, "unknown artifact"):
            self.storage.read_owned(
                self.transports[0], ArtifactRef("artifact-" + "0" * 32)
            )

        authorization = self.storage.authorize_read(
            self.service_transport,
            "campaign",
            artifact.ref,
            "evaluation",
        )
        with self.assertRaisesRegex(PermissionError, "does not match"):
            self.storage.trusted_read(
                self.service_transport, authorization, "materialization"
            )
        with self.assertRaisesRegex(PermissionError, "consumed"):
            self.storage.trusted_read(
                self.service_transport, authorization, "evaluation"
            )

    def test_shared_publication_materializes_for_peer_and_survives_restart(self) -> None:
        scope = self.collaboration.provision(
            "campaign", CollaborationVisibility.ORGANISATION_SHARED
        )
        artifact = self.service.snapshot_bytes(
            self.transports[0], b"profiling result", "text/plain"
        )
        entry = self.service.publish(
            self.transports[0],
            scope,
            "publish-profile",
            "Profile and patch attached.",
            (artifact.ref,),
            audience=PublicationAudience.ORGANISATION_SHARED,
        )
        self.assertEqual(len(entry.publication_ids), 1)
        publication_id = PublicationId(entry.publication_ids[0])
        materialized = self.service.materialize(
            self.transports[1], scope, publication_id
        )
        self.assertEqual(materialized.content, b"profiling result")
        self.assertEqual(materialized.artifact, artifact)

        restarted_registry = SqlitePublicationRegistry(
            Path(self._temporary.name) / "publications.sqlite3", self.services
        )
        record = restarted_registry.resolve(
            self.service_transport, "campaign", publication_id
        )
        self.assertEqual(record.status, PublicationStatus.BOUND)
        self.assertEqual(record.entry_id, entry.entry_id)

        retried = self.service.publish(
            self.transports[0],
            scope,
            "publish-profile",
            "Profile and patch attached.",
            (artifact.ref,),
            audience=PublicationAudience.ORGANISATION_SHARED,
        )
        self.assertEqual(retried, entry)
        self.assertEqual(len(self.publications.export("campaign").records), 1)
        with self.assertRaisesRegex(ValueError, "different collaboration content"):
            self.service.publish(
                self.transports[0],
                scope,
                "publish-profile",
                "changed body",
                (artifact.ref,),
                audience=PublicationAudience.ORGANISATION_SHARED,
            )
        self.assertEqual(
            self.publications.resolve(
                self.service_transport, "campaign", publication_id
            ).status,
            PublicationStatus.BOUND,
        )

    def test_private_scope_cannot_publish_or_read_shared_artifact(self) -> None:
        scope = self.collaboration.provision(
            "campaign", CollaborationVisibility.ACTOR_PRIVATE
        )
        artifact = self.service.snapshot_bytes(self.transports[0], b"private")
        with self.assertRaisesRegex(PermissionError, "requires a shared"):
            self.service.publish(
                self.transports[0],
                scope,
                "invalid-shared",
                "not allowed",
                (artifact.ref,),
                audience=PublicationAudience.ORGANISATION_SHARED,
            )

        entry = self.service.publish(
            self.transports[0],
            scope,
            "private-publication",
            "owner-only attachment",
            (artifact.ref,),
        )
        publication_id = PublicationId(entry.publication_ids[0])
        with self.assertRaisesRegex(PermissionError, "private to its owner"):
            self.service.materialize(self.transports[1], scope, publication_id)
        self.assertEqual(
            self.service.materialize(
                self.transports[0], scope, publication_id
            ).content,
            b"private",
        )

    def test_registry_rejects_unbound_aborted_cross_campaign_and_key_changes(self) -> None:
        artifact = self.service.snapshot_bytes(self.transports[0], b"candidate")
        publication_id = self.publications.prepare(
            self.service_transport,
            "manual-key",
            "campaign",
            self.actors[0].actor_id,
            artifact.ref,
            PublicationAudience.ACTOR_PRIVATE,
        )
        with self.assertRaisesRegex(KeyError, "not active"):
            self.publications.resolve(
                self.service_transport, "campaign", publication_id
            )
        with self.assertRaisesRegex(ValueError, "different arguments"):
            self.publications.prepare(
                self.service_transport,
                "manual-key",
                "campaign",
                self.actors[0].actor_id,
                ArtifactRef("artifact-" + "1" * 32),
                PublicationAudience.ACTOR_PRIVATE,
            )
        self.publications.abort(
            self.service_transport, publication_id, "test failure"
        )
        with self.assertRaisesRegex(KeyError, "not active"):
            self.publications.resolve(
                self.service_transport, "campaign", publication_id
            )

        second = self.publications.prepare(
            self.service_transport,
            "bound-key",
            "campaign",
            self.actors[0].actor_id,
            artifact.ref,
            PublicationAudience.ACTOR_PRIVATE,
        )
        self.publications.bind(self.service_transport, second, "entry-1")
        with self.assertRaisesRegex(ValueError, "another entry"):
            self.publications.bind(self.service_transport, second, "entry-2")
        with self.assertRaisesRegex(RuntimeError, "cannot be aborted"):
            self.publications.abort(
                self.service_transport, second, "too late"
            )
        with self.assertRaisesRegex(PermissionError, "different campaign"):
            self.publications.resolve(
                self.service_transport, "other-campaign", second
            )
        with self.assertRaisesRegex(KeyError, "unknown publication"):
            self.publications.resolve(
                self.service_transport,
                "campaign",
                PublicationId("publication-guessed"),
            )

    def test_failed_collaboration_write_aborts_prepared_publication(self) -> None:
        scope = self.collaboration.provision(
            "campaign", CollaborationVisibility.ORGANISATION_SHARED
        )
        artifact = self.service.snapshot_bytes(self.transports[0], b"candidate")
        with self.assertRaisesRegex(ValueError, "entry body"):
            self.service.publish(
                self.transports[0],
                scope,
                "failed-publication",
                "",
                (artifact.ref,),
                audience=PublicationAudience.ORGANISATION_SHARED,
            )
        snapshot = self.publications.export("campaign")
        self.assertEqual(len(snapshot.records), 1)
        self.assertEqual(snapshot.records[0].status, PublicationStatus.ABORTED)
        with self.assertRaisesRegex(KeyError, "not active"):
            self.publications.resolve(
                self.service_transport,
                "campaign",
                snapshot.records[0].publication_id,
            )

    def test_forged_service_transports_and_authorizations_are_rejected(self) -> None:
        artifact = self.service.snapshot_bytes(self.transports[0], b"candidate")
        forged_service = TrustedServiceTransport(object())
        with self.assertRaisesRegex(PermissionError, "unknown or expired"):
            self.storage.authorize_read(
                forged_service, "campaign", artifact.ref, "evaluation"
            )
        forged_authorization = ArtifactReadAuthorization(object())
        with self.assertRaisesRegex(PermissionError, "unknown or consumed"):
            self.storage.trusted_read(
                self.service_transport, forged_authorization, "evaluation"
            )


if __name__ == "__main__":
    unittest.main()
