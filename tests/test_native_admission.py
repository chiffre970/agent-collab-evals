from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent_collab_evals.native_admission import NativeFleetPlan, SqliteNativeAdmission


class NativeAdmissionTests(unittest.TestCase):
    def test_slots_survive_restart_and_completed_children_can_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native.sqlite3"
            plan = NativeFleetPlan("run", "primary", 2)
            first = SqliteNativeAdmission(path, plan)
            permit = first.reserve(caller_session_id="primary", call_id="call-1")
            first.complete(permit, child_session_id="child")
            second = SqliteNativeAdmission(path, plan)
            resumed = second.reserve(caller_session_id="primary", call_id="call-2", task_id="child")
            self.assertFalse(second.reconcile(("child",))["valid"])
            second.complete(resumed, child_session_id="child")
            self.assertTrue(second.reconcile(("child",))["valid"])
            self.assertFalse(second.reconcile(("child",))["runtime_interception_qualified"])

    def test_independent_instances_admit_only_one_remaining_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native.sqlite3"
            plan = NativeFleetPlan("run", "primary", 2)
            ledgers = [SqliteNativeAdmission(path, plan) for _ in range(2)]

            def reserve(index):
                try:
                    return ledgers[index].reserve(caller_session_id="primary", call_id=f"call-{index}")
                except PermissionError:
                    return None

            with ThreadPoolExecutor(max_workers=2) as executor:
                permits = list(executor.map(reserve, range(2)))
            self.assertEqual(sum(permit is not None for permit in permits), 1)
            self.assertFalse(ledgers[0].reconcile(())["valid"])

    def test_interrupted_dispatch_is_not_reissued(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = SqliteNativeAdmission(Path(directory) / "native.sqlite3", NativeFleetPlan("run", "primary", 2))
            ledger.reserve(caller_session_id="primary", call_id="call")
            with self.assertRaisesRegex(RuntimeError, "already admitted"):
                ledger.reserve(caller_session_id="primary", call_id="call")
            self.assertFalse(ledger.reconcile(())["valid"])


if __name__ == "__main__":
    unittest.main()
