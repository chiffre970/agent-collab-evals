from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent_collab_evals.adapters.fake_serving_evaluator import (
    FakeModelServingEvaluator,
)
from agent_collab_evals.adapters.local_artifact_storage import LocalArtifactStorage
from agent_collab_evals.adapters.sqlite_compute import SqliteComputeBroker
from agent_collab_evals.adapters.sqlite_submissions import SqliteSubmissionRegistry
from agent_collab_evals.artifact_service import ArtifactService
from agent_collab_evals.artifacts import ArtifactStoragePolicy
from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.canonical import digest_value
from agent_collab_evals.domain import AgentIdentity, SessionHandle
from agent_collab_evals.evaluation import (
    ActorComputeAllocation,
    ComputePlan,
    EvaluationReceipt,
    EvaluationScope,
    SelectionReceipt,
    SubmissionPolicy,
)
from agent_collab_evals.service_identity import ServiceIdentityRegistry
from agent_collab_evals.session_identity import SessionIdentityRegistry


CAMPAIGN_PATH = Path("campaigns/model_serving_v0/campaign.toml")


class CandidateLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.campaign_run_id = "candidate-lifecycle-run"
        self.job_id = "optimize-serving"
        self.sessions = SessionIdentityRegistry()
        self.services = ServiceIdentityRegistry()
        self.submission_service = self.services.bind("submission_registry")
        self.actors = tuple(
            AgentIdentity(self.campaign_run_id, ordinal) for ordinal in range(2)
        )
        self.transports = tuple(
            self.sessions.bind(actor, SessionHandle(f"candidate-session-{index}"))
            for index, actor in enumerate(self.actors)
        )
        self.storage = LocalArtifactStorage(
            self.root / "storage",
            self.sessions,
            self.services,
            ArtifactStoragePolicy(
                max_artifact_bytes=16_384,
                max_actor_bytes=32_768,
                max_campaign_bytes=65_536,
            ),
            {
                "submission_registry": frozenset(
                    {"candidate_lifecycle", "hidden_evaluation"}
                )
            },
        )
        self.storage.open_campaign(
            self.campaign_run_id,
            tuple(actor.actor_id for actor in self.actors),
        )
        self.campaign = ModelServingCampaign.load(CAMPAIGN_PATH)
        self.evaluator = FakeModelServingEvaluator(
            self.root / "evaluator.sqlite3",
            self.campaign,
            {
                "stock-vllm-0.21.0": 1_000_000,
                "vllm-0.21.0-stream-interval-10": 1_100_000,
            },
            {
                "stock-vllm-0.21.0": 900_000,
                "vllm-0.21.0-stream-interval-10": 950_000,
            },
            visible_used_seconds=3,
            hidden_used_seconds=4,
        )
        allocations = tuple(
            ActorComputeAllocation(self.campaign_run_id, actor.actor_id, 60)
            for actor in self.actors
        )
        self.compute_plan = ComputePlan(
            plan_id="candidate-lifecycle-compute-v1",
            campaign_run_id=self.campaign_run_id,
            organisation_limit_seconds=120,
            actor_allocations=allocations,
            hidden_evaluator_limit_seconds=60,
            source_digest=digest_value(
                {
                    "plan_id": "candidate-lifecycle-compute-v1",
                    "actors": [actor.actor_id for actor in self.actors],
                    "actor_seconds": 60,
                    "hidden_seconds": 60,
                }
            ),
        )
        self.compute = self._compute()
        self.registry = self._registry(self.compute)
        self.policy = SubmissionPolicy(
            per_actor_candidate_limit=1,
            visible_evaluation_seconds=60,
        )
        self.artifacts = (
            self.storage.put(
                self.transports[0],
                (self.campaign.root / "reference/candidate.json").read_bytes(),
                "application/json",
            ),
            self.storage.put(
                self.transports[1],
                (
                    self.campaign.root
                    / "candidates/vllm-stream-interval-10.json"
                ).read_bytes(),
                "application/json",
            ),
        )
        self.default_receipt = self.evaluator.visible_evaluate(
            (self.campaign.root / "reference/candidate.json").read_bytes(),
            None,
            f"reference:{self.campaign_run_id}:{self.job_id}",
        )
        self.default = self.evaluator.resolve(
            self.default_receipt,
            (self.campaign.root / "reference/candidate.json").read_bytes(),
            None,
            EvaluationScope.VISIBLE,
        )
        self.registry.initialize(
            self.campaign_run_id,
            self.job_id,
            tuple(actor.actor_id for actor in self.actors),
            self.policy,
            self.artifacts[0].ref,
            self.default_receipt,
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_complete_candidate_lifecycle_survives_restart(self) -> None:
        receipts = tuple(
            self.registry.submit(
                self.transports[index],
                self.job_id,
                self.artifacts[index].ref,
                "candidate-1",
            )
            for index in range(2)
        )
        self.assertEqual(
            self.registry.submit(
                self.transports[0],
                self.job_id,
                self.artifacts[0].ref,
                "candidate-1",
            ),
            receipts[0],
        )
        for receipt in receipts:
            self.registry.evaluate_visible(receipt)

        self.assertIsNone(
            self.registry.visible_result(self.transports[0], receipts[0])
        )
        with self.assertRaisesRegex(PermissionError, "unavailable"):
            self.registry.visible_result(self.transports[0], receipts[1])
        self.compute.release_visible_results(
            self.campaign_run_id, self.actors[0].actor_id
        )
        self.assertEqual(
            self.registry.visible_result(
                self.transports[0], receipts[0]
            ).criterion_units,
            1_000_000,
        )
        self.assertIsNone(
            self.registry.visible_result(self.transports[1], receipts[1])
        )

        restarted_compute = self._compute()
        restarted_registry = self._registry(restarted_compute)
        restarted_registry.initialize(
            self.campaign_run_id,
            self.job_id,
            tuple(actor.actor_id for actor in self.actors),
            self.policy,
            self.artifacts[0].ref,
            self.default_receipt,
        )
        restarted_compute.release_visible_results(
            self.campaign_run_id, self.actors[1].actor_id
        )
        submissions = restarted_registry.close(
            self.campaign_run_id, self.job_id
        )
        selection = restarted_registry.select(submissions)

        self.assertFalse(selection.used_default)
        self.assertEqual(selection.selected_receipt, receipts[1])
        self.assertEqual(selection.result.criterion_units, 1_100_000)
        final_compute = self._compute()
        final_registry = self._registry(final_compute)
        final_registry.initialize(
            self.campaign_run_id,
            self.job_id,
            tuple(actor.actor_id for actor in self.actors),
            self.policy,
            self.artifacts[0].ref,
            self.default_receipt,
        )
        hidden = final_registry.evaluate_hidden(
            selection.receipt, reserved_seconds=60
        )
        self.assertEqual(hidden.criterion_units, 950_000)
        self.assertEqual(
            final_registry.evaluate_hidden(
                selection.receipt, reserved_seconds=60
            ),
            hidden,
        )
        snapshot = final_compute.snapshot(self.campaign_run_id)
        self.assertEqual(
            snapshot.actor_reserved_seconds,
            {actor.actor_id: 60 for actor in self.actors},
        )
        self.assertEqual(
            snapshot.actor_used_seconds,
            {actor.actor_id: 3 for actor in self.actors},
        )
        self.assertEqual(snapshot.hidden_reserved_seconds, 60)
        self.assertEqual(snapshot.hidden_used_seconds, 4)

    def test_owner_and_actor_quotas_fail_closed_without_peer_interference(self) -> None:
        with self.assertRaisesRegex(PermissionError, "owned by another"):
            self.registry.submit(
                self.transports[0],
                self.job_id,
                self.artifacts[1].ref,
                "stolen",
            )
        first = self.registry.submit(
            self.transports[0],
            self.job_id,
            self.artifacts[0].ref,
            "first",
        )
        with self.assertRaisesRegex(ValueError, "submission limit"):
            self.registry.submit(
                self.transports[0],
                self.job_id,
                self.artifacts[0].ref,
                "second",
            )
        peer = self.registry.submit(
            self.transports[1],
            self.job_id,
            self.artifacts[1].ref,
            "peer-first",
        )

        self.assertNotEqual(first, peer)
        snapshot = self.compute.snapshot(self.campaign_run_id)
        self.assertEqual(
            snapshot.actor_reserved_seconds,
            {actor.actor_id: 60 for actor in self.actors},
        )
        self.assertEqual(len(snapshot.reservations), 2)

    def test_invalid_candidate_fails_evaluation_and_uses_default(self) -> None:
        invalid = self.storage.put(
            self.transports[0], b'{"not":"a candidate"}', "application/json"
        )
        receipt = self.registry.submit(
            self.transports[0], self.job_id, invalid.ref, "invalid"
        )
        self.registry.evaluate_visible(receipt)
        self.compute.release_visible_results(
            self.campaign_run_id, self.actors[0].actor_id
        )

        with self.assertRaisesRegex(RuntimeError, "evaluation failed"):
            self.registry.visible_result(self.transports[0], receipt)
        selection = self.registry.select(
            self.registry.close(self.campaign_run_id, self.job_id)
        )
        self.assertTrue(selection.used_default)
        self.assertEqual(selection.result, self.default)
        snapshot = self.compute.snapshot(self.campaign_run_id)
        self.assertEqual(snapshot.actor_used_seconds[self.actors[0].actor_id], 60)

    def test_submission_close_rejects_pending_and_orphan_compute(self) -> None:
        receipt = self.registry.submit(
            self.transports[0],
            self.job_id,
            self.artifacts[0].ref,
            "pending",
        )
        with self.assertRaisesRegex(RuntimeError, "not terminal"):
            self.registry.close(self.campaign_run_id, self.job_id)
        self.registry.evaluate_visible(receipt)
        self.compute.reserve_visible_evaluation(
            self.transports[1],
            "orphan-reservation",
            self.artifacts[1].ref,
            60,
        )

        with self.assertRaisesRegex(RuntimeError, "differ from candidate"):
            self.registry.close(self.campaign_run_id, self.job_id)

    def test_system_reference_wins_a_tie(self) -> None:
        receipt = self.registry.submit(
            self.transports[0],
            self.job_id,
            self.artifacts[0].ref,
            "reference-copy",
        )
        self.registry.evaluate_visible(receipt)

        selection = self.registry.select(
            self.registry.close(self.campaign_run_id, self.job_id)
        )

        self.assertTrue(selection.used_default)
        self.assertIsNone(selection.selected_receipt)
        self.assertEqual(selection.selected_artifact_ref, self.artifacts[0].ref)
        self.assertEqual(selection.result, self.default)
        hidden = self.registry.evaluate_hidden(
            selection.receipt, reserved_seconds=60
        )
        self.assertEqual(hidden.criterion_units, 900_000)
        snapshot = self.compute.snapshot(self.campaign_run_id)
        self.assertEqual(snapshot.hidden_reserved_seconds, 60)
        self.assertEqual(snapshot.hidden_used_seconds, 4)

    def test_hidden_evaluation_rejects_forged_or_tampered_selection(self) -> None:
        receipts = tuple(
            self.registry.submit(
                self.transports[index],
                self.job_id,
                self.artifacts[index].ref,
                "selection-integrity",
            )
            for index in range(2)
        )
        for receipt in receipts:
            self.registry.evaluate_visible(receipt)
        selection = self.registry.select(
            self.registry.close(self.campaign_run_id, self.job_id)
        )
        with self.assertRaisesRegex(PermissionError, "unavailable"):
            self.registry.evaluate_hidden(
                SelectionReceipt("selection-" + "0" * 32), reserved_seconds=60
            )
        with sqlite3.connect(self.root / "submissions.sqlite3") as connection:
            connection.execute(
                "UPDATE selections SET selection_json = replace("
                "selection_json, ?, ?) WHERE selection_receipt = ?",
                (receipts[1].value, receipts[0].value, selection.receipt.value),
            )
            connection.commit()
        with self.assertRaisesRegex(RuntimeError, "differs from recomputation"):
            self.registry.evaluate_hidden(selection.receipt, reserved_seconds=60)
        self.assertEqual(
            self.compute.snapshot(self.campaign_run_id).hidden_reserved_seconds,
            0,
        )

    def test_cancelled_provisional_admission_recovers_on_retry(self) -> None:
        receipt = self.registry.submit(
            self.transports[0],
            self.job_id,
            self.artifacts[0].ref,
            "recover-admission",
        )
        reservation = self.compute.snapshot(self.campaign_run_id).reservations[0]
        self.compute.cancel(
            reservation.reservation_id, "simulated interrupted admission"
        )
        with sqlite3.connect(self.root / "submissions.sqlite3") as connection:
            connection.execute(
                "UPDATE candidates SET admission_status = 'provisional', "
                "reservation_id = NULL WHERE receipt_id = ?",
                (receipt.value,),
            )
            connection.commit()

        self.assertEqual(
            self.registry.submit(
                self.transports[0],
                self.job_id,
                self.artifacts[0].ref,
                "recover-admission",
            ),
            receipt,
        )
        recovered = self.compute.snapshot(self.campaign_run_id).reservations[0]
        self.assertEqual(recovered.status.value, "reserved")
        self.registry.evaluate_visible(receipt)
        self.assertEqual(
            self.compute.snapshot(self.campaign_run_id).reservations[0].status.value,
            "complete",
        )

    def test_concurrent_registry_instances_share_one_evaluation_job(self) -> None:
        receipt = self.registry.submit(
            self.transports[0],
            self.job_id,
            self.artifacts[0].ref,
            "concurrent-evaluation",
        )
        second_compute = self._compute()
        second_evaluator = FakeModelServingEvaluator(
            self.root / "evaluator.sqlite3",
            self.campaign,
            {
                "stock-vllm-0.21.0": 1_000_000,
                "vllm-0.21.0-stream-interval-10": 1_100_000,
            },
            {
                "stock-vllm-0.21.0": 900_000,
                "vllm-0.21.0-stream-interval-10": 950_000,
            },
            visible_used_seconds=3,
            hidden_used_seconds=4,
        )
        second_registry = self._registry(second_compute, second_evaluator)
        second_registry.initialize(
            self.campaign_run_id,
            self.job_id,
            tuple(actor.actor_id for actor in self.actors),
            self.policy,
            self.artifacts[0].ref,
            self.default_receipt,
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            calls = (
                executor.submit(registry.evaluate_visible, receipt)
                for registry in (self.registry, second_registry)
            )
            for call in calls:
                call.result()

        with sqlite3.connect(self.root / "evaluator.sqlite3") as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM evaluation_receipts "
                "WHERE evaluation_key = ?",
                (f"visible:{receipt.value}",),
            ).fetchone()[0]
        self.assertEqual(count, 1)
        snapshot = second_compute.snapshot(self.campaign_run_id)
        self.assertEqual(len(snapshot.reservations), 1)
        self.assertEqual(snapshot.reservations[0].status.value, "complete")

    def test_submission_cannot_forge_evaluator_owned_result(self) -> None:
        receipt = self.registry.submit(
            self.transports[0],
            self.job_id,
            self.artifacts[0].ref,
            "result-integrity",
        )
        self.registry.evaluate_visible(receipt)
        with sqlite3.connect(self.root / "submissions.sqlite3") as connection:
            connection.execute(
                "UPDATE candidates SET visible_evaluation_receipt = ? "
                "WHERE receipt_id = ?",
                (EvaluationReceipt("evalreceipt-" + "0" * 32).value, receipt.value),
            )
            connection.commit()
        with self.assertRaisesRegex(RuntimeError, "not held"):
            self.registry.close(self.campaign_run_id, self.job_id)

    def test_evaluator_receipt_tampering_is_recomputed(self) -> None:
        receipt = self.registry.submit(
            self.transports[0],
            self.job_id,
            self.artifacts[0].ref,
            "evaluator-integrity",
        )
        self.registry.evaluate_visible(receipt)
        with sqlite3.connect(self.root / "evaluator.sqlite3") as connection:
            connection.execute(
                "UPDATE evaluation_receipts SET result_json = ? "
                "WHERE evaluation_key = ?",
                ('{"criterion_units":999999999}', f"visible:{receipt.value}"),
            )
            connection.commit()
        with self.assertRaisesRegex(RuntimeError, "differs from evaluator authority"):
            self.registry.close(self.campaign_run_id, self.job_id)

    def test_compute_plan_change_is_rejected_across_restart(self) -> None:
        changed = ComputePlan(
            plan_id=self.compute_plan.plan_id,
            campaign_run_id=self.campaign_run_id,
            organisation_limit_seconds=120,
            actor_allocations=self.compute_plan.actor_allocations,
            hidden_evaluator_limit_seconds=61,
            source_digest=digest_value({"changed": True}),
        )

        with self.assertRaisesRegex(ValueError, "changed across restart"):
            SqliteComputeBroker(
                self.root / "compute.sqlite3",
                self.sessions,
                self.services,
                changed,
                hidden_evaluator_service="submission_registry",
            )

    def test_compute_and_submission_authorities_detect_database_tampering(self) -> None:
        receipt = self.registry.submit(
            self.transports[0],
            self.job_id,
            self.artifacts[0].ref,
            "tamper-check",
        )
        self.registry.evaluate_visible(receipt)
        with sqlite3.connect(self.root / "compute.sqlite3") as connection:
            connection.execute(
                "UPDATE compute_plans SET organisation_limit_seconds = 999"
            )
            connection.commit()
        with self.assertRaisesRegex(RuntimeError, "differs from its authority"):
            self.compute.snapshot(self.campaign_run_id)

        with sqlite3.connect(self.root / "compute.sqlite3") as connection:
            connection.execute(
                "UPDATE compute_plans SET organisation_limit_seconds = 120"
            )
            connection.commit()
        with sqlite3.connect(self.root / "submissions.sqlite3") as connection:
            connection.execute(
                "UPDATE submission_jobs SET policy_json = ?",
                (
                    '{"per_actor_candidate_limit":99,'
                    '"selection_rule":'
                    '"highest_eligible_criterion_then_artifact_digest",'
                    '"visible_evaluation_seconds":60}',
                ),
            )
            connection.commit()
        with self.assertRaisesRegex(RuntimeError, "differs from its authority"):
            self.registry.close(self.campaign_run_id, self.job_id)

    def test_candidate_artifact_binding_is_reverified_at_close(self) -> None:
        receipt = self.registry.submit(
            self.transports[0],
            self.job_id,
            self.artifacts[0].ref,
            "artifact-tamper",
        )
        self.registry.evaluate_visible(receipt)
        with sqlite3.connect(self.root / "submissions.sqlite3") as connection:
            connection.execute(
                "UPDATE candidates SET artifact_digest = 'tampered' "
                "WHERE receipt_id = ?",
                (receipt.value,),
            )
            connection.commit()

        with self.assertRaisesRegex(RuntimeError, "artifact binding differs"):
            self.registry.close(self.campaign_run_id, self.job_id)

    def _compute(self) -> SqliteComputeBroker:
        return SqliteComputeBroker(
            self.root / "compute.sqlite3",
            self.sessions,
            self.services,
            self.compute_plan,
            hidden_evaluator_service="submission_registry",
        )

    def _registry(
        self,
        compute: SqliteComputeBroker,
        evaluator: FakeModelServingEvaluator | None = None,
    ) -> SqliteSubmissionRegistry:
        return SqliteSubmissionRegistry(
            self.root / "submissions.sqlite3",
            self.sessions,
            self.storage,
            compute,
            evaluator or self.evaluator,
            self.submission_service,
        )


if __name__ == "__main__":
    unittest.main()
