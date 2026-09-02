from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_collab_evals.adapters.disabled_research import DisabledResearchBroker
from agent_collab_evals.adapters.modal_vllm_compute import ModalVllmCliTransport
from agent_collab_evals.adapters.modal_vllm_correctness_compute import (
    ModalVllmCorrectnessCliTransport,
)
from agent_collab_evals.adapters.modal_vllm_quality_compute import (
    ModalVllmQualityCliTransport,
)
from agent_collab_evals.adapters.sqlite_collaboration import (
    SqliteCollaborationBackend,
)
from agent_collab_evals.canonical import digest_value
from agent_collab_evals.session_identity import SessionIdentityRegistry
from agent_collab_evals.registered_profiles import (
    RegisteredProfileError,
    load_collaboration_profile,
    load_enforcement_requirements,
    load_hidden_evaluation_profile,
    load_research_profile,
)
from tests.quality_fixture import REPOSITORY_ROOT


class RegisteredProfileTests(unittest.TestCase):
    def test_frozen_profiles_validate_semantically(self) -> None:
        hidden = load_hidden_evaluation_profile(
            REPOSITORY_ROOT
            / "config/evaluation_profiles/model-serving-hidden-v1.json"
        )
        collaboration = load_collaboration_profile(
            REPOSITORY_ROOT
            / "config/collaboration_profiles/sqlite-peer-v1.json"
        )
        research = load_research_profile(
            REPOSITORY_ROOT / "config/research_profiles/disabled-v1.json"
        )
        enforcement = load_enforcement_requirements(
            REPOSITORY_ROOT
            / "config/enforcement_profiles/model-serving-v0-requirements.json"
        )

        self.assertEqual(hidden.document["outer_hidden_reserved_seconds"], 9_600)
        self.assertEqual(collaboration.document["pagination_limit"], 100)
        self.assertEqual(research.document["network_access"], "none")
        self.assertFalse(enforcement.document["execution_authorized"])

    def test_hidden_allowance_tampering_fails_closed(self) -> None:
        source = (
            REPOSITORY_ROOT
            / "config/evaluation_profiles/model-serving-hidden-v1.json"
        )
        with source.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
        document["outer_hidden_reserved_seconds"] -= 1
        with tempfile.TemporaryDirectory() as temporary:
            changed = Path(temporary) / "hidden.json"
            changed.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(
                RegisteredProfileError, "do not partition"
            ):
                load_hidden_evaluation_profile(changed)

    def test_disabled_research_broker_denies_every_request(self) -> None:
        broker = DisabledResearchBroker.from_profile(
            REPOSITORY_ROOT / "config/research_profiles/disabled-v1.json"
        )

        self.assertEqual(broker.capabilities()["enabled"], False)
        with self.assertRaisesRegex(PermissionError, "disabled"):
            broker.search(object(), "query")
        with self.assertRaisesRegex(PermissionError, "disabled"):
            broker.fetch(object(), "resource")

    def test_collaboration_backend_binds_registered_profile(self) -> None:
        profile = load_collaboration_profile(
            REPOSITORY_ROOT
            / "config/collaboration_profiles/sqlite-peer-v1.json"
        )
        with tempfile.TemporaryDirectory() as temporary:
            backend = SqliteCollaborationBackend(
                Path(temporary) / "collaboration.sqlite3",
                SessionIdentityRegistry(),
                profile,
            )

        self.assertEqual(backend.profile_digest, profile.authority_digest)

    def test_modal_transport_authority_does_not_depend_on_host_path(self) -> None:
        profile = digest_value({"profile": "registered"})
        authorization = digest_value({"authorization": "registered"})
        paths = (Path("/host-a/bin/modal"), Path("/host-b/bin/modal"))

        for transport in (
            ModalVllmCliTransport,
            ModalVllmCorrectnessCliTransport,
            ModalVllmQualityCliTransport,
        ):
            self.assertEqual(
                transport.profile_digest_for(profile, paths[0], authorization),
                transport.profile_digest_for(profile, paths[1], authorization),
            )


if __name__ == "__main__":
    unittest.main()
