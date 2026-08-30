"""Modal transport and evidence adapter for hidden quality repetitions."""

from __future__ import annotations

import importlib.metadata
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..campaigns.model_serving import ModelServingCampaign
from ..campaigns.serving_quality import build_quality_requests, load_quality_workload
from ..campaigns.serving_workload import (
    HiddenWorkloadBundle,
    verify_quality_request_specs,
)
from ..canonical import (
    canonical_json_bytes,
    digest_bytes,
    digest_file,
    digest_value,
    parse_json,
)
from ..compute_backend import (
    ComputeEvidencePointer,
    ComputeExecutionRequest,
    ComputeExecutionStatus,
    ExternalDispatch,
    TransportPoll,
)
from ..evaluation import EvaluationScope
from ..ports import ComputeSpendAuthorizationService
from .local_measurements import LocalMeasurementBundleStore
from .modal_vllm_compute import (
    _failure,
    _load_object,
    _load_optional,
    _minimal_modal_environment,
    _request,
    _request_document,
    _used_seconds,
    _write_once,
)


_EXECUTION_KEY = re.compile(r".+:quality:([1-9][0-9]*):(reference|candidate)")


@dataclass(frozen=True, slots=True)
class ModalVllmQualityProfile:
    """Frozen local and private inputs for Modal quality execution."""

    profile_id: str
    modal_environment: str
    modal_client_version: str
    modal_script: Path
    modal_script_digest: str
    campaign_manifest: Path
    campaign_manifest_digest: str
    hidden_workload_manifest: Path
    hidden_workload_manifest_digest: str
    quality_profile: Path
    quality_profile_digest: str
    quality_workload: Path
    quality_workload_digest: str
    quality_requests: Path
    quality_requests_digest: str
    attempt: int
    maximum_collection_seconds: int
    evidence_volume: str
    _digest: str

    @classmethod
    def create(
        cls,
        *,
        profile_id: str,
        campaign: ModelServingCampaign,
        campaign_manifest: Path,
        hidden_workload: HiddenWorkloadBundle,
        modal_script: Path,
        modal_environment: str,
        modal_client_version: str,
        attempt: int,
        maximum_collection_seconds: int,
        evidence_volume: str,
    ) -> "ModalVllmQualityProfile":
        if not profile_id or not modal_environment or not evidence_volume:
            raise ValueError("Modal quality profile strings must be nonempty")
        if type(attempt) is not int or attempt < 1:
            raise ValueError("Modal quality attempt is invalid")
        if (
            type(maximum_collection_seconds) is not int
            or not 0 <= maximum_collection_seconds <= 300
        ):
            raise ValueError("Modal quality collection limit is invalid")
        if importlib.metadata.version("modal") != modal_client_version:
            raise ValueError("installed Modal client differs from the profile")
        campaign_manifest = campaign_manifest.resolve(strict=True)
        if ModelServingCampaign.load(campaign_manifest) != campaign:
            raise ValueError("Modal quality campaign manifest differs")
        quality_profile = campaign.quality_profile()
        if attempt > campaign.measurement_profile().max_attempts:
            raise ValueError("Modal quality attempt exceeds the campaign profile")
        quality_workload_path = hidden_workload.resource_paths["quality_workload"]
        quality_requests_path = hidden_workload.resource_paths["quality_requests"]
        workload = load_quality_workload(quality_workload_path, quality_profile)
        campaign.validate_reference_candidate()
        requests = build_quality_requests(
            quality_profile,
            workload,
            served_model_name=str(campaign.raw["reference"]["served_model_name"]),
        )
        verify_quality_request_specs(
            quality_requests_path,
            requests,
            expected_digest=hidden_workload.resource_digests["quality_requests"],
        )
        if workload.digest != hidden_workload.resource_digests["quality_workload"]:
            raise ValueError("hidden quality workload digest differs")
        document = {
            "adapter": "modal-vllm-quality-profile/v0alpha1",
            "profile_id": profile_id,
            "modal_environment": modal_environment,
            "modal_client_version": modal_client_version,
            "modal_script_digest": digest_file(modal_script),
            "campaign_manifest_digest": campaign.manifest_digest,
            "hidden_workload_manifest_digest": hidden_workload.manifest_digest,
            "quality_profile_digest": quality_profile.digest,
            "quality_workload_digest": workload.digest,
            "quality_requests_digest": hidden_workload.resource_digests[
                "quality_requests"
            ],
            "attempt": attempt,
            "maximum_collection_seconds": maximum_collection_seconds,
            "evidence_volume": evidence_volume,
        }
        profile = cls(
            profile_id=profile_id,
            modal_environment=modal_environment,
            modal_client_version=modal_client_version,
            modal_script=modal_script.resolve(strict=True),
            modal_script_digest=document["modal_script_digest"],
            campaign_manifest=campaign_manifest,
            campaign_manifest_digest=campaign.manifest_digest,
            hidden_workload_manifest=hidden_workload.manifest_path,
            hidden_workload_manifest_digest=hidden_workload.manifest_digest,
            quality_profile=quality_profile.path,
            quality_profile_digest=quality_profile.digest,
            quality_workload=quality_workload_path,
            quality_workload_digest=workload.digest,
            quality_requests=quality_requests_path,
            quality_requests_digest=hidden_workload.resource_digests[
                "quality_requests"
            ],
            attempt=attempt,
            maximum_collection_seconds=maximum_collection_seconds,
            evidence_volume=evidence_volume,
            _digest=digest_value(document),
        )
        profile.validate_inputs(campaign)
        return profile

    @property
    def digest(self) -> str:
        return self._digest

    def validate_inputs(self, campaign: ModelServingCampaign) -> None:
        if digest_file(self.modal_script) != self.modal_script_digest:
            raise RuntimeError("Modal quality script digest differs")
        if campaign.manifest_digest != self.campaign_manifest_digest:
            raise RuntimeError("Modal quality campaign digest differs")
        if digest_file(self.hidden_workload_manifest) != (
            self.hidden_workload_manifest_digest
        ):
            raise RuntimeError("hidden workload manifest digest differs")
        profile = campaign.quality_profile()
        if profile.digest != self.quality_profile_digest:
            raise RuntimeError("Modal quality profile digest differs")
        workload = load_quality_workload(self.quality_workload, profile)
        if workload.digest != self.quality_workload_digest:
            raise RuntimeError("Modal quality workload digest differs")
        campaign.validate_reference_candidate()
        requests = build_quality_requests(
            profile,
            workload,
            served_model_name=str(campaign.raw["reference"]["served_model_name"]),
        )
        verify_quality_request_specs(
            self.quality_requests,
            requests,
            expected_digest=self.quality_requests_digest,
        )


