from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_collab_evals.adapters.compute_candidate_evaluator import (
    ComputeCandidateEvaluationProfile,
    ComputeCandidateEvaluator,
)
from agent_collab_evals.adapters.local_measurements import LocalMeasurementBundleStore
from agent_collab_evals.adapters.modal_vllm_compute import (
    ModalVllmCliTransport,
    _measurement_id,
)
from agent_collab_evals.adapters.modal_vllm_performance_compute import (
    ModalVllmHiddenPerformanceEvidenceResolver,
    ModalVllmHiddenPerformanceProfile,
)
from agent_collab_evals.adapters.sqlite_execution_backend import SqliteComputeBackend
from agent_collab_evals.artifacts import ArtifactRef
from agent_collab_evals.canonical import canonical_json_bytes, digest_bytes, digest_value
from agent_collab_evals.compute_backend import (
    ComputeExecutionRequest,
    ComputeExecutionStatus,
    ExternalDispatch,
    FrozenComputeRunManifest,
    TransportPoll,
)
from agent_collab_evals.evaluation import (
    EvaluationReservation,
    EvaluationReservationStatus,
    EvaluationScope,
)
from tests.quality_fixture import REPOSITORY_ROOT, real_hidden_quality_bundle


class _SpendAuthorization:
    profile_digest = digest_value({"spend": "hidden-performance-test"})

    def consume(self, request, transport_profile_digest):
        return None


