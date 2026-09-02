"""Fail-closed authority for an incomplete registered-study composition."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .campaigns.model_serving import ModelServingCampaign
from .campaigns.serving_performance_calibration import (
    validate_scoring_profile_promotion,
)
from .campaigns.serving_scoring import ScoringProfile
from .campaigns.serving_workload import (
    HiddenWorkloadBundle,
    HiddenWorkloadExpectations,
    load_hidden_workload,
)
from .canonical import digest_file, digest_value, load_json


STUDY_COMPOSITION_SCHEMA = "registered-study-composition/v0alpha1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}")


class StudyRegistrationError(ValueError):
    """The proposed study composition is incomplete or has changed."""


@dataclass(frozen=True, slots=True)
class RegisteredFileRef:
    ref_id: str
    path: Path
    digest: str


@dataclass(frozen=True, slots=True)
class HiddenWorkloadRef:
    manifest_digest: str
    selection_seed_commitment: str
    resource_digests: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class StudyCompositionCandidate:
    """Digest-pinned composition that cannot authorize execution yet."""

    path: Path
    digest: str
    study_id: str
    study_version: str
    policy_freeze_commit: str
    campaign: ModelServingCampaign
    campaign_ref: RegisteredFileRef
    scoring_ref: RegisteredFileRef
    calibration_proposal_ref: RegisteredFileRef
    hidden_workload: HiddenWorkloadRef
    profiles: Mapping[str, RegisteredFileRef]
    unresolved_gates: tuple[str, ...]

    @classmethod
    def load(
        cls, path: Path, *, repository_root: Path
    ) -> "StudyCompositionCandidate":
        resolved = path.resolve(strict=True)
        root = repository_root.resolve(strict=True)
        with resolved.open("r", encoding="utf-8") as source:
            document = load_json(source)
        expected = {
            "schema_version",
            "status",
            "execution_authorized",
            "study_id",
            "study_version",
            "policy_freeze_commit",
            "campaign",
            "hidden_scoring",
            "hidden_workload",
            "profiles",
            "unresolved_gates",
        }
        if not isinstance(document, dict) or set(document) != expected:
            raise StudyRegistrationError("study composition fields differ")
        if (
            document["schema_version"] != STUDY_COMPOSITION_SCHEMA
            or document["status"] != "registration_candidate"
            or document["execution_authorized"] is not False
        ):
            raise StudyRegistrationError("study composition is not a candidate")
        study_id = _identifier(document["study_id"], "study ID")
        study_version = _identifier(document["study_version"], "study version")
        commit = document["policy_freeze_commit"]
        if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
            raise StudyRegistrationError("policy freeze commit is invalid")

        campaign_ref = _file_ref(
            "campaign", document["campaign"], root, digest_mode="campaign"
        )
        campaign = ModelServingCampaign.load(campaign_ref.path)
        if campaign.manifest_digest != campaign_ref.digest:
            raise StudyRegistrationError("campaign transitive digest differs")

        hidden_scoring = document["hidden_scoring"]
        if not isinstance(hidden_scoring, dict) or set(hidden_scoring) != {
            "profile",
            "calibration_proposal",
        }:
            raise StudyRegistrationError("hidden scoring references differ")
        scoring_ref = _file_ref("hidden_scoring", hidden_scoring["profile"], root)
        proposal_ref = _file_ref(
            "performance_calibration_proposal",
            hidden_scoring["calibration_proposal"],
            root,
        )
        scoring = ScoringProfile.load(scoring_ref.path)
        proposal_digest = validate_scoring_profile_promotion(
            proposal_ref.path, scoring
        )
        if proposal_digest != proposal_ref.digest:
            raise StudyRegistrationError("calibration proposal digest differs")
        scoring.validate_against(
            campaign.benchmark_plan(),
            measurement_profile_digest=campaign.measurement_profile().digest,
            measurement_repetitions=campaign.measurement_profile().repetitions,
        )

        hidden_workload = _hidden_workload_ref(document["hidden_workload"])
        raw_profiles = document["profiles"]
        if not isinstance(raw_profiles, list) or not raw_profiles:
            raise StudyRegistrationError("composition profiles are required")
        profiles: dict[str, RegisteredFileRef] = {}
        for raw_profile in raw_profiles:
            profile = _file_ref("profile", raw_profile, root)
            if profile.ref_id in profiles:
                raise StudyRegistrationError("composition profile IDs repeat")
            profiles[profile.ref_id] = profile

        gates = document["unresolved_gates"]
        if (
            not isinstance(gates, list)
            or not gates
            or len(set(gates)) != len(gates)
            or any(not isinstance(gate, str) or not _SAFE_ID.fullmatch(gate) for gate in gates)
        ):
            raise StudyRegistrationError("unresolved registration gates are invalid")
        return cls(
            path=resolved,
            digest=digest_file(resolved),
            study_id=study_id,
            study_version=study_version,
            policy_freeze_commit=commit,
            campaign=campaign,
            campaign_ref=campaign_ref,
            scoring_ref=scoring_ref,
            calibration_proposal_ref=proposal_ref,
            hidden_workload=hidden_workload,
            profiles=profiles,
            unresolved_gates=tuple(gates),
        )

    @property
    def resolved_configuration_digest(self) -> str:
        return digest_value(
            {
                "schema_version": STUDY_COMPOSITION_SCHEMA,
                "composition_digest": self.digest,
                "policy_freeze_commit": self.policy_freeze_commit,
                "campaign_manifest_digest": self.campaign_ref.digest,
                "hidden_scoring_profile_digest": self.scoring_ref.digest,
                "calibration_proposal_digest": self.calibration_proposal_ref.digest,
                "hidden_workload_manifest_digest": (
                    self.hidden_workload.manifest_digest
                ),
                "profile_digests": {
                    name: profile.digest
                    for name, profile in sorted(self.profiles.items())
                },
            }
        )

    def assert_execution_authorized(self) -> None:
        raise StudyRegistrationError(
            "registration candidate cannot authorize execution; unresolved gates: "
            + ", ".join(self.unresolved_gates)
        )

    def verify_hidden_bundle(self, manifest_path: Path) -> HiddenWorkloadBundle:
        campaign = self.campaign
        expectations = HiddenWorkloadExpectations(
            campaign_manifest_digest=campaign.manifest_digest,
            hidden_contract_digest=campaign.transitive_digests["hidden_contract"],
            quality_profile_digest=self.hidden_workload.resource_digests[
                "quality_profile"
            ],
            quality_policy_digest=self.hidden_workload.resource_digests[
                "quality_policy"
            ],
            quality_workload_digest=self.hidden_workload.resource_digests[
                "quality_workload"
            ],
            public_correctness_digest=campaign.transitive_digests[
                "public_correctness"
            ],
            public_performance_digest=campaign.transitive_digests["public_profile"],
            required_gates=tuple(campaign.hidden_contract()["required_gates"]),
        )
        bundle = load_hidden_workload(
            manifest_path,
            expectations,
            campaign.benchmark_plan(),
            registered_manifest_digest=self.hidden_workload.manifest_digest,
        )
        if (
            bundle.selection_seed_commitment
            != self.hidden_workload.selection_seed_commitment
            or dict(bundle.resource_digests)
            != dict(self.hidden_workload.resource_digests)
        ):
            raise StudyRegistrationError("registered hidden workload differs")
        return bundle


def _file_ref(
    default_id: str,
    value: Any,
    repository_root: Path,
    *,
    digest_mode: str = "file",
) -> RegisteredFileRef:
    if not isinstance(value, dict) or set(value) != {"id", "path", "digest"}:
        raise StudyRegistrationError(f"{default_id} file reference fields differ")
    ref_id = _identifier(value["id"], f"{default_id} reference ID")
    path_value = value["path"]
    digest = value["digest"]
    if not isinstance(path_value, str) or not path_value:
        raise StudyRegistrationError(f"{default_id} path is invalid")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise StudyRegistrationError(f"{default_id} digest is invalid")
    path = (repository_root / path_value).resolve(strict=True)
    try:
        path.relative_to(repository_root)
    except ValueError as error:
        raise StudyRegistrationError(f"{default_id} path escapes repository") from error
    if digest_mode == "file" and digest_file(path) != digest:
        raise StudyRegistrationError(f"{default_id} file digest differs")
    return RegisteredFileRef(ref_id, path, digest)


def _hidden_workload_ref(value: Any) -> HiddenWorkloadRef:
    if not isinstance(value, dict) or set(value) != {
        "manifest_digest",
        "selection_seed_commitment",
        "resource_digests",
    }:
        raise StudyRegistrationError("hidden workload reference fields differ")
    resources = value["resource_digests"]
    expected_resources = {
        "correctness_requests",
        "performance_profile",
        "quality_requests",
        "quality_profile",
        "quality_policy",
        "quality_workload",
    }
    if not isinstance(resources, dict) or set(resources) != expected_resources:
        raise StudyRegistrationError("hidden workload resources differ")
    for digest in (
        value["manifest_digest"],
        value["selection_seed_commitment"],
        *resources.values(),
    ):
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise StudyRegistrationError("hidden workload digest is invalid")
    return HiddenWorkloadRef(
        manifest_digest=value["manifest_digest"],
        selection_seed_commitment=value["selection_seed_commitment"],
        resource_digests=dict(resources),
    )


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise StudyRegistrationError(f"{label} is invalid")
    return value
