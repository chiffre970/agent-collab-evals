"""No-spend public-evaluator composition with exact frozen compute requests."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from agent_collab_evals.adapters.modal_serving_evaluator import ModalServingDevelopmentEvaluator
from agent_collab_evals.adapters.modal_vllm_compute import ModalVllmComputeProfile
from agent_collab_evals.adapters.sqlite_execution_backend import SqliteComputeBackend
from agent_collab_evals.candidate_services import create_solo_candidate_services
from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.canonical import canonical_json_bytes, digest_value, parse_json
from agent_collab_evals.compute_backend import FrozenComputeRunManifest
from agent_collab_evals.domain import AgentIdentity, SessionHandle
from agent_collab_evals.evaluation import ActorComputeAllocation, ComputePlan, EvaluationInProgress, SubmissionPolicy
from agent_collab_evals.solo_evaluation_handoff import SoloEvaluationHandoff
from tests.test_compute_backend import _EvidenceStore, _Transport


class _PublicTransport(_Transport):
    """Synthetic provider output in the real public evaluator's envelope."""

    def poll(self, request, external_call_id, timeout_seconds):
        poll = super().poll(request, external_call_id, timeout_seconds)
        document = parse_json(self.evidence.resolve(poll.evidence).decode())
        document["result"] = {
            "valid": True,
            "performance_score": {
                "eligible": True, "failures": [],
                "scalar_ppm": 1000000 if request.campaign_run_id == "registered-reference" else 1100000,
            },
            "modal_function_call_id": external_call_id,
        }
        return replace(poll, evidence=self.evidence.put(poll.evidence.locator, document))


