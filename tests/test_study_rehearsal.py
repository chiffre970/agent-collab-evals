from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_collab_evals.canonical import canonical_json_bytes, digest_bytes, digest_value
from agent_collab_evals.study_registration import StudyCompositionCandidate
from agent_collab_evals.study_rehearsal import (
    NoSpendStudyAuthority,
    NoSpendStudyRunner,
    StudyRehearsalError,
    verify_no_spend_study_audit,
)
from agent_collab_evals.study_schedule import BlockInput, RandomizedBlockPlan


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COMPOSITION_PATH = (
    REPOSITORY_ROOT
    / "config/studies/model-serving-flash-v0.registration-candidate.json"
)


class StudyRehearsalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.composition = StudyCompositionCandidate.load(
            COMPOSITION_PATH, repository_root=REPOSITORY_ROOT
        )
        self.task_seed = 1729
        materialized = self.composition.campaign.materialize(self.task_seed)
        self.plan = RandomizedBlockPlan.create(
            master_seed=970,
            organisation_size=4,
            blocks=(
                BlockInput(
                    "rehearsal-block-001",
                    "rehearsal-replicate-001",
                    "model-serving-v0",
                    self.task_seed,
                    materialized.material_digest,
                ),
            ),
        )

    def test_executes_complete_no_spend_four_condition_rehearsal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            authority = NoSpendStudyAuthority.create(
                root / "authority.json",
                rehearsal_id="rehearsal-001",
                composition=self.composition,
                block_plan=self.plan,
                repository_root=REPOSITORY_ROOT,
            )
            result = NoSpendStudyRunner(
                composition=self.composition,
                block_plan=self.plan,
                authority=authority,
                state_root=root / "execution",
            ).run()

            audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
            runs = audit["blocks"][0]["runs"]

            self.assertEqual(result.block_count, 1)
            self.assertEqual(result.run_count, 4)
            self.assertFalse(audit["scoreable"])
            self.assertFalse(audit["treatment_surfaces_exercised"])
            self.assertEqual(audit["external_model_calls"], 0)
            self.assertEqual(audit["external_compute_executions"], 0)
            self.assertEqual(
                {run["condition"] for run in runs},
                {"solo", "native_multiagent", "peer_isolated", "peer_collab"},
            )
            self.assertEqual(
                {
                    run["condition"]: run["top_level_session_count"]
                    for run in runs
                },
                {
                    "solo": 1,
                    "native_multiagent": 1,
                    "peer_isolated": 4,
                    "peer_collab": 4,
                },
            )
            self.assertEqual(
                {run["task_material_digest"] for run in runs},
                {self.plan.blocks[0].task_material_digest},
            )
            self.assertTrue(
                all(run["event_count"] == 6 for run in runs),
                runs,
            )
            self.assertTrue(
                all((root / "execution" / run["resolved_run_path"]).is_file()
                    for run in runs)
            )
            verified = verify_no_spend_study_audit(
                result.audit_path,
                expected_digest=result.audit_digest,
                composition=self.composition,
                block_plan=self.plan,
                authority=authority,
            )
            self.assertEqual(verified, result)

    def test_authority_semantic_tampering_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "authority.json"
            authority = NoSpendStudyAuthority.create(
                path,
                rehearsal_id="rehearsal-002",
                composition=self.composition,
                block_plan=self.plan,
                repository_root=REPOSITORY_ROOT,
            )
            document = json.loads(path.read_text(encoding="utf-8"))
            document["external_model_calls"] = True
            content = canonical_json_bytes(document)
            path.write_bytes(content)

            with self.assertRaisesRegex(
                StudyRehearsalError, "authority semantics differ"
            ):
                NoSpendStudyAuthority.load(
                    path,
                    expected_digest=digest_bytes(content),
                    composition=self.composition,
                    block_plan=self.plan,
                    repository_root=REPOSITORY_ROOT,
                )
            self.assertNotEqual(authority.digest, digest_bytes(content))

    def test_changed_block_material_fails_before_campaign_execution(self) -> None:
        changed = RandomizedBlockPlan.create(
            master_seed=970,
            organisation_size=4,
            blocks=(
                BlockInput(
                    "rehearsal-block-001",
                    "rehearsal-replicate-001",
                    "model-serving-v0",
                    self.task_seed,
                    digest_value({"materials": "changed"}),
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            authority = NoSpendStudyAuthority.create(
                root / "authority.json",
                rehearsal_id="rehearsal-003",
                composition=self.composition,
                block_plan=changed,
                repository_root=REPOSITORY_ROOT,
            )

            with self.assertRaisesRegex(
                StudyRehearsalError, "block material digest differs"
            ):
                NoSpendStudyRunner(
                    composition=self.composition,
                    block_plan=changed,
                    authority=authority,
                    state_root=root / "execution",
                ).run()
            self.assertFalse((root / "execution" / "runs").exists())

    def test_retained_evidence_tampering_fails_audit_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            authority = NoSpendStudyAuthority.create(
                root / "authority.json",
                rehearsal_id="rehearsal-evidence-tamper",
                composition=self.composition,
                block_plan=self.plan,
                repository_root=REPOSITORY_ROOT,
            )
            result = NoSpendStudyRunner(
                composition=self.composition,
                block_plan=self.plan,
                authority=authority,
                state_root=root / "execution",
            ).run()
            first_run_id = self.plan.blocks[0].runs[0].run_id
            run_audit_path = (
                root / "execution" / "runs" / first_run_id / "run-audit.json"
            )
            retained = json.loads(run_audit_path.read_text(encoding="utf-8"))
            retained["event_count"] = 999
            run_audit_path.write_bytes(canonical_json_bytes(retained))

            with self.assertRaisesRegex(
                StudyRehearsalError, "retained run audit differs"
            ):
                verify_no_spend_study_audit(
                    result.audit_path,
                    expected_digest=result.audit_digest,
                    composition=self.composition,
                    block_plan=self.plan,
                    authority=authority,
                )

    def test_rehearsal_cannot_overwrite_a_completed_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            authority = NoSpendStudyAuthority.create(
                root / "authority.json",
                rehearsal_id="rehearsal-004",
                composition=self.composition,
                block_plan=self.plan,
                repository_root=REPOSITORY_ROOT,
            )
            runner = NoSpendStudyRunner(
                composition=self.composition,
                block_plan=self.plan,
                authority=authority,
                state_root=root / "execution",
            )
            runner.run()

            with self.assertRaisesRegex(
                StudyRehearsalError, "audit already exists"
            ):
                runner.run()


if __name__ == "__main__":
    unittest.main()
