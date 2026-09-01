"""Development Modal transport for the pinned vLLM evaluator contract."""

from __future__ import annotations

import importlib.metadata
import json
import math
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from ..campaigns.model_serving import ModelServingCampaign
from ..canonical import (
    canonical_json_bytes,
    digest_bytes,
    digest_file,
    digest_value,
    load_json,
    parse_json,
)
from ..compute_backend import (
    ComputeEvidencePointer,
    ComputeExecutionRequest,
    ComputeExecutionStatus,
    ExternalDispatch,
    TransportPoll,
)
from ..ports import ComputeSpendAuthorizationService
from .local_measurements import LocalMeasurementBundleStore


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}")


@dataclass(frozen=True, slots=True)
class ModalVllmComputeProfile:
    profile_id: str
    modal_environment: str
    modal_client_version: str
    modal_script: Path
    modal_script_digest: str
    campaign_manifest: Path
    campaign_manifest_digest: str
    performance_profile: Path
    performance_profile_digest: str
    repetition: int
    attempt: int
    maximum_collection_seconds: int
    evidence_volume: str
    _digest: str

    @classmethod
    def load(
        cls, profile_path: Path, *, repository_root: Path
    ) -> "ModalVllmComputeProfile":
        repository_root = repository_root.resolve()
        with profile_path.open("r", encoding="utf-8") as source:
            document = load_json(source)
        expected = {
            "schema_version",
            "profile_id",
            "backend",
            "mode",
            "modal_environment",
            "modal_client_version",
            "modal_script",
            "modal_script_digest",
            "campaign_manifest",
            "campaign_manifest_digest",
            "performance_profile",
            "performance_profile_digest",
            "repetition",
            "attempt",
            "maximum_collection_seconds",
            "evidence_volume",
        }
        if not isinstance(document, dict) or set(document) != expected:
            raise ValueError("Modal compute profile fields differ")
        if (
            document["schema_version"] != "modal-vllm-compute-profile/v0alpha1"
            or document["backend"] != "modal_vllm_cli"
            or document["mode"] != "development_single_repetition"
        ):
            raise ValueError("Modal compute profile identity is invalid")
        modal_script = _repository_member(repository_root, document["modal_script"])
        campaign_manifest = _repository_member(
            repository_root, document["campaign_manifest"]
        )
        performance_profile = _repository_member(
            repository_root, document["performance_profile"]
        )
        if digest_file(modal_script) != document["modal_script_digest"]:
            raise ValueError("Modal evaluator script digest differs")
        campaign = ModelServingCampaign.load(campaign_manifest)
        if campaign.manifest_digest != document["campaign_manifest_digest"]:
            raise ValueError("Modal campaign manifest digest differs")
        if digest_file(performance_profile) != document["performance_profile_digest"]:
            raise ValueError("Modal performance profile digest differs")
        if (
            document["performance_profile_digest"]
            != campaign.transitive_digests["public_profile"]
        ):
            raise ValueError("development Modal profile must use public performance")
        if importlib.metadata.version("modal") != document["modal_client_version"]:
            raise ValueError("installed Modal client differs from the profile")
        for key in ("profile_id", "modal_environment", "evidence_volume"):
            if not isinstance(document[key], str) or not document[key]:
                raise ValueError(f"Modal compute profile {key} is invalid")
        if type(document["repetition"]) is not int or document["repetition"] < 1:
            raise ValueError("Modal compute repetition is invalid")
        if type(document["attempt"]) is not int or document["attempt"] < 1:
            raise ValueError("Modal compute attempt is invalid")
        maximum_collection_seconds = document["maximum_collection_seconds"]
        if (
            type(maximum_collection_seconds) is not int
            or not 0 <= maximum_collection_seconds <= 300
        ):
            raise ValueError("Modal collection limit is invalid")
        return cls(
            profile_id=str(document["profile_id"]),
            modal_environment=str(document["modal_environment"]),
            modal_client_version=str(document["modal_client_version"]),
            modal_script=modal_script,
            modal_script_digest=str(document["modal_script_digest"]),
            campaign_manifest=campaign_manifest,
            campaign_manifest_digest=str(document["campaign_manifest_digest"]),
            performance_profile=performance_profile,
            performance_profile_digest=str(document["performance_profile_digest"]),
            repetition=document["repetition"],
            attempt=document["attempt"],
            maximum_collection_seconds=maximum_collection_seconds,
            evidence_volume=str(document["evidence_volume"]),
            _digest=digest_value(document),
        )

    @property
    def digest(self) -> str:
        return self._digest

    @property
    def evaluator_profile_digest(self) -> str:
        campaign = ModelServingCampaign.load(self.campaign_manifest)
        return digest_value(
            {
                "adapter": "modal-vllm-development-evaluator/v0alpha1",
                "compute_profile_digest": self.digest,
                "campaign_manifest_digest": campaign.manifest_digest,
                "performance_profile_digest": self.performance_profile_digest,
                "measurement_profile_digest": campaign.measurement_profile().digest,
                "scoring_profile_digest": campaign.scoring_profile().digest,
                "repetitions": 1,
            }
        )


