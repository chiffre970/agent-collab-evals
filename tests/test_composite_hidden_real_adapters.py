from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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
from agent_collab_evals.adapters.quality_series_evaluator import (
    PairedQualitySeriesEvaluator,
    QualitySeriesProfile,
    quality_policy_authority_digest,
)
from agent_collab_evals.adapters.sqlite_execution_backend import SqliteComputeBackend
from agent_collab_evals.artifacts import ArtifactRef
from agent_collab_evals.canonical import canonical_json_bytes, digest_bytes, digest_value, parse_json
from agent_collab_evals.compute_backend import (
    ComputeExecutionRequest,
    ComputeExecutionStatus,
    FrozenComputeRunManifest,
)
from agent_collab_evals.evaluation import (
    EvaluationReservation,
    EvaluationReservationStatus,
    EvaluationScope,
)
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


class CompositeHiddenRealAdapterTests(unittest.TestCase):
    def test_all_three_real_phase_adapters_execute_and_reconcile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            campaign, bundle, policy = real_hidden_quality_bundle(root / "bundle")
            reference = campaign.reference_candidate_path.read_bytes()
            candidate_document = parse_json(reference.decode("utf-8"))
            candidate_document["candidate_id"] = "composite-candidate"
            candidate_document["server"]["engine_args"]["stream_interval"] = 2
            candidate = canonical_json_bytes(candidate_document)
            candidate_descriptor = campaign.validate_candidate_document(
                candidate_document
            )
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
            performance_modal = ModalVllmHiddenPerformanceProfile.create(
                profile_id="composite-performance-modal-v0",
                campaign=campaign,
                campaign_manifest=campaign_manifest,
                hidden_workload=bundle,
                scoring_profile=(
                    REPOSITORY_ROOT
                    / "campaigns/model_serving_v0/evaluator/scoring_hidden_v1.toml"
                ),
                modal_script=modal_script,
                modal_environment="dev",
                modal_client_version="1.5.4",
                repetition=1,
                attempt=1,
                maximum_collection_seconds=300,
                evidence_volume="agent-collab-evals-evaluator-evidence-v2",
            )

            correctness_state = root / "correctness-state"
            quality_state = root / "quality-state"
            performance_state = root / "performance-state"
            correctness_transport_digest = digest_value(
                {"transport": "composite-correctness"}
            )
            quality_transport_digest = digest_value(
                {"transport": "composite-quality"}
            )
            performance_transport_digest = digest_value(
                {"transport": "composite-performance"}
            )
            correctness_resolver = ModalVllmCorrectnessEvidenceResolver(
                correctness_modal,
                correctness_state,
                correctness_transport_digest,
            )
            quality_resolver = ModalVllmQualityEvidenceResolver(
                quality_modal, quality_state, quality_transport_digest
            )
            performance_resolver = ModalVllmHiddenPerformanceEvidenceResolver(
                performance_modal,
                REPOSITORY_ROOT,
                performance_state,
                performance_transport_digest,
            )
            correctness_backend_digest = SqliteComputeBackend.profile_digest_for(
                correctness_transport_digest, correctness_resolver.profile_digest
            )
            quality_backend_digest = SqliteComputeBackend.profile_digest_for(
                quality_transport_digest, quality_resolver.profile_digest
            )
            performance_backend_digest = SqliteComputeBackend.profile_digest_for(
                performance_transport_digest, performance_resolver.profile_digest
            )
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
            performance_profile = ComputeCandidateEvaluationProfile(
                profile_id="composite-performance-v0",
                phase="performance",
                campaign_manifest_digest=campaign.manifest_digest,
                hidden_workload_manifest_digest=bundle.manifest_digest,
                workload_digest=performance_modal.performance_profile_digest,
                compute_execution_profile_digest=performance_backend_digest,
                maximum_collection_seconds=300,
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
                    performance_profile.digest,
                    performance_modal.performance_profile_digest,
                    1_800,
                ),
            )
            outer = EvaluationReservation(
                reservation_id="evaluation-" + "a" * 32,
                reservation_key="hidden:composite",
                campaign_run_id="composite-real-run",
                actor_id=None,
                artifact_ref=ArtifactRef("artifact-" + "8" * 32),
                scope=EvaluationScope.HIDDEN,
                reserved_seconds=composite_profile.reserved_seconds,
                status=EvaluationReservationStatus.RESERVED,
            )
            phase_reservations = {
                phase.name: _phase_reservation(composite_profile, outer, phase)
                for phase in composite_profile.phases
            }
            correctness_request = _request(
                "hidden:composite:correctness",
                phase_reservations["correctness"],
                candidate,
                candidate_descriptor.manifest_digest,
                correctness_profile.digest,
            )
            performance_request = _request(
                "hidden:composite:performance",
                phase_reservations["performance"],
                candidate,
                candidate_descriptor.manifest_digest,
                performance_profile.digest,
            )
            quality_requests = []
            quality_reservation = phase_reservations["quality"]
            reference_descriptor = campaign.validate_reference_candidate()
            for repetition in range(1, 4):
                for role, run_candidate, descriptor in (
                    ("reference", reference, reference_descriptor),
                    ("candidate", candidate, candidate_descriptor),
                ):
                    run_reservation = _quality_run_reservation(
                        quality_series_profile,
                        quality_reservation,
                        role,
                        repetition,
                    )
                    quality_requests.append(
                        _request(
                            "hidden:composite:quality:quality:"
                            f"{repetition}:{role}",
                            run_reservation,
                            run_candidate,
                            descriptor.manifest_digest,
                            quality_repetition_profile.digest,
                        )
                    )

            correctness_authority = FrozenComputeRunManifest.load_or_create(
                root / "correctness-manifest.json",
                campaign_run_id=outer.campaign_run_id,
                compute_enabled=True,
                transport_profile_digest=correctness_transport_digest,
                backend_profile_digest=correctness_backend_digest,
                requests=(correctness_request,),
            )
            quality_authority = FrozenComputeRunManifest.load_or_create(
                root / "quality-manifest.json",
                campaign_run_id=outer.campaign_run_id,
                compute_enabled=True,
                transport_profile_digest=quality_transport_digest,
                backend_profile_digest=quality_backend_digest,
                requests=tuple(quality_requests),
            )
            performance_authority = FrozenComputeRunManifest.load_or_create(
                root / "performance-manifest.json",
                campaign_run_id=outer.campaign_run_id,
                compute_enabled=True,
                transport_profile_digest=performance_transport_digest,
                backend_profile_digest=performance_backend_digest,
                requests=(performance_request,),
            )
            correctness_transport = _RetainedCorrectnessTransport(
                correctness_state,
                correctness_modal,
                correctness_resolver,
                correctness_transport_digest,
            )
            quality_transport = _RetainedModalQualityTransport(
                quality_state,
                quality_modal,
                quality_resolver,
                quality_transport_digest,
                policy,
            )
            performance_transport = _RetainedPerformanceTransport(
                performance_state,
                performance_modal,
                performance_resolver,
                performance_transport_digest,
            )
            correctness_backend = SqliteComputeBackend(
                root / "correctness-compute.sqlite3",
                correctness_transport,
                correctness_resolver,
                correctness_authority,
            )
            quality_backend = SqliteComputeBackend(
                root / "quality-compute.sqlite3",
                quality_transport,
                quality_resolver,
                quality_authority,
            )
            performance_backend = SqliteComputeBackend(
                root / "performance-compute.sqlite3",
                performance_transport,
                performance_resolver,
                performance_authority,
            )
            correctness = ComputeCandidateEvaluator(
                root / "correctness.sqlite3",
                campaign,
                correctness_profile,
                correctness_backend,
            )
            quality_repetitions = ComputeQualityRepetitionBackend(
                root / "quality-repetitions.sqlite3",
                campaign,
                quality_repetition_profile,
                quality_backend,
            )
            quality = PairedQualitySeriesEvaluator(
                root / "quality-series.sqlite3",
                quality_series_profile,
                policy,
                reference,
                quality_repetitions,
            )
            performance = ComputeCandidateEvaluator(
                root / "performance.sqlite3",
                campaign,
                performance_profile,
                performance_backend,
            )
            composite = CompositeHiddenServingEvaluator(
                root / "composite.sqlite3",
                composite_profile,
                {
                    "correctness": correctness,
                    "quality": quality,
                    "performance": performance,
                },
            )

            receipt = composite.hidden_evaluate(
                candidate, outer, "hidden:composite"
            )
            result = composite.resolve(
                receipt, candidate, outer, EvaluationScope.HIDDEN
            )

            self.assertTrue(result.eligible)
            self.assertEqual(result.criterion_units, 1_001_000)
            self.assertEqual(composite.used_seconds(receipt), 81)
            self.assertEqual(len(correctness_backend.reconcile(outer.campaign_run_id)), 1)
            self.assertEqual(len(quality_backend.reconcile(outer.campaign_run_id)), 6)
            self.assertEqual(len(performance_backend.reconcile(outer.campaign_run_id)), 1)
            self.assertTrue(
                all(
                    item.status is ComputeExecutionStatus.COMPLETE
                    for backend in (
                        correctness_backend,
                        quality_backend,
                        performance_backend,
                    )
                    for item in backend.reconcile(outer.campaign_run_id)
                )
            )


