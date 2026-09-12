from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agent_collab_evals.adapters.sqlite_compute_routes import ComputeRouteAdapter, SqliteComputeRouteInventory
from agent_collab_evals.solo_pilot_runner import PilotComputeRoute, SoloPilotRunner

from agent_collab_evals.adapters.fake_harness import FakeHarnessRuntime
from agent_collab_evals.adapters.local_events import LocalEventSink
from agent_collab_evals.adapters.modal_serving_evaluator import ModalServingDevelopmentEvaluator
from agent_collab_evals.adapters.modal_vllm_compute import ModalVllmComputeProfile
from agent_collab_evals.adapters.split_scope_evaluator import EvaluationLaneProfile, RegisteredEvaluationProfile, SplitScopeServingEvaluator
from agent_collab_evals.adapters.sqlite_budget import SqliteBudgetAccount
from agent_collab_evals.adapters.sqlite_delivery import SqliteDeliveryOutbox
from agent_collab_evals.budget import ActorBudgetAllocation
from agent_collab_evals.candidate_services import create_solo_candidate_services
from agent_collab_evals.controller import CampaignCloseRejected, CampaignController
from agent_collab_evals.domain import AgentIdentity, CampaignStatus, CoordinationCondition, OrganisationSpec, SessionHandle
from agent_collab_evals.evaluation import ActorComputeAllocation, ComputePlan, SubmissionPolicy
from agent_collab_evals.solo_evaluation_closure import SoloEvaluationClosure
from tests.test_compute_backend import _EvidenceStore
from tests.test_solo_candidate_services import _PublicTransport
from tests.test_sqlite_budget import _JsonReceiptVerifier, _context, _plan, _rate_card, _usage

from agent_collab_evals.adapters.composite_hidden_evaluator import (
    CompositeHiddenEvaluationProfile,
    CompositeHiddenServingEvaluator,
    HiddenEvaluationPhaseProfile,
)
from agent_collab_evals.adapters.compute_candidate_evaluator import (
    ComputeCandidateEvaluationProfile,
    ComputeCandidateEvaluator,
)
from agent_collab_evals.adapters.compute_quality_backend import (
    ComputeQualityRepetitionBackend,
    ComputeQualityRepetitionProfile,
)
from agent_collab_evals.adapters.modal_vllm_correctness_compute import (
    ModalVllmCorrectnessEvidenceResolver,
    ModalVllmCorrectnessProfile,
)
from agent_collab_evals.adapters.modal_vllm_performance_compute import (
    ModalVllmHiddenPerformanceEvidenceResolver,
    ModalVllmHiddenPerformanceProfile,
)
from agent_collab_evals.adapters.modal_vllm_quality_compute import (
    ModalVllmQualityEvidenceResolver,
    ModalVllmQualityProfile,
)
from agent_collab_evals.adapters.performance_series_evaluator import (
    PerformanceSeriesEvaluator,
    PerformanceSeriesProfile,
)
from agent_collab_evals.adapters.quality_series_evaluator import (
    PairedQualitySeriesEvaluator,
    QualitySeriesProfile,
    quality_policy_authority_digest,
)
from agent_collab_evals.adapters.sqlite_execution_backend import SqliteComputeBackend
from agent_collab_evals.canonical import canonical_json_bytes, digest_bytes, digest_value, parse_json
from agent_collab_evals.campaigns.serving_scoring import ScoringProfile
from agent_collab_evals.compute_backend import ComputeExecutionStatus
from agent_collab_evals.evaluation import EvaluationScope
from tests.quality_fixture import REPOSITORY_ROOT, real_hidden_quality_bundle
from tests.test_modal_hidden_correctness_compute import (
    _RetainedCorrectnessTransport,
)
from tests.test_modal_hidden_performance_compute import (
    _RetainedPerformanceTransport,
)
from tests.test_modal_quality_compute_adapter import (
    _RetainedModalQualityTransport,
)


