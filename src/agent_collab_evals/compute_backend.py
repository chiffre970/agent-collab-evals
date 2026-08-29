"""Provider-neutral compute execution authority, values, and receipts."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .canonical import canonical_json_bytes, digest_bytes, digest_value, parse_json
from .evaluation import EvaluationScope


_SAFE_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class ComputeExecutionStatus(str, Enum):
    REGISTERED = "registered"
    DISPATCHING = "dispatching"
    DISPATCHED = "dispatched"
    COMPLETE = "complete"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class ComputeExecutionRequest:
    execution_key: str
    campaign_run_id: str
    reservation_id: str
    scope: EvaluationScope
    candidate_digest: str
    candidate_manifest_digest: str
    evaluator_profile_digest: str
    maximum_seconds: int

    def __post_init__(self) -> None:
        if not _SAFE_KEY.fullmatch(self.execution_key):
            raise ValueError("compute execution key is invalid")
        if not self.campaign_run_id or not self.reservation_id:
            raise ValueError("compute execution identity must be nonempty")
        for value in (
            self.candidate_digest,
            self.candidate_manifest_digest,
            self.evaluator_profile_digest,
        ):
            if not _DIGEST.fullmatch(value):
                raise ValueError("compute execution digest must be SHA-256")
        if type(self.maximum_seconds) is not int or self.maximum_seconds < 1:
            raise ValueError("compute execution limit must be positive")

    @property
    def request_digest(self) -> str:
        return digest_value(self.document)

    @property
    def document(self) -> dict[str, object]:
        return {
            "execution_key": self.execution_key,
            "campaign_run_id": self.campaign_run_id,
            "reservation_id": self.reservation_id,
            "scope": self.scope.value,
            "candidate_digest": self.candidate_digest,
            "candidate_manifest_digest": self.candidate_manifest_digest,
            "evaluator_profile_digest": self.evaluator_profile_digest,
            "maximum_seconds": self.maximum_seconds,
        }

    @classmethod
    def from_document(cls, value: Mapping[str, Any]) -> "ComputeExecutionRequest":
        expected = {
            "execution_key",
            "campaign_run_id",
            "reservation_id",
            "scope",
            "candidate_digest",
            "candidate_manifest_digest",
            "evaluator_profile_digest",
            "maximum_seconds",
        }
        if set(value) != expected:
            raise ValueError("compute request fields differ")
        return cls(
            execution_key=value["execution_key"],
            campaign_run_id=value["campaign_run_id"],
            reservation_id=value["reservation_id"],
            scope=EvaluationScope(value["scope"]),
            candidate_digest=value["candidate_digest"],
            candidate_manifest_digest=value["candidate_manifest_digest"],
            evaluator_profile_digest=value["evaluator_profile_digest"],
            maximum_seconds=value["maximum_seconds"],
        )


@dataclass(frozen=True, slots=True)
class FrozenComputeRunManifest:
    """Resolve exact compute authority from a write-once run manifest."""

    path: Path
    campaign_run_id: str
    compute_enabled: bool
    transport_profile_digest: str | None
    backend_profile_digest: str | None
    manifest_digest: str

    @classmethod
    def load_or_create(
        cls,
        path: Path,
        *,
        campaign_run_id: str,
        compute_enabled: bool,
        transport_profile_digest: str | None,
        backend_profile_digest: str | None,
        requests: tuple[ComputeExecutionRequest, ...],
    ) -> "FrozenComputeRunManifest":
        document = cls._validated_document(
            {
                "schema_version": "frozen-compute-run-manifest/v0alpha1",
                "campaign_run_id": campaign_run_id,
                "compute_enabled": compute_enabled,
                "transport_profile_digest": transport_profile_digest,
                "backend_profile_digest": backend_profile_digest,
                "requests": [request.document for request in requests],
            }
        )
        content = canonical_json_bytes(document)
        _write_once(path, content)
        return cls.load(path, expected_digest=digest_bytes(content))

    @classmethod
    def load(
        cls, path: Path, *, expected_digest: str
    ) -> "FrozenComputeRunManifest":
        if not _DIGEST.fullmatch(expected_digest):
            raise ValueError("compute run manifest digest is invalid")
        content = path.read_bytes()
        if digest_bytes(content) != expected_digest:
            raise RuntimeError("compute run manifest digest differs")
        value = parse_json(content.decode("utf-8"))
        document = cls._validated_document(value)
        if canonical_json_bytes(document) != content:
            raise RuntimeError("compute run manifest is not canonical")
        return cls(
            path=path.resolve(),
            campaign_run_id=document["campaign_run_id"],
            compute_enabled=document["compute_enabled"],
            transport_profile_digest=document["transport_profile_digest"],
            backend_profile_digest=document["backend_profile_digest"],
            manifest_digest=expected_digest,
        )

    def requests(self) -> tuple[ComputeExecutionRequest, ...]:
        document = self._reload()
        return tuple(
            ComputeExecutionRequest.from_document(value)
            for value in document["requests"]
        )

    def request(self, execution_key: str) -> ComputeExecutionRequest:
        matches = tuple(
            request
            for request in self.requests()
            if request.execution_key == execution_key
        )
        if len(matches) != 1:
            raise RuntimeError("compute request lacks frozen run authority")
        return matches[0]

    def assert_authorized(self, request: ComputeExecutionRequest) -> None:
        if not self.compute_enabled:
            raise RuntimeError("frozen run manifest disables compute")
        if self.request(request.execution_key) != request:
            raise RuntimeError("compute request differs from frozen run authority")

    def assert_backend_profiles(
        self, backend_profile_digest: str, transport_profile_digest: str
    ) -> None:
        self._reload()
        if not self.compute_enabled:
            raise ValueError("compute backend cannot use a no-compute run manifest")
        if (
            self.backend_profile_digest != backend_profile_digest
            or self.transport_profile_digest != transport_profile_digest
        ):
            raise ValueError("compute backend profiles differ from the run manifest")

    def assert_no_compute(self, campaign_run_id: str) -> None:
        document = self._reload()
        if campaign_run_id != self.campaign_run_id:
            raise ValueError("no-compute manifest belongs to another campaign")
        if (
            self.compute_enabled
            or self.transport_profile_digest is not None
            or self.backend_profile_digest is not None
            or document["requests"]
        ):
            raise RuntimeError("registered run manifest enables compute")

    def _reload(self) -> dict[str, Any]:
        content = self.path.read_bytes()
        if digest_bytes(content) != self.manifest_digest:
            raise RuntimeError("compute run manifest changed after registration")
        document = self._validated_document(parse_json(content.decode("utf-8")))
        if canonical_json_bytes(document) != content:
            raise RuntimeError("compute run manifest is not canonical")
        return document

    @staticmethod
    def _validated_document(value: Any) -> dict[str, Any]:
        expected = {
            "schema_version",
            "campaign_run_id",
            "compute_enabled",
            "transport_profile_digest",
            "backend_profile_digest",
            "requests",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("compute run manifest fields differ")
        if value["schema_version"] != "frozen-compute-run-manifest/v0alpha1":
            raise ValueError("compute run manifest schema differs")
        campaign_run_id = value["campaign_run_id"]
        if not isinstance(campaign_run_id, str) or not campaign_run_id:
            raise ValueError("compute run campaign ID is invalid")
        if type(value["compute_enabled"]) is not bool:
            raise ValueError("compute-enabled setting must be boolean")
        requests_value = value["requests"]
        if not isinstance(requests_value, list):
            raise ValueError("compute run requests must be a list")
        requests = tuple(
            ComputeExecutionRequest.from_document(request)
            if isinstance(request, dict)
            else (_raise_invalid_request())
            for request in requests_value
        )
        if any(request.campaign_run_id != campaign_run_id for request in requests):
            raise ValueError("compute request belongs to another campaign")
        keys = tuple(request.execution_key for request in requests)
        if len(set(keys)) != len(keys):
            raise ValueError("compute run request keys must be unique")
        profile_values = (
            value["transport_profile_digest"],
            value["backend_profile_digest"],
        )
        if value["compute_enabled"]:
            if not requests or any(
                not isinstance(item, str) or not _DIGEST.fullmatch(item)
                for item in profile_values
            ):
                raise ValueError("enabled compute run authority is incomplete")
        elif requests or any(item is not None for item in profile_values):
            raise ValueError("disabled compute run manifest must be empty")
        return value


@dataclass(frozen=True, slots=True)
class ExternalDispatch:
    external_call_id: str
    dispatch_evidence_digest: str

    def __post_init__(self) -> None:
        if not self.external_call_id:
            raise ValueError("external compute call ID is required")
        if not _DIGEST.fullmatch(self.dispatch_evidence_digest):
            raise ValueError("dispatch evidence digest must be SHA-256")


@dataclass(frozen=True, slots=True)
class ComputeEvidencePointer:
    locator: str
    digest: str

    def __post_init__(self) -> None:
        if not self.locator:
            raise ValueError("compute evidence locator is required")
        if not _DIGEST.fullmatch(self.digest):
            raise ValueError("compute evidence digest must be SHA-256")


@dataclass(frozen=True, slots=True)
class ComputeSpendAuthorization:
    authorization_id: str
    request_digest: str
    transport_profile_digest: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"spend-[0-9a-f]{32}", self.authorization_id):
            raise ValueError("compute spend authorization ID is invalid")
        if not _DIGEST.fullmatch(self.request_digest) or not _DIGEST.fullmatch(
            self.transport_profile_digest
        ):
            raise ValueError("compute spend authorization digest is invalid")


@dataclass(frozen=True, slots=True)
class TransportPoll:
    status: ComputeExecutionStatus
    evidence: ComputeEvidencePointer | None = None
    used_seconds: int | None = None
    failure: str | None = None

    def __post_init__(self) -> None:
        if self.status is ComputeExecutionStatus.DISPATCHED:
            if (
                self.evidence is not None
                or self.used_seconds is not None
                or self.failure is not None
            ):
                raise ValueError(
                    "pending compute poll cannot include terminal evidence"
                )
            return
        if self.status not in {
            ComputeExecutionStatus.COMPLETE,
            ComputeExecutionStatus.FAILED,
        }:
            raise ValueError("transport poll status is invalid")
        if self.evidence is None:
            raise ValueError("terminal compute poll requires evidence")
        if type(self.used_seconds) is not int or self.used_seconds < 0:
            raise ValueError("terminal compute use must be nonnegative")
        if self.status is ComputeExecutionStatus.COMPLETE and self.failure is not None:
            raise ValueError("successful compute poll cannot include a failure")
        if self.status is ComputeExecutionStatus.FAILED and not self.failure:
            raise ValueError("failed compute poll requires a reason")


@dataclass(frozen=True, slots=True)
class ComputeExecutionReceipt:
    execution_id: str
    execution_key: str
    request_digest: str
    status: ComputeExecutionStatus
    external_call_id: str | None
    evidence: ComputeEvidencePointer | None
    used_seconds: int | None
    failure: str | None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"execution-[0-9a-f]{32}", self.execution_id):
            raise ValueError("compute execution receipt ID is invalid")
        if not _SAFE_KEY.fullmatch(self.execution_key):
            raise ValueError("compute execution receipt key is invalid")
        if not _DIGEST.fullmatch(self.request_digest):
            raise ValueError("compute request digest must be SHA-256")
        if self.status in {
            ComputeExecutionStatus.REGISTERED,
            ComputeExecutionStatus.DISPATCHING,
        }:
            if any(
                value is not None
                for value in (
                    self.external_call_id,
                    self.evidence,
                    self.used_seconds,
                    self.failure,
                )
            ):
                raise ValueError("unsubmitted compute receipt has terminal fields")
            return
        if self.status is ComputeExecutionStatus.DISPATCHED:
            if (
                not self.external_call_id
                or self.evidence is not None
                or self.used_seconds is not None
                or self.failure is not None
            ):
                raise ValueError("dispatched compute receipt fields differ")
            return
        if self.status is ComputeExecutionStatus.COMPLETE:
            if (
                not self.external_call_id
                or self.evidence is None
                or type(self.used_seconds) is not int
                or self.used_seconds < 0
                or self.failure is not None
            ):
                raise ValueError("completed compute receipt fields differ")
            return
        if self.status is ComputeExecutionStatus.AMBIGUOUS:
            if (
                self.external_call_id is not None
                or self.evidence is not None
                or self.used_seconds is not None
                or not self.failure
            ):
                raise ValueError("ambiguous compute receipt fields differ")
            return
        if self.status is ComputeExecutionStatus.FAILED:
            terminal_fields = (
                self.external_call_id is not None,
                self.evidence is not None,
                self.used_seconds is not None,
            )
            if not self.failure or not (
                all(terminal_fields) or not any(terminal_fields)
            ):
                raise ValueError("failed compute receipt fields differ")
            if self.used_seconds is not None and (
                type(self.used_seconds) is not int or self.used_seconds < 0
            ):
                raise ValueError("failed compute receipt use must be nonnegative")
            return
        raise ValueError("compute receipt status is invalid")


class DefinitiveDispatchError(RuntimeError):
    """Dispatch was rejected before the remote provider accepted work."""


def _raise_invalid_request() -> ComputeExecutionRequest:
    raise ValueError("compute run request must be an object")


def _write_once(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except FileExistsError:
        if path.read_bytes() != content:
            raise RuntimeError("compute run manifest already differs") from None
