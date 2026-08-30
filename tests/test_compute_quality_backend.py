from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from agent_collab_evals.adapters.compute_quality_backend import (
    ComputeQualityRepetitionBackend,
    ComputeQualityRepetitionProfile,
)
from agent_collab_evals.adapters.sqlite_execution_backend import (
    SqliteComputeBackend,
)
from agent_collab_evals.artifacts import ArtifactRef
from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.campaigns.serving_quality import QualityPolicy
from agent_collab_evals.canonical import canonical_json_bytes, digest_bytes, digest_value
from agent_collab_evals.compute_backend import (
    ComputeEvidencePointer,
    ComputeExecutionReceipt,
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


class _QualityComputeBackend:
    def __init__(self) -> None:
        self.profile_digest = digest_value({"compute": "quality-fake-v1"})
        self.requests: dict[str, ComputeExecutionRequest] = {}
        self.tamper_identity = False

    def submit(self, request, candidate):
        self.requests[request.execution_key] = request
        return self._receipt(request)

    def collect(self, request, *, timeout_seconds):
        return self._receipt(request)

    def resolve(self, request):
        role = request.execution_key.rsplit(":", 1)[1]
        repetition = int(request.execution_key.rsplit(":", 2)[1])
        campaign_digest = (
            "sha256:" + "0" * 64
            if self.tamper_identity
            else CAMPAIGN_DIGEST
        )
        return self._receipt(request), {
            "result": {
                "quality_evaluation": {
                    "schema_version": (
                        "serving-quality-compute-evidence/v0alpha1"
                    ),
                    "campaign_manifest_digest": campaign_digest,
                    "hidden_workload_manifest_digest": HIDDEN_DIGEST,
                    "quality_profile_digest": QUALITY_PROFILE_DIGEST,
                    "quality_workload_digest": QUALITY_WORKLOAD_DIGEST,
                    "candidate_digest": request.candidate_digest,
                    "candidate_manifest_digest": (
                        request.candidate_manifest_digest
                    ),
                    "role": role,
                    "repetition": repetition,
                    "run": {
                        "role": role,
                        "repetition": repetition,
                        "evidence": "normalized-fake",
                    },
                }
            }
        }

    def reconcile(self, campaign_run_id):
        return tuple(self._receipt(request) for request in self.requests.values())

    @staticmethod
    def _receipt(request):
        return ComputeExecutionReceipt(
            execution_id="execution-" + request.request_digest[7:39],
            execution_key=request.execution_key,
            request_digest=request.request_digest,
            status=ComputeExecutionStatus.COMPLETE,
            external_call_id="fc-quality",
            evidence=ComputeEvidencePointer(
                "quality-evidence", "sha256:" + "6" * 64
            ),
            used_seconds=17,
            failure=None,
        )


class _DurableQualityEvidence:
    def __init__(self) -> None:
        self.profile_digest = digest_value({"evidence": "quality-integration-v1"})
        self.documents: dict[str, bytes] = {}
        self.dispatches: dict[str, bytes] = {}

    def resolve(self, pointer: ComputeEvidencePointer) -> bytes:
        return self.documents[pointer.locator]

    def resolve_dispatch(
        self, request: ComputeExecutionRequest, external_call_id: str
    ) -> bytes:
        return self.dispatches[external_call_id]


class _DurableQualityTransport:
    def __init__(self, evidence: _DurableQualityEvidence) -> None:
        self.profile_digest = digest_value({"transport": "quality-integration-v1"})
        self.evidence = evidence
        self.dispatch_count = 0

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
        role = request.execution_key.rsplit(":", 1)[1]
        repetition = int(request.execution_key.rsplit(":", 2)[1])
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
            "used_seconds": 17,
            "failure": None,
            "result": {
                "quality_evaluation": {
                    "schema_version": (
                        "serving-quality-compute-evidence/v0alpha1"
                    ),
                    "campaign_manifest_digest": CAMPAIGN_DIGEST,
                    "hidden_workload_manifest_digest": HIDDEN_DIGEST,
                    "quality_profile_digest": QUALITY_PROFILE_DIGEST,
                    "quality_workload_digest": QUALITY_WORKLOAD_DIGEST,
                    "candidate_digest": request.candidate_digest,
                    "candidate_manifest_digest": (
                        request.candidate_manifest_digest
                    ),
                    "role": role,
                    "repetition": repetition,
                    "run": {
                        "role": role,
                        "repetition": repetition,
                        "evidence": "normalized-integration",
                    },
                }
            },
        }
        content = canonical_json_bytes(document)
        pointer = ComputeEvidencePointer(
            f"quality/{request.request_digest[7:]}.json",
            digest_bytes(content),
        )
        self.evidence.documents[pointer.locator] = content
        return TransportPoll(
            ComputeExecutionStatus.COMPLETE,
            pointer,
            17,
            None,
        )


