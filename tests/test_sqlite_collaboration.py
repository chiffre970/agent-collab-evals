from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_collab_evals.adapters.sqlite_collaboration import (
    SqliteCollaborationBackend,
)
from agent_collab_evals.collaboration import CollaborationVisibility
from agent_collab_evals.domain import AgentIdentity, SessionHandle
from agent_collab_evals.session_identity import SessionIdentityRegistry


def _sessions(
    identities: SessionIdentityRegistry, campaign_run_id: str, count: int = 4
):
    actors = tuple(AgentIdentity(campaign_run_id, ordinal) for ordinal in range(count))
    transports = tuple(
        identities.bind(actor, SessionHandle(f"session-{campaign_run_id}-{actor.ordinal}"))
        for actor in actors
    )
    return actors, transports


class SqliteCollaborationBackendTests(unittest.TestCase):
    def test_actor_private_and_shared_modes_have_twin_api_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            identities = SessionIdentityRegistry()
            backend = SqliteCollaborationBackend(
                Path(directory) / "collaboration.sqlite3", identities
            )

            private_actors, private = _sessions(identities, "private-run")
            private_scope = backend.provision(
                "private-run", CollaborationVisibility.ACTOR_PRIVATE
            )
            private_entries = tuple(
                backend.publish(
                    private_scope,
                    transport,
                    f"publish-{ordinal}",
                    f"finding from actor {ordinal}",
                )
                for ordinal, transport in enumerate(private)
            )
            private_page = backend.list_recent(private_scope, private[0])
            self.assertEqual(
                [entry.entry_id for entry in private_page.items],
                [private_entries[0].entry_id],
            )
            self.assertEqual(private_page.items[0].actor_id, private_actors[0].actor_id)
            self.assertEqual(backend.notifications(private_scope, private[0]).items, ())
            with self.assertRaisesRegex(PermissionError, "not visible"):
                backend.get_thread(
                    private_scope, private[0], private_entries[1].entry_id
                )

            shared_actors, shared = _sessions(identities, "shared-run")
            shared_scope = backend.provision(
                "shared-run", CollaborationVisibility.ORGANISATION_SHARED
            )
            shared_entries = tuple(
                backend.publish(
                    shared_scope,
                    transport,
                    f"publish-{ordinal}",
                    f"finding from actor {ordinal}",
                )
                for ordinal, transport in enumerate(shared)
            )
            first = backend.list_recent(shared_scope, shared[0], limit=2)
            self.assertEqual(len(first.items), 2)
            self.assertIsNotNone(first.next_cursor)
            second = backend.list_recent(
                shared_scope, shared[0], cursor=first.next_cursor, limit=2
            )
            self.assertEqual(len(second.items), 2)
            self.assertIsNone(second.next_cursor)
            self.assertEqual(
                [entry.entry_id for entry in (*first.items, *second.items)],
                [entry.entry_id for entry in shared_entries],
            )
            with self.assertRaisesRegex(PermissionError, "another view"):
                backend.list_recent(
                    shared_scope, shared[1], cursor=first.next_cursor, limit=2
                )
            notifications = backend.notifications(shared_scope, shared[0])
            self.assertEqual(len(notifications.items), 3)
            self.assertEqual(
                {item.actor_id for item in notifications.items},
                {actor.actor_id for actor in shared_actors[1:]},
            )

    def test_idempotent_threads_search_audit_and_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "collaboration.sqlite3"
            first_identities = SessionIdentityRegistry()
            backend = SqliteCollaborationBackend(database, first_identities)
            _, sessions = _sessions(first_identities, "durable-run", 2)
            scope = backend.provision(
                "durable-run", CollaborationVisibility.ORGANISATION_SHARED
            )
            root = backend.publish(
                scope,
                sessions[0],
                "root-key",
                "GPU profile bottleneck discovery",
                publication_ids=("publication-1",),
            )
            retried = backend.publish(
                scope,
                sessions[0],
                "root-key",
                "GPU profile bottleneck discovery",
                publication_ids=("publication-1",),
            )
            self.assertEqual(retried, root)
            with self.assertRaisesRegex(ValueError, "different collaboration content"):
                backend.publish(scope, sessions[0], "root-key", "changed")
            reply = backend.publish(
                scope,
                sessions[1],
                "reply-key",
                "I reproduced the bottleneck",
                reply_to=root.entry_id,
            )
            self.assertEqual(
                backend.get_thread(scope, sessions[0], reply.entry_id),
                (root, reply),
            )
            search = backend.search(scope, sessions[1], "  BOTTLENECK ")
            self.assertEqual(
                [entry.entry_id for entry in search.items],
                [root.entry_id, reply.entry_id],
            )

            first_page = backend.list_recent(scope, sessions[0], limit=1)
            self.assertIsNotNone(first_page.next_cursor)
            first_export = backend.export(scope)
            read_events = [
                event
                for event in first_export.audit_events
                if event["kind"] == "recent.read"
            ]
            self.assertEqual(
                read_events[-1]["details"]["entry_ids"], [root.entry_id]
            )

            resumed_identities = SessionIdentityRegistry()
            _, resumed = _sessions(resumed_identities, "durable-run", 2)
            resumed_backend = SqliteCollaborationBackend(database, resumed_identities)
            second_page = resumed_backend.list_recent(
                scope, resumed[0], cursor=first_page.next_cursor, limit=1
            )
            self.assertEqual(second_page.items, (reply,))
            self.assertEqual(resumed_backend.export(scope).entries, (root, reply))

            _, foreign = _sessions(resumed_identities, "foreign-run", 1)
            with self.assertRaisesRegex(PermissionError, "different campaign"):
                resumed_backend.list_recent(scope, foreign[0])

            resumed_backend.reset(scope)
            with self.assertRaisesRegex(KeyError, "unknown collaboration scope"):
                resumed_backend.export(scope)

    def test_expired_or_forged_transport_is_rejected(self) -> None:
        identities = SessionIdentityRegistry()
        actor = AgentIdentity("identity-run", 0)
        transport = identities.bind(actor, SessionHandle("session-0"))
        identities.revoke(transport)

        with self.assertRaisesRegex(PermissionError, "unknown or expired"):
            identities.resolve(transport)

    def test_notification_cursor_advances_when_reader_is_caught_up(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            identities = SessionIdentityRegistry()
            backend = SqliteCollaborationBackend(
                Path(directory) / "collaboration.sqlite3", identities
            )
            _, sessions = _sessions(identities, "notification-run", 2)
            scope = backend.provision(
                "notification-run", CollaborationVisibility.ORGANISATION_SHARED
            )
            first = backend.publish(scope, sessions[1], "first", "first")
            first_poll = backend.notifications(scope, sessions[0])
            self.assertEqual(
                [item.entry_id for item in first_poll.items], [first.entry_id]
            )
            self.assertIsNotNone(first_poll.next_cursor)

            second = backend.publish(scope, sessions[1], "second", "second")
            second_poll = backend.notifications(
                scope, sessions[0], cursor=first_poll.next_cursor
            )
            self.assertEqual(
                [item.entry_id for item in second_poll.items], [second.entry_id]
            )
            self.assertIsNotNone(second_poll.next_cursor)
            caught_up = backend.notifications(
                scope, sessions[0], cursor=second_poll.next_cursor
            )
            self.assertEqual(caught_up.items, ())
            self.assertIsNotNone(caught_up.next_cursor)

    def test_private_sequences_and_watermarks_are_actor_local(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            identities = SessionIdentityRegistry()
            backend = SqliteCollaborationBackend(
                Path(directory) / "collaboration.sqlite3", identities
            )
            _, sessions = _sessions(identities, "private-sequence-run", 2)
            scope = backend.provision(
                "private-sequence-run", CollaborationVisibility.ACTOR_PRIVATE
            )
            for index in range(3):
                entry = backend.publish(
                    scope, sessions[0], f"actor-0-{index}", f"entry {index}"
                )
                self.assertEqual(entry.sequence, index + 1)

            poll = backend.notifications(scope, sessions[1])
            self.assertEqual(poll.items, ())
            assert poll.next_cursor is not None
            encoded_payload = poll.next_cursor.split(".", 1)[0]
            payload = json.loads(
                base64.urlsafe_b64decode(
                    encoded_payload + "=" * (-len(encoded_payload) % 4)
                )
            )
            self.assertEqual(payload["after"], 0)
            actor_one_entry = backend.publish(
                scope, sessions[1], "actor-1-first", "my first entry"
            )
            self.assertEqual(actor_one_entry.sequence, 1)
            self.assertEqual(
                backend.list_recent(scope, sessions[1]).items,
                (actor_one_entry,),
            )

    def test_tampered_cursor_denial_is_audited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            identities = SessionIdentityRegistry()
            backend = SqliteCollaborationBackend(
                Path(directory) / "collaboration.sqlite3", identities
            )
            _, sessions = _sessions(identities, "cursor-audit-run", 2)
            scope = backend.provision(
                "cursor-audit-run", CollaborationVisibility.ORGANISATION_SHARED
            )
            cursor = backend.notifications(scope, sessions[0]).next_cursor
            assert cursor is not None
            payload, signature = cursor.split(".", 1)
            replacement = "A" if signature[0] != "A" else "B"
            tampered = f"{payload}.{replacement}{signature[1:]}"
            with self.assertRaisesRegex(PermissionError, "invalid.*cursor"):
                backend.notifications(scope, sessions[0], cursor=tampered)
            denials = [
                event
                for event in backend.export(scope).audit_events
                if event["kind"] == "authorization.denied"
            ]
            self.assertEqual(denials[-1]["details"]["reason"], "invalid_cursor")

    def test_legacy_global_sequences_migrate_to_actor_local_sequences(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "collaboration.sqlite3"
            scope_id = "collab:" + hashlib.sha256(
                b"legacy-run"
            ).hexdigest()[:24]
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE scopes(
                    scope_id TEXT PRIMARY KEY,
                    campaign_run_id TEXT NOT NULL UNIQUE,
                    visibility TEXT NOT NULL
                );
                CREATE TABLE entries(
                    scope_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    entry_id TEXT NOT NULL UNIQUE,
                    actor_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    reply_to TEXT,
                    thread_root TEXT NOT NULL,
                    publication_ids TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    PRIMARY KEY(scope_id, sequence),
                    UNIQUE(scope_id, actor_id, idempotency_key)
                );
                CREATE TABLE audit(
                    scope_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    actor_id TEXT,
                    details TEXT NOT NULL,
                    PRIMARY KEY(scope_id, sequence)
                );
                """
            )
            connection.execute(
                "INSERT INTO scopes VALUES(?, 'legacy-run', 'actor_private')",
                (scope_id,),
            )
            for sequence, actor_id in enumerate(
                ("legacy-run:actor:0", "legacy-run:actor:1", "legacy-run:actor:0"),
                start=1,
            ):
                connection.execute(
                    """
                    INSERT INTO entries VALUES(
                        ?, ?, ?, ?, ?, NULL, ?, '[]', ?, ?
                    )
                    """,
                    (
                        scope_id,
                        sequence,
                        f"entry-{sequence}",
                        actor_id,
                        f"body-{sequence}",
                        f"entry-{sequence}",
                        f"key-{sequence}",
                        f"digest-{sequence}",
                    ),
                )
            connection.commit()
            connection.close()

            identities = SessionIdentityRegistry()
            backend = SqliteCollaborationBackend(database, identities)
            _, sessions = _sessions(identities, "legacy-run", 2)
            scope = backend.provision(
                "legacy-run", CollaborationVisibility.ACTOR_PRIVATE
            )
            actor_zero = backend.list_recent(scope, sessions[0]).items
            actor_one = backend.list_recent(scope, sessions[1]).items
            self.assertEqual([entry.sequence for entry in actor_zero], [1, 2])
            self.assertEqual([entry.sequence for entry in actor_one], [1])

    def test_cross_campaign_authorization_denial_is_audited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            identities = SessionIdentityRegistry()
            backend = SqliteCollaborationBackend(
                Path(directory) / "collaboration.sqlite3", identities
            )
            _, owner = _sessions(identities, "owner-run", 1)
            _, foreign = _sessions(identities, "foreign-run", 1)
            scope = backend.provision(
                "owner-run", CollaborationVisibility.ORGANISATION_SHARED
            )
            with self.assertRaisesRegex(PermissionError, "different campaign"):
                backend.list_recent(scope, foreign[0])
            denials = [
                event
                for event in backend.export(scope).audit_events
                if event["kind"] == "authorization.denied"
            ]
            self.assertEqual(len(denials), 1)
            self.assertEqual(
                denials[0]["details"]["reason"], "cross_campaign_session"
            )

    def test_one_runtime_session_cannot_bind_to_multiple_actors(self) -> None:
        identities = SessionIdentityRegistry()
        session = SessionHandle("shared-session")
        first = identities.bind(AgentIdentity("identity-run", 0), session)
        with self.assertRaisesRegex(ValueError, "already bound"):
            identities.bind(AgentIdentity("identity-run", 1), session)
        identities.revoke(first)
        rebound = identities.bind(AgentIdentity("identity-run", 1), session)
        self.assertEqual(
            identities.resolve(rebound).actor_id,
            AgentIdentity("identity-run", 1).actor_id,
        )


if __name__ == "__main__":
    unittest.main()
