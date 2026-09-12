"""Exercise live lifecycle branches using synthetic, no-spend dependencies."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent_collab_evals.adapters.opencode_harness import OpenCodeRuntimeProfile
from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.budget import BudgetCharge, BudgetSnapshot, ProviderUsage
from agent_collab_evals.canonical import canonical_json_bytes, digest_value
from agent_collab_evals.model_gateway import ModelGatewayProfile
from agent_collab_evals.sandbox import SandboxProfile
from agent_collab_evals.solo_pilot_command import LivePilotDependencies, PilotAborted, _SyntheticCandidateHarness, _execute_solo_pilot, _retain_budget_snapshot
from agent_collab_evals.solo_pilot_stack import build_no_spend_stack


REPOSITORY = Path(__file__).resolve().parents[1]


class SoloLiveLifecycleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = json.loads((REPOSITORY / "config/pilots/solo-live-v1.json").read_bytes())
        self.campaign = ModelServingCampaign.load(REPOSITORY / self.config["campaign"])
        self.gateway = ModelGatewayProfile.load(REPOSITORY / self.config["gateway_profile"], repository_root=REPOSITORY)
        self.runtime = OpenCodeRuntimeProfile.load(REPOSITORY / self.config["runtime_profile"], repository_root=REPOSITORY)
        self.sandbox = SandboxProfile.load(REPOSITORY / self.config["sandbox_profile"])
        candidate = json.loads(self.campaign.reference_candidate_path.read_bytes())
        self.authorized = []
        self.cleanup = Mock(return_value=({"status": "cancellation_requested", "terminal_confirmed": False},))

        def authorize(stack, request, config_digest):
            self.authorized.append(request)
            stack.inventory.authorize(request, approval_reference="synthetic-lifecycle-test-only")

        self.dependencies = LivePilotDependencies(
            lambda root, run_id: build_no_spend_stack(root, self.campaign, run_id, REPOSITORY),
            lambda: SimpleNamespace(), authorize, self.cleanup,
            lambda root, services, gateway, candidate_gateway: _SyntheticCandidateHarness(services, candidate),
            1_000_000_000, 60)

    def run_pilot(self):
        with (patch("agent_collab_evals.solo_pilot_command.ModelBudgetGateway", return_value=Mock(endpoint="fake://model")),
              patch("agent_collab_evals.solo_pilot_command.CandidateToolGateway")):
            return _execute_solo_pilot(self.config, self.root, "live-branch-test", REPOSITORY, self.campaign,
                self.gateway, self.runtime, self.sandbox, live=self.dependencies)

    def test_complete_lifecycle_authorizes_every_phase_and_does_not_invent_billing(self):
        result = self.run_pilot()
        audit = json.loads(Path(result["audit_path"]).read_bytes())
        self.assertEqual(audit["execution_mode"], "live")
        self.assertEqual(len(self.authorized), 12)
        self.assertEqual(audit["external_compute_executions"], 12)
        self.assertIsNone(audit["actual_spend_usd_nanos"])
        self.assertEqual(audit["billing_status"], "provider_compute_billing_unreconciled")
        self.assertFalse(audit["scoreable"])
        self.assertTrue(audit["budget_reconciliation"]["valid"])
        self.cleanup.assert_not_called()

    def test_actor_failure_runs_remote_cleanup_and_retains_unresolved_status(self):
        with patch.object(_SyntheticCandidateHarness, "deliver", side_effect=RuntimeError("test failure")):
            with self.assertRaises(PilotAborted):
                self.run_pilot()
        audit = json.loads((self.root / "live-branch-test/audit.json").read_bytes())
        self.cleanup.assert_called_once()
        self.assertEqual(audit["status"], "aborted")
        self.assertEqual(audit["remote_cleanup"][0]["status"], "cancellation_requested")
        self.assertFalse(audit["remote_cleanup"][0]["terminal_confirmed"])

    def test_cleanup_error_does_not_suppress_aborted_audit(self):
        self.cleanup.side_effect = TimeoutError()
        with patch.object(_SyntheticCandidateHarness, "deliver", side_effect=RuntimeError("test failure")):
            with self.assertRaises(PilotAborted):
                self.run_pilot()
        audit = json.loads((self.root / "live-branch-test/audit.json").read_bytes())
        self.assertEqual(audit["remote_cleanup"]["status"], "cleanup_failed")
        self.assertEqual(audit["failure"]["stage"], "agent_job")

    def test_accounting_snapshot_retains_raw_receipt_bytes_outside_json(self):
        usage = ProviderUsage(requested_model="test-model", returned_model=None, metadata_model=None,
            provider_name=None, provider_request_id=None, provider_generation_id=None,
            provider_timestamp=None, system_fingerprint=None, provider_cost_usd_nanos=5,
            prompt_tokens=1, cached_input_tokens=0, completion_tokens=1,
            raw_receipt=b"test stream bytes", raw_metadata_receipt=b"test metadata bytes")
        charge = BudgetCharge("reservation", "run", "actor", "call", 5, usage, digest_value("rate"))
        snapshot = BudgetSnapshot("run", 100, 0, 5, (), (charge,), ())
        document = _retain_budget_snapshot(self.root, snapshot)
        canonical_json_bytes(document)
        for field in ("raw_receipt", "raw_metadata_receipt"):
            digest = document["charges"][0]["usage"][field + "_digest"]
            self.assertEqual((self.root / "provider-receipts" / digest[7:]).read_bytes(), getattr(usage, field))


if __name__ == "__main__":
    unittest.main()
