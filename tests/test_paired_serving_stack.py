"""Real paired factories, raw resolver, SQLite authority, and peer lifecycle."""

from copy import deepcopy
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_collab_evals.adapters.modal_paired_serving import ModalPairedServingEvidence, ModalPairedServingTransport
from agent_collab_evals.adapters.sqlite_compute_spend import SqliteComputeSpendAuthorizationService
from agent_collab_evals.adapters.sqlite_execution_backend import SqliteComputeBackend
from agent_collab_evals.artifacts import ArtifactRef
from agent_collab_evals.candidate_services import create_candidate_services
from agent_collab_evals.campaigns.serving_correctness import load_correctness_workload
from agent_collab_evals.campaigns.serving_paired_performance import MODEL_SOURCE, invocations
from agent_collab_evals.campaigns.serving_paired_profile import PairedServingProfile
from agent_collab_evals.campaigns.serving_paired_result import score_paired_execution
from agent_collab_evals.campaigns.serving_quality import load_quality_workload
from agent_collab_evals.canonical import canonical_json_bytes, digest_bytes, digest_file, digest_value, parse_json
from agent_collab_evals.compute_backend import ComputeExecutionStatus, ExternalDispatch, FrozenComputeRunManifest
from agent_collab_evals.domain import AgentIdentity, SessionHandle
from agent_collab_evals.evaluation import ActorComputeAllocation, ComputePlan, EvaluationReservation, EvaluationReservationStatus, EvaluationScope, SubmissionPolicy
from agent_collab_evals.peer_live_configuration import build_paired_peer_stack, paired_peer_cost, prepare_peer_offline_requests, prepare_paired_stock_control
from agent_collab_evals.pilot_evidence import retain_document
from agent_collab_evals.solo_live_configuration import LivePilotConfiguration
from agent_collab_evals.solo_pilot_runner import CandidatePilotRunner
from tests.quality_fixture import REPOSITORY_ROOT, real_hidden_quality_bundle
from tests.test_serving_measurement import _result_document


def paired_fixture(profile, request, candidate, spec, *, driver="610.57.04", wrong_correctness=False,
    wrong_reference=False, speedup=1.25):
    """Simulated GPU output, using real workload/benchmark/response schemas."""
    reference = parse_json(profile.campaign.reference_candidate_path.read_text())
    document = parse_json(candidate.decode())
    gpu = {**spec["expected_gpu"], "driver_version": driver, "pci_bus_id": "00000000:01:00.0"}
    measurement = profile.campaign.measurement_profile()
    environment = {"base_image_ref": "nvidia/cuda:12.9.0-devel-ubuntu22.04",
        "base_image_digest": measurement.base_image_digest, "package_set_digest": measurement.resolved_package_digest}
    roles, raw = {}, {}
    phase = profile.slot(request).rsplit("-", 1)[0]
    for role, current in (("reference", reference), ("candidate", document)):
        common = {"candidate_id": current["candidate_id"], "model_id": profile.campaign.target_model_id,
            "model_revision": profile.campaign.target_model_revision, "served_model_name": "target-model",
            "vllm_version": "0.21.0", "ok": True, "error": None, "environment": environment,
            "gpu_before": dict(gpu), "gpu_after": dict(gpu),
            "canary_before": {"content": "READY", "returned_model": "target-model"},
            "canary_after": {"content": "READY", "returned_model": "target-model"}}
        if phase in {"public", "performance"}:
            roles[role] = {**common, **{k: spec["benchmark"][k] for k in (
                "campaign_manifest_digest", "measurement_profile_digest", "scoring_profile_digest",
                "performance_profile_digest", "repetition", "attempt")}}
            plan, scoring, _ = profile.performance_inputs(request.scope)
            for invocation in invocations(profile.campaign, plan, scoring):
                result = _result_document(invocation, direct_goodput=True)
                result.update(model_id=MODEL_SOURCE, tokenizer_id=MODEL_SOURCE)
                if role == "candidate":
                    result["duration"] /= speedup
                    for key in ("request_goodput", "request_throughput", "output_throughput", "total_token_throughput"):
                        result[key] *= speedup
                raw[f"{role}-{invocation.result_file.name}"] = json.dumps(result, sort_keys=True).encode()
        else:
            roles[role] = {**common, **{k: spec["benchmark"][k] for k in (
                "campaign_manifest_digest", "quality_profile_digest", "quality_workload_digest", "repetition", "attempt")},
                "execution": {k: spec["benchmark"][k] for k in ("max_concurrency", "request_timeout_seconds")}}
            if phase == "quality":
                workload = load_quality_workload(Path(profile.pins["quality_workload"]["file"]), profile.campaign.quality_profile())
            else:
                workload = load_correctness_workload(Path(profile.pins["correctness_requests"]["file"]))
            for index, case in enumerate(workload.cases):
                answer = f"<answer>{case.expected}</answer>" if phase == "quality" else case.expected.strip("^$")
                if wrong_correctness and phase == "correctness" and role == "candidate" and index == 0:
                    answer = "wrong"
                if wrong_reference and phase == "correctness" and role == "reference" and index == 0:
                    answer = "wrong"
                raw[f"{role}-{case.case_id}.json"] = canonical_json_bytes({"model": "target-model",
                    "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": answer}}]})
    remote = {"schema_version": "modal-paired-serving-repetition/v1", "pair_digest": spec["pair_digest"],
        "order": spec["order"], "ok": True, "errors": [], "roles": roles,
        "gpu_before": dict(gpu), "gpu_after": dict(gpu), "environment": environment,
        "reference_document_digest": digest_bytes(canonical_json_bytes(reference) + b"\n"),
        "candidate_document_digest": digest_bytes(canonical_json_bytes(document) + b"\n"),
        "timing": {"function_body_ms": 120001}}
    return remote, raw


