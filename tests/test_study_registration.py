from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_collab_evals.study_registration import (
    StudyCompositionCandidate,
    StudyRegistrationError,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COMPOSITION_PATH = (
    REPOSITORY_ROOT
    / "config/studies/model-serving-flash-v0.registration-candidate.json"
)
PRIVATE_MANIFEST = (
    REPOSITORY_ROOT
    / "tmp/evaluator-private/model-serving-hidden-study-v1/manifest.json"
)


class StudyRegistrationTests(unittest.TestCase):
    def test_candidate_pins_current_inputs_and_fails_closed(self) -> None:
        candidate = StudyCompositionCandidate.load(
            COMPOSITION_PATH, repository_root=REPOSITORY_ROOT
        )

        self.assertEqual(candidate.study_id, "model-serving-flash-v0")
        self.assertEqual(len(candidate.profiles), 14)
        self.assertTrue(candidate.resolved_configuration_digest.startswith("sha256:"))
        with self.assertRaisesRegex(
            StudyRegistrationError, "cannot authorize execution"
        ):
            candidate.assert_execution_authorized()

    @unittest.skipUnless(PRIVATE_MANIFEST.is_file(), "private study bundle unavailable")
    def test_candidate_resolves_the_fresh_private_bundle(self) -> None:
        candidate = StudyCompositionCandidate.load(
            COMPOSITION_PATH, repository_root=REPOSITORY_ROOT
        )

        bundle = candidate.verify_hidden_bundle(PRIVATE_MANIFEST)

        self.assertEqual(
            bundle.manifest_digest, candidate.hidden_workload.manifest_digest
        )

    def test_changed_profile_digest_fails_closed(self) -> None:
        with COMPOSITION_PATH.open("r", encoding="utf-8") as source:
            document = json.load(source)
        document["profiles"][0]["digest"] = "sha256:" + "0" * 64
        with tempfile.TemporaryDirectory() as temporary:
            changed = Path(temporary) / "composition.json"
            changed.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(StudyRegistrationError, "file digest differs"):
                StudyCompositionCandidate.load(
                    changed, repository_root=REPOSITORY_ROOT
                )


if __name__ == "__main__":
    unittest.main()
