from __future__ import annotations

import os
import sqlite3
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
    ArtifactStoragePolicy,
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
        self.root = root
        self.sessions = SessionIdentityRegistry()
        self.services = ServiceIdentityRegistry()
        self.service_transport = self.services.bind("artifact_service")
        self.storage = LocalArtifactStorage(
            root / "storage",
            self.sessions,
            self.services,
            ArtifactStoragePolicy(
                max_artifact_bytes=64,
                max_actor_bytes=96,
                max_campaign_bytes=192,
            ),
            {
                "artifact_service": frozenset(
                    {"evaluation", "publication_materialization"}
                )
            },
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
        self.workspaces = tuple(root / f"workspace-{index}" for index in range(2))
        for transport, workspace in zip(
            self.transports, self.workspaces, strict=True
        ):
            workspace.mkdir()
            self.sessions.assign_workspace(transport, workspace)
        self.storage.open_campaign(
            "campaign", tuple(actor.actor_id for actor in self.actors)
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

    def test_workspace_snapshot_and_materialization_reject_symlink_escape(self) -> None:
        workspace = self.workspaces[0]
        (workspace / "input").mkdir(parents=True)
        (workspace / "output").mkdir()
        (workspace / "input/candidate.json").write_bytes(b'{"candidate":true}')
        artifact = self.service.snapshot_file(
            self.transports[0],
            "input/candidate.json",
            media_type="application/json",
            max_bytes=64,
        )

        destination = self.service.materialize_owned_file(
            self.transports[0], artifact.ref, "output/candidate.json"
        )

        self.assertEqual(destination.read_bytes(), b'{"candidate":true}')
        with self.assertRaises(FileExistsError):
            self.service.materialize_owned_file(
                self.transports[0],
                artifact.ref,
                "output/candidate.json",
            )
        os.symlink(Path(self._temporary.name), workspace / "escape")
        with self.assertRaises(OSError):
            self.service.snapshot_file(
                self.transports[0], "escape/outside.txt"
            )
        with self.assertRaises(OSError):
            self.service.materialize_owned_file(
                self.transports[0], artifact.ref, "escape/copied.txt"
            )
        with self.assertRaisesRegex(ValueError, "normalized relative"):
            self.service.snapshot_file(
                self.transports[0], "input//candidate.json"
            )
        with self.assertRaisesRegex(ValueError, "already assigned"):
            self.sessions.assign_workspace(self.transports[0], self.root)

        unassigned = self.sessions.bind(
            AgentIdentity("campaign", 2), SessionHandle("unassigned-session")
        )
        with self.assertRaisesRegex(PermissionError, "no assigned workspace"):
            self.service.snapshot_file(unassigned, "repository-file.txt")

    def test_storage_seal_is_idempotent_and_prevents_new_artifacts(self) -> None:
        artifact = self.service.snapshot_bytes(
            self.transports[0], b"final candidate", "text/plain"
        )
        manifest = {
            "selection_digest": "sha256:selection",
            "selected_artifact_ref": artifact.ref.value,
        }

        first = self.storage.seal("campaign", manifest)
        repeated = self.storage.seal("campaign", manifest)

        self.assertEqual(first, repeated)
        self.assertEqual(first.artifact_count, 1)
        self.assertEqual(first.total_bytes, len(b"final candidate"))
        with self.assertRaisesRegex(RuntimeError, "sealed"):
            self.service.snapshot_bytes(self.transports[1], b"too late")
        with self.assertRaisesRegex(ValueError, "sealed differently"):
            self.storage.seal("campaign", {"selection_digest": "changed"})

    def test_storage_seal_rechecks_blob_integrity(self) -> None:
        artifact = self.service.snapshot_bytes(
            self.transports[0], b"candidate to corrupt", "text/plain"
        )
        blob = (
            Path(self._temporary.name)
            / "storage"
            / "blobs"
            / artifact.ref.value
        )
        blob.write_bytes(b"Candidate to corrupt")

        with self.assertRaisesRegex(RuntimeError, "digest does not match"):
            self.storage.seal("campaign", {"selection_digest": "sha256:test"})

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

        unrelated = self.services.bind("unrelated_service")
        with self.assertRaisesRegex(PermissionError, "not permitted"):
            self.storage.authorize_read(
                unrelated, "campaign", artifact.ref, "evaluation"
            )
        with self.assertRaisesRegex(ValueError, "already has"):
            self.services.bind("artifact_service")

        authorization = self.storage.authorize_read(
            self.service_transport, "campaign", artifact.ref, "evaluation"
        )
        self.services.revoke(self.service_transport)
        replacement = self.services.bind("artifact_service")
        with self.assertRaisesRegex(PermissionError, "does not match"):
            self.storage.trusted_read(
                replacement, authorization, "evaluation"
            )

    def test_artifact_and_actor_storage_quotas_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "per-artifact"):
            self.service.snapshot_bytes(self.transports[0], b"x" * 65)
        self.service.snapshot_bytes(self.transports[0], b"a" * 60)
        with self.assertRaisesRegex(ValueError, "actor storage quota"):
            self.service.snapshot_bytes(self.transports[0], b"b" * 40)

        self.service.snapshot_bytes(self.transports[1], b"c" * 60)
        self.service.snapshot_bytes(self.transports[1], b"d" * 36)

    def test_campaign_quota_must_cover_registered_actor_allocations(self) -> None:
        root = Path(self._temporary.name) / "undersized-storage"
        storage = LocalArtifactStorage(
            root,
            self.sessions,
            self.services,
            ArtifactStoragePolicy(
                max_artifact_bytes=64,
                max_actor_bytes=96,
                max_campaign_bytes=128,
            ),
            {},
        )
        with self.assertRaisesRegex(ValueError, "cover every registered actor"):
            storage.open_campaign(
                "campaign", tuple(actor.actor_id for actor in self.actors)
            )

        changed_policy = LocalArtifactStorage(
            Path(self._temporary.name) / "storage",
            self.sessions,
            self.services,
            ArtifactStoragePolicy(
                max_artifact_bytes=64,
                max_actor_bytes=96,
                max_campaign_bytes=256,
            ),
            {},
        )
        with self.assertRaisesRegex(ValueError, "policy changed"):
            changed_policy.open_campaign(
                "campaign", tuple(actor.actor_id for actor in self.actors)
            )
        with self.assertRaisesRegex(ValueError, "roster changed"):
            self.storage.open_campaign("campaign", (self.actors[0].actor_id,))

        unregistered_actor = AgentIdentity("campaign", 2)
        unregistered = self.sessions.bind(
            unregistered_actor, SessionHandle("session-unregistered")
        )
        with self.assertRaisesRegex(PermissionError, "not registered"):
            self.service.snapshot_bytes(unregistered, b"not admitted")

    def test_pre_roster_artifacts_require_explicit_migration(self) -> None:
        root = Path(self._temporary.name) / "legacy-storage"
        storage = LocalArtifactStorage(
            root,
            self.sessions,
            self.services,
            ArtifactStoragePolicy(
                max_artifact_bytes=64,
                max_actor_bytes=96,
                max_campaign_bytes=192,
            ),
            {},
        )
        connection = sqlite3.connect(root / "artifacts.sqlite3")
        connection.execute(
            """
            INSERT INTO artifacts(
                artifact_ref, campaign_run_id, owner_actor_id,
                digest, media_type, size_bytes
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "artifact-" + "a" * 32,
                "legacy-campaign",
                "legacy-campaign:actor:omitted",
                "0" * 64,
                "application/octet-stream",
                1,
            ),
        )
        connection.commit()
        connection.close()

        registered = (
            "legacy-campaign:actor:0",
            "legacy-campaign:actor:1",
        )
        with self.assertRaisesRegex(RuntimeError, "explicit migration"):
            storage.open_campaign("legacy-campaign", registered)

    def test_registered_campaign_revalidates_stored_owners(self) -> None:
        database = Path(self._temporary.name) / "storage" / "artifacts.sqlite3"
        connection = sqlite3.connect(database)
        connection.execute(
            """
            INSERT INTO artifacts(
                artifact_ref, campaign_run_id, owner_actor_id,
                digest, media_type, size_bytes
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "artifact-" + "b" * 32,
                "campaign",
                "campaign:actor:omitted",
                "0" * 64,
                "application/octet-stream",
                1,
            ),
        )
        connection.commit()
        connection.close()

        with self.assertRaisesRegex(RuntimeError, "outside the registered roster"):
            self.storage.open_campaign(
                "campaign", tuple(actor.actor_id for actor in self.actors)
            )


if __name__ == "__main__":
    unittest.main()
