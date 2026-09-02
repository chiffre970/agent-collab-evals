"""Hidden-performance profile and evidence adapter for the Modal vLLM runner."""

from __future__ import annotations

import importlib.metadata
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..campaigns.model_serving import ModelServingCampaign, load_benchmark_plan
from ..campaigns.serving_scoring import ScoringProfile
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
)
from .modal_vllm_compute import ModalVllmEvidenceResolver


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class ModalVllmHiddenPerformanceProfile:
    """Frozen inputs for one hidden Modal performance repetition."""

    profile_id: str
    modal_environment: str
    modal_client_version: str
    modal_script: Path
    modal_script_digest: str
    campaign_manifest: Path
    campaign_manifest_digest: str
    hidden_workload_manifest: Path
    hidden_workload_manifest_digest: str
    performance_profile: Path
    performance_profile_digest: str
    scoring_profile: Path
    scoring_profile_digest: str
    repetition: int
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
        scoring_profile: Path,
        modal_script: Path,
        modal_environment: str,
        modal_client_version: str,
        repetition: int,
        attempt: int,
        maximum_collection_seconds: int,
        evidence_volume: str,
    ) -> "ModalVllmHiddenPerformanceProfile":
        if not profile_id or not modal_environment or not evidence_volume:
            raise ValueError("Modal hidden performance strings must be nonempty")
        if type(repetition) is not int or repetition < 1:
            raise ValueError("Modal hidden performance repetition is invalid")
        if type(attempt) is not int or attempt < 1:
            raise ValueError("Modal hidden performance attempt is invalid")
        if (
            type(maximum_collection_seconds) is not int
            or not 0 <= maximum_collection_seconds <= 300
        ):
            raise ValueError("Modal hidden performance collection limit is invalid")
        if importlib.metadata.version("modal") != modal_client_version:
            raise ValueError("installed Modal client differs from the profile")
        if attempt > campaign.measurement_profile().max_attempts:
            raise ValueError("Modal hidden performance attempt exceeds the campaign")
        campaign_manifest = campaign_manifest.resolve(strict=True)
        if ModelServingCampaign.load(campaign_manifest) != campaign:
            raise ValueError("Modal hidden performance campaign manifest differs")
        performance_profile = hidden_workload.resource_paths[
            "performance_profile"
        ]
        performance_digest = hidden_workload.resource_digests[
            "performance_profile"
        ]
        if digest_file(performance_profile) != performance_digest:
            raise ValueError("hidden performance profile digest differs")
        hidden_plan = load_benchmark_plan(performance_profile)
        resolved_scoring_profile = scoring_profile.resolve(strict=True)
        scoring = ScoringProfile.load(resolved_scoring_profile)
        scoring.validate_against(
            hidden_plan,
            measurement_profile_digest=campaign.measurement_profile().digest,
            measurement_repetitions=campaign.measurement_profile().repetitions,
        )
        public_plan = campaign.benchmark_plan()
        if (
            hidden_plan.seed == public_plan.seed
            or hidden_plan.buckets != public_plan.buckets
            or hidden_plan.metric_percentiles != public_plan.metric_percentiles
        ):
            raise ValueError("hidden performance plan differs from its contract")
        document = {
            "adapter": "modal-vllm-hidden-performance-profile/v0alpha1",
            "profile_id": profile_id,
            "modal_environment": modal_environment,
            "modal_client_version": modal_client_version,
            "modal_script_digest": digest_file(modal_script),
            "campaign_manifest_digest": campaign.manifest_digest,
            "hidden_workload_manifest_digest": hidden_workload.manifest_digest,
            "performance_profile_digest": performance_digest,
            "scoring_profile_digest": scoring.digest,
            "repetition": repetition,
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
            performance_profile=performance_profile,
            performance_profile_digest=performance_digest,
            scoring_profile=resolved_scoring_profile,
            scoring_profile_digest=scoring.digest,
            repetition=repetition,
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
            raise RuntimeError("Modal hidden performance script digest differs")
        if campaign.manifest_digest != self.campaign_manifest_digest:
            raise RuntimeError("Modal hidden performance campaign digest differs")
        if digest_file(self.hidden_workload_manifest) != (
            self.hidden_workload_manifest_digest
        ):
            raise RuntimeError("hidden workload manifest digest differs")
        if digest_file(self.performance_profile) != self.performance_profile_digest:
            raise RuntimeError("hidden performance profile digest differs")
        if digest_file(self.scoring_profile) != self.scoring_profile_digest:
            raise RuntimeError("hidden scoring profile digest differs")
        hidden = load_benchmark_plan(self.performance_profile)
        public = campaign.benchmark_plan()
        if (
            hidden.seed == public.seed
            or hidden.buckets != public.buckets
            or hidden.metric_percentiles != public.metric_percentiles
        ):
            raise RuntimeError("hidden performance plan differs from its contract")
        ScoringProfile.load(self.scoring_profile).validate_against(
            hidden,
            measurement_profile_digest=campaign.measurement_profile().digest,
            measurement_repetitions=campaign.measurement_profile().repetitions,
        )


class ModalVllmHiddenPerformanceEvidenceResolver:
    """Translate retained benchmark evidence into a hidden phase result."""

    def __init__(
        self,
        profile: ModalVllmHiddenPerformanceProfile,
        repository_root: Path,
        state_root: Path,
        transport_profile_digest: str,
    ) -> None:
        self._profile = profile
        self._delegate = ModalVllmEvidenceResolver(
            profile,
            repository_root,
            state_root,
            transport_profile_digest,
        )
        self._profile_digest = self.profile_digest_for(profile.digest)

    @staticmethod
    def profile_digest_for(performance_profile_digest: str) -> str:
        return digest_value(
            {
                "adapter": "modal-vllm-hidden-performance-evidence/v0alpha1",
                "performance_profile_digest": performance_profile_digest,
                "source": "validated_modal_benchmark_evidence",
            }
        )

    @property
    def profile_digest(self) -> str:
        return self._profile_digest

    def resolve_dispatch(
        self, request: ComputeExecutionRequest, external_call_id: str
    ) -> bytes:
        return self._delegate.resolve_dispatch(request, external_call_id)

    def pointer(
        self, request: ComputeExecutionRequest, external_call_id: str
    ) -> tuple[ComputeEvidencePointer, ComputeExecutionStatus, int, str | None]:
        delegate_pointer, status, used_seconds, failure = self._delegate.pointer(
            request, external_call_id
        )
        content = self._transform(self._delegate.resolve(delegate_pointer))
        return (
            ComputeEvidencePointer(delegate_pointer.locator, digest_bytes(content)),
            status,
            used_seconds,
            failure,
        )

    def resolve(self, pointer: ComputeEvidencePointer) -> bytes:
        delegate_pointer = ComputeEvidencePointer(pointer.locator, pointer.digest)
        return self._transform(self._delegate.resolve(delegate_pointer))

    def _transform(self, content: bytes) -> bytes:
        envelope = parse_json(content.decode("utf-8"))
        if not isinstance(envelope, dict):
            raise RuntimeError("Modal performance compute evidence is invalid")
        normalized = envelope.get("result")
        if not isinstance(normalized, dict):
            raise RuntimeError("Modal performance normalized result is invalid")
        if normalized.get("scoring_profile_digest") != (
            self._profile.scoring_profile_digest
        ):
            raise RuntimeError("Modal performance scoring profile differs")
        performance = normalized.get("performance_score")
        if performance is None and envelope.get("status") == "failed":
            scalar = 0
            failures = ["execution_failed"]
            eligible = False
        else:
            if not isinstance(performance, dict):
                raise RuntimeError("Modal performance score is unavailable")
            scalar = performance.get("scalar_ppm")
            failures = performance.get("failures", [])
            if (
                type(scalar) is not int
                or not isinstance(failures, list)
                or any(not isinstance(value, str) or not value for value in failures)
            ):
                raise RuntimeError("Modal performance score fields differ")
            eligible = (
                envelope.get("status") == "complete"
                and performance.get("eligible") is True
            )
        candidate_result: dict[str, Any] = {
            "schema_version": "serving-candidate-compute-evidence/v0alpha1",
            "phase": "performance",
            "campaign_manifest_digest": self._profile.campaign_manifest_digest,
            "hidden_workload_manifest_digest": (
                self._profile.hidden_workload_manifest_digest
            ),
            "workload_digest": self._profile.performance_profile_digest,
            "candidate_digest": envelope.get("candidate_digest"),
            "candidate_manifest_digest": envelope.get(
                "candidate_manifest_digest"
            ),
            "eligible": eligible,
            "criterion_units": scalar,
            "failures": list(dict.fromkeys(failures)),
            "diagnostics": {
                "candidate_id": normalized.get("candidate_id"),
                "modal_function_call_id": normalized.get(
                    "modal_function_call_id"
                ),
                "measurement_profile_digest": normalized.get(
                    "measurement_profile_digest"
                ),
                "scoring_profile_digest": normalized.get(
                    "scoring_profile_digest"
                ),
            },
        }
        candidate_result["result_evidence_digest"] = digest_value(candidate_result)
        transformed = dict(envelope)
        transformed["evidence_profile_digest"] = self.profile_digest
        transformed["result"] = {"candidate_evaluation": candidate_result}
        return canonical_json_bytes(transformed)
