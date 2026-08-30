from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_collab_evals.canonical import digest_value
from agent_collab_evals.domain import CoordinationCondition
from agent_collab_evals.study_schedule import (
    ASSIGNMENT_ALGORITHM,
    BlockInput,
    RandomizedBlockPlan,
    ResolvedRunManifest,
)


class StudyScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inputs = (
            BlockInput(
                "block-001",
                "replicate-001",
                "model-serving-v0",
                101,
                digest_value({"materials": "block-001"}),
            ),
            BlockInput(
                "block-002",
                "replicate-002",
                "model-serving-v0",
                202,
                digest_value({"materials": "block-002"}),
            ),
        )
        self.plan = RandomizedBlockPlan.create(
            master_seed=970,
            organisation_size=4,
            blocks=self.inputs,
        )

    def test_complete_blocks_are_deterministic_and_condition_balanced(self) -> None:
        repeated = RandomizedBlockPlan.create(
            master_seed=970,
            organisation_size=4,
            blocks=self.inputs,
        )

        self.assertEqual(self.plan, repeated)
        self.assertEqual(self.plan.algorithm, ASSIGNMENT_ALGORITHM)
        self.assertEqual(self.plan.digest, repeated.digest)
        for block, source in zip(self.plan.blocks, self.inputs, strict=True):
            self.assertEqual(block.task_seed, source.task_seed)
            self.assertEqual(
                block.task_material_digest, source.task_material_digest
            )
            self.assertEqual(
                {run.assigned_condition for run in block.runs},
                set(CoordinationCondition),
            )
            self.assertEqual(
                tuple(run.execution_position for run in block.runs),
                (1, 2, 3, 4),
            )
            self.assertTrue(
                all(len(run.actor_stochastic_seeds) == 4 for run in block.runs)
            )

    def test_seed_change_creates_a_separate_plan(self) -> None:
        changed = RandomizedBlockPlan.create(
            master_seed=971,
            organisation_size=4,
            blocks=self.inputs,
        )

        self.assertNotEqual(self.plan.digest, changed.digest)
        self.assertNotEqual(
            self.plan.blocks[0].runs[0].run_stochastic_seed,
            changed.blocks[0].runs[0].run_stochastic_seed,
        )

    def test_write_load_and_resolve_are_content_addressed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.plan.write_once(root / "block-plan.json")
            loaded = RandomizedBlockPlan.load(path)
            run = loaded.blocks[0].runs[2]

            resolved = loaded.resolve(
                study_manifest_digest=digest_value({"study": "flash-v0"}),
                run_id=run.run_id,
                resolved_configuration_digest=digest_value(
                    {"configuration": "flash-v0"}
                ),
            )
            resolved_path = resolved.write_once(root / "resolved-run.json")
            loaded_resolved = ResolvedRunManifest.load(
                resolved_path,
                plan=loaded,
                study_manifest_digest=resolved.study_manifest_digest,
                resolved_configuration_digest=(
                    resolved.resolved_configuration_digest
                ),
            )

            self.assertEqual(loaded, self.plan)
            self.assertEqual(resolved.condition, run.assigned_condition)
            self.assertEqual(resolved.execution_position, 3)
            self.assertEqual(
                resolved.task_material_digest,
                self.inputs[0].task_material_digest,
            )
            self.assertEqual(len(resolved.actor_stochastic_seeds), 4)
            self.assertEqual(loaded_resolved, resolved)
            self.assertEqual(
                json.loads(resolved_path.read_text(encoding="utf-8"))[
                    "condition"
                ],
                run.assigned_condition.value,
            )

    def test_resolved_manifest_cannot_change_its_assigned_condition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = self.plan.blocks[0].runs[0]
            study_digest = digest_value({"study": "flash-v0"})
            configuration_digest = digest_value({"configuration": "flash-v0"})
            resolved = self.plan.resolve(
                study_manifest_digest=study_digest,
                run_id=run.run_id,
                resolved_configuration_digest=configuration_digest,
            )
            path = resolved.write_once(root / "resolved-run.json")
            document = json.loads(path.read_text(encoding="utf-8"))
            document["condition"] = next(
                condition.value
                for condition in CoordinationCondition
                if condition is not run.assigned_condition
            )
            path.write_text(json.dumps(document), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "differs from its block plan"):
                ResolvedRunManifest.load(
                    path,
                    plan=self.plan,
                    study_manifest_digest=study_digest,
                    resolved_configuration_digest=configuration_digest,
                )

    def test_loader_recomputes_assignment_and_rejects_condition_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self.plan.write_once(Path(temporary) / "block-plan.json")
            document = json.loads(path.read_text(encoding="utf-8"))
            first = document["blocks"][0]["runs"][0]
            second = document["blocks"][0]["runs"][1]
            first["assigned_condition"], second["assigned_condition"] = (
                second["assigned_condition"],
                first["assigned_condition"],
            )
            path.write_text(json.dumps(document), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "differs from its algorithm"):
                RandomizedBlockPlan.load(path)

    def test_write_once_rejects_a_changed_registered_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self.plan.write_once(Path(temporary) / "block-plan.json")
            changed = RandomizedBlockPlan.create(
                master_seed=971,
                organisation_size=4,
                blocks=self.inputs,
            )

            with self.assertRaisesRegex(RuntimeError, "already differs"):
                changed.write_once(path)

    def test_unknown_run_and_invalid_actor_seed_cardinality_fail_closed(self) -> None:
        with self.assertRaisesRegex(KeyError, "unavailable"):
            self.plan.assignment("missing-run")
        with self.assertRaisesRegex(ValueError, "actor seed count"):
            RandomizedBlockPlan(
                master_seed=self.plan.master_seed,
                organisation_size=3,
                blocks=self.plan.blocks,
            )


if __name__ == "__main__":
    unittest.main()
