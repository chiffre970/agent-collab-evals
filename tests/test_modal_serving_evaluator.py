from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from agent_collab_evals.adapters.modal_serving_evaluator import (
    ModalServingDevelopmentEvaluator,
)
from agent_collab_evals.adapters.modal_vllm_compute import ModalVllmComputeProfile
from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.canonical import digest_value
from agent_collab_evals.compute_backend import (
    ComputeEvidencePointer,
    ComputeExecutionReceipt,
    ComputeExecutionRequest,
    ComputeExecutionStatus,
)
from agent_collab_evals.evaluation import (
    EvaluationReservation,
    EvaluationReservationStatus,
    EvaluationScope,
)
from agent_collab_evals.artifacts import ArtifactRef


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class _CompleteBackend:
    def __init__(self) -> None:
        self.requests: dict[str, ComputeExecutionRequest] = {}
        self.submit_count = 0
        self._profile_digest = digest_value({"backend": "complete-fake"})

    @property
    def profile_digest(self) -> str:
        return self._profile_digest

    def submit(self, request, candidate):
        self.submit_count += 1
        self.requests[request.execution_key] = request
        return self._receipt(request)

    def collect(self, request, *, timeout_seconds):
        return self._receipt(request)

    def resolve(self, request):
        receipt = self._receipt(request)
        return receipt, {
            "result": {
                "valid": True,
                "candidate_id": "stock-vllm-0.21.0",
                "modal_function_call_id": receipt.external_call_id,
                "measurement_profile_digest": "sha256:" + "4" * 64,
                "scoring_profile_digest": "sha256:" + "5" * 64,
                "performance_score": {
                    "eligible": True,
                    "scalar_ppm": 1_004_000,
                    "failures": [],
                },
            }
        }

    def reconcile(self, campaign_run_id):
        return tuple(self._receipt(value) for value in self.requests.values())

    @staticmethod
    def _receipt(request):
        return ComputeExecutionReceipt(
            execution_id="execution-" + request.request_digest[7:39],
            execution_key=request.execution_key,
            request_digest=request.request_digest,
            status=ComputeExecutionStatus.COMPLETE,
            external_call_id="fc-complete",
            evidence=ComputeEvidencePointer(
                "fake-evidence", "sha256:" + "6" * 64
            ),
            used_seconds=11,
            failure=None,
        )


class _DispatchingThenCompleteBackend(_CompleteBackend):
    def __init__(self) -> None:
        super().__init__()
        self._first = True

    def submit(self, request, candidate):
        self.submit_count += 1
        self.requests[request.execution_key] = request
        if self._first:
            self._first = False
            receipt = self._receipt(request)
            return ComputeExecutionReceipt(
                execution_id=receipt.execution_id,
                execution_key=receipt.execution_key,
                request_digest=receipt.request_digest,
                status=ComputeExecutionStatus.DISPATCHING,
                external_call_id=None,
                evidence=None,
                used_seconds=None,
                failure=None,
            )
        return self._receipt(request)


class ModalServingEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.campaign = ModelServingCampaign.load(
            REPOSITORY_ROOT / "campaigns/model_serving_v0/campaign.toml"
        )
        self.profile = ModalVllmComputeProfile.load(
            REPOSITORY_ROOT / "config/compute/modal-vllm-development.json",
            repository_root=REPOSITORY_ROOT,
        )
        self.backend = _CompleteBackend()
        self.evaluator = ModalServingDevelopmentEvaluator(
            self.root / "evaluator.sqlite3",
            self.campaign,
            self.profile,
            self.backend,
        )
        self.candidate = self.campaign.reference_candidate_path.read_bytes()
        self.reservation = EvaluationReservation(
            reservation_id="evaluation-" + "7" * 32,
            reservation_key="candidate:test",
            campaign_run_id="campaign-run",
            actor_id="actor-0",
            artifact_ref=ArtifactRef("artifact-" + "8" * 32),
            scope=EvaluationScope.VISIBLE,
            reserved_seconds=60,
            status=EvaluationReservationStatus.RESERVED,
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_visible_result_is_resolved_from_compute_evidence(self) -> None:
        receipt = self.evaluator.visible_evaluate(
            self.candidate, self.reservation, "visible:candidate-test"
        )
        result = self.evaluator.resolve(
            receipt,
            self.candidate,
            self.reservation,
            EvaluationScope.VISIBLE,
        )
        self.assertTrue(result.eligible)
        self.assertEqual(result.criterion_units, 1_004_000)
        self.assertEqual(self.evaluator.used_seconds(receipt), 11)
        self.assertEqual(self.backend.submit_count, 1)
        self.assertEqual(
            self.evaluator.visible_evaluate(
                self.candidate, self.reservation, "visible:candidate-test"
            ),
            receipt,
        )

    def test_hidden_workload_fails_closed(self) -> None:
        hidden = EvaluationReservation(
            reservation_id="evaluation-" + "9" * 32,
            reservation_key="hidden:test",
            campaign_run_id="campaign-run",
            actor_id=None,
            artifact_ref=self.reservation.artifact_ref,
            scope=EvaluationScope.HIDDEN,
            reserved_seconds=60,
            status=EvaluationReservationStatus.RESERVED,
        )
        with self.assertRaisesRegex(RuntimeError, "no hidden workload"):
            self.evaluator.hidden_evaluate(
                self.candidate, hidden, "hidden:selection-test"
            )

    def test_dispatching_observation_waits_instead_of_failing(self) -> None:
        backend = _DispatchingThenCompleteBackend()
        evaluator = ModalServingDevelopmentEvaluator(
            self.root / "dispatching-evaluator.sqlite3",
            self.campaign,
            self.profile,
            backend,
        )
        receipt = evaluator.visible_evaluate(
            self.candidate,
            self.reservation,
            "visible:dispatching-test",
        )
        self.assertTrue(
            evaluator.resolve(
                receipt,
                self.candidate,
                self.reservation,
                EvaluationScope.VISIBLE,
            ).eligible
        )
        self.assertEqual(backend.submit_count, 2)

    def test_evaluator_receipt_binding_tampering_is_detected(self) -> None:
        receipt = self.evaluator.visible_evaluate(
            self.candidate, self.reservation, "visible:tamper-test"
        )
        with closing(sqlite3.connect(self.root / "evaluator.sqlite3")) as connection:
            connection.execute(
                "UPDATE modal_evaluation_receipts SET candidate_digest = ?",
                ("sha256:" + "0" * 64,),
            )
            connection.commit()
        with self.assertRaisesRegex(RuntimeError, "binding differs"):
            self.evaluator.resolve(
                receipt,
                self.candidate,
                self.reservation,
                EvaluationScope.VISIBLE,
            )


if __name__ == "__main__":
    unittest.main()