def _phase_reservation(
    profile: CompositeHiddenEvaluationProfile,
    outer: EvaluationReservation,
    phase: HiddenEvaluationPhaseProfile,
) -> EvaluationReservation:
    return EvaluationReservation(
        reservation_id="evaluation-"
        + digest_value(
            {
                "profile_digest": profile.digest,
                "outer_reservation_id": outer.reservation_id,
                "phase_digest": phase.digest,
            }
        )[7:39],
        reservation_key=f"{outer.reservation_key}:{phase.name}",
        campaign_run_id=outer.campaign_run_id,
        actor_id=None,
        artifact_ref=outer.artifact_ref,
        scope=EvaluationScope.HIDDEN,
        reserved_seconds=phase.reserved_seconds,
        status=outer.status,
    )


def _quality_run_reservation(
    profile: QualitySeriesProfile,
    outer: EvaluationReservation,
    role: str,
    repetition: int,
) -> EvaluationReservation:
    return EvaluationReservation(
        reservation_id="evaluation-"
        + digest_value(
            {
                "profile_digest": profile.digest,
                "outer_reservation_id": outer.reservation_id,
                "role": role,
                "repetition": repetition,
            }
        )[7:39],
        reservation_key=f"{outer.reservation_key}:{role}:{repetition}",
        campaign_run_id=outer.campaign_run_id,
        actor_id=None,
        artifact_ref=outer.artifact_ref,
        scope=EvaluationScope.HIDDEN,
        reserved_seconds=profile.repetition_reserved_seconds,
        status=outer.status,
    )


def _request(
    execution_key: str,
    reservation: EvaluationReservation,
    candidate: bytes,
    candidate_manifest_digest: str,
    evaluator_profile_digest: str,
) -> ComputeExecutionRequest:
    return ComputeExecutionRequest(
        execution_key=execution_key,
        campaign_run_id=reservation.campaign_run_id,
        reservation_id=reservation.reservation_id,
        scope=EvaluationScope.HIDDEN,
        candidate_digest=digest_bytes(candidate),
        candidate_manifest_digest=candidate_manifest_digest,
        evaluator_profile_digest=evaluator_profile_digest,
        maximum_seconds=reservation.reserved_seconds,
    )
