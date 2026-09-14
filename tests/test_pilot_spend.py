"""No-spend tests for the shared gross-dollar admission journal."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_collab_evals.canonical import digest_value
from agent_collab_evals.modal_pilot_cost import modal_pilot_cost
from agent_collab_evals.pilot_spend import PilotSpendEnvelope


REPOSITORY = Path(__file__).resolve().parents[1]


class PilotSpendTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.plan = json.loads((REPOSITORY / "config/pilots/solo-spend-envelope-v1.json").read_text())
        self.envelope = PilotSpendEnvelope(self.root / "journal", self.plan)

    def reserve(self, key, amount, *, envelope=None, provider="modal", purpose="pilot"):
        return (envelope or self.envelope).reserve(operation_key=key, provider=provider,
            purpose=purpose, request_digest=digest_value(key), maximum_usd_nanos=amount)

    def test_qualification_and_pilot_share_capacity_across_restarts(self):
        self.reserve("qualify", 1_000_000_000, purpose="qualification")
        self.reserve("pilot", 10_950_000_000)
        restarted = PilotSpendEnvelope(self.root / "journal", self.plan)
        self.reserve("pilot", 10_950_000_000, envelope=restarted)
        self.assertEqual(len(restarted.snapshot()["receipts"]), 2)
        with self.assertRaisesRegex(PermissionError, "exhausted"):
            self.reserve("retry-as-new", 1, envelope=restarted)
        with self.assertRaisesRegex(RuntimeError, "retry differs"):
            self.reserve("pilot", 1, envelope=restarted)
        self.assertEqual(restarted.snapshot()["remaining_usd_nanos"]["openrouter"], 3_000_000_000)

    def test_concurrent_instances_cannot_over_admit(self):
        def reserve(key):
            envelope = PilotSpendEnvelope(self.root / "journal", self.plan)
            try:
                self.reserve(key, 7_000_000_000, envelope=envelope)
                return True
            except PermissionError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(reserve, ("a", "b")))
        self.assertEqual(sorted(outcomes), [False, True])
        self.assertEqual(self.envelope.snapshot()["reserved_usd_nanos"]["modal"], 7_000_000_000)

    def test_reopening_with_larger_plan_and_modified_plan_fail(self):
        changed = {**self.plan, "total_limit_usd_nanos": 30_000_000_000,
            "provider_limits_usd_nanos": {"modal": 24_000_000_000, "openrouter": 6_000_000_000}}
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            PilotSpendEnvelope(self.root / "journal", changed)
        (self.root / "journal/plan.json").write_text(json.dumps(changed))
        with self.assertRaisesRegex(RuntimeError, "pinned authority"):
            self.reserve("new", 1)

    def test_corruption_or_invalid_amount_is_not_capacity(self):
        for amount in (True, 0, -1, 0.5):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                self.reserve("invalid", amount)
        self.reserve("operation", 10)
        path = self.root / "journal" / (digest_value("operation")[7:] + ".json")
        document = json.loads(path.read_text())
        document["plan_digest"] = digest_value("other")
        path.write_text(json.dumps(document))
        with self.assertRaises(ValueError):
            self.reserve("another", 1)

    def test_cost_estimate_uses_applied_resource_settings_and_is_not_a_cap(self):
        script = REPOSITORY / "campaigns/model_serving_v0/reference/modal_vllm.py"
        policy = REPOSITORY / "config/compute/modal-pilot-cost-v1.json"
        estimate = modal_pilot_cost(script, policy)
        self.assertEqual(estimate["per_execution_allowance_usd_nanos"], 766_080_000)
        self.assertEqual(12 * estimate["per_execution_allowance_usd_nanos"]
            + estimate["shared_overhead_allowance_usd_nanos"], 10_192_960_000)
        self.assertFalse(estimate["provider_billing_cap_verified"])
        for source in (script.read_text().replace("**GPU_RESOURCES,", "", 1),
            script.read_text().replace('"cpu": (4.0, 4.0)', '"cpu": (4.0, 8.0)')):
            changed = self.root / "changed.py"
            changed.write_text(source)
            with self.assertRaises(ValueError):
                modal_pilot_cost(changed, policy)

    def test_qualification_entrypoints_claim_shared_budget_before_provider_access(self):
        from scripts.preflight import model_gateway_live, provider_route_qualification

        for module in (provider_route_qualification, model_gateway_live):
            with self.subTest(module=module.__name__):
                with patch("sys.argv", ["qualification", "--execute"]), self.assertRaisesRegex(ValueError, "spend-envelope"):
                    module.main()
                arguments = ["qualification", "--execute", "--spend-envelope", str(self.root / "journal")]
                with (patch("sys.argv", arguments),
                    patch.dict("os.environ", {"OPENROUTER_API_KEY": "synthetic-no-network"}),
                    patch.object(module.OpenRouterUpstream, "from_profile", side_effect=RuntimeError("network deliberately disabled")) as upstream):
                    with self.assertRaisesRegex(RuntimeError, "network deliberately disabled"):
                        module.main()
                    upstream.assert_called_once()
                    with self.assertRaisesRegex(RuntimeError, "already admitted"):
                        module.main()
                    upstream.assert_called_once()
        snapshot = self.envelope.snapshot()
        self.assertEqual(snapshot["reserved_usd_nanos"]["openrouter"], 60_000_000)
        self.assertEqual(len(snapshot["receipts"]), 2)


if __name__ == "__main__":
    unittest.main()
