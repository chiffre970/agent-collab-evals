from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_collab_evals.adapters.fake_harness import FakeHarnessRuntime
from agent_collab_evals.adapters.local_events import LocalEventSink
from agent_collab_evals.adapters.local_snapshots import LocalCampaignSnapshotStore
from agent_collab_evals.controller import CampaignController
from agent_collab_evals.domain import (
    CoordinationCondition,
    Job,
    OrganisationSpec,
)


def _job(job_id: str) -> Job:
    return Job(job_id, f"mission for {job_id}", f"sha256:{job_id}", {})


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
            first = CampaignController(first_runtime, events)
            handle = first.start(self._spec("durable-run", CoordinationCondition.SOLO))
            first.deliver(handle, _job("first"))
            snapshot = first.snapshot(handle)
            store.save(snapshot)

            second_runtime = FakeHarnessRuntime()
            second = CampaignController(second_runtime, events)
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