class SoloCandidateServiceTests(unittest.TestCase):
    def test_admitted_candidate_uses_public_adapter_and_real_compute_ledger(self):
        repository = Path(__file__).resolve().parents[1]
        campaign = ModelServingCampaign.load(repository / "campaigns/model_serving_v0/campaign.toml")
        profile = ModalVllmComputeProfile.load(repository / "config/compute/modal-vllm-development.json", repository_root=repository)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = _EvidenceStore()
            transport = _PublicTransport(evidence)
            backend_digest = SqliteComputeBackend.profile_digest_for(transport.profile_digest, evidence.profile_digest)
            # Test-only routing: the caller explicitly freezes each exact request
            # after its candidate/reservation exists. No live admission is inferred.
            routes = {}
            manifests = {}
            routing = Mock(profile_digest=backend_digest)
            routing.submit.side_effect = lambda request, candidate: routes[request.execution_key].submit(request, candidate)
            routing.collect.side_effect = lambda request, **kwargs: routes[request.execution_key].collect(request, **kwargs)
            routing.resolve.side_effect = lambda request: routes[request.execution_key].resolve(request)

            def freeze(request):
                index = len(routes)
                manifest = FrozenComputeRunManifest.load_or_create(
                    root / f"request-{index}.json", campaign_run_id=request.campaign_run_id,
                    compute_enabled=True, transport_profile_digest=transport.profile_digest,
                    backend_profile_digest=backend_digest, requests=(request,),
                )
                backend = SqliteComputeBackend(root / f"execution-{index}.sqlite3", transport, evidence, manifest)
                routes[request.execution_key] = backend
                manifests[request.execution_key] = manifest
                return backend

            evaluator = ModalServingDevelopmentEvaluator(root / "evaluator.sqlite3", campaign, profile, routing)
            reference = campaign.reference_candidate_path.read_bytes()
            reference_request = evaluator.prepare_visible_request(reference, None, "reference:solo")
            freeze(reference_request)
            reference_receipt = evaluator.visible_evaluate(reference, None, "reference:solo")
            actor = AgentIdentity("solo-public-composition", 0)
            plan = ComputePlan("solo-test", actor.campaign_run_id, 60, (ActorComputeAllocation(actor.campaign_run_id, actor.actor_id, 60),), 60, digest_value({"synthetic": True}))
            services = create_solo_candidate_services(
                root / "services", campaign, evaluator=evaluator, reference_receipt=reference_receipt,
                plan=plan, policy=SubmissionPolicy(1, 60),
                host_evaluation=True,
            )
            self.assertEqual(transport.dispatch_count, 1)  # Factory did not recompute the reference.
            session = services.sessions.bind(actor, SessionHandle("solo-primary"))
            candidate = json.loads((campaign.root / "candidates/vllm-stream-interval-10.json").read_bytes())
            submission = {"candidate": candidate, "idempotency_key": "first"}
            admitted = services.tools.call(session, "submit", submission)
            self.assertEqual(services.tools.call(session, "submit", submission), admitted)
            handoff = SoloEvaluationHandoff(services.submissions, services.compute, actor.campaign_run_id)
            prepared = handoff.prepare()
            request = evaluator.prepare_visible_request(prepared.candidate, prepared.reservation, prepared.evaluation_key)
            self.assertEqual(prepared.candidate, canonical_json_bytes(candidate))
            arguments = {"receipt": admitted["receipt"]}
            # No candidate authority is installed yet. An agent tool must not
            # dispatch or permanently fail this admitted reservation.
            self.assertEqual(services.tools.call(session, "evaluate", arguments)["status"], "pending")
            self.assertEqual(transport.dispatch_count, 1)
            freeze(request)
            original_collect = routing.collect.side_effect
            routing.collect.side_effect = EvaluationInProgress("caller interrupted after dispatch")
            with self.assertRaises(EvaluationInProgress):
                handoff.evaluate()
            self.assertEqual(transport.dispatch_count, 2)
            self.assertEqual(services.tools.call(session, "result", arguments)["status"], "pending")
            # Reopen both durable execution backends and all candidate services.
            # The frozen request, not a replayed dispatch, restores authority.
            for index, key in enumerate(tuple(routes)):
                manifest_path = root / f"request-{index}.json"
                manifest = FrozenComputeRunManifest.load(
                    manifest_path, expected_digest=manifests[key].manifest_digest,
                )
                routes[key] = SqliteComputeBackend(root / f"execution-{index}.sqlite3", transport, evidence, manifest)
            routing.collect.side_effect = original_collect
            evaluator = ModalServingDevelopmentEvaluator(root / "evaluator.sqlite3", campaign, profile, routing)
            services = create_solo_candidate_services(
                root / "services", campaign, evaluator=evaluator, reference_receipt=reference_receipt,
                plan=plan, policy=SubmissionPolicy(1, 60), host_evaluation=True,
            )
            session = services.sessions.bind(actor, SessionHandle("restored-solo-primary"))
            handoff = SoloEvaluationHandoff(services.submissions, services.compute, actor.campaign_run_id)
            restored = handoff.prepare()
            self.assertEqual(evaluator.prepare_visible_request(restored.candidate, restored.reservation, restored.evaluation_key), request)
            feedback = handoff.evaluate()
            self.assertEqual(feedback.public_materials["candidate_receipt"], admitted["receipt"])
            self.assertEqual(handoff.evaluate(), feedback)
            self.assertEqual(services.tools.call(session, "result", arguments)["result"]["criterion_units"], 1100000)
            selection = services.submissions.select(services.submissions.close(actor.campaign_run_id, "optimize-serving"))
            self.assertFalse(selection.used_default)
            self.assertEqual(len(routes[request.execution_key].reconcile(actor.campaign_run_id)), 1)
            self.assertEqual(len(routes[reference_request.execution_key].reconcile("registered-reference")), 1)
            self.assertEqual(transport.dispatch_count, 2)
            self.assertEqual(services.compute.snapshot(actor.campaign_run_id).actor_used_seconds[actor.actor_id], 7)
            # Public-only composition must not silently supply a hidden score.
            with self.assertRaisesRegex(RuntimeError, "no hidden workload"):
                services.submissions.evaluate_hidden(selection.receipt, reserved_seconds=60)


if __name__ == "__main__":
    unittest.main()