class _RetainedPairedTransport(ModalPairedServingTransport):
    dispatches = []
    wrong_correctness = False
    wrong_reference = False
    speedup = 1.25

    def dispatch(self, request, candidate):
        self.spend.consume(request, self.profile_digest)
        job = self.retain_job(request, candidate)
        slot = self.profile.slot(request)
        driver = "580.95.05" if slot.endswith("-2") else "610.57.04"
        remote, raw = paired_fixture(self.profile, request, candidate, job["spec"],
            driver=driver, wrong_correctness=_RetainedPairedTransport.wrong_correctness,
            wrong_reference=_RetainedPairedTransport.wrong_reference, speedup=_RetainedPairedTransport.speedup)
        call = "fc-paired-" + request.request_digest[7:39]
        retain_document(self.resolver.dispatch_path(request), {"schema_version": "paired-serving-dispatch/v1",
            "request_digest": request.request_digest, "function_call_id": call, "job_digest": digest_value(job),
            "transport_digest": self.profile_digest, "evidence_root": job["spec"]["benchmark"]["evidence_root"]})
        durable = {"volume_name": "agent-collab-evals-evaluator-evidence-v2",
            "root": job["spec"]["benchmark"]["evidence_root"],
            "remote_receipt_digest": digest_bytes(canonical_json_bytes(remote) + b"\n"),
            "raw_digests": {k: digest_bytes(v) for k, v in raw.items()}}
        self.resolver.store.save(request.request_digest[7:], 1,
            {"request_digest": request.request_digest, "function_call_id": call,
                "remote_receipt": remote, "durable_evidence": durable}, raw)
        _RetainedPairedTransport.dispatches.append(request.request_digest)
        return ExternalDispatch(call, digest_bytes(self.resolver.resolve_dispatch(request, call)))


class PairedServingStackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.campaign, self.bundle, self.policy = real_hidden_quality_bundle(self.root / "fixture")
        self.configuration = SimpleNamespace(repository=REPOSITORY_ROOT, campaign=self.campaign,
            modal_cli=REPOSITORY_ROOT / ".venv/bin/modal", document={"test_configuration": "paired"},
            public_compute=SimpleNamespace(modal_script=self.campaign.root / "reference/modal_vllm.py"))
        _RetainedPairedTransport.dispatches = []
        _RetainedPairedTransport.wrong_correctness = False
        _RetainedPairedTransport.wrong_reference = False
        _RetainedPairedTransport.speedup = 1.25

    def stack(self, run_id="paired-peer"):
        return build_paired_peer_stack(self.root / run_id / "evaluation", self.configuration,
            run_id, self.bundle, self.policy)

    def reservation(self, scope, seconds):
        return EvaluationReservation("evaluation-" + "1" * 32, scope.value + ":test", "paired-peer",
            "paired-peer:actor:0" if scope is EvaluationScope.VISIBLE else None,
            ArtifactRef("artifact-" + "2" * 32), scope, seconds, EvaluationReservationStatus.RESERVED)

    def test_real_factory_constructs_all_requests_but_missing_authority_dispatches_nothing(self):
        with patch("subprocess.run", side_effect=AssertionError("no subprocess allowed")) as process:
            stack, adapters = self.stack()
            groups = prepare_peer_offline_requests(stack, self.configuration, 4)
            self.assertEqual(sum(len(group) for _, group in groups), 12)
            self.assertEqual(stack.hidden_seconds, 21000)
            for index, (key, requests) in enumerate(groups):
                adapter, root = adapters[key], self.root / f"route-{index}"
                manifest = FrozenComputeRunManifest.load_or_create(root / "manifest.json",
                    campaign_run_id=requests[0].campaign_run_id, compute_enabled=True,
                    transport_profile_digest=adapter.transport_profile_digest,
                    backend_profile_digest=adapter.backend_profile_digest, requests=requests)
                spend = SqliteComputeSpendAuthorizationService(root / "spend.sqlite3", manifest)
                transport, evidence = adapter.transport(root, spend), adapter.evidence(root)
                backend = SqliteComputeBackend(root / "executions.sqlite3", transport, evidence, manifest)
                for request in requests:
                    receipt = backend.submit(request, self.campaign.reference_candidate_path.read_bytes())
                    self.assertEqual(receipt.status, ComputeExecutionStatus.FAILED)
                    self.assertIn("authorization", receipt.failure)
                self.assertFalse((root / "jobs").exists())
            process.assert_not_called()

    def test_complete_four_actor_candidate_lifecycle_and_restart_close_against_raw_receipts(self):
        with patch.object(ModalPairedServingTransport, "dispatch", _RetainedPairedTransport.dispatch):
            stack, _ = self.stack()
            reference = self.campaign.reference_candidate_path.read_bytes()
            request = stack.public.prepare_visible_request(reference, None, "visible:reference")
            stack.inventory.register("public", (request,))
            stack.inventory.authorize(request, approval_reference="synthetic-no-spend-only")
            reference_receipt = stack.evaluator.visible_evaluate(reference, None, "visible:reference")
            actors = tuple(AgentIdentity("paired-peer", i) for i in range(4))
            plan = ComputePlan("peer", "paired-peer", 12000,
                tuple(ActorComputeAllocation("paired-peer", a.actor_id, 3000) for a in actors),
                stack.hidden_seconds, digest_value({"test": "raw-pairs"}))
            services = create_candidate_services(self.root / "services", self.campaign,
                evaluator=stack.evaluator, reference_receipt=reference_receipt, plan=plan, policy=SubmissionPolicy(1, 3000))
            document = parse_json((self.campaign.root / "candidates/vllm-stream-interval-10.json").read_text())
            for index, actor in enumerate(actors):
                session = services.sessions.bind(actor, SessionHandle(f"session-{index}"))
                receipt = services.tools.call(session, "submit", {"candidate": document, "idempotency_key": "first"})["receipt"]
                services.tools.call(session, "evaluate", {"receipt": receipt})
            runner = CandidatePilotRunner(services, stack.inventory, stack.public_plan, stack.hidden_plan,
                hidden_seconds=stack.hidden_seconds)
            for request in runner.prepare_public():
                stack.inventory.authorize(request, approval_reference="synthetic-no-spend-only")
            runner.collect_public()
            for request in runner.prepare_hidden():
                stack.inventory.authorize(request, approval_reference="synthetic-no-spend-only")
            result = runner.collect_hidden()
            self.assertTrue(result.eligible)
            self.assertEqual(result.criterion_units, 1250000)
            self.assertEqual(len(runner.reconcile("paired-peer")), 12)
            self.assertEqual(services.compute.snapshot("paired-peer").hidden_used_seconds, 847)
            self.assertEqual(list(services.compute.snapshot("paired-peer").actor_used_seconds.values()), [121] * 4)
            self.assertEqual(len(_RetainedPairedTransport.dispatches), 12)
            # Fresh factories and evaluator instances must recover authority and
            # rederive every score without any dispatch or receipt-cache state.
            rebuilt, _ = self.stack()
            services2 = create_candidate_services(self.root / "services", self.campaign,
                evaluator=rebuilt.evaluator, reference_receipt=reference_receipt, plan=plan, policy=SubmissionPolicy(1, 3000))
            runner2 = CandidatePilotRunner(services2, rebuilt.inventory, rebuilt.public_plan, rebuilt.hidden_plan,
                hidden_seconds=rebuilt.hidden_seconds)
            self.assertEqual(len(runner2.reconcile("paired-peer")), 12)
            self.assertEqual(len(_RetainedPairedTransport.dispatches), 12)

    def test_correctness_failure_remains_measured_ineligibility_not_an_execution_abort(self):
        _RetainedPairedTransport.wrong_correctness = True
        with patch.object(ModalPairedServingTransport, "dispatch", _RetainedPairedTransport.dispatch):
            stack, _ = self.stack()
            candidate = (self.campaign.root / "candidates/vllm-stream-interval-10.json").read_bytes()
            reservation = self.reservation(EvaluationScope.HIDDEN, stack.hidden_seconds)
            requests = stack.hidden.prepare_hidden_requests(candidate, reservation, "hidden:test")
            stack.inventory.register("hidden", requests)
            for request in requests:
                stack.inventory.authorize(request, approval_reference="synthetic-no-spend-only")
            receipt = stack.hidden.hidden_evaluate(candidate, reservation, "hidden:test")
            result = stack.hidden.resolve(receipt, candidate, reservation, EvaluationScope.HIDDEN)
            self.assertFalse(result.eligible)
            self.assertIn("candidate_correctness_failed", result.failures)
            self.assertEqual(stack.hidden.used_seconds(receipt), 847)
            self.assertEqual(result.diagnostics["performance"]["drivers"], ["610.57.04", "580.95.05", "610.57.04"])

    def test_stock_control_still_runs_all_hidden_pairs(self):
        with patch.object(ModalPairedServingTransport, "dispatch", _RetainedPairedTransport.dispatch):
            stack, _ = self.stack()
            candidate = self.campaign.reference_candidate_path.read_bytes()
            reservation = self.reservation(EvaluationScope.HIDDEN, stack.hidden_seconds)
            requests = stack.hidden.prepare_hidden_requests(candidate, reservation, "hidden:stock")
            stack.inventory.register("hidden", requests)
            for request in requests:
                stack.inventory.authorize(request, approval_reference="synthetic-no-spend-only")
            receipt = stack.hidden.hidden_evaluate(candidate, reservation, "hidden:stock")
            self.assertTrue(stack.hidden.resolve(receipt, candidate, reservation, EvaluationScope.HIDDEN).eligible)
            self.assertEqual(len(_RetainedPairedTransport.dispatches), 7)

    def test_both_composition_configs_differ_only_by_condition_and_cost_complete_inventory(self):
        docs = [parse_json((REPOSITORY_ROOT / f"config/pilots/peer-{condition}-live-composition-v1.json").read_text())
            for condition in ("isolated", "collab")]
        self.assertEqual({k: v for k, v in docs[0].items() if k != "condition"},
            {k: v for k, v in docs[1].items() if k != "condition"})
        cost = paired_peer_cost(self.configuration, 4)
        self.assertEqual(cost["new_gpu_calls"], 12)
        self.assertEqual(cost["per_arm_modal_allowance_usd_nanos"], 14655808000)
        self.assertFalse(cost["execution_authorized"])

    def test_stock_preparation_freezes_all_seven_requests_without_issuing_authority(self):
        with patch.object(ModalPairedServingTransport, "dispatch", side_effect=AssertionError("no dispatch allowed")):
            document = prepare_paired_stock_control(self.root / "control", self.configuration,
                "stock-control", self.bundle, self.policy)
        self.assertEqual(document["planned_gpu_calls"], 7)
        self.assertEqual(document["modal_allowance_usd_nanos"], 8965888000)
        self.assertEqual(document["authorization_count"], 0)
        self.assertEqual(document["dispatch_count"], 0)
        self.assertIsInstance(json.dumps(document), str)
        manifest = FrozenComputeRunManifest.load(Path(document["compute_manifests"][0]["file"]),
            expected_digest=document["compute_manifests"][0]["digest"])
        spend = SqliteComputeSpendAuthorizationService(manifest.path.parent / "spend.sqlite3", manifest)
        for request in manifest.requests():
            self.assertIsNone(spend.request_status(request, manifest.transport_profile_digest))

    def test_response_pair_checks_raw_completeness_and_identity(self):
        stack, _ = self.stack()
        profile = stack.hidden.profile
        candidate = self.campaign.reference_candidate_path.read_bytes()
        request = stack.hidden.prepare_hidden_requests(candidate, self.reservation(EvaluationScope.HIDDEN, stack.hidden_seconds), "hidden:stock")[1]
        spec = profile.spec(request)
        remote, raw = paired_fixture(profile, request, candidate, spec)
        self.assertEqual(score_paired_execution(profile, request, remote, raw, parse_json(candidate.decode()))["scores"]["candidate"]["pass_count"], 64)
        changed = deepcopy(remote)
        changed["roles"]["candidate"]["gpu_after"]["driver_version"] = "580.95.05"
        with self.assertRaisesRegex(RuntimeError, "GPU identity"):
            score_paired_execution(profile, request, changed, raw, parse_json(candidate.decode()))
        with self.assertRaisesRegex(RuntimeError, "raw case set"):
            score_paired_execution(profile, request, remote, {}, parse_json(candidate.decode()))

    def test_pinned_remote_wrapper_imports_without_deploying_or_resolving_secrets(self):
        # Import real Modal 1.5.4 and construct its function/image descriptors.
        # App.run, network, and function spawning are never invoked.
        path = self.campaign.root / "reference/modal_paired.py"
        spec = importlib.util.spec_from_file_location("paired_wrapper_import_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.PAIRED_FUNCTION_TIMEOUT_SECONDS, 3000)
        self.assertEqual(module.ALLOWED_DRIVERS, ["580.95.05", "610.57.04"])
        from modal._serialization import serialize
        self.assertGreater(len(serialize(module.paired_serving.get_raw_f())), 0)

    def test_stock_and_candidate_gpu_overrun_is_not_clamped_or_scoreable(self):
        stack, adapters = self.stack()
        reference = self.campaign.reference_candidate_path.read_bytes()
        request = stack.public.prepare_visible_request(reference, None, "visible:reference")
        adapter, root = adapters["public"], self.root / "overrun"
        manifest = FrozenComputeRunManifest.load_or_create(root / "manifest.json",
            campaign_run_id=request.campaign_run_id, compute_enabled=True,
            transport_profile_digest=adapter.transport_profile_digest,
            backend_profile_digest=adapter.backend_profile_digest, requests=(request,))
        spend = SqliteComputeSpendAuthorizationService(root / "spend.sqlite3", manifest)
        spend.issue(request, adapter.transport_profile_digest, "synthetic-no-spend-only")
        transport = _RetainedPairedTransport(stack.public.profile, root, self.configuration.modal_cli, spend)
        dispatch = transport.dispatch(request, reference)
        bundle = transport.resolver.store.load(request.request_digest[7:], 1)
        normalized = deepcopy(bundle.receipt["normalized"])
        normalized["remote_receipt"]["timing"]["function_body_ms"] = 3000001
        normalized["durable_evidence"]["remote_receipt_digest"] = digest_bytes(canonical_json_bytes(normalized["remote_receipt"]) + b"\n")
        # Corrupt a synthetic local copy, never a provider call or real receipt.
        receipt_path = root / "measurements" / request.request_digest[7:] / "repetition-0001-attempt-01/receipt.json"
        receipt_path.write_bytes(canonical_json_bytes({**bundle.receipt, "normalized": normalized}) + b"\n")
        _, _, used, _ = transport.resolver.pointer(request, dispatch.external_call_id)
        self.assertEqual(used, 3001)

    def test_phase_eligibility_and_stock_reference_failure_fail_closed(self):
        _RetainedPairedTransport.wrong_reference = True
        with patch.object(ModalPairedServingTransport, "dispatch", _RetainedPairedTransport.dispatch):
            stack, _ = self.stack()
            reference = self.campaign.reference_candidate_path.read_bytes()
            reservation = self.reservation(EvaluationScope.HIDDEN, stack.hidden_seconds)
            requests = stack.hidden.prepare_hidden_requests(reference, reservation, "hidden:stock")
            stack.inventory.register("hidden", requests)
            for request in requests:
                stack.inventory.authorize(request, approval_reference="synthetic-no-spend-only")
            receipt = stack.hidden.hidden_evaluate(reference, reservation, "hidden:stock")
            result = stack.hidden.resolve(receipt, reference, reservation, EvaluationScope.HIDDEN)
            self.assertFalse(result.eligible)
            self.assertIn("reference_correctness_failed", result.failures)

    def test_unknown_driver_is_not_admitted_by_the_quality_scorer(self):
        stack, _ = self.stack()
        profile = stack.hidden.profile
        candidate = self.campaign.reference_candidate_path.read_bytes()
        request = stack.hidden.prepare_hidden_requests(candidate, self.reservation(EvaluationScope.HIDDEN, stack.hidden_seconds), "hidden:stock")[1]
        remote, raw = paired_fixture(profile, request, candidate, profile.spec(request), driver="unknown")
        with self.assertRaisesRegex(RuntimeError, "undeclared"):
            score_paired_execution(profile, request, remote, raw, parse_json(candidate.decode()))
