from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_collab_evals.adapters.sqlite_collaboration import (
    SqliteCollaborationBackend,
)
from agent_collab_evals.collaboration import CollaborationVisibility
from agent_collab_evals.domain import AgentIdentity, SessionHandle
from agent_collab_evals.peer_tool import (
    PeerToolAccess,
    PeerToolGateway,
    PeerToolIntegrationProfile,
)
from agent_collab_evals.session_identity import SessionIdentityRegistry


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = REPOSITORY_ROOT / "config/peer_tool_profiles/peer-tool-v0.json"


def _call(
    gateway: PeerToolGateway,
    access: PeerToolAccess,
    operation: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    return dict(gateway.invoke(access, operation, arguments))


class PeerToolTests(unittest.TestCase):
    def test_profile_pins_schema_server_and_mcp_dependency(self) -> None:
        profile = PeerToolIntegrationProfile.load(
            PROFILE_PATH, repository_root=REPOSITORY_ROOT
        )
        self.assertEqual(profile.mcp_sdk_version, "1.30.0")
        self.assertEqual(
            profile.tool_names,
            (
                "publish",
                "list_recent",
                "get_thread",
                "search",
                "notifications",
            ),
        )
        self.assertTrue(profile.resolved_digest.startswith("sha256:"))

    def test_four_actor_private_and_shared_modes_use_the_same_gateway(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            identities = SessionIdentityRegistry()
            backend = SqliteCollaborationBackend(
                Path(directory) / "collaboration.sqlite3", identities
            )
            gateway = PeerToolGateway(backend, identities, serve_http=False)
            try:
                private = self._activate_campaign(
                    gateway,
                    "private-gateway-run",
                    CollaborationVisibility.ACTOR_PRIVATE,
                )
                private_entries = [
                    _call(
                        gateway,
                        access,
                        "publish",
                        {
                            "idempotency_key": f"private-{index}",
                            "body": f"private finding {index}",
                            "reply_to": None,
                        },
                    )["entry"]
                    for index, access in enumerate(private)
                ]
                private_view = _call(
                    gateway,
                    private[0], "list_recent", {"cursor": None, "limit": 50}
                )
                self.assertEqual(private_view["items"], [private_entries[0]])
                private_notifications = _call(
                    gateway,
                    private[0],
                    "notifications",
                    {"cursor": None, "limit": 50},
                )
                self.assertEqual(private_notifications["items"], [])

                shared = self._activate_campaign(
                    gateway,
                    "shared-gateway-run",
                    CollaborationVisibility.ORGANISATION_SHARED,
                )
                shared_entries = [
                    _call(
                        gateway,
                        access,
                        "publish",
                        {
                            "idempotency_key": f"shared-{index}",
                            "body": f"shared finding {index}",
                            "reply_to": None,
                        },
                    )["entry"]
                    for index, access in enumerate(shared)
                ]
                shared_reply = _call(
                    gateway,
                    shared[1],
                    "publish",
                    {
                        "idempotency_key": "shared-reply",
                        "body": "reply to shared finding 0",
                        "reply_to": shared_entries[0]["entry_id"],
                    },
                )["entry"]
                first_page = _call(
                    gateway,
                    shared[0],
                    "list_recent",
                    {"cursor": None, "limit": 2},
                )
                second_page = _call(
                    gateway,
                    shared[0],
                    "list_recent",
                    {"cursor": first_page["next_cursor"], "limit": 50},
                )
                self.assertEqual(
                    first_page["items"] + second_page["items"],
                    shared_entries + [shared_reply],
                )
                thread = _call(
                    gateway,
                    shared[0],
                    "get_thread",
                    {"entry_id": shared_reply["entry_id"]},
                )
                self.assertEqual(
                    thread["entries"], [shared_entries[0], shared_reply]
                )
                search = _call(
                    gateway,
                    shared[0],
                    "search",
                    {"query": "shared finding 2", "cursor": None, "limit": 50},
                )
                self.assertEqual(search["items"], [shared_entries[2]])
                notifications = _call(
                    gateway,
                    shared[0],
                    "notifications",
                    {"cursor": None, "limit": 50},
                )
                self.assertEqual(len(notifications["items"]), 4)

                gateway.revoke(shared[0])
                with self.assertRaisesRegex(PermissionError, "access denied"):
                    _call(
                        gateway,
                        shared[0],
                        "list_recent",
                        {"cursor": None, "limit": 50},
                    )
            finally:
                gateway.close()

    @staticmethod
    def _activate_campaign(
        gateway: PeerToolGateway,
        campaign_run_id: str,
        visibility: CollaborationVisibility,
    ) -> tuple[PeerToolAccess, ...]:
        gateway.provision(campaign_run_id, visibility)
        accesses = []
        for ordinal in range(4):
            actor = AgentIdentity(campaign_run_id, ordinal)
            access = gateway.issue(actor)
            gateway.activate(
                access, SessionHandle(f"session-{campaign_run_id}-{ordinal}")
            )
            accesses.append(access)
        return tuple(accesses)


if __name__ == "__main__":
    unittest.main()
