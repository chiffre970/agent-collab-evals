"""Real matched-score parsing and durable SQLite composition without paid calls."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_collab_evals.adapters.local_measurements import LocalMeasurementBundleStore
from agent_collab_evals.adapters.modal_paired_performance import ModalPairedPerformanceEvidence, ModalPairedPerformanceTransport
from agent_collab_evals.adapters.sqlite_compute_spend import SqliteComputeSpendAuthorizationService
from agent_collab_evals.adapters.sqlite_execution_backend import SqliteComputeBackend
from agent_collab_evals.campaigns.serving_paired_performance import MODEL_SOURCE, evaluate_pair, invocations, summarize_pairs
from agent_collab_evals.canonical import canonical_json_bytes, digest_bytes, digest_file, digest_value, parse_json
from agent_collab_evals.compute_backend import ComputeExecutionRequest, ComputeExecutionStatus, ExternalDispatch, FrozenComputeRunManifest
from agent_collab_evals.modal_pilot_cost import modal_paired_cost
from agent_collab_evals.pilot_evidence import retain_document
from agent_collab_evals.solo_performance_followup import capture_retained_inputs, prepare, run, validate_retained_inputs
from tests.quality_fixture import REPOSITORY_ROOT, real_hidden_quality_bundle
from tests.test_serving_measurement import _result_document


def measured_bundle(resolver, job, driver="610.57.04", candidate_speedup=1.0):
    spec = job["spec"]
    reference = parse_json(Path(resolver.binding["reference"]["file"]).read_text())
    candidate = parse_json(Path(resolver.binding["candidate"]["file"]).read_text())
    measurement = resolver.campaign.measurement_profile()
    environment = {"base_image_ref": "nvidia/cuda:12.9.0-devel-ubuntu22.04",
        "base_image_digest": measurement.base_image_digest, "package_set_digest": measurement.resolved_package_digest,
        "package_count": 200}
    gpu = {**spec["expected_gpu"], "driver_version": driver, "pci_bus_id": "00000000:01:00.0"}
    roles, raw = {}, {}
    for role, document in (("reference", reference), ("candidate", candidate)):
        roles[role] = {**{k: spec["benchmark"][k] for k in ("campaign_manifest_digest", "measurement_profile_digest",
            "scoring_profile_digest", "performance_profile_digest", "repetition", "attempt")},
            "candidate_id": document["candidate_id"], "model_id": resolver.campaign.target_model_id,
            "model_revision": resolver.campaign.target_model_revision, "served_model_name": "target-model",
            "vllm_version": document["server"]["engine_version"], "ok": True, "error": None,
            "environment": deepcopy(environment), "gpu_before": dict(gpu), "gpu_after": dict(gpu),
            "canary_before": {"content": "READY", "returned_model": "target-model"},
            "canary_after": {"content": "READY", "returned_model": "target-model"}}
        for i in invocations(resolver.campaign, resolver.plan, resolver.scoring):
            result = _result_document(i, direct_goodput=True)
            result["model_id"] = MODEL_SOURCE
            result["tokenizer_id"] = MODEL_SOURCE
            if role == "candidate":
                result["duration"] /= candidate_speedup
                for k in ("request_goodput", "request_throughput", "output_throughput", "total_token_throughput"):
                    result[k] *= candidate_speedup
            raw[f"{role}-{i.result_file.name}"] = json.dumps(result, sort_keys=True).encode()
    return {"schema_version": "modal-paired-serving-repetition/v1", "pair_digest": spec["pair_digest"],
        "ok": True, "errors": [], "order": spec["order"], "gpu_before": dict(gpu), "gpu_after": dict(gpu),
        "roles": roles, "reference_document_digest": digest_bytes(canonical_json_bytes(reference) + b"\n"),
        "candidate_document_digest": digest_bytes(canonical_json_bytes(candidate) + b"\n"),
        "timing": {"function_body_ms": 120_001}, "environment": environment}, raw


class _RetainedTransport(ModalPairedPerformanceTransport):
    dispatches = []
    fail = False

    def dispatch(self, request, candidate):
        self.spend.consume(request, self.profile_digest)
        call = "fc-paired-" + request.request_digest[7:23]
        job = self.resolver._job(request)
        retain_document(self.root / "dispatch" / (request.request_digest[7:] + ".json"),
            {"schema_version": "modal-paired-dispatch/v1", "request_digest": request.request_digest,
            "function_call_id": call, "binding_digest": digest_value(self.binding),
            "evidence_root": job["spec"]["benchmark"]["evidence_root"], "git_commit": self.binding["git_commit"]})
        remote, raw = measured_bundle(self.resolver, job)
        if self.fail:
            remote.update(ok=False, errors=["unqualified GPU driver"])
        durable = {"volume_name": "agent-collab-evals-evaluator-evidence-v2",
            "root": job["spec"]["benchmark"]["evidence_root"],
            "remote_receipt_digest": digest_bytes(canonical_json_bytes(remote) + b"\n"),
            "raw_digests": {k: digest_bytes(v) for k, v in raw.items()}}
        LocalMeasurementBundleStore(self.root / "measurements").save(request.request_digest[7:], 1,
            {"request_digest": request.request_digest, "function_call_id": call,
             "remote_receipt": remote, "durable_evidence": durable}, raw)
        self.dispatches.append(call)
        return ExternalDispatch(call, digest_bytes(self.resolver.resolve_dispatch(request, call)))


class PairedPerformanceTests(unittest.TestCase):
    def setUp(self):
        from tests.test_solo_evaluation_continuation import EvaluationContinuationTests
        self.fixture = EvaluationContinuationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        previous = self.fixture.prepare()
        for slot in ("quality-2-reference", "quality-3-reference", "quality-3-candidate"):
            request = previous.requests[slot]
            self.fixture.admit(previous, request)
            backend = previous.inventory.backend("quality")
            backend.submit(request, self.fixture.candidate)
            backend.collect(request, timeout_seconds=0)
            _, evidence = backend.resolve(request)
            retain_document(previous.root / "completed" / (request.request_digest[7:] + ".json"), evidence)
        self.retained = self.root / "retained-inputs.json"
        capture_retained_inputs(previous, self.retained)
        self.campaign, self.hidden, _ = real_hidden_quality_bundle(self.root / "hidden")
        from agent_collab_evals.adapters.modal_vllm_compute import ModalVllmComputeProfile
        public = ModalVllmComputeProfile.load(REPOSITORY_ROOT / "config/compute/modal-vllm-development.json", repository_root=REPOSITORY_ROOT)
        configuration = SimpleNamespace(repository=REPOSITORY_ROOT, campaign=self.campaign,
            public_compute=public, hidden_bundle=lambda: self.hidden)
        self.state = self.root / "performance"
        self.document = prepare(configuration, self.retained, self.state, "paired-test")
        self.binding = self.document["binding"]
        self.resolver = ModalPairedPerformanceEvidence(self.state, self.binding)
        _RetainedTransport.dispatches = []
        _RetainedTransport.fail = False

    def test_production_preparation_reuses_nine_results_and_grants_no_authority(self):
        checked = validate_retained_inputs(self.retained, self.campaign)
        self.assertEqual(len(checked["evidence_digests"]), 9)
        self.assertTrue(checked["quality"]["eligible"])
        self.assertEqual(len(self.binding["jobs"]), 3)
        self.assertEqual([j["spec"]["order"] for j in self.binding["jobs"]],
            [["reference", "candidate"], ["candidate", "reference"], ["reference", "candidate"]])
        self.assertFalse((self.state / "spend.sqlite3").exists())
        self.assertFalse((self.state / "executions.sqlite3").exists())
        self.assertEqual(self.document["estimate"]["per_execution_allowance_usd_nanos"], 1_137_984_000)

    def test_both_qualified_drivers_use_the_same_gpu_reference_not_old_baseline(self):
        job = self.binding["jobs"][0]
        reference = parse_json((self.state / "reference.json").read_text())
        candidate = parse_json((self.state / "candidate.json").read_text())
        for driver in ("580.95.05", "610.57.04"):
            receipt, raw = measured_bundle(self.resolver, job, driver, candidate_speedup=1.25)
            result = evaluate_pair(self.campaign, self.resolver.plan, self.resolver.scoring, job["spec"],
                reference, candidate, receipt, raw)
            self.assertTrue(result["eligible"])
            self.assertEqual(result["scores"]["candidate"]["scalar_ppm"], 1_250_000)
            self.assertEqual(result["scores"]["reference"]["scalar_ppm"], 1_000_000)

    def test_mismatched_identity_missing_points_and_unknown_driver_fail_closed(self):
        job = self.binding["jobs"][0]
        reference = parse_json((self.state / "reference.json").read_text())
        candidate = parse_json((self.state / "candidate.json").read_text())
        original, raw = measured_bundle(self.resolver, job)
        for mutate in (lambda d: d["roles"]["candidate"]["gpu_before"].update(driver_version="580.95.05"),
            lambda d: d["gpu_before"].update(driver_version="unknown"),
            lambda d: d["roles"]["reference"].update(candidate_id="wrong"),
            lambda d: d["roles"]["candidate"]["canary_after"].update(content="wrong"),
            lambda d: d["environment"].update(package_set_digest=digest_value("wrong"))):
            changed = deepcopy(original)
            mutate(changed)
            with self.assertRaises(RuntimeError):
                evaluate_pair(self.campaign, self.resolver.plan, self.resolver.scoring, job["spec"], reference, candidate, changed, raw)
        with self.assertRaisesRegex(RuntimeError, "point set"):
            evaluate_pair(self.campaign, self.resolver.plan, self.resolver.scoring, job["spec"], reference, candidate, original, {})

    def test_durable_backend_and_real_single_use_spend_service_compose(self):
        authority = FrozenComputeRunManifest.load(self.state / "compute-manifest.json", expected_digest=self.document["compute_manifest_digest"])
        spend = SqliteComputeSpendAuthorizationService(self.state / "spend.sqlite3", authority)
        transport = _RetainedTransport(self.state, self.binding, self.state / "manifest.json", spend)
        backend = SqliteComputeBackend(self.state / "executions.sqlite3", transport, self.resolver, authority=authority)
        values = []
        for job in self.binding["jobs"]:
            request = ComputeExecutionRequest.from_document(job["request"])
            spend.issue(request, transport.profile_digest, "synthetic-only")
            backend.submit(request, (self.state / "candidate.json").read_bytes())
            receipt = backend.collect(request, timeout_seconds=0)
            self.assertEqual(receipt.status, ComputeExecutionStatus.COMPLETE)
            self.assertEqual(receipt.used_seconds, 121)
            values.append(backend.resolve(request)[1]["result"]["paired_performance"])
            backend.submit(request, (self.state / "candidate.json").read_bytes())
        self.assertEqual(len(backend.reconcile("paired-test")), 3)
        self.assertEqual(len(_RetainedTransport.dispatches), 3)
        self.assertTrue(summarize_pairs(values)["eligible"])
        values[0]["eligible"] = False
        self.assertFalse(summarize_pairs(values)["eligible"])

    def test_rejected_pair_is_a_reconcilable_failure(self):
        authority = FrozenComputeRunManifest.load(self.state / "compute-manifest.json", expected_digest=self.document["compute_manifest_digest"])
        spend = SqliteComputeSpendAuthorizationService(self.state / "spend.sqlite3", authority)
        transport = _RetainedTransport(self.state, self.binding, self.state / "manifest.json", spend)
        backend = SqliteComputeBackend(self.state / "executions.sqlite3", transport, self.resolver, authority=authority)
        request = authority.requests()[0]
        spend.issue(request, transport.profile_digest, "synthetic-only")
        _RetainedTransport.fail = True
        backend.submit(request, (self.state / "candidate.json").read_bytes())
        result = backend.collect(request, timeout_seconds=0)
        self.assertEqual(result.status, ComputeExecutionStatus.FAILED)
        self.assertEqual(backend.resolve(request)[1]["result"], {})

    def test_tampered_retained_evidence_blocks_followup(self):
        retained = parse_json(self.retained.read_text())
        Path(retained["evidence"][0]["evidence"]["file"]).write_text("{}")
        with self.assertRaisesRegex(RuntimeError, "digest differs"):
            validate_retained_inputs(self.retained, self.campaign)

    def test_full_runner_uses_real_budget_and_authority_and_reconciles_three_pairs(self):
        """Only historical settlement and remote execution are simulated."""
        from agent_collab_evals.pilot_spend import PilotSpendEnvelope
        from agent_collab_evals.pilot_retry import validate_retry as real_validate
        from agent_collab_evals.solo_evaluation_spend import evaluation_admissions
        journal = self.root / "journal"
        plan = parse_json((REPOSITORY_ROOT / "config/pilots/solo-spend-envelope-v1.json").read_text())
        chain = []
        names = ("approval.json", "settlement-approval.json", "final-settlement-approval.json",
            "feedback-settlement-approval.json", "collector-settlement-approval.json", "series-settlement-approval.json",
            "connected-settlement-approval.json", "reference-probe-approval.json", "staging-settlement-approval.json",
            "environment-settlement-approval.json")
        for index, name in enumerate(names, 1):
            doc = {"schema_version": f"exploratory-solo-retry/v{index}", "run_id": f"prior-{index}",
                "provider_limits_usd_nanos": {"modal": 20_000_000_000, "openrouter": 3_100_000_000},
                "total_limit_usd_nanos": 23_100_000_000}
            if chain:
                doc["previous_amendment"] = chain[-1]
            path = self.root / f"history-{index}.json"
            chain.append({"file": str(path), "digest": retain_document(path, doc)})
            retain_document(journal / "retry" / name, doc)
        old_manifest = parse_json(self.retained.read_text())["previous_manifest"]
        previous = {"schema_version": "exploratory-solo-retry/v11", "previous_amendment": chain[-1],
            "run_id": "eval-only", "continuation_manifest": old_manifest,
            "provider_limits_usd_nanos": {"modal": 20_000_000_000, "openrouter": 3_100_000_000},
            "total_limit_usd_nanos": 23_100_000_000}
        previous_path = self.root / "previous-amendment.json"
        previous_ref = {"file": str(previous_path), "digest": retain_document(previous_path, previous)}
        def validate(doc, digest):
            if doc["schema_version"] == "exploratory-solo-retry/v11":
                return [], {"modal": 0, "openrouter": 0}
            return real_validate(doc, digest)
        with patch("agent_collab_evals.pilot_retry.validate_retry", side_effect=validate):
            old = PilotSpendEnvelope(journal, plan, retry=previous)
            for key, (provider, purpose, amount) in evaluation_admissions(previous).items():
                old.reserve(operation_key=key, provider=provider, purpose=purpose,
                    request_digest=digest_value(key), maximum_usd_nanos=amount)
            stop = {"schema_version": "evaluation-continuation-stop/v1", "status": "stopped", "scoreable": False,
                "manifest_digest": old_manifest["digest"], "spend_admission": old.snapshot()}
            stop_path = self.root / "prior-stop.json"
            stop_ref = {"file": str(stop_path), "digest": retain_document(stop_path, stop)}
            manifest_ref = {"file": str(self.state / "manifest.json"), "digest": digest_file(self.state / "manifest.json")}
            amendment = {"schema_version": "exploratory-solo-retry/v12", "previous_amendment": previous_ref,
                "prior_plan_digest": digest_value(plan), "prior_stop": stop_ref,
                "prior_receipts_digest": digest_value(stop["spend_admission"]["receipts"]),
                "followup_manifest": manifest_ref, "run_id": "paired-test",
                "provider_limits_usd_nanos": {"modal": 20_000_000_000, "openrouter": 3_100_000_000},
                "total_limit_usd_nanos": 23_100_000_000}
            amendment_path = self.root / "extension.json"
            amendment_ref = {"file": str(amendment_path), "digest": retain_document(amendment_path, amendment)}
            auth = {"schema_version": "paired-performance-authorization/v1",
                "scope": "one_exploratory_paired_performance_followup", "run_id": "paired-test",
                "manifest_digest": manifest_ref["digest"], "git_commit": self.binding["git_commit"],
                "state_root": str(self.state), "spend_journal": str(journal), "retry_amendment": amendment_ref,
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
            auth_path = self.root / "authorization.json"
            auth_digest = retain_document(auth_path, auth)
            def git(argv, **kwargs):
                return self.binding["git_commit"] + "\n" if argv[:3] == ["git", "rev-parse", "HEAD"] else ""
            with (patch("agent_collab_evals.solo_performance_followup.subprocess.check_output", side_effect=git),
                  patch("agent_collab_evals.solo_performance_followup.ModalPairedPerformanceTransport", _RetainedTransport)):
                outcome = run(self.state, auth_path, auth_digest)
                self.assertEqual(run(self.state, auth_path, auth_digest), outcome)
            self.assertEqual(outcome["status"], "complete")
            self.assertTrue(outcome["performance"]["eligible"])
            self.assertFalse(outcome["eligible"])
            self.assertFalse(outcome["quality_verified_on_performance_drivers"])
            self.assertFalse(outcome["scoreable"])
            self.assertEqual(len(_RetainedTransport.dispatches), 3)
            envelope = PilotSpendEnvelope(journal, plan, retry=amendment)
            self.assertEqual(len(envelope.snapshot()["receipts"]), 11)
            self.assertEqual(envelope.snapshot()["reserved_usd_nanos"], {"modal": 10_010_432_000, "openrouter": 0})
            self.assertEqual(envelope.snapshot()["released_allowances_usd_nanos"], {"modal": 0, "openrouter": 0})
            with self.assertRaises(PermissionError):
                envelope.reserve(operation_key="pilot:paired-test:model", provider="openrouter", purpose="pilot",
                    request_digest=digest_value("model"), maximum_usd_nanos=1)
            with self.assertRaisesRegex(PermissionError, "performance-only extension"):
                PilotSpendEnvelope(journal, plan, retry=previous)
            expired_path = self.root / "expired.json"
            expired_digest = retain_document(expired_path, {**auth, "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()})
            with self.assertRaisesRegex(PermissionError, "expired"):
                run(self.state, expired_path, expired_digest)
            invalid_path = self.root / "invalid-authority.json"
            invalid_digest = retain_document(invalid_path, {**auth, "schema_version": "unrecognized/v1"})
            with self.assertRaisesRegex(PermissionError, "fields differ"):
                run(self.state, invalid_path, invalid_digest)
