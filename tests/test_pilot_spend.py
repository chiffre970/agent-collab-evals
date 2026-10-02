"""No-spend tests for the shared gross-dollar admission journal."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import sqlite3
import unittest
from unittest.mock import patch

from agent_collab_evals.canonical import canonical_json_bytes, digest_bytes, digest_file, digest_value
from agent_collab_evals.adapters.local_measurements import LocalMeasurementBundleStore
from agent_collab_evals.pilot_evidence import retain_document
from agent_collab_evals.modal_pilot_cost import modal_pilot_cost
from agent_collab_evals.pilot_spend import PilotSpendEnvelope
from agent_collab_evals.pilot_retry import (
    _validate_collector_failure_settlement,
    _validate_feedback_failure_settlement,
    _validate_reference_probe_settlement,
)


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

    def retry_document(self):
        self.reserve("qualification:provider-route-v1", 50_000_000, provider="openrouter", purpose="qualification")
        self.reserve("qualification:modal-access-v1", 1_532_160_000, purpose="qualification")
        self.reserve("pilot:single-attempt:openrouter:base", 2_900_000_000, provider="openrouter")
        self.reserve("pilot:single-attempt:modal:base", 1_000_000_000, purpose="overhead")
        self.reserve("pilot:first-solo:compute:reference", 766_080_000)
        audit = self.root / "prior-audit.json"
        retain_document(audit, {"run_id": "first-solo", "status": "aborted", "scoreable": False,
            "budget_reconciliation": {"valid": True}, "remote_cleanup": [{"terminal_confirmed": True}],
            "spend_admission": self.envelope.snapshot()})
        budget = self.root / "budget.sqlite3"
        connection = sqlite3.connect(budget)
        try:
            connection.executescript("CREATE TABLE budget_campaigns (campaign_run_id TEXT, charged_usd_nanos INTEGER);"
                "INSERT INTO budget_campaigns VALUES ('first-solo', 0);"
                "CREATE TABLE budget_reservations (reservation_id TEXT);")
        finally:
            connection.close()
        return {"schema_version": "exploratory-solo-retry/v1", "prior_plan_digest": self.envelope.plan_digest,
            "prior_receipts_digest": digest_value(self.envelope.snapshot()["receipts"]),
            "prior_audit": {"file": str(audit), "digest": digest_file(audit)},
            "prior_budget": {"file": str(budget), "digest": digest_file(budget)},
            "run_id": "retry-one", "provider_limits_usd_nanos": {"modal": 14_000_000_000, "openrouter": 3_000_000_000},
            "total_limit_usd_nanos": 17_000_000_000}

    def test_explicit_retry_preserves_modal_allowances_and_releases_only_unused_model(self):
        document = self.retry_document()
        original = {p.name: p.read_bytes() for p in self.envelope.root.glob("*.json")}
        retry = PilotSpendEnvelope(self.envelope.root, self.plan, retry=document)
        self.assertEqual(retry.snapshot()["remaining_usd_nanos"], {"modal": 10_701_760_000, "openrouter": 2_950_000_000})
        key = f"pilot:retry-{retry.retry_digest[7:]}:openrouter:base"
        retry.reserve(operation_key=key, provider="openrouter", purpose="pilot",
            request_digest=digest_value("retry"), maximum_usd_nanos=2_900_000_000, allow_existing=False)
        restarted = PilotSpendEnvelope(retry.root, self.plan, retry=document)
        self.assertEqual(restarted.snapshot()["remaining_usd_nanos"]["openrouter"], 50_000_000)
        with self.assertRaisesRegex(RuntimeError, "already admitted"):
            restarted.reserve(operation_key=key, provider="openrouter", purpose="pilot",
                request_digest=digest_value("retry"), maximum_usd_nanos=2_900_000_000, allow_existing=False)
        with self.assertRaisesRegex(PermissionError, "pinned retry"):
            self.envelope.snapshot()
        with self.assertRaisesRegex(PermissionError, "approved attempt"):
            self.reserve("pilot:unapproved:compute:x", 1, envelope=retry)
        for name, raw in original.items():
            self.assertEqual((retry.root / name).read_bytes(), raw)

    def test_retry_rejects_nonempty_model_ledger(self):
        document = self.retry_document()
        budget = Path(document["prior_budget"]["file"])
        connection = sqlite3.connect(budget)
        try:
            connection.execute("INSERT INTO budget_reservations VALUES ('in-flight')")
            connection.commit()
        finally:
            connection.close()
        document["prior_budget"]["digest"] = digest_file(budget)
        with self.assertRaisesRegex(ValueError, "not entirely unused"):
            PilotSpendEnvelope(self.envelope.root, self.plan, retry=document)
        self.assertFalse((self.envelope.root / "retry/approval.json").exists())

    def test_retry_requires_exact_existing_receipts(self):
        document = self.retry_document()
        self.reserve("unaccounted-prior-admission", 1)
        with self.assertRaisesRegex(RuntimeError, "unapproved retry"):
            PilotSpendEnvelope(self.envelope.root, self.plan, retry=document)

    def settlement_document(self):
        previous = self.retry_document()
        previous_path = self.root / "previous.json"
        retain_document(previous_path, previous)
        retry = PilotSpendEnvelope(self.envelope.root, self.plan, retry=previous)
        prefix = "pilot:retry-" + retry.retry_digest[7:]
        self.reserve(prefix + ":openrouter:base", 2_900_000_000, provider="openrouter", envelope=retry)
        self.reserve(prefix + ":modal:base", 1_000_000_000, purpose="overhead", envelope=retry)
        request_digest = digest_value("second-reference")
        self.reserve("pilot:retry-one:compute:" + request_digest[7:], 766_080_000, envelope=retry)
        prior = self.root / "failed-reference"
        config = prior / "run-config.json"
        retain_document(config, {"git_commit": "c116488efd2bdd3af6abe766320781104c793420", "git_dirty": False})
        audit = prior / "audit.json"
        retain_document(audit, {"run_id": "retry-one", "status": "aborted", "scoreable": False,
            "failure": {"stage": "reference"}, "run_config_digest": digest_file(config),
            "remote_cleanup": [{"terminal_confirmed": True, "request_digest": request_digest}],
            "spend_admission": retry.snapshot()})
        billing = self.root / "billing.json"
        retain_document(billing, [{"object_id": "app-reference", "environment": "dev", "cost": "0.22239952"}])
        def ref(path):
            return {"file": str(path), "digest": digest_file(path)}
        return retry, {"schema_version": "exploratory-solo-retry/v2", "previous_amendment": ref(previous_path),
            "prior_plan_digest": retry.plan_digest, "prior_receipts_digest": digest_value(retry.snapshot()["receipts"]),
            "prior_audit": ref(audit), "prior_run_config": ref(config), "billing_report": ref(billing),
            "reference_app_id": "app-reference", "run_id": "settled-retry",
            "provider_limits_usd_nanos": previous["provider_limits_usd_nanos"],
            "total_limit_usd_nanos": 17_000_000_000, "billing_buffer_usd_nanos": 100_000_000}

    def test_reviewed_settlement_fits_full_run_without_raising_limits(self):
        prior, document = self.settlement_document()
        original = {p.name: p.read_bytes() for p in prior.root.glob("*.json")}
        settled = PilotSpendEnvelope(prior.root, self.plan, retry=document)
        snapshot = settled.snapshot()
        self.assertEqual(snapshot["remaining_usd_nanos"], {"modal": 10_379_360_480, "openrouter": 2_950_000_000})
        self.assertGreater(snapshot["remaining_usd_nanos"]["modal"], 10_192_960_000)
        with self.assertRaisesRegex(PermissionError, "settlement"):
            prior.snapshot()
        with self.assertRaisesRegex(PermissionError, "settlement"):
            PilotSpendEnvelope(prior.root, self.plan, retry=prior.retry)
        key = "pilot:retry-" + settled.retry_digest[7:] + ":openrouter:base"
        self.reserve(key, 2_900_000_000, provider="openrouter", envelope=settled)
        reopened = PilotSpendEnvelope(prior.root, self.plan, retry=document)
        self.assertEqual(reopened.snapshot()["remaining_usd_nanos"]["openrouter"], 50_000_000)
        for name, content in original.items():
            self.assertEqual((prior.root / name).read_bytes(), content)

    def test_settlement_rejects_any_actor_budget_or_missing_report(self):
        prior, document = self.settlement_document()
        budget = Path(document["prior_audit"]["file"]).parent / "budget.sqlite3"
        budget.touch()
        with self.assertRaisesRegex(ValueError, "pre-agent"):
            PilotSpendEnvelope(prior.root, self.plan, retry=document)
        budget.unlink()
        with self.assertRaisesRegex(ValueError, "billing row"):
            PilotSpendEnvelope(prior.root, self.plan, retry={**document, "reference_app_id": "absent"})
        self.assertFalse((prior.root / "retry/settlement-approval.json").exists())

    def test_final_settlement_preserves_markers_and_bounds_another_attempt(self):
        _, second = self.settlement_document()
        second_path = self.root / "second-amendment.json"
        retain_document(second_path, second)
        settled = PilotSpendEnvelope(self.envelope.root, self.plan, retry=second)
        prefix = "pilot:retry-" + settled.retry_digest[7:]
        self.reserve(prefix + ":openrouter:base", 2_900_000_000,
            provider="openrouter", envelope=settled)
        self.reserve(prefix + ":modal:base", 1_000_000_000,
            purpose="overhead", envelope=settled)
        for index in range(2):
            self.reserve(f"pilot:settled-retry:compute:{index}", 766_080_000,
                envelope=settled)
        prior = self.root / "failed-public"
        config = prior / "run-config.json"
        retain_document(config, {"git_commit": "90746ce881d053b7c2d0ec11ee1ce0abd8a20cbd",
            "git_dirty": False, "config": {"gateway_profile": "unused-in-mocked-reconciler"}})
        audit = prior / "audit.json"
        retain_document(audit, {"run_id": "settled-retry", "status": "aborted", "scoreable": False,
            "failure": {"stage": "public_evaluation"}, "run_config_digest": digest_file(config),
            "budget_reconciliation": {"valid": True},
            "remote_cleanup": [{"terminal_confirmed": False, "function_call_id": "fc-complete"},
                               {"terminal_confirmed": True}],
            "spend_admission": settled.snapshot()})
        def ref(path):
            return {"file": str(path), "digest": digest_file(path)}
        recovery = self.root / "recovery.json"
        manifest = self.root / "manifest.json"
        receipt = self.root / "remote-receipt.json"
        score = self.root / "score.json"
        cleanup = self.root / "cleanup.json"
        billing = self.root / "billing-current.json"
        receipt_value = {"ok": True}
        receipt_digest = digest_bytes(canonical_json_bytes(receipt_value) + b"\n")
        retain_document(receipt, receipt_value)
        retain_document(manifest, {"root": "model-serving/finished", "remote_receipt_digest": receipt_digest})
        retain_document(recovery, {"output_status": "recovered", "new_compute_dispatched": False,
            "call_id": "fc-complete", "result": {"root": "model-serving/finished",
            "remote_receipt_digest": receipt_digest}})
        retain_document(score, {"campaign_status": "aborted", "diagnostic_only": True,
            "remote_ok": True, "all_points_valid": True, "hidden_quality_evaluated": False,
            "new_compute_dispatched": False, "identity_errors": [], "points": [{}] * 9})
        retain_document(cleanup, {"run_id": "settled-retry", "active_modal_apps": [],
            "actor_containers": []})
        current = ("app-candidate", "app-helper", "app-reference-current")
        rows = [{"object_id": "ap-prLVu6B1U3GH3DTD9D8CzI", "environment": "dev",
            "description": "agent-collab-evals-model-serving-reference",
            "interval_start": "2026-09-15T00:00:00", "cost": "0.23026748"}]
        rows += [{"object_id": item, "environment": "dev",
            "description": "agent-collab-evals-model-serving-reference",
            "interval_start": "2026-09-21T00:00:00", "cost": cost}
            for item, cost in (("app-reference", "0.22239952"),
                (current[0], "0.21471068"), (current[1], "0.00006161"),
                (current[2], "0.21475082"))]
        retain_document(billing, rows)
        document = {"schema_version": "exploratory-solo-retry/v3",
            "previous_amendment": ref(second_path), "prior_plan_digest": settled.plan_digest,
            "prior_receipts_digest": digest_value(settled.snapshot()["receipts"]),
            "prior_audit": ref(audit), "prior_run_config": ref(config),
            "prior_budget_plan": ref(config), "prior_budget_database": ref(config),
            "recovery_output": ref(recovery), "recovery_manifest": ref(manifest),
            "recovery_receipt": ref(receipt), "recovery_score": ref(score),
            "cleanup_observation": ref(cleanup), "billing_report": ref(billing),
            "first_reference_app_id": "ap-prLVu6B1U3GH3DTD9D8CzI",
            "current_app_ids": list(current), "run_id": "next-attempt",
            "provider_limits_usd_nanos": second["provider_limits_usd_nanos"],
            "total_limit_usd_nanos": second["total_limit_usd_nanos"]}
        with patch("agent_collab_evals.pilot_retry._reconcile_prior_model", return_value=1_351_620):
            with self.assertRaisesRegex(ValueError, "recovery or cleanup"):
                PilotSpendEnvelope(settled.root, self.plan, retry={**document,
                    "recovery_output": ref(score)})
            final = PilotSpendEnvelope(settled.root, self.plan, retry=document)
            self.assertEqual(final.snapshot()["remaining_usd_nanos"],
                {"modal": 10_283_013_000, "openrouter": 2_938_648_380})
            reopened = PilotSpendEnvelope(settled.root, self.plan, retry=document)
            self.assertEqual(reopened.snapshot()["remaining_usd_nanos"],
                final.snapshot()["remaining_usd_nanos"])
        with self.assertRaisesRegex(PermissionError, "final settlement"):
            settled.snapshot()
        self.assertTrue((settled.root / "retry/approval.json").exists())
        self.assertTrue((settled.root / "retry/settlement-approval.json").exists())
        self.assertTrue((settled.root / "retry/final-settlement-approval.json").exists())

    def test_feedback_settlement_requires_terminal_public_only_evidence(self):
        previous = {"schema_version": "exploratory-solo-retry/v3", "run_id": "solo-next-0923",
            "provider_limits_usd_nanos": {"modal": 14_000_000_000, "openrouter": 3_000_000_000},
            "total_limit_usd_nanos": 17_000_000_000}
        previous_path = self.root / "third.json"
        retain_document(previous_path, previous)
        plan_digest = self.envelope.plan_digest
        def receipt(key, provider, amount):
            return {"operation_key": key, "provider": provider, "maximum_usd_nanos": amount}
        prior = [receipt("qualification:modal-access-v1", "modal", 1_532_160_000)]
        prefix = "pilot:retry-" + digest_value(previous)[7:]
        requests = (digest_value("reference"), digest_value("candidate"))
        current = [receipt(prefix + ":openrouter:base", "openrouter", 2_900_000_000),
            receipt(prefix + ":modal:base", "modal", 1_000_000_000),
            *(receipt("pilot:solo-next-0923:compute:" + item[7:], "modal", 766_080_000)
                for item in requests)]
        receipts = prior + current
        config_path = self.root / "run-config.json"
        retain_document(config_path, {"git_commit": "ad75c700dfbdc0986950cd6c4e43834eb952f705",
            "git_dirty": False, "config": {"sandbox_profile":
                "config/enforcement_profiles/oci-opencode-podman-development-v1.json"}})
        audit_path = self.root / "audit.json"
        audit = {"run_id": previous["run_id"], "status": "aborted", "scoreable": False,
            "failure": {"stage": "public_feedback", "type": "RuntimeError"},
            "cleanup_failure": "ExceptionGroup", "run_config_digest": digest_file(config_path),
            "budget_reconciliation": {"valid": True},
            "partial_compute_snapshot": {"hidden_reserved_seconds": 0,
                "hidden_used_seconds": 0, "reservations": [{"scope": "visible", "status": "complete"}]},
            "remote_cleanup": [{"terminal_confirmed": True, "request_digest": item} for item in requests],
            "spend_admission": {"receipts": receipts, "retry_amendment_digest": digest_value(previous)}}
        retain_document(audit_path, audit)
        cleanup_path = self.root / "cleanup.json"
        retain_document(cleanup_path, {"run_id": previous["run_id"],
            "active_modal_apps": [], "actor_containers": []})
        billing_path = self.root / "billing.json"
        rows = [("ap-vWm2nqnesAmOq740ajOMHs", "0.23874382"),
            ("ap-U1QlcwmrZURytic8pXj78g", "0.21029472"),
            ("ap-s5Ta3RtUBuCCak6J5Qaqya", "0.00005459"),
            ("ap-LNPq0tjXccxhwJhmuPCjG2", "0.00004966")]
        retain_document(billing_path, {"schema_version": "modal-billing-snapshot/v1",
            "retrieved_at": "2026-09-23T09:00:00+00:00", "start": "2026-09-01", "end": "2026-09-24",
            "rows": [{"object_id": name, "environment": "dev",
                "interval_start": "2026-09-23T00:00:00", "cost": cost} for name, cost in rows]})
        budget_path = self.root / "budget.sqlite3"
        budget_path.touch()
        def ref(path):
            return {"file": str(path), "digest": digest_file(path)}
        document = {"schema_version": "exploratory-solo-retry/v4",
            "previous_amendment": ref(previous_path), "prior_plan_digest": plan_digest,
            "prior_receipts_digest": digest_value(receipts), "prior_audit": ref(audit_path),
            "prior_run_config": ref(config_path), "prior_budget_plan": ref(config_path),
            "prior_budget_database": ref(budget_path), "cleanup_observation": ref(cleanup_path),
            "billing_report": ref(billing_path), "reference_app_id": rows[0][0],
            "candidate_app_id": rows[1][0], "helper_app_ids": [rows[2][0], rows[3][0]],
            "run_id": "solo-timeoutfix-0923", "provider_limits_usd_nanos": previous["provider_limits_usd_nanos"],
            "total_limit_usd_nanos": previous["total_limit_usd_nanos"],
            "billing_buffer_usd_nanos": 100_000_000, "model_buffer_usd_nanos": 10_000_000,
            "qualification_release_usd_nanos": 500_000_000}
        releases = {"modal": 3_879_493_000, "openrouter": 8_688_648_380}
        with (patch("agent_collab_evals.pilot_retry.validate_retry", return_value=(prior, releases)),
              patch("agent_collab_evals.pilot_retry._reconcile_prior_model", return_value=3_081_720) as reconcile):
            self.assertEqual(_validate_feedback_failure_settlement(document, plan_digest),
                (receipts, {"modal": 6_362_510_210, "openrouter": 11_575_566_660}))
            reconcile.assert_called_once()
            with self.assertRaisesRegex(ValueError, "release bounds"):
                _validate_feedback_failure_settlement({**document, "qualification_release_usd_nanos": 600_000_000}, plan_digest)
            changed = self.root / "hidden-audit.json"
            retain_document(changed, {**audit, "partial_compute_snapshot":
                {**audit["partial_compute_snapshot"], "hidden_reserved_seconds": 1}})
            with self.assertRaisesRegex(ValueError, "public-only"):
                _validate_feedback_failure_settlement({**document, "prior_audit": ref(changed)}, plan_digest)

    def test_feedback_settlement_marker_prevents_older_authorities(self):
        chain = []
        for index, version in enumerate(("v1", "v2", "v3", "v4"), 1):
            value = {"schema_version": f"exploratory-solo-retry/{version}", "run_id": f"run-{index}",
                "provider_limits_usd_nanos": self.plan["provider_limits_usd_nanos"],
                "total_limit_usd_nanos": self.plan["total_limit_usd_nanos"]}
            if chain:
                value["previous_amendment"] = {"file": str(chain[-1][0]), "digest": digest_file(chain[-1][0])}
            path = self.root / f"amendment-{index}.json"
            retain_document(path, value)
            chain.append((path, value))
        for filename, (_, value) in zip(("approval.json", "settlement-approval.json",
                                          "final-settlement-approval.json"), chain):
            retain_document(self.envelope.root / "retry" / filename, value)
        with patch("agent_collab_evals.pilot_retry.validate_retry", return_value=([], {"modal": 0, "openrouter": 0})):
            latest = PilotSpendEnvelope(self.envelope.root, self.plan, retry=chain[-1][1])
            self.assertTrue((latest.root / "retry/feedback-settlement-approval.json").exists())
            self.assertEqual(PilotSpendEnvelope(latest.root, self.plan, retry=chain[-1][1]).snapshot()["receipts"], [])
            with self.assertRaisesRegex(PermissionError, "feedback settlement"):
                PilotSpendEnvelope(latest.root, self.plan, retry=chain[-2][1])

    def test_collector_settlement_verifies_recovered_reference_and_billing(self):
        plan_digest = self.envelope.plan_digest
        call_id = "fc-01M36RFHNVSH1NPN1NR2YSMBW4"
        request_digest = digest_value("reference-request")
        measurement = "exec-" + request_digest[7:]
        evidence_root = ("model-serving/d8b2ac9486364026f3975d15598bc86e52e7a1fe784889530275f93e0a00647d/"
            "repetition-0001-attempt-01")
        previous_billing = self.root / "previous-billing.json"
        older_apps = ("ap-vWm2nqnesAmOq740ajOMHs", "ap-U1QlcwmrZURytic8pXj78g",
            "ap-s5Ta3RtUBuCCak6J5Qaqya", "ap-LNPq0tjXccxhwJhmuPCjG2")
        old_rows = [{"object_id": app, "environment": "dev", "cost": "0.00000001",
            "interval_start": "2026-09-23T00:00:00"} for app in older_apps]
        retain_document(previous_billing, {"rows": old_rows})
        previous = {"schema_version": "exploratory-solo-retry/v4", "run_id": "solo-timeoutfix-0923",
            "provider_limits_usd_nanos": self.plan["provider_limits_usd_nanos"],
            "total_limit_usd_nanos": self.plan["total_limit_usd_nanos"],
            "billing_report": {"file": str(previous_billing), "digest": digest_file(previous_billing)}}
        previous_path = self.root / "previous.json"
        retain_document(previous_path, previous)
        prior = [{"operation_key": "qualification:modal-access-v1",
            "provider": "modal", "maximum_usd_nanos": 1_532_160_000}]
        prefix = "pilot:retry-" + digest_value(previous)[7:]
        receipts = prior + [
            {"operation_key": prefix + ":openrouter:base", "provider": "openrouter",
                "maximum_usd_nanos": 2_900_000_000},
            {"operation_key": prefix + ":modal:base", "provider": "modal",
                "maximum_usd_nanos": 1_000_000_000},
            {"operation_key": "pilot:solo-timeoutfix-0923:compute:" + request_digest[7:],
                "provider": "modal", "maximum_usd_nanos": 766_080_000},
        ]
        run = self.root / "solo-timeoutfix-0923"
        run.mkdir()
        config_path = run / "run-config.json"
        retain_document(config_path, {"git_commit": "9312b743d256dfbae87fd4e117e68bca31abb75c",
            "git_dirty": False, "config": {"sandbox_profile":
                "config/enforcement_profiles/oci-opencode-podman-development-v2.json"}})
        dispatch_path = run / "dispatch.json"
        retain_document(dispatch_path, {"function_call_id": call_id, "measurement_id": measurement,
            "campaign_manifest_digest": digest_value("campaign"), "evidence_root": evidence_root})
        dispatch = json.loads(dispatch_path.read_text())
        audit_path = run / "audit.json"
        terminal = {"status": "cancellation_requested", "terminal_confirmed": False,
            "function_call_id": call_id, "request_digest": request_digest,
            "dispatch_digest": digest_value(dispatch)}
        retain_document(audit_path, {"run_id": previous["run_id"], "status": "aborted",
            "scoreable": False, "failure": {"stage": "reference", "type": "RuntimeError"},
            "run_config_digest": digest_file(config_path), "remote_cleanup": [terminal],
            "spend_admission": {"receipts": receipts,
                "retry_amendment_digest": digest_value(previous)}})
        compute_path = run / "executions.sqlite3"
        connection = sqlite3.connect(compute_path)
        try:
            connection.execute("CREATE TABLE compute_executions (status TEXT, request_digest TEXT, "
                "external_call_id TEXT, dispatch_evidence_digest TEXT, evidence_locator TEXT, evidence_digest TEXT)")
            connection.execute("INSERT INTO compute_executions VALUES (?, ?, ?, ?, NULL, NULL)",
                ("dispatched", request_digest, call_id, digest_value(dispatch)))
            connection.commit()
        finally:
            connection.close()
        staged = self.root / "staged"
        staged.mkdir()
        remote_path = staged / "remote-receipt.json"
        remote = {"ok": True, "candidate_id": "stock-vllm-0.21.0",
            "campaign_manifest_digest": dispatch["campaign_manifest_digest"]}
        retain_document(remote_path, remote)
        raw = {f"point-{index}.json": b'{}\n' for index in range(9)}
        raw_root = staged / "raw"
        raw_root.mkdir()
        for name, content in raw.items():
            (raw_root / name).write_bytes(content)
        manifest_path = staged / "manifest.json"
        raw_digests = {name: digest_bytes(content) for name, content in raw.items()}
        retain_document(manifest_path, {"schema_version": "modal-evaluator-evidence/v0alpha1",
            "volume_name": "agent-collab-evals-evaluator-staging-v2", "root": evidence_root,
            "remote_receipt_digest": digest_file(remote_path), "raw_digests": raw_digests})
        pointer_path = self.root / "pointer.json"
        retain_document(pointer_path, {"function_call_id": call_id,
            "result": {"schema_version": "modal-evaluator-staging-pointer/v0alpha1",
                "volume_name": "agent-collab-evals-evaluator-staging-v2", "root": evidence_root,
                "remote_receipt_digest": digest_file(remote_path)}})
        normalized = {"valid": True, "modal_function_call_id": call_id,
            "remote_receipt": remote, "performance_score": {"eligible": True},
            "durable_evidence": {"volume_name": "agent-collab-evals-evaluator-evidence-v2",
                "remote_receipt_digest": digest_file(remote_path)},
            "platform_build": {"git_commit": "9312b743d256dfbae87fd4e117e68bca31abb75c",
                "collector_git_commit": "bfca53e06cdb5f787c076cda2b5242326ee8a328"}}
        bundle = LocalMeasurementBundleStore(self.root / "conformance/measurements").save(
            measurement, 1, normalized, raw)
        conformance_path = bundle / "receipt.json"
        cleanup_path = self.root / "cleanup.json"
        retain_document(cleanup_path, {"run_id": previous["run_id"],
            "active_modal_apps": [], "actor_containers": []})
        billing_path = self.root / "billing.json"
        retain_document(billing_path, {"schema_version": "modal-billing-snapshot/v1",
            "retrieved_at": "2026-09-24T05:00:00+00:00", "start": "2026-09-01",
            "end": "2026-09-25", "rows": old_rows + [{"object_id": "ap-1Wao2Jn6Msdl2fGA3XyMJ4",
                "environment": "dev", "interval_start": "2026-09-23T00:00:00",
                "cost": "0.20902658"}]})
        def ref(path):
            return {"file": str(path), "digest": digest_file(path)}
        document = {"schema_version": "exploratory-solo-retry/v5",
            "previous_amendment": ref(previous_path), "prior_plan_digest": plan_digest,
            "prior_receipts_digest": digest_value(receipts), "prior_audit": ref(audit_path),
            "prior_run_config": ref(config_path), "prior_compute_database": ref(compute_path),
            "prior_dispatch_record": ref(dispatch_path), "call_pointer": ref(pointer_path),
            "staged_manifest": ref(manifest_path), "staged_remote_receipt": ref(remote_path),
            "conformance_receipt": ref(conformance_path),
            "cleanup_observation": ref(cleanup_path), "billing_report": ref(billing_path),
            "reference_app_id": "ap-1Wao2Jn6Msdl2fGA3XyMJ4",
            "conformance_app_id": "ap-WVjBgH1M2Js1XxxAtHTuB7",
            "run_id": "solo-collectorfix-0924",
            "provider_limits_usd_nanos": previous["provider_limits_usd_nanos"],
            "total_limit_usd_nanos": previous["total_limit_usd_nanos"],
            "billing_buffer_usd_nanos": 100_000_000,
            "conformance_buffer_usd_nanos": 10_000_000,
            "model_buffer_usd_nanos": 10_000_000,
            "qualification_release_usd_nanos": 500_000_000}
        releases = {"modal": 6_362_510_210, "openrouter": 11_575_566_660}
        with patch("agent_collab_evals.pilot_retry.validate_retry", return_value=(prior, releases)):
            validated = _validate_collector_failure_settlement(document, plan_digest)
            self.assertEqual(validated[0], receipts)
            self.assertEqual(validated[1], {"modal": 8_309_563_630,
                "openrouter": 14_465_566_660})
            (raw_root / "point-0.json").write_bytes(b'{"tampered":true}\n')
            with self.assertRaisesRegex(ValueError, "raw evidence"):
                _validate_collector_failure_settlement(document, plan_digest)
            (raw_root / "point-0.json").write_bytes(raw["point-0.json"])
            connection = sqlite3.connect(compute_path)
            try:
                connection.execute("UPDATE compute_executions SET status='complete'")
                connection.commit()
            finally:
                connection.close()
            changed = {**document, "prior_compute_database": ref(compute_path)}
            with self.assertRaisesRegex(ValueError, "compute ledger"):
                _validate_collector_failure_settlement(changed, plan_digest)

    def test_collector_settlement_marker_prevents_older_authorities(self):
        chain = []
        for index, version in enumerate(("v1", "v2", "v3", "v4", "v5"), 1):
            value = {"schema_version": f"exploratory-solo-retry/{version}",
                "run_id": f"run-{index}",
                "provider_limits_usd_nanos": self.plan["provider_limits_usd_nanos"],
                "total_limit_usd_nanos": self.plan["total_limit_usd_nanos"]}
            if chain:
                value["previous_amendment"] = {"file": str(chain[-1][0]),
                    "digest": digest_file(chain[-1][0])}
            path = self.root / f"collector-amendment-{index}.json"
            retain_document(path, value)
            chain.append((path, value))
        for name, (_, value) in zip(("approval.json", "settlement-approval.json",
                "final-settlement-approval.json", "feedback-settlement-approval.json"), chain):
            retain_document(self.envelope.root / "retry" / name, value)
        with patch("agent_collab_evals.pilot_retry.validate_retry",
                return_value=([], {"modal": 0, "openrouter": 0})):
            latest = PilotSpendEnvelope(self.envelope.root, self.plan, retry=chain[-1][1])
            self.assertTrue((latest.root / "retry/collector-settlement-approval.json").exists())
            with self.assertRaisesRegex(PermissionError, "collector settlement"):
                PilotSpendEnvelope(latest.root, self.plan, retry=chain[-2][1])

    def test_series_settlement_marker_preserves_chain_and_rejects_older_authority(self):
        chain = []
        for index in range(1, 7):
            value = {"schema_version": f"exploratory-solo-retry/v{index}",
                "run_id": f"run-{index}",
                "provider_limits_usd_nanos": {"modal": 16_000_000_000,
                    "openrouter": 3_000_000_000},
                "total_limit_usd_nanos": 19_000_000_000}
            if chain:
                value["previous_amendment"] = {"file": str(chain[-1][0]),
                    "digest": digest_file(chain[-1][0])}
            path = self.root / f"series-amendment-{index}.json"
            retain_document(path, value)
            chain.append((path, value))
        names = ("approval.json", "settlement-approval.json",
            "final-settlement-approval.json", "feedback-settlement-approval.json",
            "collector-settlement-approval.json")
        for name, (_, value) in zip(names, chain):
            retain_document(self.envelope.root / "retry" / name, value)
        with patch("agent_collab_evals.pilot_retry.validate_retry",
                return_value=([], {"modal": 0, "openrouter": 0})):
            latest = PilotSpendEnvelope(self.envelope.root, self.plan, retry=chain[-1][1])
            self.assertTrue((latest.root / "retry/series-settlement-approval.json").exists())
            reopened = PilotSpendEnvelope(self.envelope.root, self.plan, retry=chain[-1][1])
            self.assertEqual(reopened.snapshot()["provider_limits_usd_nanos"]["modal"], 16_000_000_000)
            with self.assertRaisesRegex(PermissionError, "series settlement"):
                PilotSpendEnvelope(latest.root, self.plan, retry=chain[-2][1])

    def test_connected_settlement_marker_preserves_chain_and_rejects_older_authority(self):
        chain = []
        for index in range(1, 8):
            value = {"schema_version": f"exploratory-solo-retry/v{index}",
                "run_id": f"run-{index}",
                "provider_limits_usd_nanos": {"modal": 17_000_000_000,
                    "openrouter": 3_100_000_000},
                "total_limit_usd_nanos": 20_100_000_000}
            if chain:
                value["previous_amendment"] = {"file": str(chain[-1][0]),
                    "digest": digest_file(chain[-1][0])}
            path = self.root / f"connected-amendment-{index}.json"
            retain_document(path, value)
            chain.append((path, value))
        names = ("approval.json", "settlement-approval.json",
            "final-settlement-approval.json", "feedback-settlement-approval.json",
            "collector-settlement-approval.json", "series-settlement-approval.json")
        for name, (_, value) in zip(names, chain):
            retain_document(self.envelope.root / "retry" / name, value)
        with patch("agent_collab_evals.pilot_retry.validate_retry",
                return_value=([], {"modal": 0, "openrouter": 0})):
            latest = PilotSpendEnvelope(self.envelope.root, self.plan, retry=chain[-1][1])
            self.assertTrue((latest.root / "retry/connected-settlement-approval.json").exists())
            self.assertEqual(latest.snapshot()["provider_limits_usd_nanos"],
                {"modal": 17_000_000_000, "openrouter": 3_100_000_000})
            with self.assertRaisesRegex(PermissionError, "connected settlement"):
                PilotSpendEnvelope(latest.root, self.plan, retry=chain[-2][1])

    def test_reference_probe_settlement_releases_only_pre_model_allowance(self):
        def ref(path):
            return {"file": str(path), "digest": digest_file(path)}

        previous = {"schema_version": "exploratory-solo-retry/v7",
            "run_id": "solo-connected-1002"}
        previous_path = self.root / "previous.json"
        retain_document(previous_path, previous)
        prefix = f"pilot:retry-{digest_value(previous)[7:]}:"
        admitted = [
            {"operation_key": key, "provider": provider, "purpose": purpose,
                "maximum_usd_nanos": amount, "request_digest": digest_value(key),
                "plan_digest": self.envelope.plan_digest}
            for key, provider, purpose, amount in (
                (prefix + "openrouter:base", "openrouter", "pilot", 2_900_000_000),
                (prefix + "modal:base", "modal", "overhead", 1_000_000_000),
                ("pilot:solo-connected-1002:compute:d0b948032089035fcab6d071c3205c593186c7d407aecc81757750a8a70c4316",
                    "modal", "pilot", 766_080_000),
            )
        ]
        config_path = self.root / "run-config.json"
        retain_document(config_path, {"git_commit": "f5ea74212535a29dce73c412b7d807d834219d80",
            "git_dirty": False})
        audit_path = self.root / "audit.json"
        retain_document(audit_path, {"run_id": "solo-connected-1002", "status": "aborted",
            "scoreable": False, "failure": {"stage": "reference", "type": "RuntimeError"},
            "budget_reconciliation": None, "run_config_digest": digest_file(config_path),
            "remote_cleanup": [{"function_call_id": "fc-01M3XVBN06YC79HA1EACHJJTVA",
                "status": "cancellation_requested", "terminal_confirmed": False}],
            "spend_admission": {"receipts": admitted,
                "retry_amendment_digest": digest_value(previous)}})
        probe_key = prefix + "modal:cpu-pending-probe-v1"
        probe_receipt = {"operation_key": probe_key, "provider": "modal",
            "purpose": "qualification", "maximum_usd_nanos": 100_000_000,
            "request_digest": digest_value("probe"), "plan_digest": self.envelope.plan_digest}
        probe_path = self.root / "probe.json"
        retain_document(probe_path, {"schema_version": "modal-pending-probe-result/v1",
            "operation_key": probe_key, "admission": probe_receipt,
            "pending_before_completion": True, "terminal_after_completion": False,
            "sentinel_verified": True, "error_type": None})
        complete = sorted((*admitted, probe_receipt),
            key=lambda item: digest_value(item["operation_key"])[7:])
        document = {"schema_version": "exploratory-solo-retry/v8",
            "previous_amendment": ref(previous_path),
            "prior_plan_digest": self.envelope.plan_digest,
            "prior_receipts_digest": digest_value(complete),
            "prior_audit": ref(audit_path), "prior_run_config": ref(config_path),
            "diagnostic_result": ref(probe_path), "run_id": "solo-statusprobe-1003",
            "provider_limits_usd_nanos": {"modal": 20_000_000_000,
                "openrouter": 3_100_000_000},
            "total_limit_usd_nanos": 23_100_000_000,
            "retained_modal_release_usd_nanos": 0}
        with (patch("agent_collab_evals.pilot_retry.validate_retry",
                    return_value=([], {"modal": 123, "openrouter": 456})),
              patch("agent_collab_evals.pilot_retry._REFERENCE_ABORT_AUDIT_DIGEST",
                    digest_file(audit_path))):
            receipts, releases = _validate_reference_probe_settlement(
                document, self.envelope.plan_digest)
            self.assertEqual(receipts, complete)
            self.assertEqual(releases, {"modal": 123, "openrouter": 2_900_000_456})
            with self.assertRaisesRegex(ValueError, "prior receipts"):
                _validate_reference_probe_settlement(
                    {**document, "prior_receipts_digest": digest_value("tampered")},
                    self.envelope.plan_digest)
            (self.root / "budget.sqlite3").write_bytes(b"unexpected")
            with self.assertRaisesRegex(ValueError, "pre-model reference abort"):
                _validate_reference_probe_settlement(document, self.envelope.plan_digest)

    def test_reference_probe_marker_blocks_older_authority(self):
        chain = []
        for index in range(1, 9):
            value = {"schema_version": f"exploratory-solo-retry/v{index}",
                "run_id": f"run-{index}",
                "provider_limits_usd_nanos": {"modal": 20_000_000_000,
                    "openrouter": 3_100_000_000},
                "total_limit_usd_nanos": 23_100_000_000}
            if chain:
                value["previous_amendment"] = {"file": str(chain[-1][0]),
                    "digest": digest_file(chain[-1][0])}
            path = self.root / f"amendment-{index}.json"
            retain_document(path, value)
            chain.append((path, value))
        names = ("approval.json", "settlement-approval.json",
            "final-settlement-approval.json", "feedback-settlement-approval.json",
            "collector-settlement-approval.json", "series-settlement-approval.json",
            "connected-settlement-approval.json")
        for name, (_, value) in zip(names, chain):
            retain_document(self.envelope.root / "retry" / name, value)
        with patch("agent_collab_evals.pilot_retry.validate_retry",
                return_value=([], {"modal": 0, "openrouter": 0})):
            latest = PilotSpendEnvelope(self.envelope.root, self.plan, retry=chain[-1][1])
            self.assertTrue((latest.root / "retry/reference-probe-approval.json").exists())
            with self.assertRaisesRegex(PermissionError, "reference-probe"):
                PilotSpendEnvelope(latest.root, self.plan, retry=chain[-2][1])


if __name__ == "__main__":
    unittest.main()
