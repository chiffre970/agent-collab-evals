"""Matched peer candidate lifecycles without external model or compute calls."""

import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agent_collab_evals.adapters.fake_serving_evaluator import FakeModelServingEvaluator
from agent_collab_evals.adapters.synthetic_pilot_compute import SyntheticPilotTransport
from agent_collab_evals.adapters.sqlite_compute import SqliteComputeBroker
from agent_collab_evals.candidate_services import create_candidate_services
from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.canonical import digest_bytes, digest_file, digest_value
from agent_collab_evals.cli import main
from agent_collab_evals.domain import AgentIdentity, SessionHandle
from agent_collab_evals.evaluation import ActorComputeAllocation, ComputePlan, EvaluationInProgress, SubmissionPolicy
from agent_collab_evals.pilot_evidence import retain_document
from agent_collab_evals.solo_evaluation_handoff import CandidateEvaluationHandoff
from agent_collab_evals.solo_pilot_command import PilotAborted, run_peer_pilot


REPOSITORY = Path(__file__).resolve().parents[1]
CONFIG = REPOSITORY / "config/pilots/peer-isolated-no-spend-oci-v1.json"


class PeerPilotTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def config(self, **changes):
        document = json.loads(CONFIG.read_bytes())
        document.update(runtime="fake", sandbox_profile="config/sandbox_profiles/darwin-loopback-network-v0.json")
        document.update(changes)
        path = self.root / f"config-{len(list(self.root.glob('config-*')))}.json"
        retain_document(path, document)
        return path

    def test_both_arms_complete_the_same_candidate_and_evaluation_path(self):
        tasks = []
        for condition in ("peer_isolated", "peer_collab"):
            with self.subTest(condition=condition):
                result = run_peer_pilot(self.config(condition=condition), self.root, condition)
                root = Path(result["audit_path"]).parent
                audit = json.loads((root / "audit.json").read_bytes())
                self.assertEqual(result["audit_digest"], digest_file(root / "audit.json"))
                self.assertEqual(audit["status"], "complete")
                self.assertEqual(audit["condition"], condition)
                self.assertFalse(audit["scoreable"])
                self.assertEqual(audit["actual_spend_usd_nanos"], 0)
                self.assertEqual(audit["external_compute_executions"], 0)
                self.assertEqual(audit["synthetic_compute_executions"], 15)
                self.assertEqual(audit["synthetic_compute_seconds"], 105)
                self.assertTrue(audit["budget_reconciliation"]["valid"])
                self.assertEqual(audit["peer_entry_count"], 4)
                self.assertEqual(audit["peer_read_count"], 4)
                self.assertEqual(audit["cross_actor_read_count"], 0 if condition == "peer_isolated" else 12)
                for name, digest in audit["evidence_digests"].items():
                    self.assertEqual(digest_file(root / name), digest)
                budget = json.loads((root / "budget-plan.json").read_bytes())
                self.assertEqual(budget["organisation_limit_usd_nanos"], 1000000000)
                self.assertEqual([a["limit_usd_nanos"] for a in budget["allocations"]], [250000000] * 4)
                compute = json.loads((root / "compute-plan.json").read_bytes())
                self.assertEqual(compute["organisation_limit_seconds"], 240)
                self.assertEqual([a["limit_seconds"] for a in compute["actor_allocations"]], [60] * 4)
                snapshot = json.loads((root / "compute-snapshot.json").read_bytes())
                self.assertEqual(list(snapshot["actor_used_seconds"].values()), [7] * 4)
                submissions = json.loads((root / "public-submissions.json").read_bytes())
                self.assertEqual(len(submissions["candidates"]), 4)
                self.assertEqual(len({c["owner_actor_id"] for c in submissions["candidates"]}), 4)
                tasks.append(json.loads((root / "task.json").read_bytes()))
        self.assertEqual(tasks[0], tasks[1])

    def test_stock_reference_winner_still_receives_hidden_evaluation(self):
        result = run_peer_pilot(self.config(synthetic_candidate_public_ppm=900000), self.root, "default")
        audit = json.loads(Path(result["audit_path"]).read_bytes())
        self.assertTrue(audit["used_default"])
        self.assertEqual(audit["synthetic_compute_executions"], 15)
        self.assertTrue(audit["hidden_result"]["eligible"])

    def test_twin_configs_differ_only_in_visibility_condition(self):
        isolated = json.loads(CONFIG.read_bytes())
        collaborative = json.loads((REPOSITORY / "config/pilots/peer-collab-no-spend-oci-v1.json").read_bytes())
        self.assertEqual(isolated.pop("condition"), "peer_isolated")
        self.assertEqual(collaborative.pop("condition"), "peer_collab")
        self.assertEqual(isolated, collaborative)

    def test_retained_real_sandbox_observation_binds_both_no_spend_arms(self):
        record = json.loads((REPOSITORY / "evidence/deployment/peer-candidate-nospend-20261005.json").read_bytes())
        self.assertFalse(record["registered_conformance_complete"])
        self.assertTrue(record["working_tree_implementation"])
        self.assertEqual(len(record["runs"]), 2)
        materials, sources, profiles = [], [], []
        for run in record["runs"]:
            audit, config = run["audit"], run["configuration"]
            self.assertEqual(digest_value(audit), run["audit_digest"])
            self.assertEqual(digest_value(config), run["configuration_digest"])
            self.assertEqual(audit["run_config_digest"], run["configuration_digest"])
            for name in ("engine_identity", "container_observation"):
                raw = (json.dumps(run[name], indent=2) + "\n").encode()
                self.assertEqual(digest_bytes(raw), run[name + "_file_digest"])
            self.assertEqual(audit["status"], "complete")
            self.assertFalse(audit["scoreable"])
            self.assertFalse(audit["live_execution_authorized"])
            self.assertEqual(audit["actual_spend_usd_nanos"], 0)
            self.assertEqual(audit["external_model_calls"], 0)
            self.assertEqual(audit["external_compute_executions"], 0)
            self.assertEqual(audit["synthetic_compute_executions"], 15)
            self.assertEqual(audit["synthetic_model_calls"], 32)
            self.assertTrue(audit["budget_reconciliation"]["valid"])
            self.assertEqual(run["container_observation"]["new_containers_remaining"], [])
            self.assertEqual(audit["cross_actor_read_count"], 0 if audit["condition"] == "peer_isolated" else 24)
            materials.append(run["task_material_digest"])
            sources.append(config["platform_source_digest"])
            profiles.append(config["peer_tool_profile_digest"])
        self.assertEqual(materials[0], materials[1])
        self.assertEqual(sources[0], sources[1])
        self.assertEqual(profiles[0], profiles[1])

    def test_per_actor_accounting_redistribution_rejects_closure(self):
        original = SqliteComputeBroker.snapshot

        def snapshot(broker, run_id):
            result = original(broker, run_id)
            actors = list(result.actor_used_seconds)
            usage = dict(result.actor_used_seconds)
            if len(actors) == 4 and all(usage.values()):
                usage[actors[0]] -= 1
                usage[actors[1]] += 1
                return replace(result, actor_used_seconds=usage)
            return result

        with patch.object(SqliteComputeBroker, "snapshot", snapshot):
            with self.assertRaises(PilotAborted):
                run_peer_pilot(self.config(), self.root, "redistributed")
        audit = json.loads((self.root / "redistributed/audit.json").read_bytes())
        self.assertEqual(audit["failure"]["stage"], "closure")

    def test_invalid_conditions_allocations_and_live_mode_fail_before_run_creation(self):
        for changes in ({"condition": "solo"}, {"condition": "native_multiagent"},
                        {"organisation_size": True}, {"organisation_size": 1}, {"organisation_size": 9},
                        {"synthetic_model_limit_usd_nanos": 1000000001}, {"public_compute_seconds": 241},
                        {"execution_mode": "live"}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    run_peer_pilot(self.config(**changes), self.root, "invalid")
                self.assertFalse((self.root / "invalid").exists())

    def test_peer_cli_uses_the_shared_no_spend_command(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["peer-pilot", "--config", str(self.config()),
                "--state-root", str(self.root), "--run-id", "command"]), 0)
        self.assertEqual(json.loads(output.getvalue())["compute_executions"], 15)

    def test_failed_actor_evaluation_aborts_without_any_public_release(self):
        original = SyntheticPilotTransport._result
        count = 0

        def result(transport, request):
            nonlocal count
            if transport.recipe["phase"] == "public" and request.campaign_run_id != "registered-reference":
                count += 1
                if count == 2:
                    raise RuntimeError("second actor evaluation failed")
            return original(transport, request)

        with patch.object(SyntheticPilotTransport, "_result", result):
            with self.assertRaises(PilotAborted):
                run_peer_pilot(self.config(), self.root, "failed")
        audit = json.loads((self.root / "failed/audit.json").read_bytes())
        self.assertEqual(audit["failure"]["stage"], "public_evaluation")
        self.assertEqual(audit["partial_compute_snapshot"]["released_actor_ids"], [])


class PeerCandidateHandoffTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.campaign = ModelServingCampaign.load(REPOSITORY / "campaigns/model_serving_v0/campaign.toml")
        self.run_id = "peer-handoff"
        self.actors = tuple(AgentIdentity(self.run_id, i) for i in range(2))
        self.plan = ComputePlan("peer-test", self.run_id, 120,
            tuple(ActorComputeAllocation(self.run_id, a.actor_id, 60) for a in self.actors),
            60, digest_value({"synthetic": True}))
        self.services = self.open()
        self.transports = tuple(self.services.sessions.bind(a, SessionHandle(f"session-{i}")) for i, a in enumerate(self.actors))
        candidate = json.loads((self.campaign.root / "candidates/vllm-stream-interval-10.json").read_bytes())
        self.receipts = tuple(self.services.tools.call(t, "submit", {
            "candidate": candidate, "idempotency_key": "first"})["receipt"] for t in self.transports)
        self.handoff = CandidateEvaluationHandoff(self.services.submissions, self.services.compute,
            self.run_id, tuple(a.actor_id for a in self.actors))

    def open(self):
        evaluator = FakeModelServingEvaluator(self.root / "evaluator.sqlite3", self.campaign,
            {"stock-vllm-0.21.0": 1000000, "vllm-0.21.0-stream-interval-10": 1100000}, {})
        receipt = evaluator.visible_evaluate(self.campaign.reference_candidate_path.read_bytes(), None, "reference")
        return create_candidate_services(self.root / "services", self.campaign, evaluator=evaluator,
            reference_receipt=receipt, plan=self.plan, policy=SubmissionPolicy(1, 60))

    def test_batch_release_is_owner_private_and_retryable_after_restart(self):
        original = self.services.evaluator.visible_evaluate
        prepared = self.handoff.prepare()

        def evaluate(candidate, reservation, key):
            if reservation.actor_id == self.actors[1].actor_id:
                raise EvaluationInProgress("still running")
            return original(candidate, reservation, key)

        with patch.object(self.services.evaluator, "visible_evaluate", side_effect=evaluate):
            with self.assertRaises(EvaluationInProgress):
                self.handoff.evaluate()
        self.assertEqual(self.services.compute.snapshot(self.run_id).released_actor_ids, ())
        services = self.open()
        handoff = CandidateEvaluationHandoff(services.submissions, services.compute,
            self.run_id, tuple(a.actor_id for a in self.actors))
        self.assertEqual([p.receipt for p in handoff.prepare()], [p.receipt for p in prepared])
        feedback = handoff.evaluate()
        self.assertEqual(feedback.public_materials, {})
        for receipt in self.receipts:
            self.assertNotIn(receipt, feedback.mission)
            self.assertNotIn(receipt, feedback.materials_digest)
        for i, actor in enumerate(self.actors):
            transport = services.sessions.bind(actor, SessionHandle(f"restored-{i}"))
            self.assertEqual(services.tools.call(transport, "result", {"receipt": self.receipts[i]})["status"], "released")
            with self.assertRaises(PermissionError):
                services.tools.call(transport, "result", {"receipt": self.receipts[1-i]})
        self.assertEqual(handoff.evaluate(), feedback)
        self.assertEqual(len(services.compute.snapshot(self.run_id).reservations), 2)

    def test_missing_actor_never_starts_any_evaluation(self):
        handoff = CandidateEvaluationHandoff(self.services.submissions, self.services.compute,
            self.run_id, tuple(a.actor_id for a in self.actors) + (AgentIdentity(self.run_id, 2).actor_id,))
        with patch.object(self.services.evaluator, "visible_evaluate") as evaluate:
            with self.assertRaisesRegex(RuntimeError, "exactly one admitted"):
                handoff.evaluate()
            evaluate.assert_not_called()
        self.assertEqual(self.services.compute.snapshot(self.run_id).released_actor_ids, ())


if __name__ == "__main__":
    unittest.main()
