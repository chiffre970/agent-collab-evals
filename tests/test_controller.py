from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from agent_collab_evals.adapters.fake_harness import FakeHarnessRuntime
from agent_collab_evals.adapters.local_events import LocalEventSink
from agent_collab_evals.adapters.local_snapshots import LocalCampaignSnapshotStore
from agent_collab_evals.adapters.no_model_budget import NoModelBudgetReconciler
from agent_collab_evals.adapters.no_compute_reconciliation import (
    NoComputeExecutionReconciler,
)
from agent_collab_evals.budget import BudgetReconciliation
from agent_collab_evals.controller import CampaignCloseRejected, CampaignController
from agent_collab_evals.domain import (
    CoordinationCondition,
    Job,
    OrganisationSpec,
)


def _job(job_id: str) -> Job:
    return Job(job_id, f"mission for {job_id}", f"sha256:{job_id}", {})


def _no_compute(root: Path, campaign_run_id: str) -> NoComputeExecutionReconciler:
    return NoComputeExecutionReconciler.from_frozen_manifest(
        root / f"{campaign_run_id}-compute-run.json", campaign_run_id
    )


class CampaignControllerTests(unittest.TestCase):
    def test_delivery_can_retry_after_partial_fanout(self) -> None:
        class FailSecondSessionOnce(FakeHarnessRuntime):
            failed = False

            def deliver(self, session, job):  # type: ignore[no-untyped-def]
                if session.value.endswith(":session:1") and not self.failed:
                    self.failed = True
                    raise RuntimeError("injected delivery failure")
                super().deliver(session, job)

        with tempfile.TemporaryDirectory() as directory:
            runtime = FailSecondSessionOnce()
            controller = CampaignController(
                runtime, LocalEventSink(Path(directory) / "events")
            )
            handle = controller.start(
                self._spec("retry-run", CoordinationCondition.PEER_ISOLATED)
            )
            job = _job("retryable")

            with self.assertRaisesRegex(RuntimeError, "injected"):
                controller.deliver(handle, job)
            controller.deliver(handle, job)

            self.assertEqual(handle.delivered_job_ids, ["retryable"])
            for session in handle.sessions:
                self.assertEqual(runtime.delivered_jobs(session), ("retryable",))

    def test_two_jobs_cross_a_process_style_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = LocalEventSink(root / "events")
            store = LocalCampaignSnapshotStore(root / "snapshots")
            first_runtime = FakeHarnessRuntime()
            first = CampaignController(
                first_runtime, events, NoModelBudgetReconciler()
            )
            handle = first.start(self._spec("durable-run", CoordinationCondition.SOLO))
            first.deliver(handle, _job("first"))
            snapshot = first.snapshot(handle)
            store.save(snapshot)

            second_runtime = FakeHarnessRuntime()
            second = CampaignController(
                second_runtime,
                events,
                NoModelBudgetReconciler(),
                _no_compute(root, "durable-run"),
            )
            resumed = second.resume(store.load("durable-run"))
            self.assertEqual(resumed.sessions, handle.sessions)
            second.deliver(resumed, _job("second"))
            result = second.close(resumed, "complete")

            self.assertEqual(result.delivered_job_ids, ("first", "second"))
            self.assertEqual(
                second_runtime.delivered_jobs(resumed.sessions[0]),
                ("first", "second"),
            )
            self.assertEqual(
                [event["kind"] for event in events.read("durable-run")],
                [
                    "campaign.started",
                    "job.delivered",
                    "campaign.snapshotted",
                    "campaign.resumed",
                    "job.delivered",
                    "campaign.budget_reconciled",
                    "campaign.compute_reconciled",
                    "campaign.closed",
                ],
            )

    def test_top_level_session_cardinality_matches_condition(self) -> None:
        expected = {
            CoordinationCondition.SOLO: 1,
            CoordinationCondition.NATIVE_MULTIAGENT: 1,
            CoordinationCondition.PEER_ISOLATED: 4,
            CoordinationCondition.PEER_COLLAB: 4,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for condition, count in expected.items():
                with self.subTest(condition=condition):
                    runtime = FakeHarnessRuntime()
                    controller = CampaignController(runtime, LocalEventSink(root))
                    handle = controller.start(
                        self._spec(f"run-{condition.value}", condition)
                    )
                    self.assertEqual(len(handle.actors), count)
                    self.assertEqual(len(handle.sessions), count)

    def test_peer_delivery_fans_out_concurrently(self) -> None:
        class BarrierHarness(FakeHarnessRuntime):
            barrier = threading.Barrier(4, timeout=2)

            def deliver(self, session, job):  # type: ignore[no-untyped-def]
                self.barrier.wait()
                super().deliver(session, job)

        with tempfile.TemporaryDirectory() as directory:
            runtime = BarrierHarness()
            controller = CampaignController(
                runtime, LocalEventSink(Path(directory) / "events")
            )
            handle = controller.start(
                self._spec("parallel-delivery", CoordinationCondition.PEER_ISOLATED)
            )

            controller.deliver(handle, _job("parallel"))

            for session in handle.sessions:
                self.assertEqual(runtime.delivered_jobs(session), ("parallel",))

    def test_close_requires_budget_reconciliation_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = FakeHarnessRuntime()
            controller = CampaignController(
                runtime, LocalEventSink(Path(directory) / "events")
            )
            handle = controller.start(
                self._spec("missing-budget-gate", CoordinationCondition.SOLO)
            )

            with self.assertRaisesRegex(
                RuntimeError, "requires a configured budget reconciliation gate"
            ):
                controller.close(handle, "complete")

            self.assertEqual(handle.status.value, "active")

    def test_invalid_budget_terminal_state_rejects_campaign_result(self) -> None:
        class InvalidBudget:
            def reconcile(self, campaign_run_id: str) -> BudgetReconciliation:
                return BudgetReconciliation(
                    campaign_run_id,
                    "provider_receipts_required",
                    forfeited_reservation_ids=("reservation-forfeited",),
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = FakeHarnessRuntime()
            events = LocalEventSink(root / "events")
            controller = CampaignController(
                runtime,
                events,
                InvalidBudget(),
                _no_compute(root, "invalid-budget"),
            )
            handle = controller.start(
                self._spec("invalid-budget", CoordinationCondition.SOLO)
            )

            with self.assertRaises(CampaignCloseRejected) as caught:
                controller.close(handle, "complete")

            self.assertEqual(handle.status.value, "invalid")
            self.assertIsNotNone(caught.exception.reconciliation)
            self.assertEqual(
                [event["kind"] for event in events.read("invalid-budget")],
                ["campaign.started", "campaign.invalid"],
            )

    def test_close_requires_compute_reconciliation_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = FakeHarnessRuntime()
            controller = CampaignController(
                runtime,
                LocalEventSink(Path(directory) / "events"),
                NoModelBudgetReconciler(),
            )
            handle = controller.start(
                self._spec("missing-compute-gate", CoordinationCondition.SOLO)
            )

            with self.assertRaisesRegex(
                RuntimeError, "requires a configured compute reconciliation gate"
            ):
                controller.close(handle, "complete")

            self.assertEqual(handle.status.value, "active")

    def test_compute_reconciliation_failure_invalidates_campaign(self) -> None:
        class InvalidCompute:
            def reconcile(self, campaign_run_id: str):  # type: ignore[no-untyped-def]
                raise RuntimeError("pending external execution")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = LocalEventSink(root / "events")
            controller = CampaignController(
                FakeHarnessRuntime(),
                events,
                NoModelBudgetReconciler(),
                InvalidCompute(),
            )
            handle = controller.start(
                self._spec("invalid-compute", CoordinationCondition.SOLO)
            )

            with self.assertRaisesRegex(
                CampaignCloseRejected, "compute reconciliation failed"
            ):
                controller.close(handle, "complete")

            self.assertEqual(handle.status.value, "invalid")
            self.assertEqual(
                [event["kind"] for event in events.read("invalid-compute")],
                [
                    "campaign.started",
                    "campaign.budget_reconciled",
                    "campaign.invalid",
                ],
            )

    def test_close_rejects_reconciliation_for_another_campaign(self) -> None:
        class WrongCampaignBudget:
            def reconcile(self, campaign_run_id: str) -> BudgetReconciliation:
                return BudgetReconciliation("another-campaign", "test")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = LocalEventSink(root / "events")
            controller = CampaignController(
                FakeHarnessRuntime(),
                events,
                WrongCampaignBudget(),
                _no_compute(root, "budget-mismatch"),
            )
            handle = controller.start(
                self._spec("budget-mismatch", CoordinationCondition.SOLO)
            )

            with self.assertRaises(CampaignCloseRejected):
                controller.close(handle, "complete")

            invalid = events.read("budget-mismatch")[-1]
            self.assertEqual(
                invalid["payload"]["reason"],
                "budget_reconciliation_campaign_mismatch",
            )

    @staticmethod
    def _spec(run_id: str, condition: CoordinationCondition) -> OrganisationSpec:
        return OrganisationSpec(
            campaign_run_id=run_id,
            condition=condition,
            organisation_size=4,
            workspace_root=Path("/tmp") / run_id,
            model_endpoint="fake://model",
        )


if __name__ == "__main__":
    unittest.main()
