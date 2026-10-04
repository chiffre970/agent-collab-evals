"""Full evaluation-only recovery with real SQLite ledgers and no paid calls."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_collab_evals.adapters.sqlite_compute_routes import ComputeRouteAdapter
from agent_collab_evals.adapters.synthetic_pilot_compute import SyntheticPilotEvidence, SyntheticPilotTransport
from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.canonical import digest_bytes, digest_file, digest_value, parse_json
from agent_collab_evals.compute_backend import ComputeEvidencePointer, ComputeExecutionStatus
from agent_collab_evals.pilot_evidence import retain_document
from agent_collab_evals.solo_evaluation_continuation import EvaluationContinuation
from agent_collab_evals.solo_evaluation_recovery import plan_evaluation_recovery
from agent_collab_evals.solo_live_configuration import prepare_offline_requests
from agent_collab_evals.solo_pilot_stack import build_no_spend_stack
from tests.quality_fixture import REPOSITORY_ROOT


class _RetainedSyntheticEvidence(SyntheticPilotEvidence):
    def pointer(self, request, call_id):
        locator = f"result-{request.request_digest[7:]}.json"
        document = parse_json((self.root / locator).read_text())
        return (ComputeEvidencePointer(locator, digest_value(document)),
            ComputeExecutionStatus(document["status"]), document["used_seconds"], document["failure"])


class EvaluationContinuationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.source = self.root / "source"
        self.campaign = ModelServingCampaign.load(REPOSITORY_ROOT / "campaigns/model_serving_v0/campaign.toml")
        self.stack = build_no_spend_stack(self.source / "evaluation", self.campaign, "offline-check", REPOSITORY_ROOT)
        self.quality = self.stack.hidden._evaluators["quality"]._backend
        self.performance = self.stack.hidden._evaluators["performance"]
        self.policy = self.stack.hidden._evaluators["quality"]._policy
        self.phase_profiles = {"correctness": self.stack.hidden._evaluators["correctness"]._profile,
            **{f"performance-{r}": self.performance._evaluators[r]._profile for r in range(1, 4)}}
        self.candidate = self.campaign.reference_candidate_path.read_bytes()
        class Configuration:
            campaign = self.campaign
            document = {"phase_seconds": {"public": 60}}
        groups = prepare_offline_requests(self.stack, Configuration())
        for key, requests in groups:
            from dataclasses import replace
            requests = tuple(replace(request, maximum_seconds=1800) for request in requests)
            self.stack.inventory.register(key, requests)
            for request in requests:
                self.stack.inventory.authorize(request, approval_reference="synthetic-only-source")
                completed = (key in {"public", "correctness"} or
                    (key == "quality" and request.execution_key.endswith(
                        (":quality:1:reference", ":quality:1:candidate", ":quality:2:candidate"))))
                if completed:
                    self.stack.inventory.backend(key).submit(request, self.candidate)
                    self.stack.inventory.backend(key).collect(request, timeout_seconds=0)
                elif key == "quality" and request.execution_key.endswith(":quality:2:reference"):
                    backend = self.stack.inventory.backend(key)
                    receipt = backend.submit(request, self.candidate)
                    route = backend._backend(request)._transport.evidence.root
                    transport = backend._backend(request)._transport
                    rejected = {"schema_version": "compute-execution-evidence/v0alpha1",
                        "request_digest": request.request_digest, "candidate_digest": request.candidate_digest,
                        "candidate_manifest_digest": request.candidate_manifest_digest,
                        "evaluator_profile_digest": request.evaluator_profile_digest,
                        "transport_profile_digest": transport.profile_digest,
                        "evidence_profile_digest": transport.evidence.profile_digest,
                        "external_call_id": receipt.external_call_id, "status": "failed", "used_seconds": 7,
                        "failure": "gpu.driver_version differs; gpu_after.driver_version differs", "result": {}}
                    retain_document(route / f"result-{request.request_digest[7:]}.json", rejected)
        retain_document(self.source / "compute-inventory-seal.json", {"inventory_digest": self.stack.inventory.seal()})
        config_digest = retain_document(self.source / "run-config.json", {"synthetic": True})
        self.audit_digest = retain_document(self.source / "audit.json", {"run_id": "offline-check",
            "status": "aborted", "scoreable": False, "run_config_digest": config_digest,
            "budget_reconciliation": {"valid": True}})
        self.recovery = self.root / "recovery/plan.json"
        plan_evaluation_recovery(self.source, self.audit_digest,
            lambda key, route, manifest: _RetainedSyntheticEvidence(route / "evidence"), self.recovery.parent)
        self.plan_digest = digest_file(self.recovery)
        self.calls = []
        original = self.stack.inventory._adapters
        self.adapters = {}
        for key, adapter in original.items():
            def factory(root, spend, key=key, adapter=adapter):
                transport = adapter.transport(root, spend)
                dispatch = transport.dispatch
                def counted(request, candidate):
                    self.calls.append(request.request_digest)
                    return dispatch(request, candidate)
                transport.dispatch = counted
                return transport
            self.adapters[key] = ComputeRouteAdapter(adapter.transport_profile_digest,
                adapter.evidence_profile_digest, factory, adapter.evidence)

    def prepare(self, **changes):
        arguments = dict(campaign=self.campaign, adapters=self.adapters,
            quality_profile=self.quality._profile, phase_profiles=self.phase_profiles,
            policy=self.policy, scoring=self.performance._scoring,
            candidates={digest_bytes(self.candidate): self.candidate},
            build_binding={"synthetic": True}, execution_mode="no_spend")
        arguments.update(changes)
        return EvaluationContinuation(self.root / "continuation", self.recovery, self.plan_digest,
            "eval-only", **arguments)

    @staticmethod
    def admit(continuation, request):
        continuation.inventory.authorize(request, approval_reference="synthetic-only-continuation")

    def test_complete_series_runs_six_jobs_only_and_resume_never_redispatches(self):
        before = {path: digest_file(path) for path in self.source.rglob("*")
            if path.is_file() and not path.name.endswith("-shm")}
        with (patch("subprocess.run", side_effect=AssertionError("no external command")),
              patch("http.client.HTTPSConnection", side_effect=AssertionError("no network"))):
            continuation = self.prepare()
            self.assertEqual(len(continuation.requests), 6)
            outcome = continuation.run(expected_digest=continuation.digest, admit=self.admit, collection_seconds=0)
            self.assertEqual(len(self.calls), 6)
            self.assertEqual(len(set(self.calls)), 6)
            self.assertTrue(outcome["eligible"])
            self.assertFalse(outcome["scoreable"])
            self.assertEqual(outcome["agent_reruns"], 0)
            self.assertEqual(len(outcome["provenance"]), 12)
            self.assertEqual(sum(p["used_seconds"] for p in outcome["provenance"]), 84)
            self.assertEqual(outcome["source_status"], "aborted")
            restored = self.prepare()
            self.assertEqual(restored.run(expected_digest=continuation.digest, admit=self.admit, collection_seconds=0), outcome)
            self.assertEqual(len(self.calls), 6)
        self.assertEqual(before, {path: digest_file(path) for path in before})

    def test_no_authority_or_wrong_manifest_cannot_dispatch(self):
        continuation = self.prepare()
        with self.assertRaisesRegex(RuntimeError, "explicit authorization"):
            continuation.run(expected_digest=continuation.digest, admit=lambda *args: None, collection_seconds=0)
        with self.assertRaisesRegex(RuntimeError, "approved authority"):
            continuation.run(expected_digest=digest_value("wrong"), admit=self.admit, collection_seconds=0)
        self.assertEqual(self.calls, [])

    def test_reused_result_tampering_blocks_before_any_new_compute(self):
        continuation = self.prepare()
        ref = continuation.entries["quality-1-reference"]["retained_evidence"]
        (self.recovery.parent / ref["file"]).write_text("{}")
        with self.assertRaisesRegex(RuntimeError, "evidence digest"):
            continuation.run(expected_digest=continuation.digest, admit=self.admit, collection_seconds=0)
        self.assertEqual(self.calls, [])

    def test_changed_hidden_workload_does_not_relabel_source_evidence(self):
        from dataclasses import replace
        with self.assertRaises((RuntimeError, ValueError)):
            self.prepare(quality_profile=replace(self.quality._profile, quality_workload_digest=digest_value("different")))
        self.assertFalse((self.root / "continuation/manifest.json").exists())

    def test_completed_receipt_tampering_blocks_close(self):
        continuation = self.prepare()
        continuation.run(expected_digest=continuation.digest, admit=self.admit, collection_seconds=0)
        path = next((continuation.root / "compute/routes").glob("*/evidence/result-*.json"))
        path.write_text("{}")
        with self.assertRaises(RuntimeError):
            continuation.close(expected_digest=continuation.digest)

    def test_unconsumed_authority_cannot_produce_a_closed_outcome(self):
        import sqlite3
        from contextlib import closing
        continuation = self.prepare()
        continuation.run(expected_digest=continuation.digest, admit=self.admit, collection_seconds=0)
        path = next((continuation.root / "compute/routes").glob("*/spend.sqlite3"))
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("UPDATE compute_spend_authorizations SET status='issued'")
            connection.commit()
        with self.assertRaisesRegex(RuntimeError, "explicit authorization"):
            continuation.close(expected_digest=continuation.digest)

    def test_operator_gate_rejects_wrong_scope_digest_and_expiry(self):
        from datetime import UTC, datetime, timedelta
        from agent_collab_evals.solo_evaluation_authorization import EvaluationContinuationAuthorization
        continuation = self.prepare()
        document = {"schema_version": "evaluation-continuation-authorization/v1",
            "scope": "one_exploratory_evaluation_continuation", "run_id": "eval-only",
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "manifest_digest": continuation.digest, "git_commit": "0" * 40,
            "retry_amendment": {"file": str(self.root / "amendment.json"), "digest": digest_value("amendment")},
            "spend_journal": str(self.root / "journal"), "state_root": str(continuation.root)}
        path = self.root / "operator-approval.json"
        pin = retain_document(path, document)
        loaded = EvaluationContinuationAuthorization.load(path, pin)
        with self.assertRaisesRegex(ValueError, "digest differs"):
            EvaluationContinuationAuthorization.load(path, digest_value("wrong"))
        with self.assertRaisesRegex(PermissionError, "run differs"):
            loaded.envelope(continuation, REPOSITORY_ROOT)  # No-spend cannot become live.
        for changes in ({"scope": "one_exploratory_solo_attempt"},
            {"expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}):
            other = self.root / (digest_value(changes)[7:] + ".json")
            pin = retain_document(other, {**document, **changes})
            with self.assertRaises((ValueError, PermissionError)):
                EvaluationContinuationAuthorization.load(other, pin)

    def test_ineligible_performance_is_a_complete_outcome_not_an_abort(self):
        original = SyntheticPilotTransport._result
        def ineligible(transport, request):
            result = original(transport, request)
            if transport.recipe["phase"] == "performance":
                record = result["candidate_evaluation"]
                record["eligible"] = False
                record.pop("result_evidence_digest")
                record["result_evidence_digest"] = digest_value(record)
            return result
        with patch.object(SyntheticPilotTransport, "_result", ineligible):
            continuation = self.prepare()
            outcome = continuation.run(expected_digest=continuation.digest, admit=self.admit, collection_seconds=0)
        self.assertEqual(outcome["status"], "complete")
        self.assertFalse(outcome["eligible"])
        self.assertFalse(outcome["performance"]["eligible"])
        self.assertEqual(len(self.calls), 6)

    def test_restart_after_dispatch_collects_the_original_call_once(self):
        continuation = self.prepare()
        slot = next(iter(continuation.requests))
        request = continuation.requests[slot]
        self.admit(continuation, request)
        continuation.inventory.backend("quality").submit(request, self.candidate)
        self.assertEqual(len(self.calls), 1)
        restored = self.prepare()
        outcome = restored.run(expected_digest=continuation.digest, admit=self.admit, collection_seconds=0)
        self.assertEqual(outcome["status"], "complete")
        self.assertEqual(len(self.calls), 6)

    def test_durable_dollar_guard_and_sqlite_authority_compose_without_spend(self):
        from datetime import UTC, datetime, timedelta
        from agent_collab_evals.modal_pilot_cost import modal_pilot_cost
        from agent_collab_evals.pilot_spend import PilotSpendEnvelope
        from agent_collab_evals.solo_evaluation_authorization import EvaluationContinuationAuthorization, EvaluationContinuationSpendGuard
        continuation = self.prepare()
        journal = self.root / "journal"
        plan = parse_json((REPOSITORY_ROOT / "config/pilots/solo-spend-envelope-v1.json").read_text())
        chain = []
        for index in range(1, 11):
            document = {"schema_version": f"exploratory-solo-retry/v{index}", "run_id": f"prior-{index}",
                "provider_limits_usd_nanos": {"modal": 20_000_000_000, "openrouter": 3_100_000_000},
                "total_limit_usd_nanos": 23_100_000_000}
            if chain:
                document["previous_amendment"] = chain[-1][0]
            path = self.root / f"amendment-{index}.json"
            chain.append(({"file": str(path), "digest": retain_document(path, document)}, document))
        names = ("approval.json", "settlement-approval.json", "final-settlement-approval.json",
            "feedback-settlement-approval.json", "collector-settlement-approval.json", "series-settlement-approval.json",
            "connected-settlement-approval.json", "reference-probe-approval.json", "staging-settlement-approval.json",
            "environment-settlement-approval.json")
        for name, (_, document) in zip(names, chain):
            retain_document(journal / "retry" / name, document)
        amendment = {"schema_version": "exploratory-solo-retry/v11", "previous_amendment": chain[-1][0],
            "run_id": continuation.document["run_id"], "continuation_manifest": {
                "file": str(continuation.root / "manifest.json"), "digest": continuation.digest},
            "provider_limits_usd_nanos": {"modal": 20_000_000_000, "openrouter": 3_100_000_000},
            "total_limit_usd_nanos": 23_100_000_000}
        # Only the unrelated historical settlement is stubbed here. New dollar
        # receipts, cap checks, request authority, dispatch and closure are real.
        with patch("agent_collab_evals.pilot_retry.validate_retry", return_value=([], {"modal": 0, "openrouter": 0})):
            envelope = PilotSpendEnvelope(journal, plan, retry=amendment)
            authority = EvaluationContinuationAuthorization({"manifest_digest": continuation.digest,
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}, digest_value("synthetic-authority"))
            estimate = modal_pilot_cost(REPOSITORY_ROOT / "campaigns/model_serving_v0/reference/modal_vllm.py",
                REPOSITORY_ROOT / "config/compute/modal-pilot-cost-v1.json")
            guard = EvaluationContinuationSpendGuard(continuation, envelope, estimate, authority=authority)
            guard.begin()
            self.assertEqual(len(envelope.snapshot()["receipts"]), 7)
            self.assertEqual(self.calls, [])
            outcome = continuation.run(expected_digest=continuation.digest, admit=guard.admit, collection_seconds=0)
            self.assertEqual(outcome["status"], "complete")
            self.assertEqual(envelope.snapshot()["reserved_usd_nanos"], {"modal": 5_596_480_000, "openrouter": 0})
            self.assertEqual(len(envelope.snapshot()["receipts"]), 7)
            for provider, key, amount in (("openrouter", "pilot:eval-only:model", 1),
                ("modal", "pilot:eval-only:compute:unplanned", 766_080_000)):
                with self.assertRaisesRegex(PermissionError, "cannot fund"):
                    envelope.reserve(operation_key=key, provider=provider, purpose="pilot",
                        request_digest=digest_value(key), maximum_usd_nanos=amount)
            restored_envelope = PilotSpendEnvelope(journal, plan, retry=amendment)
            self.assertEqual(restored_envelope.snapshot(), envelope.snapshot())
            with self.assertRaisesRegex(PermissionError, "evaluation-only settlement"):
                PilotSpendEnvelope(journal, plan, retry=chain[-1][1])
        self.assertEqual(len(self.calls), 6)

    def test_evaluation_settlement_checks_raw_billing_request_set_and_prior_admissions(self):
        """Stub history verification only; exercise the new settlement validator."""
        from copy import deepcopy
        from agent_collab_evals.solo_evaluation_spend import validate_evaluation_settlement
        continuation = self.prepare()
        folder = self.root / "settlement-fixture"
        def ref(name, document):
            path = folder / name
            return {"file": str(path), "digest": retain_document(path, document)}
        previous = {"schema_version": "exploratory-solo-retry/v10", "run_id": "offline-check"}
        previous_ref = ref("previous.json", previous)
        prefix = "pilot:retry-" + digest_value(previous)[7:] + ":"
        plan_digest = digest_value("spend-plan")
        specs = [(prefix + "modal:base", "modal", "overhead", 1_000_000_000),
            (prefix + "openrouter:base", "openrouter", "pilot", 2_900_000_000)]
        specs.extend((f"pilot:offline-check:compute:{e['request_digest'][7:]}", "modal", "pilot", 766_080_000)
            for e in continuation.recovery["entries"])
        receipts = [{"operation_key": key, "provider": provider, "purpose": purpose,
            "maximum_usd_nanos": amount, "request_digest": digest_value(key), "plan_digest": plan_digest}
            for key, provider, purpose, amount in specs]
        audit = parse_json((self.source / "audit.json").read_text())
        audit["spend_admission"] = {"receipts": receipts, "retry_amendment_digest": digest_value(previous)}
        # Mutating a disposable synthetic fixture does not revise real evidence.
        from agent_collab_evals.canonical import canonical_json_bytes
        (self.source / "audit.json").write_bytes(canonical_json_bytes(audit))
        audit_ref = {"file": str(self.source / "audit.json"), "digest": digest_file(self.source / "audit.json")}
        recovery = {**continuation.recovery, "source_audit_digest": audit_ref["digest"]}
        recovery_ref = ref("recovery.json", recovery)
        manifest = {**parse_json((continuation.root / "manifest.json").read_text()),
            "execution_mode": "live", "source_recovery_plan": recovery_ref, "source_audit_digest": audit_ref["digest"]}
        manifest_ref = ref("manifest.json", manifest)
        calls = [{"function_call_id": entry["source_call_id"], "status": "terminal_result",
            "provider_metadata": {"function_call_id": entry["source_call_id"], "app_id": f"ap-{index}"}}
            for index, entry in enumerate(e for e in recovery["entries"] if e["source_call_id"])]
        terminal = {"schema_version": "devbound-terminal-observation/v1", "calls": calls,
            "actor_containers_active": False, "new_model_calls": 0, "new_scored_dispatches": 0, "apps": []}
        rows = [{"object_id": call["provider_metadata"]["app_id"], "environment": "dev",
            "resource": resource, "interval_start": "2026-10-03T13:00:00", "cost": "0.001"}
            for call in calls for resource in ("CPU", "Memory", "L4")]
        rows[0]["cost"] = "1.18508955"
        billing = {"schema_version": "devbound-billing-snapshot/v1", "rows": rows,
            "attributed_app_ids": [call["provider_metadata"]["app_id"] for call in calls],
            "command": ["billing", "report", "--start", "2026-10-03", "--end", "2026-10-05",
                "-r", "h", "--json", "--show-resources"]}
        document = {"schema_version": "exploratory-solo-retry/v11", "previous_amendment": previous_ref,
            "prior_plan_digest": plan_digest, "prior_receipts_digest": digest_value(receipts),
            "prior_audit": audit_ref, "prior_run_config": {"file": str(self.source / "run-config.json"),
                "digest": audit["run_config_digest"]},
            "prior_budget_database": {"file": str(self.source / "budget.sqlite3"), "digest": digest_value("budget")},
            "prior_budget_plan": {"file": str(self.source / "budget-plan.json"), "digest": digest_value("budget-plan")},
            "recovery_plan": recovery_ref, "continuation_manifest": manifest_ref,
            "terminal_observation": ref("terminal.json", terminal), "billing_report": ref("billing.json", billing),
            "run_id": "eval-only", "provider_limits_usd_nanos": {"modal": 20_000_000_000, "openrouter": 3_100_000_000},
            "total_limit_usd_nanos": 23_100_000_000, "billing_buffer_usd_nanos": 100_000_000}
        with (patch("agent_collab_evals.solo_evaluation_spend.SOURCE_AUDIT_DIGEST", audit_ref["digest"]),
              patch("agent_collab_evals.pilot_retry.validate_retry", return_value=([], {"modal": 0, "openrouter": 0})),
              patch("agent_collab_evals.pilot_retry._reconcile_prior_model", return_value=4_291_740) as model):
            retained, released = validate_evaluation_settlement(document, plan_digest)
            self.assertEqual(retained, receipts)
            self.assertEqual(released, {"modal": 8_887_870_450, "openrouter": 2_895_708_260})
            model.assert_called_once()
            for field, value in (("total_limit_usd_nanos", 24_000_000_000), ("billing_buffer_usd_nanos", 0),
                ("prior_receipts_digest", digest_value("tampered"))):
                with self.subTest(field=field), self.assertRaises(ValueError):
                    validate_evaluation_settlement({**document, field: value}, plan_digest)
            for field, original, mutate in (
                ("terminal_observation", terminal, lambda d: d["calls"].pop()),
                ("billing_report", billing, lambda d: d["rows"].pop()),
                ("billing_report", billing, lambda d: d["rows"][0].update(cost="0.01")),
                ("continuation_manifest", manifest, lambda d: d["jobs"][-1].update(replacement_request=None)),
                ("continuation_manifest", manifest, lambda d: d["jobs"][-1]["replacement_request"].update(candidate_digest=digest_value("different"))),
            ):
                altered = deepcopy(original)
                mutate(altered)
                with self.subTest(field=field), self.assertRaises(ValueError):
                    validate_evaluation_settlement({**document, field: ref(digest_value(altered)[7:] + ".json", altered)}, plan_digest)