class _RetainedPerformanceTransport:
    def __init__(
        self,
        state_root: Path,
        profile: ModalVllmHiddenPerformanceProfile,
        resolver: ModalVllmHiddenPerformanceEvidenceResolver,
        profile_digest: str,
    ) -> None:
        self.state_root = state_root
        self.profile = profile
        self.resolver = resolver
        self.profile_digest = profile_digest
        self.dispatch_count = 0

    def dispatch(
        self, request: ComputeExecutionRequest, candidate: bytes
    ) -> ExternalDispatch:
        self.dispatch_count += 1
        measurement_id = _measurement_id(request)
        call_id = "fc-" + request.request_digest[7:23]
        request_path = (
            self.state_root / "requests" / f"{request.request_digest[7:]}.json"
        )
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_bytes(
            canonical_json_bytes(
                {
                    "schema_version": "modal-vllm-compute-request/v0alpha1",
                    "request": request.document,
                    "transport_profile_digest": self.profile_digest,
                }
            )
        )
        dispatch = {
            "measurement_id": measurement_id,
            "campaign_manifest_digest": self.profile.campaign_manifest_digest,
            "performance_profile_digest": self.profile.performance_profile_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "repetition": self.profile.repetition,
            "attempt": self.profile.attempt,
            "function_call_id": call_id,
            "git_commit": "f" * 40,
        }
        dispatch_path = (
            self.state_root
            / "measurements/.dispatch"
            / measurement_id
            / "repetition-0001-attempt-01.json"
        )
        dispatch_path.parent.mkdir(parents=True, exist_ok=True)
        dispatch_path.write_bytes(canonical_json_bytes(dispatch))
        normalized = {
            "campaign_manifest_digest": self.profile.campaign_manifest_digest,
            "performance_profile_digest": self.profile.performance_profile_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "candidate_id": "stock-vllm-0.21.0",
            "modal_function_call_id": call_id,
            "repetition": self.profile.repetition,
            "attempt": self.profile.attempt,
            "valid": True,
            "failure": None,
            "parse_errors": [],
            "environment_errors": [],
            "remote_receipt": {"timing": {"function_body_ms": 21_001}},
            "performance_score": {
                "eligible": True,
                "scalar_ppm": 1_001_000,
                "failures": [],
            },
            "measurement_profile_digest": digest_value({"measurement": "test"}),
            "scoring_profile_digest": digest_value({"scoring": "test"}),
            "platform_build": {
                "git_commit": "f" * 40,
                "modal_client_version": self.profile.modal_client_version,
            },
            "durable_evidence": {"volume_name": self.profile.evidence_volume},
        }
        normalized["durable_evidence"]["normalized_digest"] = digest_bytes(
            json.dumps(
                normalized,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
        LocalMeasurementBundleStore(self.state_root / "measurements").save(
            measurement_id,
            self.profile.repetition,
            normalized,
            {"point.json": b'{"ok":true}'},
            attempt=self.profile.attempt,
        )
        evidence = self.resolver.resolve_dispatch(request, call_id)
        return ExternalDispatch(call_id, digest_bytes(evidence))

    def poll(
        self,
        request: ComputeExecutionRequest,
        external_call_id: str,
        timeout_seconds: int,
    ) -> TransportPoll:
        pointer, status, used_seconds, failure = self.resolver.pointer(
            request, external_call_id
        )
        return TransportPoll(status, pointer, used_seconds, failure)


class ModalHiddenPerformanceComputeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.campaign, self.bundle, _ = real_hidden_quality_bundle(
            self.root / "bundle"
        )
        self.modal_profile = ModalVllmHiddenPerformanceProfile.create(
            profile_id="modal-hidden-performance-test-v0",
            campaign=self.campaign,
            campaign_manifest=(
                REPOSITORY_ROOT / "campaigns/model_serving_v0/campaign.toml"
            ),
            hidden_workload=self.bundle,
            modal_script=(
                REPOSITORY_ROOT
                / "campaigns/model_serving_v0/reference/modal_vllm.py"
            ),
            modal_environment="dev",
            modal_client_version="1.5.4",
            repetition=1,
            attempt=1,
            maximum_collection_seconds=300,
            evidence_volume="agent-collab-evals-evaluator-evidence-v2",
        )
        self.candidate = self.campaign.reference_candidate_path.read_bytes()
        self.reservation = EvaluationReservation(
            reservation_id="evaluation-" + "d" * 32,
            reservation_key="hidden:performance",
            campaign_run_id="hidden-performance-run",
            actor_id=None,
            artifact_ref=ArtifactRef("artifact-" + "e" * 32),
            scope=EvaluationScope.HIDDEN,
            reserved_seconds=1_800,
            status=EvaluationReservationStatus.RESERVED,
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_profile_uses_the_real_disjoint_hidden_performance_plan(self) -> None:
        self.assertEqual(
            self.modal_profile.performance_profile_digest,
            self.bundle.resource_digests["performance_profile"],
        )
        self.assertNotEqual(
            self.modal_profile.performance_profile_digest,
            self.campaign.transitive_digests["public_profile"],
        )
        self.modal_profile.validate_inputs(self.campaign)

    def test_existing_modal_transport_builds_the_hidden_profile_command(self) -> None:
        evaluator_digest = digest_value({"evaluator": "hidden-performance"})
        transport = ModalVllmCliTransport(
            self.modal_profile,
            REPOSITORY_ROOT,
            self.root / "command-state",
            REPOSITORY_ROOT / ".venv/bin/modal",
            _SpendAuthorization(),
            evaluator_profile_digest=evaluator_digest,
        )
        command = transport._command(
            self.root / "candidate.json",
            "hidden-performance",
            dispatch_only=True,
        )
        profile_index = command.index("--performance-profile-path")
        self.assertEqual(
            command[profile_index + 1], str(self.modal_profile.performance_profile)
        )
        self.assertIn("--dispatch-only", command)

    def test_hidden_performance_composes_with_durable_candidate_evaluator(
        self,
    ) -> None:
        transport_digest = digest_value({"transport": "hidden-performance"})
        state_root = self.root / "durable-state"
        resolver = ModalVllmHiddenPerformanceEvidenceResolver(
            self.modal_profile, REPOSITORY_ROOT, state_root, transport_digest
        )
        backend_digest = SqliteComputeBackend.profile_digest_for(
            transport_digest, resolver.profile_digest
        )
        profile = ComputeCandidateEvaluationProfile(
            profile_id="hidden-performance-candidate-v0",
            phase="performance",
            campaign_manifest_digest=self.campaign.manifest_digest,
            hidden_workload_manifest_digest=self.bundle.manifest_digest,
            workload_digest=self.modal_profile.performance_profile_digest,
            compute_execution_profile_digest=backend_digest,
            maximum_collection_seconds=300,
        )
        descriptor = self.campaign.validate_reference_candidate()
        request = ComputeExecutionRequest(
            execution_key="hidden:selection:performance",
            campaign_run_id=self.reservation.campaign_run_id,
            reservation_id=self.reservation.reservation_id,
            scope=EvaluationScope.HIDDEN,
            candidate_digest=digest_bytes(self.candidate),
            candidate_manifest_digest=descriptor.manifest_digest,
            evaluator_profile_digest=profile.digest,
            maximum_seconds=self.reservation.reserved_seconds,
        )
        manifest = FrozenComputeRunManifest.load_or_create(
            self.root / "compute-manifest.json",
            campaign_run_id=self.reservation.campaign_run_id,
            compute_enabled=True,
            transport_profile_digest=transport_digest,
            backend_profile_digest=backend_digest,
            requests=(request,),
        )
        transport = _RetainedPerformanceTransport(
            state_root,
            self.modal_profile,
            resolver,
            transport_digest,
        )
        durable = SqliteComputeBackend(
            self.root / "compute.sqlite3", transport, resolver, manifest
        )
        evaluator = ComputeCandidateEvaluator(
            self.root / "performance.sqlite3",
            self.campaign,
            profile,
            durable,
        )

        receipt = evaluator.hidden_evaluate(
            self.candidate, self.reservation, "hidden:selection:performance"
        )
        result = evaluator.resolve(
            receipt,
            self.candidate,
            self.reservation,
            EvaluationScope.HIDDEN,
        )

        self.assertTrue(result.eligible)
        self.assertEqual(result.criterion_units, 1_001_000)
        self.assertEqual(evaluator.used_seconds(receipt), 22)
        self.assertEqual(transport.dispatch_count, 1)
        self.assertEqual(
            durable.reconcile(self.reservation.campaign_run_id)[0].status,
            ComputeExecutionStatus.COMPLETE,
        )


if __name__ == "__main__":
    unittest.main()
