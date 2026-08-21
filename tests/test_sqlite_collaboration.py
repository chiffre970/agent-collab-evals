from __future__ import annotations

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
            with self.assertRaisesRegex(KeyError, "not visible"):
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


if __name__ == "__main__":
    unittest.main()
