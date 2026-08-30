"""Modal transport and trusted evidence adapter for hidden correctness."""

from __future__ import annotations

import importlib.metadata
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..campaigns.model_serving import ModelServingCampaign
from ..campaigns.serving_correctness import (
    load_correctness_workload,
    score_correctness_responses,
)
from ..campaigns.serving_workload import HiddenWorkloadBundle
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


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_EXECUTION_KEY = re.compile(r".+:correctness")


@dataclass(frozen=True, slots=True)
class ModalVllmCorrectnessProfile:
    """Frozen inputs for one hidden Modal correctness execution."""

    profile_id: str
    modal_environment: str
    modal_client_version: str
    modal_script: Path
    modal_script_digest: str
    campaign_manifest: Path
    campaign_manifest_digest: str
    hidden_workload_manifest: Path
    hidden_workload_manifest_digest: str
    correctness_workload: Path
    correctness_workload_digest: str
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
    ) -> "ModalVllmCorrectnessProfile":
        if not profile_id or not modal_environment or not evidence_volume:
            raise ValueError("Modal correctness profile strings must be nonempty")
        if type(attempt) is not int or attempt < 1:
            raise ValueError("Modal correctness attempt is invalid")
        if (
            type(maximum_collection_seconds) is not int
            or not 0 <= maximum_collection_seconds <= 300
        ):
            raise ValueError("Modal correctness collection limit is invalid")
        if importlib.metadata.version("modal") != modal_client_version:
            raise ValueError("installed Modal client differs from the profile")
        if attempt > campaign.measurement_profile().max_attempts:
            raise ValueError("Modal correctness attempt exceeds the campaign profile")
        campaign_manifest = campaign_manifest.resolve(strict=True)
        if ModelServingCampaign.load(campaign_manifest) != campaign:
            raise ValueError("Modal correctness campaign manifest differs")
        workload_path = hidden_workload.resource_paths["correctness_requests"]
        workload = load_correctness_workload(workload_path)
        workload_digest = hidden_workload.resource_digests[
            "correctness_requests"
        ]
        if workload.digest != workload_digest:
            raise ValueError("hidden correctness workload digest differs")
        campaign.validate_reference_candidate()
        document = {
            "adapter": "modal-vllm-correctness-profile/v0alpha1",
            "profile_id": profile_id,
            "modal_environment": modal_environment,
            "modal_client_version": modal_client_version,
            "modal_script_digest": digest_file(modal_script),
            "campaign_manifest_digest": campaign.manifest_digest,
            "hidden_workload_manifest_digest": hidden_workload.manifest_digest,
            "correctness_workload_digest": workload_digest,
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
            correctness_workload=workload_path,
            correctness_workload_digest=workload_digest,
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
            raise RuntimeError("Modal correctness script digest differs")
        if campaign.manifest_digest != self.campaign_manifest_digest:
            raise RuntimeError("Modal correctness campaign digest differs")
        if digest_file(self.hidden_workload_manifest) != (
            self.hidden_workload_manifest_digest
        ):
            raise RuntimeError("hidden workload manifest digest differs")
        workload = load_correctness_workload(self.correctness_workload)
        if workload.digest != self.correctness_workload_digest:
            raise RuntimeError("hidden correctness workload digest differs")


class ModalVllmCorrectnessCliTransport:
    """Dispatch one registered hidden correctness execution through Modal."""

    def __init__(
        self,
        profile: ModalVllmCorrectnessProfile,
        repository_root: Path,
        state_root: Path,
        modal_cli: Path,
        spend_authorization: ComputeSpendAuthorizationService,
        *,
        evaluator_profile_digest: str,
    ) -> None:
        if not _DIGEST.fullmatch(evaluator_profile_digest):
            raise ValueError("Modal correctness evaluator digest is invalid")
        self._profile = profile
        self._repository_root = repository_root.resolve()
        self._state_root = state_root.resolve()
        self._modal_cli = modal_cli.resolve()
        self._spend_authorization = spend_authorization
        self._evaluator_profile_digest = evaluator_profile_digest
        if not self._modal_cli.is_file():
            raise ValueError("Modal CLI path does not exist")
        self._campaign = ModelServingCampaign.load(profile.campaign_manifest)
        profile.validate_inputs(self._campaign)
        self._measurements = LocalMeasurementBundleStore(
            self._state_root / "correctness-measurements"
        )
        self._profile_digest = self.profile_digest_for(
            profile.digest, self._modal_cli, spend_authorization.profile_digest
        )

    @staticmethod
    def profile_digest_for(
        correctness_profile_digest: str,
        modal_cli: Path,
        spend_authorization_profile_digest: str,
    ) -> str:
        return digest_value(
            {
                "adapter": "modal-vllm-correctness-cli-transport/v0alpha1",
                "correctness_profile_digest": correctness_profile_digest,
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
        role = self._validate_request(request, candidate)
        candidate_path = self._prepare_request(request, candidate)
        measurement_id = _measurement_id(request)
        if _load_optional(
            self._measurements, measurement_id, 1, self._profile.attempt
        ) is not None:
            raise RuntimeError("Modal correctness already has terminal evidence")
        self._spend_authorization.consume(request, self.profile_digest)
        result = subprocess.run(
            self._command(
                candidate_path, measurement_id, role, dispatch_only=True
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
        dispatch = self._dispatch_record(measurement_id)
        if result.returncode != 0 and dispatch is None:
            raise RuntimeError(
                "Modal correctness dispatch outcome is ambiguous: "
                + result.stdout[-4000:]
            )
        if dispatch is None:
            raise RuntimeError("Modal correctness dispatch has no durable call record")
        external_call_id = dispatch.get("function_call_id")
        if not isinstance(external_call_id, str) or not external_call_id:
            raise RuntimeError("Modal correctness dispatch call ID is invalid")
        self._validate_dispatch(dispatch, request, role, external_call_id)
        return ExternalDispatch(external_call_id, digest_value(dispatch))

    def poll(
        self,
        request: ComputeExecutionRequest,
        external_call_id: str,
        timeout_seconds: int,
    ) -> TransportPoll:
        if timeout_seconds > self._profile.maximum_collection_seconds:
            raise ValueError("collection timeout exceeds the correctness profile")
        role = self._request_role(request)
        candidate_path = self._candidate_path(request)
        self._validate_prepared_request(request, candidate_path)
        measurement_id = _measurement_id(request)
        bundle = _load_optional(
            self._measurements, measurement_id, 1, self._profile.attempt
        )
        if bundle is None:
            result = subprocess.run(
                self._command(
                    candidate_path,
                    measurement_id,
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
                self._measurements, measurement_id, 1, self._profile.attempt
            )
            if bundle is None:
                if result.returncode == 0:
                    return TransportPoll(ComputeExecutionStatus.DISPATCHED)
                raise RuntimeError(
                    "Modal correctness collection failed without evidence: "
                    + result.stdout[-4000:]
                )
        resolver = ModalVllmCorrectnessEvidenceResolver(
            self._profile, self._state_root, self.profile_digest
        )
        pointer, status, used_seconds, failure = resolver.pointer(
            request, external_call_id
        )
        return TransportPoll(status, pointer, used_seconds, failure)

    def _validate_request(
        self, request: ComputeExecutionRequest, candidate: bytes
    ) -> str:
        if request.scope is not EvaluationScope.HIDDEN:
            raise ValueError("Modal correctness requires hidden scope")
        if request.evaluator_profile_digest != self._evaluator_profile_digest:
            raise ValueError("Modal correctness evaluator profile differs")
        if digest_bytes(candidate) != request.candidate_digest:
            raise ValueError("candidate bytes differ from the compute request")
        descriptor = self._campaign.validate_candidate_document(
            parse_json(candidate.decode("utf-8"))
        )
        if descriptor.manifest_digest != request.candidate_manifest_digest:
            raise ValueError("candidate manifest digest differs from the request")
        self._profile.validate_inputs(self._campaign)
        role = self._request_role(request)
        reference = self._campaign.validate_reference_candidate()
        if role == "reference" and descriptor.manifest_digest != (
            reference.manifest_digest
        ):
            raise ValueError("correctness reference role requires the reference artifact")
        return role

    @staticmethod
    def _request_role(request: ComputeExecutionRequest) -> str:
        if _EXECUTION_KEY.fullmatch(request.execution_key) is None:
            raise ValueError("Modal correctness execution key is invalid")
        return (
            "reference"
            if request.execution_key.startswith("hidden:reference:")
            else "candidate"
        )

    def _prepare_request(
        self, request: ComputeExecutionRequest, candidate: bytes
    ) -> Path:
        candidate_path = self._candidate_path(request)
        _write_once(candidate_path, candidate)
        _write_once(
            self._request_path(request),
            canonical_json_bytes(
                {
                    "schema_version": "modal-vllm-correctness-request/v0alpha1",
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
            raise RuntimeError("prepared correctness candidate digest differs")
        expected = {
            "schema_version": "modal-vllm-correctness-request/v0alpha1",
            "request": _request_document(request),
            "transport_profile_digest": self.profile_digest,
        }
        if _load_object(self._request_path(request)) != expected:
            raise RuntimeError("prepared correctness request differs")
        self._profile.validate_inputs(self._campaign)

    def _command(
        self,
        candidate_path: Path,
        measurement_id: str,
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
            "--correctness",
            "--candidate-path",
            str(candidate_path),
            "--repetition",
            "1",
            "--attempt",
            str(self._profile.attempt),
            "--correctness-output-root",
            str(self._state_root / "correctness-measurements"),
            "--correctness-workload-path",
            str(self._profile.correctness_workload),
            "--correctness-profile-digest",
            self._profile.digest,
            "--correctness-hidden-manifest-digest",
            self._profile.hidden_workload_manifest_digest,
            "--correctness-role",
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
        role: str,
        external_call_id: str,
    ) -> None:
        expected = {
            "measurement_id": _measurement_id(request),
            "campaign_manifest_digest": self._profile.campaign_manifest_digest,
            "hidden_workload_manifest_digest": (
                self._profile.hidden_workload_manifest_digest
            ),
            "correctness_profile_digest": self._profile.digest,
            "correctness_workload_digest": (
                self._profile.correctness_workload_digest
            ),
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "role": role,
            "repetition": 1,
            "attempt": self._profile.attempt,
            "function_call_id": external_call_id,
        }
        if any(dispatch.get(key) != value for key, value in expected.items()):
            raise RuntimeError("Modal correctness dispatch identity differs")

    def _dispatch_record(self, measurement_id: str) -> Mapping[str, Any] | None:
        try:
            return _load_object(
                _dispatch_path(self._state_root, measurement_id, self._profile.attempt)
            )
        except FileNotFoundError:
            return None

    def _candidate_path(self, request: ComputeExecutionRequest) -> Path:
        return (
            self._state_root
            / "correctness-candidates"
            / f"{request.request_digest[7:]}.json"
        )

    def _request_path(self, request: ComputeExecutionRequest) -> Path:
        return (
            self._state_root
            / "correctness-requests"
            / f"{request.request_digest[7:]}.json"
        )


class ModalVllmCorrectnessEvidenceResolver:
    """Verify trusted scoring and normalize a correctness phase result."""

    def __init__(
        self,
        profile: ModalVllmCorrectnessProfile,
        state_root: Path,
        transport_profile_digest: str,
    ) -> None:
        self._profile = profile
        self._state_root = state_root.resolve()
        self._transport_profile_digest = transport_profile_digest
        self._campaign = ModelServingCampaign.load(profile.campaign_manifest)
        profile.validate_inputs(self._campaign)
        self._workload = load_correctness_workload(
            profile.correctness_workload
        )
        self._measurements = LocalMeasurementBundleStore(
            self._state_root / "correctness-measurements"
        )
        self._profile_digest = self.profile_digest_for(profile.digest)

    @staticmethod
    def profile_digest_for(correctness_profile_digest: str) -> str:
        return digest_value(
            {
                "adapter": "modal-vllm-correctness-evidence/v0alpha1",
                "correctness_profile_digest": correctness_profile_digest,
                "source": "trusted_score_over_digest_verified_modal_responses",
            }
        )

    @property
    def profile_digest(self) -> str:
        return self._profile_digest

    def resolve_dispatch(
        self, request: ComputeExecutionRequest, external_call_id: str
    ) -> bytes:
        role = ModalVllmCorrectnessCliTransport._request_role(request)
        dispatch = _load_object(
            _dispatch_path(
                self._state_root,
                _measurement_id(request),
                self._profile.attempt,
            )
        )
        expected = self._dispatch_identity(request, role, external_call_id)
        if any(dispatch.get(key) != value for key, value in expected.items()):
            raise RuntimeError("Modal correctness dispatch evidence differs")
        return canonical_json_bytes(dispatch)

    def pointer(
        self, request: ComputeExecutionRequest, external_call_id: str
    ) -> tuple[ComputeEvidencePointer, ComputeExecutionStatus, int, str | None]:
        content, status, used_seconds, failure = self._build(
            request, external_call_id
        )
        return (
            ComputeEvidencePointer(_measurement_id(request), digest_bytes(content)),
            status,
            used_seconds,
            failure,
        )

    def resolve(self, pointer: ComputeEvidencePointer) -> bytes:
        request_path = (
            self._state_root
            / "correctness-requests"
            / (pointer.locator.removeprefix("exec-") + ".json")
        )
        request = _request(_load_object(request_path).get("request"))
        dispatch = _load_object(
            _dispatch_path(self._state_root, pointer.locator, self._profile.attempt)
        )
        call_id = dispatch.get("function_call_id")
        if not isinstance(call_id, str) or not call_id:
            raise RuntimeError("Modal correctness dispatch has no call ID")
        content, _, _, _ = self._build(request, call_id)
        return content

    def _build(
        self, request: ComputeExecutionRequest, external_call_id: str
    ) -> tuple[bytes, ComputeExecutionStatus, int, str | None]:
        role = ModalVllmCorrectnessCliTransport._request_role(request)
        measurement_id = _measurement_id(request)
        bundle = self._measurements.load(
            measurement_id, 1, attempt=self._profile.attempt
        )
        normalized = bundle.receipt["normalized"]
        if not isinstance(normalized, dict):
            raise RuntimeError("Modal correctness normalized evidence is invalid")
        expected = {
            "campaign_manifest_digest": self._profile.campaign_manifest_digest,
            "hidden_workload_manifest_digest": (
                self._profile.hidden_workload_manifest_digest
            ),
            "correctness_profile_digest": self._profile.digest,
            "correctness_workload_digest": (
                self._profile.correctness_workload_digest
            ),
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "role": role,
            "modal_function_call_id": external_call_id,
            "repetition": 1,
            "attempt": self._profile.attempt,
        }
        if any(normalized.get(key) != value for key, value in expected.items()):
            raise RuntimeError("Modal correctness normalized identity differs")
        dispatch = _load_object(
            _dispatch_path(self._state_root, measurement_id, self._profile.attempt)
        )
        platform = normalized.get("platform_build")
        if (
            not isinstance(platform, dict)
            or platform.get("git_commit") != dispatch.get("git_commit")
            or platform.get("modal_client_version")
            != self._profile.modal_client_version
        ):
            raise RuntimeError("Modal correctness platform build differs")
        valid = normalized.get("valid") is True
        if valid:
            self._validate_durable_evidence(normalized)
        elif normalized.get("correctness_result") is not None:
            raise RuntimeError("failed correctness execution contains a result")
        status = (
            ComputeExecutionStatus.COMPLETE
            if valid
            else ComputeExecutionStatus.FAILED
        )
        used_seconds = _used_seconds(normalized, request.maximum_seconds)
        failure = None if valid else _failure(normalized)
        candidate_result = self._candidate_result(
            normalized, bundle.raw_documents, request, valid
        )
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
            "result": {"candidate_evaluation": candidate_result},
        }
        return canonical_json_bytes(document), status, used_seconds, failure

    def _candidate_result(
        self,
        normalized: Mapping[str, Any],
        raw_documents: Mapping[str, bytes],
        request: ComputeExecutionRequest,
        valid: bool,
    ) -> dict[str, Any]:
        scored = normalized.get("correctness_result")
        if valid:
            if not isinstance(scored, dict) or set(scored) != {
                "workload_digest",
                "eligible",
                "passed_cases",
                "total_cases",
                "failures",
                "response_digests",
                "evidence_digest",
            }:
                raise RuntimeError("trusted correctness result fields differ")
            passed = scored.get("passed_cases")
            total = scored.get("total_cases")
            failures = scored.get("failures")
            if (
                scored.get("workload_digest")
                != self._profile.correctness_workload_digest
                or type(scored.get("eligible")) is not bool
                or type(passed) is not int
                or type(total) is not int
                or total < 1
                or not 0 <= passed <= total
                or not isinstance(failures, list)
                or any(not isinstance(value, str) or not value for value in failures)
                or not isinstance(scored.get("response_digests"), dict)
                or not _DIGEST.fullmatch(str(scored.get("evidence_digest", "")))
            ):
                raise RuntimeError("trusted correctness result is invalid")
            expected_names = {
                f"{case.case_id}.json" for case in self._workload.cases
            }
            if set(raw_documents) != expected_names:
                raise RuntimeError("trusted correctness response set differs")
            recomputed = score_correctness_responses(
                self._workload,
                {
                    case.case_id: raw_documents[f"{case.case_id}.json"]
                    for case in self._workload.cases
                },
                served_model_name=str(
                    self._campaign.raw["reference"]["served_model_name"]
                ),
            ).to_document()
            if recomputed != scored:
                raise RuntimeError("trusted correctness score differs from raw evidence")
            criterion_units = passed * 1_000_000 // total
            eligible = scored["eligible"]
            phase_failures = list(failures)
            diagnostics = {
                "passed_cases": passed,
                "total_cases": total,
                "scorer_evidence_digest": scored["evidence_digest"],
                "response_set_digest": digest_value(scored["response_digests"]),
            }
        else:
            criterion_units = 0
            eligible = False
            phase_failures = ["execution_failed"]
            diagnostics = {}
        result = {
            "schema_version": "serving-candidate-compute-evidence/v0alpha1",
            "phase": "correctness",
            "campaign_manifest_digest": self._profile.campaign_manifest_digest,
            "hidden_workload_manifest_digest": (
                self._profile.hidden_workload_manifest_digest
            ),
            "workload_digest": self._profile.correctness_workload_digest,
            "candidate_digest": request.candidate_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "eligible": eligible,
            "criterion_units": criterion_units,
            "failures": phase_failures,
            "diagnostics": diagnostics,
        }
        result["result_evidence_digest"] = digest_value(result)
        return result

    def _validate_durable_evidence(self, normalized: Mapping[str, Any]) -> None:
        evidence = normalized.get("durable_evidence")
        if not isinstance(evidence, dict):
            raise RuntimeError("Modal correctness durable evidence is invalid")
        normalized_digest = evidence.get("normalized_digest")
        if (
            evidence.get("volume_name") != self._profile.evidence_volume
            or not isinstance(normalized_digest, str)
            or not _DIGEST.fullmatch(normalized_digest)
        ):
            raise RuntimeError("Modal correctness durable evidence differs")
        unsealed = dict(normalized)
        unsealed["durable_evidence"] = {
            key: value for key, value in evidence.items() if key != "normalized_digest"
        }
        content = (
            json.dumps(
                unsealed,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
        if digest_bytes(content) != normalized_digest:
            raise RuntimeError("Modal correctness normalized digest differs")

    def _dispatch_identity(
        self,
        request: ComputeExecutionRequest,
        role: str,
        external_call_id: str,
    ) -> dict[str, object]:
        return {
            "measurement_id": _measurement_id(request),
            "campaign_manifest_digest": self._profile.campaign_manifest_digest,
            "hidden_workload_manifest_digest": (
                self._profile.hidden_workload_manifest_digest
            ),
            "correctness_profile_digest": self._profile.digest,
            "correctness_workload_digest": (
                self._profile.correctness_workload_digest
            ),
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "role": role,
            "repetition": 1,
            "attempt": self._profile.attempt,
            "function_call_id": external_call_id,
        }


def _measurement_id(request: ComputeExecutionRequest) -> str:
    return "exec-" + request.request_digest[7:]


def _dispatch_path(state_root: Path, measurement_id: str, attempt: int) -> Path:
    return (
        state_root
        / "correctness-measurements/.dispatch"
        / measurement_id
        / f"repetition-0001-attempt-{attempt:02d}.json"
    )