class ModalEvidencePointerResolver(Protocol):
    """Resolve one retained Modal result into terminal compute evidence."""

    def pointer(
        self,
        request: ComputeExecutionRequest,
        external_call_id: str,
    ) -> tuple[
        ComputeEvidencePointer,
        ComputeExecutionStatus,
        int,
        str | None,
    ]: ...


class ModalVllmCliTransport:
    """Dispatch one explicit development repetition through Modal's CLI."""

    def __init__(
        self,
        profile: ModalVllmComputeProfile,
        repository_root: Path,
        state_root: Path,
        modal_cli: Path,
        spend_authorization: ComputeSpendAuthorizationService,
        *,
        evaluator_profile_digest: str | None = None,
        evidence_resolver: ModalEvidencePointerResolver | None = None,
    ) -> None:
        self._profile = profile
        self._repository_root = repository_root.resolve()
        self._state_root = state_root.resolve()
        self._modal_cli = modal_cli.resolve()
        self._spend_authorization = spend_authorization
        self._evidence_resolver = evidence_resolver
        self._evaluator_profile_digest = (
            evaluator_profile_digest or profile.evaluator_profile_digest
        )
        if not re.fullmatch(
            r"sha256:[0-9a-f]{64}", self._evaluator_profile_digest
        ):
            raise ValueError("Modal evaluator profile digest is invalid")
        if not self._modal_cli.is_file():
            raise ValueError("Modal CLI path does not exist")
        self._campaign = ModelServingCampaign.load(profile.campaign_manifest)
        self._measurements = LocalMeasurementBundleStore(
            self._state_root / "measurements"
        )
        self._profile_digest = self.profile_digest_for(
            profile.digest,
            self._modal_cli,
            spend_authorization.profile_digest,
        )

    @staticmethod
    def profile_digest_for(
        compute_profile_digest: str,
        modal_cli: Path,
        spend_authorization_profile_digest: str,
    ) -> str:
        return digest_value(
            {
                "adapter": "modal-vllm-cli-transport/v0alpha3",
                "compute_profile_digest": compute_profile_digest,
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
        candidate_path = self._prepare_request(request, candidate)
        measurement_id = _measurement_id(request)
        completed = _load_optional(
            self._measurements,
            measurement_id,
            self._profile.repetition,
            self._profile.attempt,
        )
        if completed is not None:
            raise RuntimeError("Modal execution already has terminal evidence")
        self._spend_authorization.consume(request, self.profile_digest)
        command = self._command(
            candidate_path, measurement_id, dispatch_only=True
        )
        result = subprocess.run(
            command,
            cwd=self._repository_root,
            env=_minimal_modal_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
            check=False,
        )
        dispatch_record = self._dispatch_record(measurement_id)
        if result.returncode != 0 and dispatch_record is None:
            raise RuntimeError(
                "Modal dispatch outcome is ambiguous: " + result.stdout[-4000:]
            )
        if dispatch_record is None:
            raise RuntimeError("Modal dispatch returned without a durable call record")
        external_call_id = dispatch_record.get("function_call_id")
        if not isinstance(external_call_id, str) or not external_call_id:
            raise RuntimeError("Modal dispatch call ID is invalid")
        expected = {
            "measurement_id": measurement_id,
            "campaign_manifest_digest": self._campaign.manifest_digest,
            "performance_profile_digest": self._profile.performance_profile_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "repetition": self._profile.repetition,
            "attempt": self._profile.attempt,
        }
        if any(dispatch_record.get(key) != value for key, value in expected.items()):
            raise RuntimeError("Modal dispatch record differs from the compute request")
        return ExternalDispatch(
            external_call_id,
            digest_value(dispatch_record),
        )

    def poll(
        self,
        request: ComputeExecutionRequest,
        external_call_id: str,
        timeout_seconds: int,
    ) -> TransportPoll:
        if timeout_seconds > self._profile.maximum_collection_seconds:
            raise ValueError("collection timeout exceeds the Modal profile")
        candidate_path = self._candidate_path(request)
        self._validate_prepared_request(request, candidate_path)
        measurement_id = _measurement_id(request)
        bundle = _load_optional(
            self._measurements,
            measurement_id,
            self._profile.repetition,
            self._profile.attempt,
        )
        if bundle is None:
            result = subprocess.run(
                self._command(
                    candidate_path,
                    measurement_id,
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
                self._profile.repetition,
                self._profile.attempt,
            )
            if bundle is None:
                if result.returncode == 0:
                    return TransportPoll(ComputeExecutionStatus.DISPATCHED)
                raise RuntimeError(
                    "Modal collection failed without terminal evidence: "
                    + result.stdout[-4000:]
                )
        resolver = self._evidence_resolver or ModalVllmEvidenceResolver(
            self._profile,
            self._repository_root,
            self._state_root,
            self.profile_digest,
        )
        pointer, status, used_seconds, failure = resolver.pointer(
            request, external_call_id
        )
        return TransportPoll(status, pointer, used_seconds, failure)

    def _prepare_request(
        self, request: ComputeExecutionRequest, candidate: bytes
    ) -> Path:
        if request.evaluator_profile_digest != self._evaluator_profile_digest:
            raise ValueError("compute request uses another evaluator profile")
        if digest_bytes(candidate) != request.candidate_digest:
            raise ValueError("candidate bytes differ from the compute request")
        document = parse_json(candidate.decode("utf-8"))
        descriptor = self._campaign.validate_candidate_document(document)
        if descriptor.manifest_digest != request.candidate_manifest_digest:
            raise ValueError("candidate manifest digest differs from the request")
        candidate_path = self._candidate_path(request)
        _write_once(candidate_path, candidate)
        request_path = self._request_path(request)
        _write_once(
            request_path,
            canonical_json_bytes(
                {
                    "schema_version": "modal-vllm-compute-request/v0alpha1",
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
            raise RuntimeError("prepared Modal candidate digest differs")
        request_document = _load_object(self._request_path(request))
        expected = {
            "schema_version": "modal-vllm-compute-request/v0alpha1",
            "request": _request_document(request),
            "transport_profile_digest": self.profile_digest,
        }
        if request_document != expected:
            raise RuntimeError("prepared Modal request differs")

    def _command(
        self,
        candidate_path: Path,
        measurement_id: str,
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
            "--baseline",
            "--candidate-path",
            str(candidate_path),
            "--repetition",
            str(self._profile.repetition),
            "--attempt",
            str(self._profile.attempt),
            "--baseline-output-root",
            str(self._state_root / "measurements"),
            "--performance-profile-path",
            str(self._profile.performance_profile),
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

    def _dispatch_record(self, measurement_id: str) -> Mapping[str, Any] | None:
        path = (
            self._state_root
            / "measurements"
            / ".dispatch"
            / measurement_id
            / (
                f"repetition-{self._profile.repetition:04d}-"
                f"attempt-{self._profile.attempt:02d}.json"
            )
        )
        try:
            return _load_object(path)
        except FileNotFoundError:
            return None

    def _candidate_path(self, request: ComputeExecutionRequest) -> Path:
        return self._state_root / "candidates" / f"{request.request_digest[7:]}.json"

    def _request_path(self, request: ComputeExecutionRequest) -> Path:
        return self._state_root / "requests" / f"{request.request_digest[7:]}.json"


class ModalVllmEvidenceResolver:
    """Reconstruct normalized evidence from the immutable local Modal mirror."""

    def __init__(
        self,
        profile: ModalVllmComputeProfile,
        repository_root: Path,
        state_root: Path,
        transport_profile_digest: str,
    ) -> None:
        self._profile = profile
        self._repository_root = repository_root.resolve()
        self._state_root = state_root.resolve()
        self._measurements = LocalMeasurementBundleStore(
            self._state_root / "measurements"
        )
        self._transport_profile_digest = transport_profile_digest
        self._profile_digest = self.profile_digest_for(profile.digest)

    @staticmethod
    def profile_digest_for(compute_profile_digest: str) -> str:
        return digest_value(
            {
                "adapter": "modal-vllm-evidence-resolver/v0alpha1",
                "compute_profile_digest": compute_profile_digest,
                "source": "digest_verified_local_mirror_of_modal_volume",
            }
        )

    @property
    def profile_digest(self) -> str:
        return self._profile_digest

    def resolve_dispatch(
        self, request: ComputeExecutionRequest, external_call_id: str
    ) -> bytes:
        measurement_id = _measurement_id(request)
        dispatch = self._dispatch_record(measurement_id)
        expected = {
            "measurement_id": measurement_id,
            "campaign_manifest_digest": self._profile.campaign_manifest_digest,
            "performance_profile_digest": self._profile.performance_profile_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "repetition": self._profile.repetition,
            "attempt": self._profile.attempt,
            "function_call_id": external_call_id,
        }
        if any(dispatch.get(key) != value for key, value in expected.items()):
            raise RuntimeError("Modal dispatch evidence identity differs")
        return canonical_json_bytes(dispatch)

    def pointer(
        self, request: ComputeExecutionRequest, external_call_id: str
    ) -> tuple[
        ComputeEvidencePointer, ComputeExecutionStatus, int, str | None
    ]:
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
        request_name = pointer.locator.removeprefix("exec-") + ".json"
        request_path = self._state_root / "requests" / request_name
        envelope = _load_object(request_path)
        request = _request(envelope.get("request"))
        dispatch = self._dispatch_record(pointer.locator)
        external_call_id = dispatch.get("function_call_id")
        if not isinstance(external_call_id, str) or not external_call_id:
            raise RuntimeError("Modal dispatch evidence has no call ID")
        content, _, _, _ = self._build(request, external_call_id)
        return content

    def _build(
        self, request: ComputeExecutionRequest, external_call_id: str
    ) -> tuple[bytes, ComputeExecutionStatus, int, str | None]:
        measurement_id = _measurement_id(request)
        bundle = self._measurements.load(
            measurement_id,
            self._profile.repetition,
            attempt=self._profile.attempt,
        )
        normalized = bundle.receipt["normalized"]
        if not isinstance(normalized, dict):
            raise RuntimeError("Modal normalized evidence is invalid")
        expected = {
            "campaign_manifest_digest": self._profile.campaign_manifest_digest,
            "performance_profile_digest": self._profile.performance_profile_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "modal_function_call_id": external_call_id,
            "repetition": self._profile.repetition,
            "attempt": self._profile.attempt,
        }
        if any(normalized.get(key) != value for key, value in expected.items()):
            raise RuntimeError("Modal normalized evidence identity differs")
        valid = normalized.get("valid") is True
        measurement_complete = valid or (
            normalized.get("failure") is None
            and isinstance(normalized.get("performance_score"), dict)
            and normalized.get("parse_errors") == []
            and normalized.get("environment_errors") == []
        )
        dispatch = self._dispatch_record(measurement_id)
        platform_build = normalized.get("platform_build")
        if (
            not isinstance(platform_build, dict)
            or platform_build.get("git_commit") != dispatch.get("git_commit")
            or platform_build.get("modal_client_version")
            != self._profile.modal_client_version
        ):
            raise RuntimeError("Modal platform build evidence differs")
        durable_evidence = normalized.get("durable_evidence")
        if measurement_complete:
            self._validate_durable_evidence(normalized, durable_evidence)
        elif (
            not isinstance(normalized.get("failure"), dict)
            or normalized.get("performance_score") is not None
        ):
            raise RuntimeError("Modal terminal failure evidence is invalid")
        status = (
            ComputeExecutionStatus.COMPLETE
            if measurement_complete
            else ComputeExecutionStatus.FAILED
        )
        used_seconds = _used_seconds(normalized, request.maximum_seconds)
        failure = None if measurement_complete else _failure(normalized)
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
            "result": normalized,
        }
        return canonical_json_bytes(document), status, used_seconds, failure

    def _validate_durable_evidence(
        self, normalized: Mapping[str, Any], durable_evidence: object
    ) -> None:
        if not isinstance(durable_evidence, dict):
            raise RuntimeError("Modal durable evidence identity is invalid")
        normalized_digest = durable_evidence.get("normalized_digest")
        if (
            durable_evidence.get("volume_name") != self._profile.evidence_volume
            or not isinstance(normalized_digest, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", normalized_digest)
        ):
            raise RuntimeError("Modal durable evidence identity is invalid")
        unsealed_normalized = dict(normalized)
        unsealed_normalized["durable_evidence"] = {
            key: value
            for key, value in durable_evidence.items()
            if key != "normalized_digest"
        }
        normalized_bytes = (
            json.dumps(
                unsealed_normalized,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
        if digest_bytes(normalized_bytes) != normalized_digest:
            raise RuntimeError("Modal durable normalized evidence digest differs")

    def _dispatch_record(self, measurement_id: str) -> Mapping[str, Any]:
        path = (
            self._state_root
            / "measurements"
            / ".dispatch"
            / measurement_id
            / (
                f"repetition-{self._profile.repetition:04d}-"
                f"attempt-{self._profile.attempt:02d}.json"
            )
        )
        return _load_object(path)


def _measurement_id(request: ComputeExecutionRequest) -> str:
    return "exec-" + request.request_digest[7:]


def _request_document(request: ComputeExecutionRequest) -> dict[str, object]:
    return {
        "execution_key": request.execution_key,
        "campaign_run_id": request.campaign_run_id,
        "reservation_id": request.reservation_id,
        "scope": request.scope.value,
        "candidate_digest": request.candidate_digest,
        "candidate_manifest_digest": request.candidate_manifest_digest,
        "evaluator_profile_digest": request.evaluator_profile_digest,
        "maximum_seconds": request.maximum_seconds,
    }


def _request(value: object) -> ComputeExecutionRequest:
    if not isinstance(value, dict):
        raise RuntimeError("stored Modal compute request is invalid")
    from ..evaluation import EvaluationScope

    return ComputeExecutionRequest(
        execution_key=str(value["execution_key"]),
        campaign_run_id=str(value["campaign_run_id"]),
        reservation_id=str(value["reservation_id"]),
        scope=EvaluationScope(str(value["scope"])),
        candidate_digest=str(value["candidate_digest"]),
        candidate_manifest_digest=str(value["candidate_manifest_digest"]),
        evaluator_profile_digest=str(value["evaluator_profile_digest"]),
        maximum_seconds=value["maximum_seconds"],
    )


def _repository_member(repository_root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("repository member path is invalid")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("repository member path must be relative")
    resolved = (repository_root / relative).resolve(strict=True)
    if not resolved.is_relative_to(repository_root):
        raise ValueError("repository member escapes the repository")
    return resolved


def _minimal_modal_environment() -> dict[str, str]:
    allowed = (
        "HOME",
        "PATH",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "MODAL_TOKEN_ID",
        "MODAL_TOKEN_SECRET",
    )
    return {key: os.environ[key] for key in allowed if key in os.environ}


def _write_once(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = path.read_bytes()
    except FileNotFoundError:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                existing = path.read_bytes()
                if existing != content:
                    raise RuntimeError("durable Modal input already differs")
            finally:
                temporary.unlink(missing_ok=True)
            _fsync_directory(path.parent)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    else:
        if existing != content:
            raise RuntimeError("durable Modal input already differs")


def _load_object(path: Path) -> Mapping[str, Any]:
    with path.open("r", encoding="utf-8") as source:
        document = load_json(source)
    if not isinstance(document, dict):
        raise RuntimeError(f"JSON object expected at {path}")
    return document


def _load_optional(
    store: LocalMeasurementBundleStore,
    measurement_id: str,
    repetition: int,
    attempt: int,
):
    try:
        return store.load(measurement_id, repetition, attempt=attempt)
    except KeyError:
        return None


def _used_seconds(normalized: Mapping[str, Any], maximum_seconds: int) -> int:
    remote = normalized.get("remote_receipt")
    if not isinstance(remote, dict):
        return maximum_seconds
    timing = remote.get("timing")
    if not isinstance(timing, dict):
        return maximum_seconds
    milliseconds = timing.get("function_body_ms")
    if type(milliseconds) not in {int, float} or not math.isfinite(milliseconds):
        return maximum_seconds
    return max(0, math.ceil(milliseconds / 1000))


def _failure(normalized: Mapping[str, Any]) -> str:
    failure = normalized.get("failure")
    if isinstance(failure, dict):
        return json.dumps(failure, sort_keys=True, separators=(",", ":"))[-2000:]
    errors = normalized.get("parse_errors") or normalized.get("environment_errors")
    return str(errors or "Modal evaluator marked the repetition invalid")[-2000:]


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