class _ApprovedSyntheticTransport:
    """Require real durable authorization before the simulated side effect."""

    def __init__(self, transport, spend):
        self.transport, self.spend = transport, spend
        self.profile_digest = transport.profile_digest

    def dispatch(self, request, candidate):
        self.spend.consume(request, self.profile_digest)
        return self.transport.dispatch(request, candidate)

    def poll(self, request, external_call_id, timeout_seconds):
        return self.transport.poll(request, external_call_id, timeout_seconds)


class CompositeHiddenRealAdapterTests(unittest.TestCase):
    def test_all_three_real_phase_adapters_execute_and_reconcile(self) -> None:
        self._run_solo_path()

    def test_reference_winner_runs_all_hidden_phases_before_closure(self) -> None:
        self._run_solo_path(default_wins=True)

    def test_failed_hidden_evaluation_rejects_campaign_closure(self) -> None:
        self._run_solo_path(fail_hidden=True)

    def test_unsettled_model_call_rejects_otherwise_complete_evaluation(self) -> None:
        self._run_solo_path(unsettled_model=True)

    def test_unresolved_compute_evidence_rejects_campaign_closure(self) -> None:
        self._run_solo_path(fail_reconcile=True)

    def _run_solo_path(self, *, default_wins=False, fail_hidden=False, unsettled_model=False, fail_reconcile=False) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            campaign, bundle, policy = real_hidden_quality_bundle(root / "bundle")
            reference = campaign.reference_candidate_path.read_bytes()
            candidate_document = parse_json(reference.decode("utf-8"))
            candidate_document["candidate_id"] = "composite-candidate"
            candidate_document["server"]["engine_args"]["stream_interval"] = 2
            candidate = canonical_json_bytes(candidate_document)
            campaign_manifest = (
                REPOSITORY_ROOT / "campaigns/model_serving_v0/campaign.toml"
            )
            modal_script = (
                REPOSITORY_ROOT
                / "campaigns/model_serving_v0/reference/modal_vllm.py"
            )
            correctness_modal = ModalVllmCorrectnessProfile.create(
                profile_id="composite-correctness-modal-v0",
                campaign=campaign,
                campaign_manifest=campaign_manifest,
                hidden_workload=bundle,
                modal_script=modal_script,
                modal_environment="dev",
                modal_client_version="1.5.4",
                attempt=1,
                maximum_collection_seconds=300,
                evidence_volume="agent-collab-evals-evaluator-evidence-v2",
            )
            quality_modal = ModalVllmQualityProfile.create(
                profile_id="composite-quality-modal-v0",
                campaign=campaign,
                campaign_manifest=campaign_manifest,
                hidden_workload=bundle,
                modal_script=modal_script,
                modal_environment="dev",
                modal_client_version="1.5.4",
                attempt=1,
                maximum_collection_seconds=300,
                evidence_volume="agent-collab-evals-evaluator-evidence-v2",
            )
            scoring = ScoringProfile.load(
                REPOSITORY_ROOT
                / "campaigns/model_serving_v0/evaluator/scoring_hidden_v1.toml"
            )
            performance_modals = {
                repetition: ModalVllmHiddenPerformanceProfile.create(
                    profile_id=(
                        f"composite-performance-modal-v0-repetition-{repetition}"
                    ),
                    campaign=campaign,
                    campaign_manifest=campaign_manifest,
                    hidden_workload=bundle,
                    scoring_profile=scoring.path,
                    modal_script=modal_script,
                    modal_environment="dev",
                    modal_client_version="1.5.4",
                    repetition=repetition,
                    attempt=1,
                    maximum_collection_seconds=300,
                    evidence_volume="agent-collab-evals-evaluator-evidence-v2",
                )
                for repetition in range(1, 4)
            }

            correctness_state = root / "correctness-state"
            quality_state = root / "quality-state"
            performance_states = {
                repetition: root / f"performance-state-{repetition}"
                for repetition in range(1, 4)
            }
            correctness_transport_digest = digest_value(
                {"transport": "composite-correctness"}
            )
            quality_transport_digest = digest_value(
                {"transport": "composite-quality"}
            )
            performance_transport_digests = {
                repetition: digest_value(
                    {"transport": "composite-performance", "repetition": repetition}
                )
                for repetition in range(1, 4)
            }
            correctness_resolver = ModalVllmCorrectnessEvidenceResolver(
                correctness_modal,
                correctness_state,
                correctness_transport_digest,
            )
            quality_resolver = ModalVllmQualityEvidenceResolver(
                quality_modal, quality_state, quality_transport_digest
            )
            performance_resolvers = {
                repetition: ModalVllmHiddenPerformanceEvidenceResolver(
                    performance_modals[repetition],
                    REPOSITORY_ROOT,
                    performance_states[repetition],
                    performance_transport_digests[repetition],
                )
                for repetition in range(1, 4)
            }
            correctness_backend_digest = SqliteComputeBackend.profile_digest_for(
                correctness_transport_digest, correctness_resolver.profile_digest
            )
            quality_backend_digest = SqliteComputeBackend.profile_digest_for(
                quality_transport_digest, quality_resolver.profile_digest
            )
            performance_backend_digests = {
                repetition: SqliteComputeBackend.profile_digest_for(
                    performance_transport_digests[repetition],
                    performance_resolvers[repetition].profile_digest,
                )
                for repetition in range(1, 4)
            }
            correctness_profile = ComputeCandidateEvaluationProfile(
                profile_id="composite-correctness-v0",
                phase="correctness",
                campaign_manifest_digest=campaign.manifest_digest,
                hidden_workload_manifest_digest=bundle.manifest_digest,
                workload_digest=correctness_modal.correctness_workload_digest,
                compute_execution_profile_digest=correctness_backend_digest,
                maximum_collection_seconds=300,
            )
            quality_repetition_profile = ComputeQualityRepetitionProfile(
                profile_id="composite-quality-repetition-v0",
                campaign_manifest_digest=campaign.manifest_digest,
                hidden_workload_manifest_digest=bundle.manifest_digest,
                quality_profile_digest=quality_modal.quality_profile_digest,
                quality_workload_digest=quality_modal.quality_workload_digest,
                compute_execution_profile_digest=quality_backend_digest,
                repetitions=3,
                maximum_collection_seconds=300,
            )
            quality_series_profile = QualitySeriesProfile(
                profile_id="composite-quality-series-v0",
                campaign_manifest_digest=campaign.manifest_digest,
                hidden_workload_manifest_digest=bundle.manifest_digest,
                quality_profile_digest=quality_modal.quality_profile_digest,
                quality_policy_digest=policy.digest,
                quality_policy_authority_digest=quality_policy_authority_digest(
                    policy
                ),
                quality_workload_digest=quality_modal.quality_workload_digest,
                reference_artifact_ref="artifact-" + "6" * 32,
                reference_candidate_digest=digest_bytes(reference),
                repetition_backend_profile_digest=quality_repetition_profile.digest,
                repetitions=3,
                repetition_reserved_seconds=600,
                role_order_by_repetition=(
                    ("reference", "candidate"),
                    ("candidate", "reference"),
                    ("reference", "candidate"),
                ),
            )
            performance_profiles = {
                repetition: ComputeCandidateEvaluationProfile(
                    profile_id=f"composite-performance-v0-{repetition}",
                    phase="performance",
                    campaign_manifest_digest=campaign.manifest_digest,
                    hidden_workload_manifest_digest=bundle.manifest_digest,
                    workload_digest=(
                        performance_modals[repetition].performance_profile_digest
                    ),
                    compute_execution_profile_digest=(
                        performance_backend_digests[repetition]
                    ),
                    maximum_collection_seconds=300,
                )
                for repetition in range(1, 4)
            }
            performance_series_profile = PerformanceSeriesProfile(
                profile_id="composite-performance-series-v0",
                campaign_manifest_digest=campaign.manifest_digest,
                hidden_workload_manifest_digest=bundle.manifest_digest,
                workload_digest=performance_modals[1].performance_profile_digest,
                scoring_profile_digest=scoring.digest,
                repetition_evaluator_profile_digests=tuple(
                    performance_profiles[repetition].digest
                    for repetition in range(1, 4)
                ),
                repetition_reserved_seconds=1_800,
            )
            composite_profile = CompositeHiddenEvaluationProfile(
                profile_id="composite-real-adapters-v0",
                campaign_manifest_digest=campaign.manifest_digest,
                hidden_workload_manifest_digest=bundle.manifest_digest,
                correctness=HiddenEvaluationPhaseProfile(
                    "correctness",
                    correctness_profile.digest,
                    correctness_modal.correctness_workload_digest,
                    600,
                ),
                quality=HiddenEvaluationPhaseProfile(
                    "quality",
                    quality_series_profile.digest,
                    quality_modal.quality_workload_digest,
                    quality_series_profile.reserved_seconds,
                ),
                performance=HiddenEvaluationPhaseProfile(
                    "performance",
                    performance_series_profile.digest,
                    performance_modals[1].performance_profile_digest,
                    performance_series_profile.reserved_seconds,
                ),
            )
            # Compose real public evaluation and durable admission before hidden
            # request preparation. Only external transport responses are simulated.
            correctness_transport = _RetainedCorrectnessTransport(
                correctness_state, correctness_modal, correctness_resolver,
                correctness_transport_digest,
            )
            quality_transport = _RetainedModalQualityTransport(
                quality_state, quality_modal, quality_resolver,
                quality_transport_digest, policy,
            )
            performance_transports = {
                repetition: _RetainedPerformanceTransport(
                    performance_states[repetition], performance_modals[repetition],
                    performance_resolvers[repetition], performance_transport_digests[repetition],
                )
                for repetition in range(1, 4)
            }
            public_evidence = _EvidenceStore()
            public_transport = _PublicTransport(public_evidence)
            public_digest = SqliteComputeBackend.profile_digest_for(public_transport.profile_digest, public_evidence.profile_digest)
            adapters = {
                "public": ComputeRouteAdapter(public_transport.profile_digest, public_evidence.profile_digest,
                    lambda route_root, spend: _ApprovedSyntheticTransport(public_transport, spend), lambda route_root: public_evidence),
                "correctness": ComputeRouteAdapter(correctness_transport_digest, correctness_resolver.profile_digest,
                    lambda route_root, spend: _ApprovedSyntheticTransport(correctness_transport, spend), lambda route_root: correctness_resolver),
                "quality": ComputeRouteAdapter(quality_transport_digest, quality_resolver.profile_digest,
                    lambda route_root, spend: _ApprovedSyntheticTransport(quality_transport, spend), lambda route_root: quality_resolver),
                **{f"performance-{r}": ComputeRouteAdapter(performance_transport_digests[r], performance_resolvers[r].profile_digest,
                    lambda route_root, spend, r=r: _ApprovedSyntheticTransport(performance_transports[r], spend),
                    lambda route_root, r=r: performance_resolvers[r]) for r in range(1, 4)},
            }
            inventory = SqliteComputeRouteInventory(root / "routes", "composite-real-run", adapters)
            routing = inventory.backend("public")
            correctness_backend = inventory.backend("correctness")
            quality_backend = inventory.backend("quality")
            performance_backends = {r: inventory.backend(f"performance-{r}") for r in range(1, 4)}
            correctness = ComputeCandidateEvaluator(
                root / "correctness.sqlite3", campaign, correctness_profile, correctness_backend,
            )
            quality_repetitions = ComputeQualityRepetitionBackend(
                root / "quality-repetitions.sqlite3", campaign,
                quality_repetition_profile, quality_backend,
            )
            quality = PairedQualitySeriesEvaluator(
                root / "quality-series.sqlite3", quality_series_profile,
                policy, reference, quality_repetitions,
            )
            performance_repetitions = {
                repetition: ComputeCandidateEvaluator(
                    root / f"performance-{repetition}.sqlite3", campaign,
                    performance_profiles[repetition], performance_backends[repetition],
                )
                for repetition in range(1, 4)
            }
            performance = PerformanceSeriesEvaluator(
                root / "performance-series.sqlite3", performance_series_profile,
                scoring, performance_repetitions,
            )
            composite = CompositeHiddenServingEvaluator(
                root / "composite.sqlite3", composite_profile,
                {"correctness": correctness, "quality": quality, "performance": performance},
            )

            def freeze_public(request):
                inventory.register("public", (request,))
                inventory.authorize(request, approval_reference="synthetic-no-spend-test")

            public_profile = ModalVllmComputeProfile.load(REPOSITORY_ROOT / "config/compute/modal-vllm-development.json", repository_root=REPOSITORY_ROOT)
            public = ModalServingDevelopmentEvaluator(root / "public.sqlite3", campaign, public_profile, routing)
            lanes = {
                scope: EvaluationLaneProfile(
                    scope, public.profile_digest if scope is EvaluationScope.VISIBLE else composite_profile.digest,
                    public_digest if scope is EvaluationScope.VISIBLE else digest_value({"hidden_backends": [correctness_backend_digest, quality_backend_digest, *performance_backend_digests.values()]}),
                    digest_value({"workload": scope.value}), f"compute-{scope.value}",
                    digest_value({"schedule": scope.value}), f"evidence-{scope.value}",
                ) for scope in EvaluationScope
            }
            split = SplitScopeServingEvaluator(
                root / "split.sqlite3", RegisteredEvaluationProfile(
                    "solo-composition", campaign.manifest_digest, digest_value({"synthetic_registration": True}),
                    lanes[EvaluationScope.VISIBLE], lanes[EvaluationScope.HIDDEN],
                ), public, composite,
            )
            reference_key = "visible:reference"
            freeze_public(public.prepare_visible_request(reference, None, reference_key))
            reference_receipt = split.visible_evaluate(reference, None, reference_key)
            actor = AgentIdentity("composite-real-run", 0)
            compute_plan = ComputePlan("solo-composition", actor.campaign_run_id, 60,
                (ActorComputeAllocation(actor.campaign_run_id, actor.actor_id, 60),),
                composite_profile.reserved_seconds, digest_value({"test_compute": True}))
            services = create_solo_candidate_services(root / "services", campaign, evaluator=split,
                reference_receipt=reference_receipt, plan=compute_plan, policy=SubmissionPolicy(1, 60))
            session = services.sessions.bind(actor, SessionHandle("solo-primary"))
            services.tools.call(session, "submit", {"candidate": candidate_document, "idempotency_key": "first"})
            hidden_adapters = {
                correctness_profile.digest: "correctness",
                quality_repetition_profile.digest: "quality",
                **{profile.digest: f"performance-{r}" for r, profile in performance_profiles.items()},
            }

            def plan_hidden(item):
                requests = composite.prepare_hidden_requests(
                    item.candidate, item.reservation, item.evaluation_key,
                )
                return tuple(
                    PilotComputeRoute(adapter, tuple(
                        request for request in requests if request.evaluator_profile_digest == digest
                    ))
                    for digest, adapter in hidden_adapters.items()
                )

            runner = SoloPilotRunner(services, inventory,
                lambda item: (PilotComputeRoute("public", (public.prepare_visible_request(item.candidate, item.reservation, item.evaluation_key),)),),
                plan_hidden, hidden_seconds=composite_profile.reserved_seconds)
            public_requests = runner.prepare_public()
            with self.assertRaisesRegex(RuntimeError, "explicit authorization"):
                runner.collect_public()
            for request in public_requests:
                inventory.authorize(request, approval_reference="synthetic-no-spend-test")
            original_poll = public_transport.poll

            def candidate_poll(request, external_call_id, timeout_seconds):
                result = original_poll(request, external_call_id, timeout_seconds)
                if default_wins:
                    document = parse_json(public_evidence.resolve(result.evidence).decode())
                    document["result"]["performance_score"]["scalar_ppm"] = 900000
                    result = replace(result, evidence=public_evidence.put(result.evidence.locator, document))
                return result

            with patch.object(public_transport, "poll", side_effect=candidate_poll):
                feedback = runner.collect_public()
            selection = services.submissions.select(services.submissions.close(actor.campaign_run_id, "optimize-serving"))
            self.assertEqual(selection.used_default, default_wins)
            prepared = services.submissions.prepare_hidden_evaluation(selection.receipt, reserved_seconds=composite_profile.reserved_seconds)
            candidate, outer = prepared.candidate, prepared.reservation
            self.assertEqual(candidate, reference if default_wins else canonical_json_bytes(candidate_document))
            with self.assertRaisesRegex(RuntimeError, "no completed hidden"):
                services.submissions.resolve_hidden(selection.receipt)
            # Preparation reserves and seals, but never issues permission to spend.
            hidden_requests = runner.prepare_hidden()
            self.assertEqual(len(hidden_requests), 10)
            self.assertEqual(sum(request.maximum_seconds for request in hidden_requests), composite_profile.reserved_seconds)
            self.assertEqual(runner.prepare_hidden(), hidden_requests)
            quality_requests = tuple(request for request in hidden_requests
                                     if request.evaluator_profile_digest == quality_repetition_profile.digest)
            self.assertEqual(
                [request.execution_key.rsplit(":", 2)[-2:] for request in quality_requests],
                [["1", "reference"], ["1", "candidate"], ["2", "candidate"],
                 ["2", "reference"], ["3", "reference"], ["3", "candidate"]],
            )
            self.assertEqual([request.candidate_digest for request in quality_requests],
                             [digest_bytes(reference), digest_bytes(candidate), digest_bytes(candidate),
                              digest_bytes(reference), digest_bytes(reference), digest_bytes(candidate)])
            self.assertEqual(quality_transport.dispatch_count, 0)
            with self.assertRaisesRegex(RuntimeError, "explicit authorization"):
                runner.collect_hidden()
            for request in hidden_requests:
                inventory.authorize(request, approval_reference="synthetic-no-spend-test")
            sources = inventory.sources()
            closure = SoloEvaluationClosure(services.submissions, services.compute, compute_plan, sources)
            allocations = (ActorBudgetAllocation(actor.campaign_run_id, actor.actor_id, 1000000),)
            budget = SqliteBudgetAccount(root / "model-budget.sqlite3", _rate_card(),
                require_metadata_receipts=False, budget_plan=_plan(actor.campaign_run_id, allocations),
                receipt_verifier=_JsonReceiptVerifier())
            budget.open_campaign(actor.campaign_run_id, 1000000, allocations)
            charge = budget.reserve(actor.campaign_run_id, actor.actor_id, _context("solo-call"))
            if not unsettled_model:
                budget.settle(charge.reservation_id, _usage())
            events = LocalEventSink(root / "events")
            controller = CampaignController(FakeHarnessRuntime(), events, budget, runner, SqliteDeliveryOutbox(root / "delivery.sqlite3"))
            handle = controller.start(OrganisationSpec(actor.campaign_run_id, CoordinationCondition.SOLO, 1, root / "workspace", "http://synthetic.invalid"))
            controller.deliver(handle, feedback)
            if fail_hidden:
                with patch.object(correctness, "hidden_evaluate", side_effect=RuntimeError("evaluation failed")):
                    with self.assertRaisesRegex(RuntimeError, "evaluation failed"):
                        runner.collect_hidden()
                with self.assertRaises(CampaignCloseRejected):
                    controller.close(handle, "failed evaluation")
                self.assertIs(handle.status, CampaignStatus.INVALID)
                return
            result = runner.collect_hidden()
            self.assertEqual(services.submissions.resolve_hidden(selection.receipt), result)
            self.assertEqual(len(closure.reconcile(actor.campaign_run_id)), 12)
            # Reconstruct persisted selection and wrapper receipts without
            # evaluating again. Closure must not depend on the original objects.
            restarted_split = SplitScopeServingEvaluator(root / "split.sqlite3", split._profile, public, composite)
            restarted_services = create_solo_candidate_services(root / "services", campaign, evaluator=restarted_split,
                reference_receipt=reference_receipt, plan=compute_plan, policy=SubmissionPolicy(1, 60))
            sealed_digest = inventory.seal()
            restored_inventory = SqliteComputeRouteInventory(root / "routes", actor.campaign_run_id, adapters, expected_seal_digest=sealed_digest)
            restarted_closure = SoloEvaluationClosure(restarted_services.submissions, restarted_services.compute, compute_plan, restored_inventory.sources())
            self.assertEqual(restarted_services.submissions.resolve_hidden(selection.receipt), result)
            self.assertEqual(restarted_closure.reconcile(actor.campaign_run_id), closure.reconcile(actor.campaign_run_id))
            self.assertEqual(public_transport.dispatch_count, 2)
            self.assertEqual(quality_transport.dispatch_count, 6)
            # Source omissions and duplicates must not produce a valid close.
            without_performance = tuple(source for source in sources if source.backend.profile_digest != performance_backends[3].profile_digest)
            with self.assertRaisesRegex(RuntimeError, "usage differs"):
                SoloEvaluationClosure(services.submissions, services.compute, compute_plan, without_performance).reconcile(actor.campaign_run_id)
            with self.assertRaisesRegex(RuntimeError, "duplicate compute"):
                SoloEvaluationClosure(services.submissions, services.compute, compute_plan, (*sources, sources[0])).reconcile(actor.campaign_run_id)
            # A measured ineligible outcome is not an infrastructure failure.
            with patch.object(services.submissions, "resolve_hidden", return_value=replace(result, eligible=False, criterion_units=0, failures=("quality_failed",))):
                self.assertEqual(len(closure.reconcile(actor.campaign_run_id)), 12)
            if fail_reconcile:
                quality_source = next(source for source in sources if source.backend.profile_digest == quality_backend.profile_digest)
                with patch.object(quality_source.backend, "reconcile", side_effect=RuntimeError("compute evidence unavailable")):
                    with self.assertRaises(CampaignCloseRejected):
                        controller.close(handle, "missing compute evidence")
                self.assertIs(handle.status, CampaignStatus.INVALID)
                return
            if unsettled_model:
                with self.assertRaises(CampaignCloseRejected):
                    controller.close(handle, "missing model receipt")
                self.assertIs(handle.status, CampaignStatus.INVALID)
                return
            controller.close(handle, "solo synthetic evaluation complete")
            self.assertIs(handle.status, CampaignStatus.CLOSED)
            self.assertTrue(budget.reconcile(actor.campaign_run_id).valid)

            self.assertTrue(result.eligible)
            self.assertEqual(result.criterion_units, 1_001_000)
            self.assertEqual(services.compute.snapshot(actor.campaign_run_id).hidden_used_seconds, 125)
            self.assertEqual(len(correctness_backend.reconcile(outer.campaign_run_id)), 1)
            self.assertEqual(len(quality_backend.reconcile(outer.campaign_run_id)), 6)
            self.assertEqual(
                sum(
                    len(backend.reconcile(outer.campaign_run_id))
                    for backend in performance_backends.values()
                ),
                3,
            )
            self.assertTrue(
                all(
                    item.status is ComputeExecutionStatus.COMPLETE
                    for backend in (
                        correctness_backend,
                        quality_backend,
                        *performance_backends.values(),
                    )
                    for item in backend.reconcile(outer.campaign_run_id)
                )
            )
