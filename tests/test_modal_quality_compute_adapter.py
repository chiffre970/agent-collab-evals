from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Mapping
from unittest.mock import patch

from agent_collab_evals.adapters.compute_quality_backend import (
    ComputeQualityRepetitionBackend,
    ComputeQualityRepetitionProfile,
)
from agent_collab_evals.adapters.local_measurements import LocalMeasurementBundleStore
from agent_collab_evals.adapters.modal_vllm_quality_compute import (
    ModalVllmQualityCliTransport,
    ModalVllmQualityEvidenceResolver,
    ModalVllmQualityProfile,
    _dispatch_path,
    _measurement_id,
)
from agent_collab_evals.adapters.quality_series_evaluator import (
    PairedQualitySeriesEvaluator,
    QualitySeriesProfile,
    quality_policy_authority_digest,
)
from agent_collab_evals.adapters.sqlite_compute_spend import (
    SqliteComputeSpendAuthorizationService,
)
from agent_collab_evals.adapters.sqlite_execution_backend import SqliteComputeBackend
from agent_collab_evals.artifacts import ArtifactRef
from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.campaigns.serving_quality import (
    QUALITY_RUN_SCHEMA,
    QualityPolicy,
)
from agent_collab_evals.canonical import (
    canonical_json_bytes,
    digest_bytes,
    digest_file,
    digest_value,
    parse_json,
)
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
from tests.quality_fixture import real_hidden_quality_bundle


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ModelServingCampaign.load(
    REPOSITORY_ROOT / "campaigns/model_serving_v0/campaign.toml"
)


class _RetainedModalQualityTransport:
    def __init__(
        self,
        state_root: Path,
        profile: ModalVllmQualityProfile,
        resolver: ModalVllmQualityEvidenceResolver,
        profile_digest: str,
        policy: QualityPolicy,
    ) -> None:
        self.state_root = state_root
        self.quality_profile = profile
        self.resolver = resolver
        self.profile_digest = profile_digest
        self.policy = policy
        self.dispatch_count = 0
        self.execution_keys: list[str] = []

    def dispatch(
        self, request: ComputeExecutionRequest, candidate: bytes
    ) -> ExternalDispatch:
        self.dispatch_count += 1
        self.execution_keys.append(request.execution_key)
        repetition, role = ModalVllmQualityCliTransport._request_identity(request)
        measurement_id = _measurement_id(request)
        call_id = "fc-" + request.request_digest[7:23]
        request_path = (
            self.state_root
            / "quality-requests"
            / f"{request.request_digest[7:]}.json"
        )
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_bytes(
            canonical_json_bytes(
                {
                    "schema_version": (
                        "modal-vllm-quality-compute-request/v0alpha1"
                    ),
                    "request": request.document,
                    "transport_profile_digest": self.profile_digest,
                }
            )
        )
        dispatch = {
            "measurement_id": measurement_id,
            "campaign_manifest_digest": (
                self.quality_profile.campaign_manifest_digest
            ),
            "quality_profile_digest": self.quality_profile.quality_profile_digest,
            "quality_workload_digest": (
                self.quality_profile.quality_workload_digest
            ),
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "role": role,
            "repetition": repetition,
            "attempt": self.quality_profile.attempt,
            "function_call_id": call_id,
            "git_commit": "f" * 40,
        }
        dispatch_path = _dispatch_path(
            self.state_root,
            measurement_id,
            repetition,
            self.quality_profile.attempt,
        )
        dispatch_path.parent.mkdir(parents=True, exist_ok=True)
        dispatch_path.write_bytes(canonical_json_bytes(dispatch))

        quality_run = _quality_run(self.policy, role, repetition)
        normalized = {
            "campaign_manifest_digest": (
                self.quality_profile.campaign_manifest_digest
            ),
            "quality_profile_digest": self.quality_profile.quality_profile_digest,
            "quality_workload_digest": (
                self.quality_profile.quality_workload_digest
            ),
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "role": role,
            "platform_build": {
                "git_commit": "f" * 40,
                "modal_client_version": self.quality_profile.modal_client_version,
            },
            "modal_function_call_id": call_id,
            "durable_evidence": {
                "volume_name": self.quality_profile.evidence_volume,
            },
            "repetition": repetition,
            "attempt": self.quality_profile.attempt,
            "valid": True,
            "remote_receipt": {"timing": {"function_body_ms": 7_001}},
            "quality_score": quality_run,
        }
        normalized["durable_evidence"]["normalized_digest"] = digest_bytes(
            canonical_json_bytes(normalized) + b"\n"
        )
        LocalMeasurementBundleStore(
            self.state_root / "quality-measurements"
        ).save(
            measurement_id,
            repetition,
            normalized,
            {},
            attempt=self.quality_profile.attempt,
        )
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


