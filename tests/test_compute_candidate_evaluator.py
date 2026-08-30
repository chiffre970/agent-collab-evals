from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_collab_evals.adapters.compute_candidate_evaluator import (
    ComputeCandidateEvaluationProfile,
    ComputeCandidateEvaluator,
)
from agent_collab_evals.adapters.sqlite_execution_backend import SqliteComputeBackend
from agent_collab_evals.artifacts import ArtifactRef
from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.canonical import canonical_json_bytes, digest_bytes, digest_value
from agent_collab_evals.compute_backend import (
    ComputeEvidencePointer,
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


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ModelServingCampaign.load(
    REPOSITORY_ROOT / "campaigns/model_serving_v0/campaign.toml"
)


class _CandidateEvidence:
    profile_digest = digest_value({"evidence": "candidate-phase-v0"})

    def __init__(self) -> None:
        self.documents: dict[str, bytes] = {}
        self.dispatches: dict[str, bytes] = {}

    def resolve(self, pointer: ComputeEvidencePointer) -> bytes:
        return self.documents[pointer.locator]

    def resolve_dispatch(
        self, request: ComputeExecutionRequest, external_call_id: str
    ) -> bytes:
        return self.dispatches[external_call_id]


class _CandidateTransport:
    profile_digest = digest_value({"transport": "candidate-phase-v0"})

    def __init__(
        self,
        evidence: _CandidateEvidence,
        *,
        phase: str,
        campaign_digest: str,
        hidden_digest: str,
        workload_digest: str,
    ) -> None:
        self.evidence = evidence
        self.phase = phase
        self.campaign_digest = campaign_digest
        self.hidden_digest = hidden_digest
        self.workload_digest = workload_digest
        self.dispatch_count = 0
        self.tamper_result_digest = False

    def dispatch(
        self, request: ComputeExecutionRequest, candidate: bytes
    ) -> ExternalDispatch:
        self.dispatch_count += 1
        call_id = "fc-" + request.request_digest[7:23]
        dispatch = canonical_json_bytes(
            {
                "request_digest": request.request_digest,
                "candidate_digest": digest_bytes(candidate),
                "external_call_id": call_id,
            }
        )
        self.evidence.dispatches[call_id] = dispatch
        return ExternalDispatch(call_id, digest_bytes(dispatch))

    def poll(
        self,
        request: ComputeExecutionRequest,
        external_call_id: str,
        timeout_seconds: int,
    ) -> TransportPoll:
        result = {
            "schema_version": "serving-candidate-compute-evidence/v0alpha1",
            "phase": self.phase,
            "campaign_manifest_digest": self.campaign_digest,
            "hidden_workload_manifest_digest": self.hidden_digest,
            "workload_digest": self.workload_digest,
            "candidate_digest": request.candidate_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "eligible": True,
            "criterion_units": 970_000,
            "failures": [],
            "diagnostics": {"source": "durable-compute-test"},
        }
        result["result_evidence_digest"] = (
            "sha256:" + "0" * 64
            if self.tamper_result_digest
            else digest_value(result)
        )
        document = {
            "schema_version": "compute-execution-evidence/v0alpha1",
            "request_digest": request.request_digest,
            "candidate_digest": request.candidate_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "evaluator_profile_digest": request.evaluator_profile_digest,
            "transport_profile_digest": self.profile_digest,
            "evidence_profile_digest": self.evidence.profile_digest,
            "external_call_id": external_call_id,
            "status": "complete",
            "used_seconds": 19,
            "failure": None,
            "result": {"candidate_evaluation": result},
        }
        content = canonical_json_bytes(document)
        pointer = ComputeEvidencePointer(
            "candidate/" + request.request_digest[7:] + ".json",
            digest_bytes(content),
        )
        self.evidence.documents[pointer.locator] = content
        return TransportPoll(ComputeExecutionStatus.COMPLETE, pointer, 19, None)


class ComputeCandidateEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.hidden_digest = digest_value({"hidden": "candidate-phase"})
        self.workload_digest = digest_value({"workload": "correctness"})
        self.evidence = _CandidateEvidence()
        self.transport = _CandidateTransport(
            self.evidence,
            phase="correctness",
            campaign_digest=CAMPAIGN.manifest_digest,
            hidden_digest=self.hidden_digest,
            workload_digest=self.workload_digest,
        )
        backend_digest = SqliteComputeBackend.profile_digest_for(
            self.transport.profile_digest, self.evidence.profile_digest
        )
        self.profile = ComputeCandidateEvaluationProfile(
            profile_id="hidden-correctness-compute-v0",
            phase="correctness",
            campaign_manifest_digest=CAMPAIGN.manifest_digest,
            hidden_workload_manifest_digest=self.hidden_digest,
            workload_digest=self.workload_digest,
            compute_execution_profile_digest=backend_digest,
            maximum_collection_seconds=300,
        )
        self.candidate = CAMPAIGN.reference_candidate_path.read_bytes()
        self.reservation = EvaluationReservation(
            reservation_id="evaluation-" + "4" * 32,
            reservation_key="hidden:correctness",
            campaign_run_id="candidate-phase-run",
            actor_id=None,
            artifact_ref=ArtifactRef("artifact-" + "5" * 32),
            scope=EvaluationScope.HIDDEN,
            reserved_seconds=600,
            status=EvaluationReservationStatus.RESERVED,
        )
        descriptor = CAMPAIGN.validate_reference_candidate()
        self.request = ComputeExecutionRequest(
            execution_key="hidden:selection:correctness",
            campaign_run_id=self.reservation.campaign_run_id,
            reservation_id=self.reservation.reservation_id,
            scope=EvaluationScope.HIDDEN,
            candidate_digest=digest_bytes(self.candidate),
            candidate_manifest_digest=descriptor.manifest_digest,
            evaluator_profile_digest=self.profile.digest,
            maximum_seconds=self.reservation.reserved_seconds,
        )
        self.manifest = FrozenComputeRunManifest.load_or_create(
            self.root / "compute-manifest.json",
            campaign_run_id=self.reservation.campaign_run_id,
            compute_enabled=True,
            transport_profile_digest=self.transport.profile_digest,
            backend_profile_digest=backend_digest,
            requests=(self.request,),
        )
        self.durable = SqliteComputeBackend(
            self.root / "compute.sqlite3",
            self.transport,
            self.evidence,
            self.manifest,
        )
        self.evaluator = ComputeCandidateEvaluator(
            self.root / "candidate.sqlite3", CAMPAIGN, self.profile, self.durable
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_composes_hidden_candidate_result_with_durable_compute(self) -> None:
        receipt = self.evaluator.hidden_evaluate(
            self.candidate, self.reservation, "hidden:selection:correctness"
        )
        result = self.evaluator.resolve(
            receipt,
            self.candidate,
            self.reservation,
            EvaluationScope.HIDDEN,
        )

        self.assertTrue(result.eligible)
        self.assertEqual(result.criterion_units, 970_000)
        self.assertEqual(result.diagnostics["source"], "durable-compute-test")
        self.assertEqual(self.evaluator.used_seconds(receipt), 19)
        self.assertEqual(self.transport.dispatch_count, 1)
        self.assertEqual(
            self.durable.reconcile(self.reservation.campaign_run_id)[0].status,
            ComputeExecutionStatus.COMPLETE,
        )

        restarted = ComputeCandidateEvaluator(
            self.root / "candidate.sqlite3", CAMPAIGN, self.profile, self.durable
        )
        self.assertEqual(
            restarted.hidden_evaluate(
                self.candidate,
                self.reservation,
                "hidden:selection:correctness",
            ),
            receipt,
        )

    def test_wrong_scope_phase_and_result_digest_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "key differs"):
            self.evaluator.hidden_evaluate(
                self.candidate, self.reservation, "hidden:selection:performance"
            )
        with self.assertRaisesRegex(RuntimeError, "cannot serve visible"):
            self.evaluator.visible_evaluate(
                self.candidate, None, "visible:selection:correctness"
            )

        self.transport.tamper_result_digest = True
        with self.assertRaisesRegex(RuntimeError, "evidence digest differs"):
            self.evaluator.hidden_evaluate(
                self.candidate,
                self.reservation,
                "hidden:selection:correctness",
            )


if __name__ == "__main__":
    unittest.main()
