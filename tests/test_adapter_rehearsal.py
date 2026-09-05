from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_collab_evals.adapter_rehearsal import (
    run_adapter_condition_rehearsal,
    run_solo_adapter_rehearsal,
    verify_adapter_condition_rehearsal,
    verify_solo_adapter_rehearsal,
)
from agent_collab_evals.canonical import canonical_json_bytes, digest_bytes
from agent_collab_evals.domain import CoordinationCondition


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.environ.get("RUN_MODEL_GATEWAY_INTEGRATION") == "1",
    "set RUN_MODEL_GATEWAY_INTEGRATION=1 to run the real-adapter rehearsal",
)
class AdapterRehearsalIntegrationTests(unittest.TestCase):
    def test_real_adapters_complete_and_retain_a_no_spend_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_solo_adapter_rehearsal(
                campaign_path=REPOSITORY_ROOT
                / "campaigns/model_serving_v0/campaign.toml",
                state_root=Path(directory),
                run_id="real-adapter-integration",
                repository_root=REPOSITORY_ROOT,
            )

            content = result.audit_path.read_bytes()
            audit = json.loads(content)
            self.assertEqual(result.audit_digest, digest_bytes(content))
            self.assertFalse(audit["scoreable"])
            self.assertEqual(audit["schema_version"], "real-adapter-condition-rehearsal/v2")
            self.assertEqual(audit["task_seed"], 1729)
            self.assertTrue((result.audit_path.parent / "model-requests.json").is_file())
            self.assertEqual(audit["external_model_calls"], 0)
            self.assertEqual(audit["external_compute_executions"], 0)
            self.assertGreater(audit["synthetic_model_calls"], 0)
            self.assertEqual(audit["compute_execution_count"], 0)
            self.assertTrue(audit["budget_reconciliation"]["valid"])
            self.assertEqual(
                audit["delivered_job_ids"],
                ["optimize-serving", "coordination-conformance"],
            )
            self.assertTrue(
                (result.audit_path.parent / "final-harness-snapshot.json").is_file()
            )
            verified = verify_solo_adapter_rehearsal(
                result.audit_path,
                expected_digest=result.audit_digest,
                campaign_path=REPOSITORY_ROOT
                / "campaigns/model_serving_v0/campaign.toml",
                repository_root=REPOSITORY_ROOT,
            )
            self.assertEqual(verified, result)

            audit["treatment_evidence"]["peer_publish_calls"] = 1
            changed = canonical_json_bytes(audit)
            result.audit_path.write_bytes(changed)
            with self.assertRaisesRegex(RuntimeError, "treatment evidence differs"):
                verify_solo_adapter_rehearsal(
                    result.audit_path,
                    expected_digest=digest_bytes(changed),
                    campaign_path=REPOSITORY_ROOT
                    / "campaigns/model_serving_v0/campaign.toml",
                    repository_root=REPOSITORY_ROOT,
                )

    def test_all_conditions_exercise_their_real_runtime_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audits: dict[CoordinationCondition, dict[str, object]] = {}
            for condition in CoordinationCondition:
                result = run_adapter_condition_rehearsal(
                    campaign_path=REPOSITORY_ROOT
                    / "campaigns/model_serving_v0/campaign.toml",
                    state_root=root,
                    run_id=f"real-adapter-{condition.value}",
                    condition=condition,
                    organisation_size=4,
                    repository_root=REPOSITORY_ROOT,
                )
                audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
                audits[condition] = audit
                self.assertTrue(audit["treatment_surfaces_exercised"])
                self.assertEqual(audit["external_model_calls"], 0)
                self.assertEqual(audit["external_compute_executions"], 0)
                self.assertEqual(
                    verify_adapter_condition_rehearsal(
                        result.audit_path,
                        expected_digest=result.audit_digest,
                        campaign_path=REPOSITORY_ROOT
                        / "campaigns/model_serving_v0/campaign.toml",
                        expected_condition=condition,
                        repository_root=REPOSITORY_ROOT,
                    ),
                    result,
                )

            self.assertGreater(
                audits[CoordinationCondition.NATIVE_MULTIAGENT][
                    "treatment_evidence"
                ]["native_child_model_calls"],
                0,
            )
            self.assertEqual(
                audits[CoordinationCondition.PEER_ISOLATED][
                    "collaboration_evidence"
                ]["cross_actor_read_count"],
                0,
            )
            self.assertGreater(
                audits[CoordinationCondition.PEER_COLLAB][
                    "collaboration_evidence"
                ]["cross_actor_read_count"],
                0,
            )


if __name__ == "__main__":
    unittest.main()