CAMPAIGN = ModelServingCampaign.load(
    REPOSITORY_ROOT / "campaigns/model_serving_v0/campaign.toml"
)
CAMPAIGN_DIGEST = CAMPAIGN.manifest_digest
HIDDEN_DIGEST = digest_value({"hidden_workload": "v0"})
QUALITY_PROFILE_DIGEST = CAMPAIGN.transitive_digests["quality_profile"]
QUALITY_WORKLOAD_DIGEST = QualityPolicy.load(
    REPOSITORY_ROOT / "campaigns/model_serving_v0/evaluator/quality_policy.toml"
).quality_workload_digest


class ComputeQualityRepetitionBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.compute = _QualityComputeBackend()
        self.profile = ComputeQualityRepetitionProfile(
            profile_id="hidden-quality-compute-v0",
            campaign_manifest_digest=CAMPAIGN_DIGEST,
            hidden_workload_manifest_digest=HIDDEN_DIGEST,
            quality_profile_digest=QUALITY_PROFILE_DIGEST,
            quality_workload_digest=QUALITY_WORKLOAD_DIGEST,
            compute_execution_profile_digest=self.compute.profile_digest,
            repetitions=3,
            maximum_collection_seconds=300,
        )
        self.backend = self._backend()
        self.candidate = CAMPAIGN.reference_candidate_path.read_bytes()
        self.reservation = EvaluationReservation(
            reservation_id="evaluation-" + "7" * 32,
            reservation_key="hidden:quality:1:reference",
            campaign_run_id="campaign-run",
            actor_id=None,
            artifact_ref=ArtifactRef("artifact-" + "8" * 32),
            scope=EvaluationScope.HIDDEN,
            reserved_seconds=600,
            status=EvaluationReservationStatus.RESERVED,
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_maps_one_quality_run_to_durable_compute_evidence(self) -> None:
        key = "hidden:selection:quality:1:reference"
        receipt = self.backend.evaluate(
            self.candidate,
            self.reservation,
            key,
            role="reference",
            repetition=1,
        )
        run = self.backend.resolve(
            receipt,
            self.candidate,
            self.reservation,
            role="reference",
            repetition=1,
        )

        self.assertEqual(run["role"], "reference")
        self.assertEqual(run["repetition"], 1)
        self.assertEqual(self.backend.used_seconds(receipt), 17)
        request = self.compute.requests[key]
        self.assertEqual(request.scope, EvaluationScope.HIDDEN)
        self.assertEqual(request.maximum_seconds, 600)
        self.assertEqual(request.evaluator_profile_digest, self.profile.digest)

        restarted = self._backend()
        self.assertEqual(
            restarted.evaluate(
                self.candidate,
                self.reservation,
                key,
                role="reference",
                repetition=1,
            ),
            receipt,
        )
        self.assertEqual(
            restarted.resolve(
                receipt,
                self.candidate,
                self.reservation,
                role="reference",
                repetition=1,
            ),
            run,
        )

    def test_composes_with_real_durable_compute_envelope_and_reconciliation(
        self,
    ) -> None:
        evidence = _DurableQualityEvidence()
        transport = _DurableQualityTransport(evidence)
        durable_profile_digest = SqliteComputeBackend.profile_digest_for(
            transport.profile_digest, evidence.profile_digest
        )
        profile = ComputeQualityRepetitionProfile(
            profile_id="hidden-quality-durable-integration-v0",
            campaign_manifest_digest=CAMPAIGN_DIGEST,
            hidden_workload_manifest_digest=HIDDEN_DIGEST,
            quality_profile_digest=QUALITY_PROFILE_DIGEST,
            quality_workload_digest=QUALITY_WORKLOAD_DIGEST,
            compute_execution_profile_digest=durable_profile_digest,
            repetitions=3,
            maximum_collection_seconds=300,
        )
        key = "hidden:durable:quality:1:reference"
        descriptor = CAMPAIGN.validate_reference_candidate()
        request = ComputeExecutionRequest(
            execution_key=key,
            campaign_run_id=self.reservation.campaign_run_id,
            reservation_id=self.reservation.reservation_id,
            scope=EvaluationScope.HIDDEN,
            candidate_digest=digest_bytes(self.candidate),
            candidate_manifest_digest=descriptor.manifest_digest,
            evaluator_profile_digest=profile.digest,
            maximum_seconds=self.reservation.reserved_seconds,
        )
        manifest = FrozenComputeRunManifest.load_or_create(
            self.root / "durable-compute-manifest.json",
            campaign_run_id=self.reservation.campaign_run_id,
            compute_enabled=True,
            transport_profile_digest=transport.profile_digest,
            backend_profile_digest=durable_profile_digest,
            requests=(request,),
        )
        durable = SqliteComputeBackend(
            self.root / "durable-executions.sqlite3",
            transport,
            evidence,
            manifest,
        )
        adapter = ComputeQualityRepetitionBackend(
            self.root / "durable-quality.sqlite3",
            CAMPAIGN,
            profile,
            durable,
        )

        receipt = adapter.evaluate(
            self.candidate,
            self.reservation,
            key,
            role="reference",
            repetition=1,
        )
        run = adapter.resolve(
            receipt,
            self.candidate,
            self.reservation,
            role="reference",
            repetition=1,
        )

        self.assertEqual(run["evidence"], "normalized-integration")
        self.assertEqual(adapter.used_seconds(receipt), 17)
        self.assertEqual(transport.dispatch_count, 1)
        reconciled = durable.reconcile(self.reservation.campaign_run_id)
        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0].status, ComputeExecutionStatus.COMPLETE)

    def test_role_repetition_scope_and_evidence_identity_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "key differs"):
            self.backend.evaluate(
                self.candidate,
                self.reservation,
                "hidden:selection:quality:1:candidate",
                role="reference",
                repetition=1,
            )
        visible = EvaluationReservation(
            reservation_id="evaluation-" + "9" * 32,
            reservation_key="visible:quality",
            campaign_run_id="campaign-run",
            actor_id="actor-0",
            artifact_ref=self.reservation.artifact_ref,
            scope=EvaluationScope.VISIBLE,
            reserved_seconds=600,
            status=EvaluationReservationStatus.RESERVED,
        )
        with self.assertRaisesRegex(ValueError, "hidden reservation"):
            self.backend.evaluate(
                self.candidate,
                visible,
                "hidden:selection:quality:1:reference",
                role="reference",
                repetition=1,
            )

        self.compute.tamper_identity = True
        with self.assertRaisesRegex(RuntimeError, "identity differs"):
            self.backend.evaluate(
                self.candidate,
                self.reservation,
                "hidden:tampered:quality:1:reference",
                role="reference",
                repetition=1,
            )

    def test_receipt_ledger_and_backend_profile_tampering_fail_closed(self) -> None:
        key = "hidden:selection:quality:1:reference"
        receipt = self.backend.evaluate(
            self.candidate,
            self.reservation,
            key,
            role="reference",
            repetition=1,
        )
        with closing(sqlite3.connect(self.root / "quality.sqlite3")) as connection:
            connection.execute(
                "UPDATE compute_quality_receipts SET profile_digest = ?",
                ("sha256:" + "0" * 64,),
            )
            connection.commit()
        with self.assertRaisesRegex(RuntimeError, "binding differs"):
            self.backend.resolve(
                receipt,
                self.candidate,
                self.reservation,
                role="reference",
                repetition=1,
            )

        wrong_compute = _QualityComputeBackend()
        wrong_compute.profile_digest = digest_value({"compute": "wrong"})
        with self.assertRaisesRegex(ValueError, "backend differs"):
            ComputeQualityRepetitionBackend(
                self.root / "wrong.sqlite3",
                CAMPAIGN,
                self.profile,
                wrong_compute,
            )

    def _backend(self) -> ComputeQualityRepetitionBackend:
        return ComputeQualityRepetitionBackend(
            self.root / "quality.sqlite3",
            CAMPAIGN,
            self.profile,
            self.compute,
        )


if __name__ == "__main__":
    unittest.main()
