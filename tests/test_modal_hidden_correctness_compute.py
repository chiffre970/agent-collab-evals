from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from agent_collab_evals.adapters.compute_candidate_evaluator import (
    ComputeCandidateEvaluationProfile,
    ComputeCandidateEvaluator,
)
from agent_collab_evals.adapters.local_measurements import LocalMeasurementBundleStore
from agent_collab_evals.adapters.modal_vllm_correctness_compute import (
    ModalVllmCorrectnessCliTransport,
    ModalVllmCorrectnessEvidenceResolver,
    ModalVllmCorrectnessProfile,
    _dispatch_path,
    _measurement_id,
)
from agent_collab_evals.adapters.sqlite_execution_backend import SqliteComputeBackend
from agent_collab_evals.adapters.sqlite_compute_spend import (
    SqliteComputeSpendAuthorizationService,
)
from agent_collab_evals.artifacts import ArtifactRef
from agent_collab_evals.campaigns.serving_correctness import (
    load_correctness_workload,
    score_correctness_responses,
)
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
    profile_digest = digest_value({"spend": "hidden-correctness-test"})

    def consume(self, request, transport_profile_digest):
        return None


class _RetainedCorrectnessTransport:
    def __init__(
        self,
        state_root: Path,
        profile: ModalVllmCorrectnessProfile,
        resolver: ModalVllmCorrectnessEvidenceResolver,
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
            self.state_root
            / "correctness-requests"
            / f"{request.request_digest[7:]}.json"
        )
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_bytes(
            canonical_json_bytes(
                {
                    "schema_version": "modal-vllm-correctness-request/v0alpha1",
                    "request": request.document,
                    "transport_profile_digest": self.profile_digest,
                }
            )
        )
        dispatch = {
            "measurement_id": measurement_id,
            "campaign_manifest_digest": self.profile.campaign_manifest_digest,
            "hidden_workload_manifest_digest": (
                self.profile.hidden_workload_manifest_digest
            ),
            "correctness_profile_digest": self.profile.digest,
            "correctness_workload_digest": (
                self.profile.correctness_workload_digest
            ),
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "role": "candidate",
            "repetition": 1,
            "attempt": self.profile.attempt,
            "function_call_id": call_id,
            "git_commit": "f" * 40,
        }
        dispatch_path = _dispatch_path(
            self.state_root, measurement_id, self.profile.attempt
        )
        dispatch_path.parent.mkdir(parents=True, exist_ok=True)
        dispatch_path.write_bytes(canonical_json_bytes(dispatch))

        workload = load_correctness_workload(self.profile.correctness_workload)
        raw = {
            f"{case.case_id}.json": _response(_matching_content(case.expected))
            for case in workload.cases
        }
        scored = score_correctness_responses(
            workload,
            {case.case_id: raw[f"{case.case_id}.json"] for case in workload.cases},
            served_model_name="target-model",
        ).to_document()
        normalized = {
            "campaign_manifest_digest": self.profile.campaign_manifest_digest,
            "hidden_workload_manifest_digest": (
                self.profile.hidden_workload_manifest_digest
            ),
            "correctness_profile_digest": self.profile.digest,
            "correctness_workload_digest": (
                self.profile.correctness_workload_digest
            ),
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "candidate_id": "stock-vllm-0.21.0",
            "role": "candidate",
            "platform_build": {
                "git_commit": "f" * 40,
                "modal_client_version": self.profile.modal_client_version,
            },
            "modal_function_call_id": call_id,
            "durable_evidence": {"volume_name": self.profile.evidence_volume},
            "repetition": 1,
            "attempt": self.profile.attempt,
            "valid": True,
            "validation_errors": [],
            "client_observed_ms": 100,
            "remote_receipt": {"timing": {"function_body_ms": 10_001}},
            "correctness_result": scored,
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
        LocalMeasurementBundleStore(
            self.state_root / "correctness-measurements"
        ).save(measurement_id, 1, normalized, raw, attempt=self.profile.attempt)
        return ExternalDispatch(call_id, digest_bytes(canonical_json_bytes(dispatch)))

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


class ModalHiddenCorrectnessComputeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.campaign, self.bundle, _ = real_hidden_quality_bundle(
            self.root / "bundle"
        )
        self.modal_profile = ModalVllmCorrectnessProfile.create(
            profile_id="modal-hidden-correctness-test-v0",
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
            attempt=1,
            maximum_collection_seconds=300,
            evidence_volume="agent-collab-evals-evaluator-evidence-v2",
        )
        self.candidate = self.campaign.reference_candidate_path.read_bytes()
        self.reservation = EvaluationReservation(
            reservation_id="evaluation-" + "c" * 32,
            reservation_key="hidden:correctness",
            campaign_run_id="hidden-correctness-run",
            actor_id=None,
            artifact_ref=ArtifactRef("artifact-" + "e" * 32),
            scope=EvaluationScope.HIDDEN,
            reserved_seconds=1_800,
            status=EvaluationReservationStatus.RESERVED,
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_profile_factory_pins_the_real_hidden_correctness_workload(self) -> None:
        self.assertEqual(
            self.modal_profile.correctness_workload_digest,
            self.bundle.resource_digests["correctness_requests"],
        )
        self.assertNotEqual(
            self.modal_profile.correctness_workload_digest,
            self.campaign.transitive_digests["public_correctness"],
        )
        self.modal_profile.validate_inputs(self.campaign)

    def test_transport_builds_the_registered_correctness_command(self) -> None:
        evaluator_digest = digest_value({"evaluator": "hidden-correctness"})
        transport = ModalVllmCorrectnessCliTransport(
            self.modal_profile,
            REPOSITORY_ROOT,
            self.root / "command-state",
            REPOSITORY_ROOT / ".venv/bin/modal",
            _SpendAuthorization(),
            evaluator_profile_digest=evaluator_digest,
        )
        command = transport._command(
            self.root / "candidate.json",
            "hidden-correctness",
            "candidate",
            dispatch_only=True,
        )
        self.assertIn("--correctness", command)
        workload_index = command.index("--correctness-workload-path")
        self.assertEqual(
            command[workload_index + 1], str(self.modal_profile.correctness_workload)
        )
        self.assertIn("--dispatch-only", command)

    def test_correctness_composes_with_the_durable_compute_backend(self) -> None:
        state_root = self.root / "durable-state"
        spend = _SpendAuthorization()
        transport_digest = ModalVllmCorrectnessCliTransport.profile_digest_for(
            self.modal_profile.digest,
            REPOSITORY_ROOT / ".venv/bin/modal",
            spend.profile_digest,
        )
        resolver = ModalVllmCorrectnessEvidenceResolver(
            self.modal_profile, state_root, transport_digest
        )
        backend_digest = SqliteComputeBackend.profile_digest_for(
            transport_digest, resolver.profile_digest
        )
        evaluator_profile = ComputeCandidateEvaluationProfile(
            profile_id="hidden-correctness-candidate-v0",
            phase="correctness",
            campaign_manifest_digest=self.campaign.manifest_digest,
            hidden_workload_manifest_digest=self.bundle.manifest_digest,
            workload_digest=self.modal_profile.correctness_workload_digest,
            compute_execution_profile_digest=backend_digest,
            maximum_collection_seconds=300,
        )
        descriptor = self.campaign.validate_reference_candidate()
        request = ComputeExecutionRequest(
            execution_key="hidden:selection:correctness",
            campaign_run_id=self.reservation.campaign_run_id,
            reservation_id=self.reservation.reservation_id,
            scope=EvaluationScope.HIDDEN,
            candidate_digest=digest_bytes(self.candidate),
            candidate_manifest_digest=descriptor.manifest_digest,
            evaluator_profile_digest=evaluator_profile.digest,
            maximum_seconds=self.reservation.reserved_seconds,
        )
        authority = FrozenComputeRunManifest.load_or_create(
            self.root / "compute-manifest.json",
            campaign_run_id=self.reservation.campaign_run_id,
            compute_enabled=True,
            transport_profile_digest=transport_digest,
            backend_profile_digest=backend_digest,
            requests=(request,),
        )
        transport = _RetainedCorrectnessTransport(
            state_root, self.modal_profile, resolver, transport_digest
        )
        durable = SqliteComputeBackend(
            self.root / "compute.sqlite3", transport, resolver, authority
        )
        evaluator = ComputeCandidateEvaluator(
            self.root / "correctness.sqlite3",
            self.campaign,
            evaluator_profile,
            durable,
        )

        receipt = evaluator.hidden_evaluate(
            self.candidate, self.reservation, "hidden:selection:correctness"
        )
        result = evaluator.resolve(
            receipt,
            self.candidate,
            self.reservation,
            EvaluationScope.HIDDEN,
        )

        self.assertTrue(result.eligible)
        self.assertEqual(result.criterion_units, 1_000_000)
        self.assertEqual(evaluator.used_seconds(receipt), 11)
        self.assertEqual(transport.dispatch_count, 1)
        self.assertEqual(
            durable.reconcile(self.reservation.campaign_run_id)[0].status,
            ComputeExecutionStatus.COMPLETE,
        )

    def test_actual_transport_consumes_durable_spend_authorization(self) -> None:
        candidate = self.candidate
        descriptor = self.campaign.validate_reference_candidate()
        evaluator_digest = digest_value({"correctness": "authorized"})
        request = ComputeExecutionRequest(
            execution_key="hidden:reference:correctness",
            campaign_run_id="correctness-authorized-run",
            reservation_id="evaluation-" + "b" * 32,
            scope=EvaluationScope.HIDDEN,
            candidate_digest=digest_bytes(candidate),
            candidate_manifest_digest=descriptor.manifest_digest,
            evaluator_profile_digest=evaluator_digest,
            maximum_seconds=600,
        )
        authorization_profile = (
            SqliteComputeSpendAuthorizationService.profile_digest_for()
        )
        transport_digest = ModalVllmCorrectnessCliTransport.profile_digest_for(
            self.modal_profile.digest,
            REPOSITORY_ROOT / ".venv/bin/modal",
            authorization_profile,
        )
        evidence_digest = ModalVllmCorrectnessEvidenceResolver.profile_digest_for(
            self.modal_profile.digest
        )
        authority = FrozenComputeRunManifest.load_or_create(
            self.root / "authorized-manifest.json",
            campaign_run_id=request.campaign_run_id,
            compute_enabled=True,
            transport_profile_digest=transport_digest,
            backend_profile_digest=SqliteComputeBackend.profile_digest_for(
                transport_digest, evidence_digest
            ),
            requests=(request,),
        )
        authorizations = SqliteComputeSpendAuthorizationService(
            self.root / "authorized-spend.sqlite3", authority
        )
        transport = ModalVllmCorrectnessCliTransport(
            self.modal_profile,
            REPOSITORY_ROOT,
            self.root / "authorized-state",
            REPOSITORY_ROOT / ".venv/bin/modal",
            authorizations,
            evaluator_profile_digest=evaluator_digest,
        )
        authorization = authorizations.issue(
            request, transport.profile_digest, "unit-test-approved"
        )

        def fake_dispatch(command, **kwargs):
            self.assertIn("--correctness", command)
            measurement_id = _measurement_id(request)
            dispatch = {
                "measurement_id": measurement_id,
                "campaign_manifest_digest": self.campaign.manifest_digest,
                "hidden_workload_manifest_digest": self.bundle.manifest_digest,
                "correctness_profile_digest": self.modal_profile.digest,
                "correctness_workload_digest": (
                    self.modal_profile.correctness_workload_digest
                ),
                "candidate_manifest_digest": descriptor.manifest_digest,
                "role": "reference",
                "repetition": 1,
                "attempt": 1,
                "function_call_id": "fc-correctness-authorized",
            }
            path = _dispatch_path(
                self.root / "authorized-state", measurement_id, 1
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(canonical_json_bytes(dispatch))
            return CompletedProcess(command, 0, stdout="dispatched")

        with patch(
            "agent_collab_evals.adapters.modal_vllm_correctness_compute.subprocess.run",
            side_effect=fake_dispatch,
        ):
            dispatch = transport.dispatch(request, candidate)

        self.assertEqual(dispatch.external_call_id, "fc-correctness-authorized")
        self.assertEqual(
            authorizations.status(authorization.authorization_id), "consumed"
        )


def _matching_content(expected: str) -> str:
    if expected.startswith("^") and expected.endswith("$"):
        candidate = expected[1:-1]
        if re.fullmatch(expected, candidate) is not None:
            return candidate
    return expected


def _response(content: str) -> bytes:
    return canonical_json_bytes(
        {
            "id": "chatcmpl-test",
            "model": "target-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
        }
    )