class ModalVllmQualityCliTransport:
    """Dispatch one registered hidden quality repetition through Modal."""

    def __init__(
        self,
        profile: ModalVllmQualityProfile,
        repository_root: Path,
        state_root: Path,
        modal_cli: Path,
        spend_authorization: ComputeSpendAuthorizationService,
    ) -> None:
        self._profile = profile
        self._repository_root = repository_root.resolve()
        self._state_root = state_root.resolve()
        self._modal_cli = modal_cli.resolve()
        self._spend_authorization = spend_authorization
        if not self._modal_cli.is_file():
            raise ValueError("Modal CLI path does not exist")
        self._campaign = ModelServingCampaign.load(profile.campaign_manifest)
        profile.validate_inputs(self._campaign)
        self._measurements = LocalMeasurementBundleStore(
            self._state_root / "quality-measurements"
        )
        self._profile_digest = self.profile_digest_for(
            profile.digest, self._modal_cli, spend_authorization.profile_digest
        )

    @staticmethod
    def profile_digest_for(
        quality_profile_digest: str,
        modal_cli: Path,
        spend_authorization_profile_digest: str,
    ) -> str:
        return digest_value(
            {
                "adapter": "modal-vllm-quality-cli-transport/v0alpha1",
                "quality_profile_digest": quality_profile_digest,
                "modal_cli": str(modal_cli.resolve()),
                "spend_authorization_profile_digest": (
                    spend_authorization_profile_digest
                ),
                "dispatch_policy": "one_remote_call_then_fail_closed",
                "app_lifecycle": "detached_until_function_call_terminal",
            }
        )

    @property
    def profile_digest(self) -> str:
        return self._profile_digest

    def dispatch(
        self, request: ComputeExecutionRequest, candidate: bytes
    ) -> ExternalDispatch:
        repetition, role = self._validate_request(request, candidate)
        candidate_path = self._prepare_request(request, candidate)
        measurement_id = _measurement_id(request)
        if _load_optional(
            self._measurements,
            measurement_id,
            repetition,
            self._profile.attempt,
        ) is not None:
            raise RuntimeError("Modal quality execution already has terminal evidence")
        self._spend_authorization.consume(request, self.profile_digest)
        result = subprocess.run(
            self._command(
                candidate_path,
                measurement_id,
                repetition,
                role,
                dispatch_only=True,
            ),
            cwd=self._repository_root,
            env=_minimal_modal_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
            check=False,
        )
        dispatch = self._dispatch_record(measurement_id, repetition)
        if result.returncode != 0 and dispatch is None:
            raise RuntimeError(
                "Modal quality dispatch outcome is ambiguous: " + result.stdout[-4000:]
            )
        if dispatch is None:
            raise RuntimeError("Modal quality dispatch has no durable call record")
        external_call_id = dispatch.get("function_call_id")
        if not isinstance(external_call_id, str) or not external_call_id:
            raise RuntimeError("Modal quality dispatch call ID is invalid")
        self._validate_dispatch(dispatch, request, repetition, role, external_call_id)
        return ExternalDispatch(external_call_id, digest_value(dispatch))

    def poll(
        self,
        request: ComputeExecutionRequest,
        external_call_id: str,
        timeout_seconds: int,
    ) -> TransportPoll:
        if timeout_seconds > self._profile.maximum_collection_seconds:
            raise ValueError("collection timeout exceeds the Modal quality profile")
        repetition, role = self._request_identity(request)
        candidate_path = self._candidate_path(request)
        self._validate_prepared_request(request, candidate_path)
        measurement_id = _measurement_id(request)
        bundle = _load_optional(
            self._measurements,
            measurement_id,
            repetition,
            self._profile.attempt,
        )
        if bundle is None:
            result = subprocess.run(
                self._command(
                    candidate_path,
                    measurement_id,
                    repetition,
                    role,
                    collect_only=True,
                    timeout_seconds=timeout_seconds,
                ),
                cwd=self._repository_root,
                env=_minimal_modal_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=max(120, timeout_seconds + 60),
                check=False,
            )
            bundle = _load_optional(
                self._measurements,
                measurement_id,
                repetition,
                self._profile.attempt,
            )
            if bundle is None:
                if result.returncode == 0:
                    return TransportPoll(ComputeExecutionStatus.DISPATCHED)
                raise RuntimeError(
                    "Modal quality collection failed without terminal evidence: "
                    + result.stdout[-4000:]
                )
        resolver = ModalVllmQualityEvidenceResolver(
            self._profile, self._state_root, self.profile_digest
        )
        pointer, status, used_seconds, failure = resolver.pointer(
            request, external_call_id
        )
        return TransportPoll(status, pointer, used_seconds, failure)

    def _validate_request(
        self, request: ComputeExecutionRequest, candidate: bytes
    ) -> tuple[int, str]:
        if request.scope is not EvaluationScope.HIDDEN:
            raise ValueError("Modal quality execution requires hidden scope")
        if digest_bytes(candidate) != request.candidate_digest:
            raise ValueError("candidate bytes differ from the compute request")
        descriptor = self._campaign.validate_candidate_document(
            parse_json(candidate.decode("utf-8"))
        )
        if descriptor.manifest_digest != request.candidate_manifest_digest:
            raise ValueError("candidate manifest digest differs from the request")
        self._profile.validate_inputs(self._campaign)
        repetition, role = self._request_identity(request)
        if repetition > self._campaign.quality_profile().repetitions:
            raise ValueError("Modal quality repetition exceeds the campaign profile")
        reference = self._campaign.validate_reference_candidate()
        if role == "reference" and descriptor.manifest_digest != (
            reference.manifest_digest
        ):
            raise ValueError("Modal quality reference role requires the reference artifact")
        return repetition, role

    @staticmethod
    def _request_identity(request: ComputeExecutionRequest) -> tuple[int, str]:
        match = _EXECUTION_KEY.fullmatch(request.execution_key)
        if match is None:
            raise ValueError("Modal quality execution key is invalid")
        return int(match.group(1)), match.group(2)

    def _prepare_request(
        self, request: ComputeExecutionRequest, candidate: bytes
    ) -> Path:
        candidate_path = self._candidate_path(request)
        _write_once(candidate_path, candidate)
        _write_once(
            self._request_path(request),
            canonical_json_bytes(
                {
                    "schema_version": "modal-vllm-quality-compute-request/v0alpha1",
                    "request": _request_document(request),
                    "transport_profile_digest": self.profile_digest,
                }
            ),
        )
        return candidate_path

    def _validate_prepared_request(
        self, request: ComputeExecutionRequest, candidate_path: Path
    ) -> None:
        if digest_file(candidate_path) != request.candidate_digest:
            raise RuntimeError("prepared Modal quality candidate digest differs")
        expected = {
            "schema_version": "modal-vllm-quality-compute-request/v0alpha1",
            "request": _request_document(request),
            "transport_profile_digest": self.profile_digest,
        }
        if _load_object(self._request_path(request)) != expected:
            raise RuntimeError("prepared Modal quality request differs")
        self._profile.validate_inputs(self._campaign)

    def _command(
        self,
        candidate_path: Path,
        measurement_id: str,
        repetition: int,
        role: str,
        *,
        dispatch_only: bool = False,
        collect_only: bool = False,
        timeout_seconds: int = 0,
    ) -> tuple[str, ...]:
        command = [
            str(self._modal_cli),
            "run",
            "--detach",
            "-e",
            self._profile.modal_environment,
            str(self._profile.modal_script),
            "--quality",
            "--candidate-path",
            str(candidate_path),
            "--repetition",
            str(repetition),
            "--attempt",
            str(self._profile.attempt),
            "--quality-output-root",
            str(self._state_root / "quality-measurements"),
            "--quality-profile-path",
            str(self._profile.quality_profile),
            "--quality-workload-path",
            str(self._profile.quality_workload),
            "--quality-role",
            role,
            "--measurement-id",
            measurement_id,
        ]
        if dispatch_only:
            command.append("--dispatch-only")
        if collect_only:
            command.extend(
                ["--collect-only", "--collect-timeout-seconds", str(timeout_seconds)]
            )
        return tuple(command)

    def _validate_dispatch(
        self,
        dispatch: Mapping[str, Any],
        request: ComputeExecutionRequest,
        repetition: int,
        role: str,
        external_call_id: str,
    ) -> None:
        expected = {
            "measurement_id": _measurement_id(request),
            "campaign_manifest_digest": self._profile.campaign_manifest_digest,
            "quality_profile_digest": self._profile.quality_profile_digest,
            "quality_workload_digest": self._profile.quality_workload_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "role": role,
            "repetition": repetition,
            "attempt": self._profile.attempt,
            "function_call_id": external_call_id,
        }
        if any(dispatch.get(key) != value for key, value in expected.items()):
            raise RuntimeError("Modal quality dispatch identity differs")

    def _dispatch_record(
        self, measurement_id: str, repetition: int
    ) -> Mapping[str, Any] | None:
        path = _dispatch_path(
            self._state_root, measurement_id, repetition, self._profile.attempt
        )
        try:
            return _load_object(path)
        except FileNotFoundError:
            return None

    def _candidate_path(self, request: ComputeExecutionRequest) -> Path:
        return self._state_root / "quality-candidates" / f"{request.request_digest[7:]}.json"

    def _request_path(self, request: ComputeExecutionRequest) -> Path:
        return self._state_root / "quality-requests" / f"{request.request_digest[7:]}.json"