class _SpendAuthorization:
    profile_digest = digest_value({"spend_authorization": "quality-test"})

    def __init__(self) -> None:
        self.consumed: list[str] = []

    def consume(self, request, transport_profile_digest):
        self.consumed.append(request.request_digest)
        return None


def _quality_run(
    policy: QualityPolicy, role: str, repetition: int
) -> Mapping[str, object]:
    cases: list[dict[str, object]] = []
    family_scores: dict[str, dict[str, int]] = {}
    per_family = policy.case_count // len(policy.families)
    for family in policy.families:
        for index in range(per_family):
            cases.append(
                {
                    "case_id": f"{family}-{index:02d}",
                    "family_id": family,
                    "passed": True,
                    "extracted": "answer",
                    "content_digest": digest_value(
                        {
                            "role": role,
                            "repetition": repetition,
                            "family": family,
                            "case": index,
                        }
                    ),
                }
            )
        family_scores[family] = {
            "case_count": per_family,
            "pass_count": per_family,
            "score_ppm": 1_000_000,
        }
    return {
        "schema_version": QUALITY_RUN_SCHEMA,
        "profile_digest": policy.quality_profile_digest,
        "workload_digest": policy.quality_workload_digest,
        "role": role,
        "repetition": repetition,
        "case_count": policy.case_count,
        "pass_count": policy.case_count,
        "score_ppm": 1_000_000,
        "family_scores": family_scores,
        "cases": cases,
    }


class ModalQualityComputeAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.hidden_manifest = self.root / "hidden-manifest.json"
        self.hidden_manifest.write_bytes(b'{"hidden":true}\n')
        self.quality_workload = self.root / "quality-workload.json"
        self.quality_workload.write_bytes(b'{"private":"workload"}\n')
        self.quality_requests = self.root / "quality-requests.json"
        self.quality_requests.write_bytes(b'{"private":"requests"}\n')
        profile_document = {"modal_quality": "test"}
        self.profile = ModalVllmQualityProfile(
            profile_id="modal-quality-test-v0",
            modal_environment="dev",
            modal_client_version="1.5.4",
            modal_script=(
                REPOSITORY_ROOT
                / "campaigns/model_serving_v0/reference/modal_vllm.py"
            ),
            modal_script_digest=digest_file(
                REPOSITORY_ROOT
                / "campaigns/model_serving_v0/reference/modal_vllm.py"
            ),
            campaign_manifest=(
                REPOSITORY_ROOT / "campaigns/model_serving_v0/campaign.toml"
            ),
            campaign_manifest_digest=CAMPAIGN.manifest_digest,
            hidden_workload_manifest=self.hidden_manifest,
            hidden_workload_manifest_digest=digest_file(self.hidden_manifest),
            quality_profile=CAMPAIGN.quality_profile_path,
            quality_profile_digest=CAMPAIGN.quality_profile().digest,
            quality_workload=self.quality_workload,
            quality_workload_digest=digest_file(self.quality_workload),
            quality_requests=self.quality_requests,
            quality_requests_digest=digest_file(self.quality_requests),
            attempt=1,
            maximum_collection_seconds=300,
            evidence_volume="test-quality-evidence",
            _digest=digest_value(profile_document),
        )
        self.policy = replace(
            CAMPAIGN.quality_policy(),
            quality_workload_digest=self.profile.quality_workload_digest,
            bootstrap_resamples=100,
        )
        self.candidate = CAMPAIGN.reference_candidate_path.read_bytes()
        self.reservation = EvaluationReservation(
            reservation_id="evaluation-" + "7" * 32,
            reservation_key="hidden:quality",
            campaign_run_id="modal-quality-run",
            actor_id=None,
            artifact_ref=ArtifactRef("artifact-" + "8" * 32),
            scope=EvaluationScope.HIDDEN,
            reserved_seconds=600,
            status=EvaluationReservationStatus.RESERVED,
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_retained_modal_bundle_composes_with_durable_quality_backend(
        self,
    ) -> None:
        transport_digest = digest_value({"transport": "retained-modal-quality"})
        resolver = ModalVllmQualityEvidenceResolver(
            self.profile, self.root, transport_digest
        )
        backend_digest = SqliteComputeBackend.profile_digest_for(
            transport_digest, resolver.profile_digest
        )
        quality_profile = ComputeQualityRepetitionProfile(
            profile_id="modal-quality-durable-v0",
            campaign_manifest_digest=CAMPAIGN.manifest_digest,
            hidden_workload_manifest_digest=(
                self.profile.hidden_workload_manifest_digest
            ),
            quality_profile_digest=self.profile.quality_profile_digest,
            quality_workload_digest=self.profile.quality_workload_digest,
            compute_execution_profile_digest=backend_digest,
            repetitions=3,
            maximum_collection_seconds=300,
        )
        key = "hidden:modal:quality:1:reference"
        descriptor = CAMPAIGN.validate_reference_candidate()
        request = ComputeExecutionRequest(
            execution_key=key,
            campaign_run_id=self.reservation.campaign_run_id,
            reservation_id=self.reservation.reservation_id,
            scope=EvaluationScope.HIDDEN,
            candidate_digest=digest_bytes(self.candidate),
            candidate_manifest_digest=descriptor.manifest_digest,
            evaluator_profile_digest=quality_profile.digest,
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
        transport = _RetainedModalQualityTransport(
            self.root, self.profile, resolver, transport_digest, self.policy
        )
        durable = SqliteComputeBackend(
            self.root / "compute.sqlite3", transport, resolver, manifest
        )
        quality = ComputeQualityRepetitionBackend(
            self.root / "quality.sqlite3", CAMPAIGN, quality_profile, durable
        )

        receipt = quality.evaluate(
            self.candidate,
            self.reservation,
            key,
            role="reference",
            repetition=1,
        )
        run = quality.resolve(
            receipt,
            self.candidate,
            self.reservation,
            role="reference",
            repetition=1,
        )

        self.assertEqual(run["score_ppm"], 1_000_000)
        self.assertEqual(quality.used_seconds(receipt), 8)
        self.assertEqual(transport.dispatch_count, 1)
        self.assertEqual(
            durable.reconcile(self.reservation.campaign_run_id)[0].status,
            ComputeExecutionStatus.COMPLETE,
        )

    def test_production_profile_factory_loads_a_real_hidden_bundle(self) -> None:
        campaign, bundle, _ = real_hidden_quality_bundle(self.root / "factory")

        profile = ModalVllmQualityProfile.create(
            profile_id="modal-quality-real-bundle-v0",
            campaign=campaign,
            campaign_manifest=(
                REPOSITORY_ROOT / "campaigns/model_serving_v0/campaign.toml"
            ),
            hidden_workload=bundle,
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

        self.assertEqual(profile.hidden_workload_manifest_digest, bundle.manifest_digest)
        self.assertEqual(
            profile.quality_requests_digest,
            bundle.resource_digests["quality_requests"],
        )
        profile.validate_inputs(campaign)

    def test_actual_transport_consumes_durable_sqlite_authorization(self) -> None:
        campaign, bundle, _ = real_hidden_quality_bundle(self.root / "authorized")
        profile = ModalVllmQualityProfile.create(
            profile_id="modal-quality-authorized-v0",
            campaign=campaign,
            campaign_manifest=(
                REPOSITORY_ROOT / "campaigns/model_serving_v0/campaign.toml"
            ),
            hidden_workload=bundle,
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
        candidate = campaign.reference_candidate_path.read_bytes()
        descriptor = campaign.validate_reference_candidate()
        request = ComputeExecutionRequest(
            execution_key="hidden:authorized:quality:1:reference",
            campaign_run_id="modal-quality-authorized-run",
            reservation_id="evaluation-" + "a" * 32,
            scope=EvaluationScope.HIDDEN,
            candidate_digest=digest_bytes(candidate),
            candidate_manifest_digest=descriptor.manifest_digest,
            evaluator_profile_digest=digest_value({"quality": "authorized"}),
            maximum_seconds=600,
        )
        authorization_profile = (
            SqliteComputeSpendAuthorizationService.profile_digest_for()
        )
        transport_digest = ModalVllmQualityCliTransport.profile_digest_for(
            profile.digest,
            REPOSITORY_ROOT / ".venv/bin/modal",
            authorization_profile,
        )
        evidence_digest = ModalVllmQualityEvidenceResolver.profile_digest_for(
            profile.digest
        )
        manifest = FrozenComputeRunManifest.load_or_create(
            self.root / "authorized-compute-manifest.json",
            campaign_run_id=request.campaign_run_id,
            compute_enabled=True,
            transport_profile_digest=transport_digest,
            backend_profile_digest=SqliteComputeBackend.profile_digest_for(
                transport_digest, evidence_digest
            ),
            requests=(request,),
        )
        authorizations = SqliteComputeSpendAuthorizationService(
            self.root / "authorized-spend.sqlite3", manifest
        )
        transport = ModalVllmQualityCliTransport(
            profile,
            REPOSITORY_ROOT,
            self.root / "authorized-state",
            REPOSITORY_ROOT / ".venv/bin/modal",
            authorizations,
        )
        authorization = authorizations.issue(
            request, transport.profile_digest, "unit-test-approved"
        )

        def fake_dispatch(command, **kwargs):
            measurement_id = _measurement_id(request)
            dispatch = {
                "measurement_id": measurement_id,
                "campaign_manifest_digest": campaign.manifest_digest,
                "quality_profile_digest": profile.quality_profile_digest,
                "quality_workload_digest": profile.quality_workload_digest,
                "candidate_manifest_digest": descriptor.manifest_digest,
                "role": "reference",
                "repetition": 1,
                "attempt": 1,
                "function_call_id": "fc-quality-authorized",
            }
            path = _dispatch_path(
                self.root / "authorized-state", measurement_id, 1, 1
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(canonical_json_bytes(dispatch))
            from subprocess import CompletedProcess

            return CompletedProcess(command, 0, stdout="dispatched")

        with patch(
            "agent_collab_evals.adapters.modal_vllm_quality_compute.subprocess.run",
            side_effect=fake_dispatch,
        ):
            dispatch = transport.dispatch(request, candidate)

        self.assertEqual(dispatch.external_call_id, "fc-quality-authorized")
        self.assertEqual(
            authorizations.status(authorization.authorization_id), "consumed"
        )

    def test_full_paired_series_uses_six_durable_modal_compute_executions(
        self,
    ) -> None:
        campaign, bundle, policy = real_hidden_quality_bundle(
            self.root / "paired-series"
        )
        modal_profile = ModalVllmQualityProfile.create(
            profile_id="modal-quality-paired-series-v0",
            campaign=campaign,
            campaign_manifest=(
                REPOSITORY_ROOT / "campaigns/model_serving_v0/campaign.toml"
            ),
            hidden_workload=bundle,
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
        reference = campaign.reference_candidate_path.read_bytes()
        candidate_document = parse_json(reference.decode("utf-8"))
        candidate_document["candidate_id"] = "paired-series-candidate"
        engine_args = candidate_document["server"]["engine_args"]
        engine_args["max_num_seqs"] = (
            1 if engine_args["max_num_seqs"] != 1 else 2
        )
        candidate = canonical_json_bytes(candidate_document)
        campaign.validate_candidate_document(candidate_document)

        transport_digest = digest_value({"transport": "paired-modal-quality"})
        resolver = ModalVllmQualityEvidenceResolver(
            modal_profile, self.root / "paired-state", transport_digest
        )
        backend_digest = SqliteComputeBackend.profile_digest_for(
            transport_digest, resolver.profile_digest
        )
        repetition_profile = ComputeQualityRepetitionProfile(
            profile_id="paired-modal-quality-repetitions-v0",
            campaign_manifest_digest=campaign.manifest_digest,
            hidden_workload_manifest_digest=bundle.manifest_digest,
            quality_profile_digest=modal_profile.quality_profile_digest,
            quality_workload_digest=modal_profile.quality_workload_digest,
            compute_execution_profile_digest=backend_digest,
            repetitions=3,
            maximum_collection_seconds=300,
        )
        series_profile = QualitySeriesProfile(
            profile_id="paired-modal-quality-series-v0",
            campaign_manifest_digest=campaign.manifest_digest,
            hidden_workload_manifest_digest=bundle.manifest_digest,
            quality_profile_digest=modal_profile.quality_profile_digest,
            quality_policy_digest=policy.digest,
            quality_policy_authority_digest=quality_policy_authority_digest(policy),
            quality_workload_digest=modal_profile.quality_workload_digest,
            reference_artifact_ref="artifact-" + "6" * 32,
            reference_candidate_digest=digest_bytes(reference),
            repetition_backend_profile_digest=repetition_profile.digest,
            repetitions=3,
            repetition_reserved_seconds=600,
            role_order_by_repetition=(
                ("reference", "candidate"),
                ("candidate", "reference"),
                ("reference", "candidate"),
            ),
        )
        outer = EvaluationReservation(
            reservation_id="evaluation-" + "b" * 32,
            reservation_key="hidden:paired-quality",
            campaign_run_id="modal-quality-paired-run",
            actor_id=None,
            artifact_ref=ArtifactRef("artifact-" + "8" * 32),
            scope=EvaluationScope.HIDDEN,
            reserved_seconds=series_profile.reserved_seconds,
            status=EvaluationReservationStatus.RESERVED,
        )
        requests: list[ComputeExecutionRequest] = []
        for repetition in range(1, 4):
            for role, run_candidate in (
                ("reference", reference),
                ("candidate", candidate),
            ):
                run_reservation_id = "evaluation-" + digest_value(
                    {
                        "profile_digest": series_profile.digest,
                        "outer_reservation_id": outer.reservation_id,
                        "role": role,
                        "repetition": repetition,
                    }
                )[7:39]
                descriptor = campaign.validate_candidate_document(
                    parse_json(run_candidate.decode("utf-8"))
                )
                requests.append(
                    ComputeExecutionRequest(
                        execution_key=(
                            f"hidden:modal-series:quality:{repetition}:{role}"
                        ),
                        campaign_run_id=outer.campaign_run_id,
                        reservation_id=run_reservation_id,
                        scope=EvaluationScope.HIDDEN,
                        candidate_digest=digest_bytes(run_candidate),
                        candidate_manifest_digest=descriptor.manifest_digest,
                        evaluator_profile_digest=repetition_profile.digest,
                        maximum_seconds=600,
                    )
                )
        manifest = FrozenComputeRunManifest.load_or_create(
            self.root / "paired-compute-manifest.json",
            campaign_run_id=outer.campaign_run_id,
            compute_enabled=True,
            transport_profile_digest=transport_digest,
            backend_profile_digest=backend_digest,
            requests=tuple(requests),
        )
        transport = _RetainedModalQualityTransport(
            self.root / "paired-state",
            modal_profile,
            resolver,
            transport_digest,
            policy,
        )
        durable = SqliteComputeBackend(
            self.root / "paired-compute.sqlite3", transport, resolver, manifest
        )
        repetitions = ComputeQualityRepetitionBackend(
            self.root / "paired-repetitions.sqlite3",
            campaign,
            repetition_profile,
            durable,
        )
        series = PairedQualitySeriesEvaluator(
            self.root / "paired-series.sqlite3",
            series_profile,
            policy,
            reference,
            repetitions,
        )

        receipt = series.hidden_evaluate(candidate, outer, "hidden:modal-series")
        result = series.resolve(
            receipt, candidate, outer, EvaluationScope.HIDDEN
        )

        self.assertTrue(result.eligible)
        self.assertEqual(result.criterion_units, 0)
        self.assertEqual(series.used_seconds(receipt), 48)
        self.assertEqual(transport.dispatch_count, 6)
        self.assertEqual(
            transport.execution_keys,
            [
                "hidden:modal-series:quality:1:reference",
                "hidden:modal-series:quality:1:candidate",
                "hidden:modal-series:quality:2:candidate",
                "hidden:modal-series:quality:2:reference",
                "hidden:modal-series:quality:3:reference",
                "hidden:modal-series:quality:3:candidate",
            ],
        )
        reconciled = durable.reconcile(outer.campaign_run_id)
        self.assertEqual(len(reconciled), 6)
        self.assertTrue(
            all(item.status is ComputeExecutionStatus.COMPLETE for item in reconciled)
        )

    def test_real_transport_builds_fixed_command_and_consumes_authorization(
        self,
    ) -> None:
        descriptor = CAMPAIGN.validate_reference_candidate()
        request = ComputeExecutionRequest(
            execution_key="hidden:modal:quality:1:reference",
            campaign_run_id=self.reservation.campaign_run_id,
            reservation_id=self.reservation.reservation_id,
            scope=EvaluationScope.HIDDEN,
            candidate_digest=digest_bytes(self.candidate),
            candidate_manifest_digest=descriptor.manifest_digest,
            evaluator_profile_digest=digest_value({"quality": "evaluator"}),
            maximum_seconds=self.reservation.reserved_seconds,
        )
        spend = _SpendAuthorization()
        with patch.object(ModalVllmQualityProfile, "validate_inputs"):
            transport = ModalVllmQualityCliTransport(
                self.profile,
                REPOSITORY_ROOT,
                self.root,
                REPOSITORY_ROOT / ".venv/bin/modal",
                spend,
            )

            def fake_dispatch(command, **kwargs):
                self.assertIn("--quality", command)
                self.assertNotIn("--baseline", command)
                self.assertEqual(
                    command[command.index("--quality-role") + 1], "reference"
                )
                measurement_id = _measurement_id(request)
                dispatch = {
                    "measurement_id": measurement_id,
                    "campaign_manifest_digest": CAMPAIGN.manifest_digest,
                    "quality_profile_digest": self.profile.quality_profile_digest,
                    "quality_workload_digest": self.profile.quality_workload_digest,
                    "candidate_manifest_digest": descriptor.manifest_digest,
                    "role": "reference",
                    "repetition": 1,
                    "attempt": 1,
                    "function_call_id": "fc-quality-dispatch",
                }
                path = _dispatch_path(self.root, measurement_id, 1, 1)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(canonical_json_bytes(dispatch))
                from subprocess import CompletedProcess

                return CompletedProcess(command, 0, stdout="dispatched")

            with patch(
                "agent_collab_evals.adapters.modal_vllm_quality_compute.subprocess.run",
                side_effect=fake_dispatch,
            ):
                dispatch = transport.dispatch(request, self.candidate)

        self.assertEqual(dispatch.external_call_id, "fc-quality-dispatch")
        self.assertEqual(spend.consumed, [request.request_digest])

    def test_resolver_rejects_changed_private_input(self) -> None:
        resolver = ModalVllmQualityEvidenceResolver(
            self.profile, self.root, digest_value({"transport": "test"})
        )
        self.quality_requests.write_bytes(b"changed\n")

        with self.assertRaisesRegex(RuntimeError, "quality requests digest differs"):
            resolver._validate_static_inputs()


if __name__ == "__main__":
    unittest.main()
