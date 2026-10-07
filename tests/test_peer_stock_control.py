"""Full stock-control spending composition with simulated retained GPU output."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_collab_evals.adapters.modal_paired_serving import ModalPairedServingTransport
from agent_collab_evals.adapters.sqlite_compute_spend import SqliteComputeSpendAuthorizationService
from agent_collab_evals.canonical import digest_file, digest_value, parse_json
from agent_collab_evals.compute_backend import FrozenComputeRunManifest
from agent_collab_evals.evaluation import EvaluationInProgress
from agent_collab_evals.peer_live_configuration import prepare_paired_stock_control
from agent_collab_evals.peer_stock_control import run_stock_control, stock_batch
from agent_collab_evals.pilot_evidence import retain_document
from agent_collab_evals.pilot_spend import PilotSpendEnvelope
from tests.quality_fixture import REPOSITORY_ROOT, real_hidden_quality_bundle
from tests.test_paired_serving_stack import _RetainedPairedTransport


class PeerStockControlTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        campaign, bundle, policy = real_hidden_quality_bundle(self.root / "fixture")
        self.policy = policy
        self.configuration = SimpleNamespace(repository=REPOSITORY_ROOT, campaign=campaign,
            modal_cli=REPOSITORY_ROOT / ".venv/bin/modal", document={"test_configuration": "stock-control"},
            hidden_bundle=lambda: bundle, public_compute=SimpleNamespace(modal_script=campaign.root / "reference/modal_vllm.py"))
        self.state = self.root / "control"
        self.preparation = prepare_paired_stock_control(self.state, self.configuration, "stock-control", bundle, policy)
        configuration_path = self.root / "configuration.json"
        retain_document(configuration_path, self.configuration.document)
        plan = parse_json((REPOSITORY_ROOT / "config/pilots/solo-spend-envelope-v1.json").read_text())
        self.envelope = PilotSpendEnvelope(self.root / "journal", plan)
        self.envelope.reserve(operation_key="unresolved-old-call", provider="modal", purpose="pilot",
            request_digest=digest_value("old"), maximum_usd_nanos=11_000_000_000)
        self.authorization = {"schema_version": "paired-stock-control-authorization/v1",
            "scope": "one_bounded_wrapper_conformance_and_stock_control", "run_id": "stock-control",
            "expires_at": (datetime.now(UTC) + timedelta(hours=12)).isoformat(),
            "preparation_digest": digest_file(self.state / "stock-control.json"),
            "configuration": {"file": str(configuration_path), "digest": digest_file(configuration_path)},
            "state_root": str(self.state), "repository": str(REPOSITORY_ROOT),
            "git_commit": self.preparation["git_commit"],
            "journal": {"root": str(self.envelope.root), "plan_digest": self.envelope.plan_digest,
                "retry_amendment": None, "prior_batches": []},
            "batch_approval": stock_batch(self.preparation, self.envelope.snapshot(),
                {"modal": 20_000_000_000, "openrouter": 3_000_000_000})}
        _RetainedPairedTransport.dispatches = []
        _RetainedPairedTransport.wrong_correctness = False
        _RetainedPairedTransport.wrong_reference = False
        _RetainedPairedTransport.speedup = 1

    def run_control(self, document=None, *, clean=True, dispatch=None):
        document = document or self.authorization
        path = self.root / (digest_value(document)[7:] + ".json")
        retain_document(path, document)
        original_policy = type(self.configuration.campaign).quality_policy
        with (patch("agent_collab_evals.peer_stock_control.LivePilotConfiguration.load", return_value=self.configuration),
            patch.object(type(self.configuration.campaign), "quality_policy", lambda campaign:
                self.policy if campaign is self.configuration.campaign else original_policy(campaign)),
            patch("agent_collab_evals.peer_stock_control.require_clean_build", **({"return_value": None} if clean else
                {"side_effect": PermissionError("dirty build")})),
            patch.object(ModalPairedServingTransport, "dispatch", dispatch or _RetainedPairedTransport.dispatch)):
            return run_stock_control(self.state, path, digest_file(path))

    def statuses(self):
        pin, = self.preparation["compute_manifests"]
        manifest = FrozenComputeRunManifest.load(Path(pin["file"]), expected_digest=pin["digest"])
        spend = SqliteComputeSpendAuthorizationService(manifest.path.parent / "spend.sqlite3", manifest)
        return [spend.request_status(r, manifest.transport_profile_digest) for r in manifest.requests()]

    def test_seven_requests_real_ledgers_closure_and_restart_without_redispatch(self):
        outcome = self.run_control()
        self.assertTrue(outcome["result"]["eligible"])
        self.assertFalse(outcome["scoreable"])
        self.assertEqual(outcome["used_seconds"], 847)
        self.assertEqual(outcome["model_calls"], 0)
        self.assertEqual(len(outcome["compute_receipts"]), 7)
        self.assertEqual(self.statuses(), ["consumed"] * 7)
        self.assertEqual(len(_RetainedPairedTransport.dispatches), 7)
        self.assertEqual(self.run_control(), outcome)
        self.assertEqual(len(_RetainedPairedTransport.dispatches), 7)
        self.assertEqual(outcome["spend_admission"]["reserved_usd_nanos"]["modal"], 19_965_888_000)
        self.assertEqual(outcome["spend_admission"]["reserved_usd_nanos"]["openrouter"], 0)

    def test_exhausted_cap_grants_nothing_and_keeps_historical_reserve(self):
        changed = deepcopy(self.authorization)
        changed["batch_approval"]["provider_limits_usd_nanos"] = self.envelope.snapshot()["provider_limits_usd_nanos"]
        changed["batch_approval"]["total_limit_usd_nanos"] = sum(changed["batch_approval"]["provider_limits_usd_nanos"].values())
        with self.assertRaisesRegex(PermissionError, "exhausted"):
            self.run_control(changed)
        self.assertEqual(self.statuses(), [None] * 7)
        self.assertEqual(_RetainedPairedTransport.dispatches, [])
        self.assertEqual(self.envelope.snapshot()["reserved_usd_nanos"]["modal"], 11_000_000_000)

    def test_expired_dirty_or_extra_work_authority_cannot_issue_jobs(self):
        expired = {**self.authorization, "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
        with self.assertRaisesRegex(PermissionError, "expire"):
            self.run_control(expired)
        with self.assertRaisesRegex(PermissionError, "dirty"):
            self.run_control(clean=False)
        changed = deepcopy(self.authorization)
        changed["batch_approval"]["admissions"][0]["maximum_usd_nanos"] += 1
        with self.assertRaisesRegex(PermissionError, "outside its seven"):
            self.run_control(changed)
        self.assertEqual(self.statuses(), [None] * 7)
        self.assertFalse((self.envelope.root / "batches").exists())

    def test_no_new_request_authority_after_expiry_even_if_controller_claim_exists(self):
        # A crash immediately after the immutable claim must not turn it into
        # an indefinite right to start the seven physical jobs.
        expired = {**self.authorization, "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
        retain_document(self.state / "operator-authorization.json", expired)
        with self.assertRaisesRegex(PermissionError, "expire"):
            self.run_control(expired)
        self.assertEqual(self.statuses(), [None] * 7)
        self.assertEqual(_RetainedPairedTransport.dispatches, [])

    def test_pending_call_is_preserved_and_cannot_be_replaced(self):
        original = _RetainedPairedTransport.dispatch
        def interrupted(transport, request, candidate):
            result = original(transport, request, candidate)
            raise RuntimeError("simulated interruption after remote dispatch before acknowledgment")
        with self.assertRaisesRegex(EvaluationInProgress, "uncertain"):
            self.run_control(dispatch=interrupted)
        self.assertEqual(len(_RetainedPairedTransport.dispatches), 1)
        # The durable backend leaves the dispatch uncertain, not failed. A
        # restart needs explicit dispatch reconciliation, never another spawn.
        with self.assertRaisesRegex(EvaluationInProgress, "uncertain"):
            self.run_control()
        self.assertEqual(len(_RetainedPairedTransport.dispatches), 1)

    def test_reference_ineligibility_is_a_completed_negative_control_not_an_abort(self):
        _RetainedPairedTransport.wrong_reference = True
        outcome = self.run_control()
        self.assertEqual(outcome["status"], "complete")
        self.assertFalse(outcome["result"]["eligible"])
        self.assertIn("reference_correctness_failed", outcome["result"]["failures"])
