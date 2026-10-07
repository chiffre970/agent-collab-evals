"""No-spend atomic batch admissions on the retained cumulative journal."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from agent_collab_evals.canonical import canonical_json_bytes, digest_value, parse_json
from agent_collab_evals.pilot_spend import PilotSpendEnvelope
from agent_collab_evals.pilot_spend_batch import batch_paths


class PilotSpendBatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "journal"
        self.plan = parse_json((Path(__file__).resolve().parents[1] / "config/pilots/solo-spend-envelope-v1.json").read_text())
        self.base = PilotSpendEnvelope(self.root, self.plan)
        self.base.reserve(operation_key="historical-uncertain", provider="modal", purpose="pilot",
            request_digest=digest_value("old-call"), maximum_usd_nanos=11_000_000_000)
        self.original = {p.name: p.read_bytes() for p in self.root.glob("*.json")}

    def approval(self, snapshot=None, name="control", amount=8_965_888_000):
        snapshot = snapshot or self.base.snapshot()
        return {"schema_version": "pilot-spend-batch/v1", "batch_id": name,
            "prior_snapshot_digest": digest_value(snapshot),
            "provider_limits_usd_nanos": {"modal": 20_000_000_000, "openrouter": 3_000_000_000},
            "total_limit_usd_nanos": 23_000_000_000,
            "admissions": [{"operation_key": f"pilot:batch-{name}:modal:control", "provider": "modal", "purpose": "pilot",
                "request_digest": digest_value(name), "maximum_usd_nanos": amount, "plan_digest": snapshot["plan_digest"]}]}

    def test_batch_preserves_old_reserve_and_is_idempotent_across_restart(self):
        approval = self.approval()
        envelope = PilotSpendEnvelope(self.root, self.plan, batch_approvals=(approval,))
        debit = envelope.admit_batch()
        restarted = PilotSpendEnvelope(self.root, self.plan, batch_approvals=(approval,))
        self.assertEqual(restarted.admit_batch(), debit)
        self.assertEqual(restarted.snapshot()["reserved_usd_nanos"]["modal"], 19_965_888_000)
        self.assertEqual(restarted.snapshot()["remaining_usd_nanos"]["openrouter"], 3_000_000_000)
        self.assertEqual(len(restarted.snapshot()["receipts"]), 2)
        for name, raw in self.original.items():
            self.assertEqual((self.root / name).read_bytes(), raw)
        with self.assertRaisesRegex(PermissionError, "independently pinned"):
            self.base.snapshot()
        with self.assertRaisesRegex(PermissionError, "exact approved inventory"):
            envelope.reserve(operation_key="anything-else", provider="modal", purpose="pilot",
                request_digest=digest_value("other"), maximum_usd_nanos=1)

    def test_exhausted_or_stale_approval_writes_no_partial_batch(self):
        approval = self.approval()
        approval["provider_limits_usd_nanos"] = self.plan["provider_limits_usd_nanos"]
        approval["total_limit_usd_nanos"] = self.plan["total_limit_usd_nanos"]
        with self.assertRaisesRegex(PermissionError, "exhausted"):
            PilotSpendEnvelope(self.root, self.plan, batch_approvals=(approval,))
        self.assertFalse((self.root / "batches").exists())
        approval = self.approval()
        self.base.reserve(operation_key="new-prior", provider="modal", purpose="pilot",
            request_digest=digest_value("new-prior"), maximum_usd_nanos=1)
        with self.assertRaisesRegex(PermissionError, "preceding journal"):
            PilotSpendEnvelope(self.root, self.plan, batch_approvals=(approval,))

    def test_concurrent_admission_burns_exactly_one_batch(self):
        approval = self.approval()
        def admit(_):
            return PilotSpendEnvelope(self.root, self.plan, batch_approvals=(approval,)).admit_batch()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(admit, range(2)))
        self.assertEqual(first, second)
        self.assertEqual(len(list((self.root / "batches").rglob("admission.json"))), 1)

    def test_conflicting_concurrent_batches_cannot_reuse_the_prior_snapshot(self):
        approvals = [self.approval(name=name) for name in ("one", "two")]
        def admit(approval):
            try:
                PilotSpendEnvelope(self.root, self.plan, batch_approvals=(approval,)).admit_batch()
                return True
            except PermissionError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(admit, approvals)), [False, True])

    def test_retained_batch_tampering_and_missing_pins_fail_closed(self):
        approval = self.approval()
        envelope = PilotSpendEnvelope(self.root, self.plan, batch_approvals=(approval,))
        debit = envelope.admit_batch()
        _, path = batch_paths(self.root, approval)
        changed = deepcopy(debit)
        changed["receipts"][0]["maximum_usd_nanos"] = 1
        path.write_bytes(canonical_json_bytes(changed))
        with self.assertRaisesRegex(RuntimeError, "admission differs"):
            envelope.snapshot()

    def test_next_batch_requires_the_entire_pinned_history_without_refunds(self):
        first = self.approval()
        old = PilotSpendEnvelope(self.root, self.plan, batch_approvals=(first,))
        old.admit_batch()
        second = self.approval(old.snapshot(), name="later", amount=1)
        new = PilotSpendEnvelope(self.root, self.plan, batch_approvals=(first, second))
        new.admit_batch()
        self.assertEqual(new.snapshot()["reserved_usd_nanos"]["modal"], 19_965_888_001)
        with self.assertRaises(PermissionError):
            PilotSpendEnvelope(self.root, self.plan, batch_approvals=(second,))
