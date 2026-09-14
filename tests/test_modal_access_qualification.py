"""No-spend checks for the one-shot Modal device/cancellation qualification."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from agent_collab_evals.pilot_spend import PilotSpendEnvelope


REPOSITORY = Path(__file__).resolve().parents[1]


class ModalAccessQualificationTests(unittest.TestCase):
    def setUp(self):
        try:
            from scripts.preflight.modal_access import qualify_gpu
        except ModuleNotFoundError as error:
            if error.name != "modal":
                raise
            self.skipTest("Modal SDK is not installed")
        self.qualify = qualify_gpu
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        plan = json.loads((REPOSITORY / "config/pilots/solo-spend-envelope-v1.json").read_text())
        self.envelope = PilotSpendEnvelope(self.root / "journal", plan)
        self.calls = [Mock(object_id=f"fc-test-{i}") for i in range(2)]
        self.calls[0].get.return_value = {"name": "NVIDIA L4"}
        self.function = Mock()

        def spawn(**kwargs):
            self.assertEqual(self.envelope.snapshot()["reserved_usd_nanos"]["modal"], 1_532_160_000)
            return self.calls[0 if kwargs["hold_seconds"] == 0 else 1]

        self.function.spawn.side_effect = spawn
        self.function.get_current_stats.side_effect = [self.stats(0), self.stats(1), self.stats(0)]

    @staticmethod
    def stats(running):
        return SimpleNamespace(backlog=0, num_total_runners=running, num_running_inputs=running)

    def run_qualification(self, **kwargs):
        return self.qualify(self.function, self.envelope, self.root / "evidence", REPOSITORY, **kwargs)

    def test_two_calls_are_admitted_before_dispatch_and_cannot_repeat(self):
        result = self.run_qualification()
        self.assertFalse(result["provider_billing_reconciled"])
        self.assertFalse(result["scored_boundary_qualified"])
        self.assertEqual(self.function.spawn.call_count, 2)
        for index, call in enumerate(self.calls):
            call.cancel.assert_called_once_with(terminate_containers=True)
            dispatch = json.loads((self.root / f"evidence/call-{index}-dispatch.json").read_text())
            self.assertEqual(dispatch["call_id"], call.object_id)
        with self.assertRaisesRegex(RuntimeError, "already admitted"):
            self.run_qualification()
        self.assertEqual(self.function.spawn.call_count, 2)

    def test_exhaustion_stops_before_dispatch(self):
        self.envelope.reserve(operation_key="prior", provider="modal", purpose="pilot",
            request_digest="sha256:" + "1" * 64, maximum_usd_nanos=11_000_000_000)
        with self.assertRaisesRegex(PermissionError, "exhausted"):
            self.run_qualification()
        self.function.spawn.assert_not_called()

    def test_cancel_failure_keeps_allowance_and_stops_second_call(self):
        self.calls[0].cancel.side_effect = RuntimeError("synthetic cancellation failure")
        with self.assertRaisesRegex(RuntimeError, "cancellation failure"):
            self.run_qualification()
        self.assertEqual(self.function.spawn.call_count, 1)
        self.assertTrue((self.root / "evidence/call-0-cleanup-failure.json").exists())
        self.assertEqual(self.envelope.snapshot()["reserved_usd_nanos"]["modal"], 1_532_160_000)

    def test_ambiguous_dispatch_is_retained_and_not_retried(self):
        self.function.spawn.side_effect = TimeoutError("unknown dispatch")
        with self.assertRaises(TimeoutError):
            self.run_qualification()
        failure = json.loads((self.root / "evidence/call-0-failure.json").read_text())
        self.assertEqual(failure["dispatch_status"], "unknown")
        self.function.spawn.assert_called_once()
        with self.assertRaisesRegex(RuntimeError, "already admitted"):
            self.run_qualification()

    def test_failed_device_check_still_cancels_and_stops(self):
        self.calls[0].get.return_value = {"name": "unexpected"}
        with self.assertRaisesRegex(RuntimeError, "unexpected GPU"):
            self.run_qualification()
        self.calls[0].cancel.assert_called_once_with(terminate_containers=True)
        self.assertEqual(self.function.spawn.call_count, 1)

    def test_unresolved_cleanup_observation_stops_before_second_call(self):
        self.function.get_current_stats.side_effect = None
        self.function.get_current_stats.return_value = self.stats(1)
        with self.assertRaises(TimeoutError):
            self.run_qualification(monotonic=Mock(side_effect=[0, 46]), sleep=Mock())
        self.assertEqual(self.function.spawn.call_count, 1)
        self.assertFalse((self.root / "evidence/outcome.json").exists())


if __name__ == "__main__":
    unittest.main()
