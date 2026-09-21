"""No-spend operator-gate tests, using real profiles and retained qualification."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import tempfile
import sqlite3
import unittest
from unittest.mock import patch

from agent_collab_evals.adapters.oci_sandbox import OciSandboxExec
from agent_collab_evals.canonical import digest_file, digest_value
from agent_collab_evals.pilot_evidence import retain_document
from agent_collab_evals.pilot_spend import PilotSpendEnvelope
from agent_collab_evals.pilot_spend_guard import PilotSpendGuard
from agent_collab_evals.solo_authorization import ExploratorySoloAuthorization, configuration_binding, run_authorized_solo
from agent_collab_evals.solo_live_configuration import LivePilotConfiguration, make_live_dependencies
from tests.quality_fixture import REPOSITORY_ROOT as ROOT, real_hidden_quality_bundle


class SoloAuthorizationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        _, bundle, _ = real_hidden_quality_bundle(self.root / "bundle")
        document = json.loads((ROOT / "config/pilots/solo-live-oci-v1.json").read_text())
        document.update(hidden_manifest=str(bundle.manifest_path), hidden_manifest_digest=bundle.manifest_digest,
            sandbox_profile="config/enforcement_profiles/oci-opencode-podman-development-v1.json",
            sandbox_engine_identity_digest=digest_value("fixture-engine"))
        retain_document(self.root / "configuration.json", document)
        self.configuration = LivePilotConfiguration.load(self.root / "configuration.json", ROOT)
        # The fixture uses synthetic private quality data; hidden-loader policy
        # checks have their own production tests. All other bindings are real.
        mock_hidden = patch.object(LivePilotConfiguration, "hidden_bundle", return_value=bundle)
        mock_hidden.start()
        self.addCleanup(mock_hidden.stop)
        plan = json.loads((ROOT / "config/pilots/solo-spend-envelope-v1.json").read_text())
        self.envelope = PilotSpendEnvelope(self.root / "journal", plan)
        evidence_root = ROOT / "evidence/deployment/solo-qualification-20260914"
        for path in sorted(evidence_root.glob("*.json")):
            receipt = json.loads(path.read_text())
            if "operation_key" in receipt:
                self.envelope.reserve(**{key: value for key, value in receipt.items() if key != "plan_digest"})
        selection = "config/provider_qualification/deepseek-v4-flash-deepinfra-development-selection-20260914.json"
        readiness = self.root / "synthetic-operator-assessment.json"
        retain_document(readiness, {"test_only": True, "no_spend": True})
        reference = {"file": str(readiness), "digest": digest_file(readiness)}
        self.document = {"schema_version": "exploratory-solo-authorization/v1",
            "scope": "one_exploratory_solo_attempt", "run_id": "one-attempt",
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "configuration_digest": configuration_binding(self.configuration),
            "spend_plan_digest": self.envelope.plan_digest, "spend_journal": str(self.envelope.root),
            "state_root": str(self.root / "runs"), "engine_executable": "/usr/bin/true",
            "engine_identity_digest": document["sandbox_engine_identity_digest"],
            "qualification_admissions_digest": digest_value(self.envelope.snapshot()["receipts"]),
            "provider_selection": {"file": selection, "digest": digest_file(ROOT / selection)},
            "readiness_evidence": {name: reference for name in ("deployment", "evaluator", "billing", "modal_cancellation")}}
        cancellation = evidence_root / "modal-access-v1/outcome.json"
        self.document["readiness_evidence"]["modal_cancellation"] = {"file": str(cancellation), "digest": digest_file(cancellation)}

    def authority(self, **changes):
        path = self.root / f"approval-{len(list(self.root.glob('approval-*')))}.json"
        digest = retain_document(path, {**self.document, **changes})
        return ExploratorySoloAuthorization.load(path, digest)

    def validate(self, authority, configuration=None, run_id="one-attempt", state_root=None):
        authority.validate(configuration or self.configuration, self.envelope, run_id, state_root or self.root / "runs")

    def test_approved_development_composition_keeps_registration_unresolved(self):
        authority = self.authority()
        self.validate(authority)
        sandbox = OciSandboxExec(self.configuration.sandbox, Path("/usr/bin/true"), self.document["engine_identity_digest"])
        guard = PilotSpendGuard(self.configuration, self.envelope, "one-attempt")
        live = make_live_dependencies(self.configuration, api_key="unused", process_sandbox=sandbox,
            spend_guard=guard, exploratory_authorization=authority, state_root=self.root / "runs")
        self.assertEqual(live.operator_authorization["digest"], authority.digest)
        self.assertIs(guard.operator_authorization, authority)
        self.assertEqual(self.configuration.sandbox.status, "development_conformance")
        self.assertTrue(self.configuration.sandbox.unresolved_gates)
        self.assertEqual(len(self.envelope.snapshot()["receipts"]), 2)
        with self.assertRaisesRegex(ValueError, "not execution-authorized"):
            make_live_dependencies(self.configuration, api_key="unused", process_sandbox=sandbox, spend_guard=guard)

    def test_run_configuration_journal_and_deadline_are_bound(self):
        authority = self.authority()
        with self.assertRaisesRegex(PermissionError, "run or state"):
            self.validate(authority, run_id="another")
        with self.assertRaisesRegex(PermissionError, "run or state"):
            self.validate(authority, state_root=self.root / "elsewhere")
        with self.assertRaisesRegex(PermissionError, "configuration differs"):
            self.validate(authority, replace(self.configuration, document={**self.configuration.document, "task_seed": 4}))
        with self.assertRaisesRegex(PermissionError, "journal differs"):
            self.validate(self.authority(spend_journal=str(self.root / "other-journal")))
        with self.assertRaisesRegex(PermissionError, "expired"):
            self.authority(expires_at="2020-01-01T00:00:00+00:00")

    def test_approval_requires_independent_digest_and_all_readiness_files(self):
        with self.assertRaisesRegex(ValueError, "explicit operator"):
            ExploratorySoloAuthorization.load(self.root / "absent", "")
        with self.assertRaisesRegex(ValueError, "every readiness"):
            self.validate(self.authority(readiness_evidence={}))
        missing = {**self.document["readiness_evidence"], "evaluator": {"file": str(self.root / "absent"), "digest": digest_value("none")}}
        with self.assertRaises(FileNotFoundError):
            self.validate(self.authority(readiness_evidence=missing))

    def test_one_attempt_cannot_restart_with_a_new_run_id(self):
        authority = self.authority()
        self.validate(authority)
        PilotSpendGuard(self.configuration, self.envelope, "one-attempt").begin("one-attempt", digest_value("run"))
        with self.assertRaisesRegex(PermissionError, "already admitted"):
            self.validate(authority)
        with self.assertRaisesRegex(RuntimeError, "retry differs"):
            PilotSpendGuard(self.configuration, self.envelope, "second").begin("second", digest_value("run-2"))

    def test_operator_entrypoint_uses_the_existing_lifecycle_after_checks(self):
        authority = self.authority()
        sandbox = OciSandboxExec(self.configuration.sandbox, Path("/usr/bin/true"), self.document["engine_identity_digest"])
        with (patch.object(ExploratorySoloAuthorization, "load", return_value=authority),
              patch.object(ExploratorySoloAuthorization, "process_sandbox", return_value=sandbox),
              patch.object(LivePilotConfiguration, "load", return_value=self.configuration),
              patch.dict("os.environ", {"OPENROUTER_API_KEY": "synthetic-no-network"}),
              patch("agent_collab_evals.solo_pilot_command._execute_solo_pilot", return_value={"test_only": True}) as execute):
            result = run_authorized_solo(self.root / "configuration.json", self.root / "runs",
                "one-attempt", self.root / "approval-0.json", authority.digest)
            self.assertTrue(result["test_only"])
            execute.assert_called_once()
            self.assertEqual(execute.call_args.kwargs["live"].operator_authorization["digest"], authority.digest)
            self.assertEqual(len(self.envelope.snapshot()["receipts"]), 2)
            with self.assertRaisesRegex(PermissionError, "run or state"):
                run_authorized_solo(self.root / "configuration.json", self.root / "runs", "wrong",
                    self.root / "approval-0.json", authority.digest)
            execute.assert_called_once()

    def test_reviewed_retry_composes_with_authority_and_guard_once(self):
        PilotSpendGuard(self.configuration, self.envelope, "first-solo").begin("first-solo", digest_value("first-run"))
        audit = self.root / "aborted.json"
        retain_document(audit, {"run_id": "first-solo", "status": "aborted", "scoreable": False,
            "budget_reconciliation": {"valid": True}, "remote_cleanup": [{"terminal_confirmed": True}],
            "spend_admission": self.envelope.snapshot()})
        budget = self.root / "budget.sqlite3"
        connection = sqlite3.connect(budget)
        try:
            connection.executescript("CREATE TABLE budget_campaigns (campaign_run_id TEXT, charged_usd_nanos INTEGER);"
                "INSERT INTO budget_campaigns VALUES ('first-solo', 0); CREATE TABLE budget_reservations (id TEXT);")
        finally:
            connection.close()
        amendment = {"schema_version": "exploratory-solo-retry/v1", "prior_plan_digest": self.envelope.plan_digest,
            "prior_receipts_digest": digest_value(self.envelope.snapshot()["receipts"]),
            "prior_audit": {"file": str(audit), "digest": digest_file(audit)},
            "prior_budget": {"file": str(budget), "digest": digest_file(budget)},
            "run_id": "one-attempt", "provider_limits_usd_nanos": {"modal": 14_000_000_000, "openrouter": 3_000_000_000},
            "total_limit_usd_nanos": 17_000_000_000}
        path = self.root / "amendment.json"
        retain_document(path, amendment)
        plan = json.loads((ROOT / "config/pilots/solo-spend-envelope-v1.json").read_text())
        self.envelope = PilotSpendEnvelope(self.envelope.root, plan, retry=amendment)
        self.configuration = replace(self.configuration,
            document={**self.configuration.document, "modal_limit_usd_nanos": 14_000_000_000})
        authority = self.authority(schema_version="exploratory-solo-authorization/v2",
            retry_amendment={"file": str(path), "digest": digest_file(path)},
            configuration_digest=configuration_binding(self.configuration))
        self.validate(authority)
        guard = PilotSpendGuard(self.configuration, self.envelope, "one-attempt")
        guard.operator_authorization = authority
        guard.begin("one-attempt", digest_value("retry-run"))
        self.assertEqual(guard.snapshot()["remaining_usd_nanos"]["openrouter"], 50_000_000)
        with self.assertRaisesRegex(PermissionError, "already admitted"):
            self.validate(authority)
        with self.assertRaisesRegex(RuntimeError, "already admitted"):
            guard.begin("one-attempt", digest_value("retry-run"))


if __name__ == "__main__":
    unittest.main()