class ModalVllmQualityEvidenceResolver:
    """Normalize a retained Modal quality bundle for the compute backend."""

    def __init__(
        self,
        profile: ModalVllmQualityProfile,
        state_root: Path,
        transport_profile_digest: str,
    ) -> None:
        self._profile = profile
        self._state_root = state_root.resolve()
        self._transport_profile_digest = transport_profile_digest
        self._measurements = LocalMeasurementBundleStore(
            self._state_root / "quality-measurements"
        )
        self._profile_digest = self.profile_digest_for(profile.digest)

    @staticmethod
    def profile_digest_for(quality_profile_digest: str) -> str:
        return digest_value(
            {
                "adapter": "modal-vllm-quality-evidence-resolver/v0alpha1",
                "quality_profile_digest": quality_profile_digest,
                "source": "digest_verified_local_mirror_of_modal_volume",
            }
        )

    @property
    def profile_digest(self) -> str:
        return self._profile_digest

    def resolve_dispatch(
        self, request: ComputeExecutionRequest, external_call_id: str
    ) -> bytes:
        repetition, role = ModalVllmQualityCliTransport._request_identity(request)
        dispatch = _load_object(
            _dispatch_path(
                self._state_root,
                _measurement_id(request),
                repetition,
                self._profile.attempt,
            )
        )
        expected = {
            "measurement_id": _measurement_id(request),
            "campaign_manifest_digest": self._profile.campaign_manifest_digest,
            "quality_profile_digest": self._profile.quality_profile_digest,
            "quality_workload_digest": self._profile.quality_workload_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "role": role,
            "repetition": repetition,
            "attempt": self._profile.attempt,
            "function_call_id": external_call_id,
        }
        if any(dispatch.get(key) != value for key, value in expected.items()):
            raise RuntimeError("Modal quality dispatch evidence identity differs")
        return canonical_json_bytes(dispatch)

    def pointer(
        self, request: ComputeExecutionRequest, external_call_id: str
    ) -> tuple[ComputeEvidencePointer, ComputeExecutionStatus, int, str | None]:
        content, status, used_seconds, failure = self._build(request, external_call_id)
        return (
            ComputeEvidencePointer(_measurement_id(request), digest_bytes(content)),
            status,
            used_seconds,
            failure,
        )

    def resolve(self, pointer: ComputeEvidencePointer) -> bytes:
        request_path = (
            self._state_root
            / "quality-requests"
            / (pointer.locator.removeprefix("exec-") + ".json")
        )
        request = _request(_load_object(request_path).get("request"))
        repetition, _ = ModalVllmQualityCliTransport._request_identity(request)
        dispatch = _load_object(
            _dispatch_path(
                self._state_root,
                pointer.locator,
                repetition,
                self._profile.attempt,
            )
        )
        external_call_id = dispatch.get("function_call_id")
        if not isinstance(external_call_id, str) or not external_call_id:
            raise RuntimeError("Modal quality dispatch evidence has no call ID")
        content, _, _, _ = self._build(request, external_call_id)
        return content

    def _build(
        self, request: ComputeExecutionRequest, external_call_id: str
    ) -> tuple[bytes, ComputeExecutionStatus, int, str | None]:
        self._validate_static_inputs()
        repetition, role = ModalVllmQualityCliTransport._request_identity(request)
        measurement_id = _measurement_id(request)
        bundle = self._measurements.load(
            measurement_id, repetition, attempt=self._profile.attempt
        )
        normalized = bundle.receipt["normalized"]
        if not isinstance(normalized, dict):
            raise RuntimeError("Modal quality normalized evidence is invalid")
        expected = {
            "campaign_manifest_digest": self._profile.campaign_manifest_digest,
            "quality_profile_digest": self._profile.quality_profile_digest,
            "quality_workload_digest": self._profile.quality_workload_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "role": role,
            "modal_function_call_id": external_call_id,
            "repetition": repetition,
            "attempt": self._profile.attempt,
        }
        if any(normalized.get(key) != value for key, value in expected.items()):
            raise RuntimeError("Modal quality normalized evidence identity differs")
        dispatch = _load_object(
            _dispatch_path(
                self._state_root,
                measurement_id,
                repetition,
                self._profile.attempt,
            )
        )
        platform_build = normalized.get("platform_build")
        if (
            not isinstance(platform_build, dict)
            or platform_build.get("git_commit") != dispatch.get("git_commit")
            or platform_build.get("modal_client_version")
            != self._profile.modal_client_version
        ):
            raise RuntimeError("Modal quality platform build evidence differs")
        valid = normalized.get("valid") is True
        quality_run = normalized.get("quality_score")
        if valid:
            if not isinstance(quality_run, dict):
                raise RuntimeError("Modal quality evidence has no normalized score")
            expected_run = {
                "schema_version": "model-serving-quality-run/v0alpha1",
                "profile_digest": self._profile.quality_profile_digest,
                "workload_digest": self._profile.quality_workload_digest,
                "role": role,
                "repetition": repetition,
            }
            if any(quality_run.get(key) != value for key, value in expected_run.items()):
                raise RuntimeError("Modal quality score identity differs")
            _validate_durable_evidence(normalized, self._profile.evidence_volume)
        elif quality_run is not None or not isinstance(normalized.get("failure"), dict):
            raise RuntimeError("Modal quality terminal failure evidence is invalid")
        status = (
            ComputeExecutionStatus.COMPLETE
            if valid
            else ComputeExecutionStatus.FAILED
        )
        used_seconds = _used_seconds(normalized, request.maximum_seconds)
        failure = None if valid else _failure(normalized)
        quality_evaluation = {
            "schema_version": "serving-quality-compute-evidence/v0alpha1",
            "campaign_manifest_digest": self._profile.campaign_manifest_digest,
            "hidden_workload_manifest_digest": (
                self._profile.hidden_workload_manifest_digest
            ),
            "quality_profile_digest": self._profile.quality_profile_digest,
            "quality_workload_digest": self._profile.quality_workload_digest,
            "candidate_digest": request.candidate_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "role": role,
            "repetition": repetition,
            "run": quality_run,
        }
        document = {
            "schema_version": "compute-execution-evidence/v0alpha1",
            "request_digest": request.request_digest,
            "candidate_digest": request.candidate_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "evaluator_profile_digest": request.evaluator_profile_digest,
            "transport_profile_digest": self._transport_profile_digest,
            "evidence_profile_digest": self.profile_digest,
            "external_call_id": external_call_id,
            "status": status.value,
            "used_seconds": used_seconds,
            "failure": failure,
            "result": {"quality_evaluation": quality_evaluation},
        }
        return canonical_json_bytes(document), status, used_seconds, failure

    def _validate_static_inputs(self) -> None:
        if digest_file(self._profile.modal_script) != self._profile.modal_script_digest:
            raise RuntimeError("Modal quality script digest differs")
        campaign = ModelServingCampaign.load(self._profile.campaign_manifest)
        if campaign.manifest_digest != self._profile.campaign_manifest_digest:
            raise RuntimeError("Modal quality campaign digest differs")
        for path, expected, name in (
            (
                self._profile.hidden_workload_manifest,
                self._profile.hidden_workload_manifest_digest,
                "hidden workload manifest",
            ),
            (
                self._profile.quality_profile,
                self._profile.quality_profile_digest,
                "quality profile",
            ),
            (
                self._profile.quality_workload,
                self._profile.quality_workload_digest,
                "quality workload",
            ),
            (
                self._profile.quality_requests,
                self._profile.quality_requests_digest,
                "quality requests",
            ),
        ):
            if digest_file(path) != expected:
                raise RuntimeError(f"Modal quality {name} digest differs")


def _validate_durable_evidence(
    normalized: Mapping[str, Any], evidence_volume: str
) -> None:
    durable = normalized.get("durable_evidence")
    if not isinstance(durable, dict):
        raise RuntimeError("Modal quality durable evidence identity is invalid")
    normalized_digest = durable.get("normalized_digest")
    if (
        durable.get("volume_name") != evidence_volume
        or not isinstance(normalized_digest, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", normalized_digest)
    ):
        raise RuntimeError("Modal quality durable evidence identity is invalid")
    unsealed = dict(normalized)
    unsealed["durable_evidence"] = {
        key: value for key, value in durable.items() if key != "normalized_digest"
    }
    if digest_bytes(canonical_json_bytes(unsealed) + b"\n") != normalized_digest:
        raise RuntimeError("Modal quality normalized evidence digest differs")


def _measurement_id(request: ComputeExecutionRequest) -> str:
    return "exec-" + request.request_digest[7:]


def _dispatch_path(
    state_root: Path, measurement_id: str, repetition: int, attempt: int
) -> Path:
    return (
        state_root
        / "quality-measurements"
        / ".dispatch"
        / measurement_id
        / f"repetition-{repetition:04d}-attempt-{attempt:02d}.json"
    )
