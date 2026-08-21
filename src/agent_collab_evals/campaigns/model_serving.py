"""Executable definition for the first small-model serving campaign."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from ..canonical import DuplicateKeyError, digest_file, digest_value, load_json
from ..domain import Job, MaterializedJobs


CAMPAIGN_SCHEMA = "model-serving-campaign/v0alpha1"
CANDIDATE_SCHEMA = "model-serving-candidate/v0alpha1"
_REVISION = re.compile(r"[0-9a-f]{40}")
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")


class ManifestValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CandidateDescriptor:
    candidate_id: str
    engine: str
    engine_version: str
    manifest_digest: str


@dataclass(frozen=True, slots=True)
class BenchmarkBucket:
    bucket_id: str
    input_tokens: int
    output_tokens: int
    request_rates: tuple[int, ...]
    num_prompts: int


@dataclass(frozen=True, slots=True)
class BenchmarkPlan:
    seed: int
    metric_percentiles: tuple[int, ...]
    buckets: tuple[BenchmarkBucket, ...]


@dataclass(frozen=True, slots=True)
class ModelServingCampaign:
    root: Path
    raw: Mapping[str, Any]
    transitive_digests: Mapping[str, str]
    _manifest_digest: str

    @classmethod
    def load(cls, manifest_path: Path) -> "ModelServingCampaign":
        manifest_path = manifest_path.resolve()
        with manifest_path.open("rb") as source:
            raw = tomllib.load(source)
        root = manifest_path.parent
        cls._validate_manifest(raw)

        declared_files = {
            "mission": cls._member(root, str(raw["mission_file"])),
            "submission_schema": cls._member(
                root, str(raw["submission_schema"])
            ),
            "reference_candidate": cls._member(
                root, str(raw["reference"]["candidate_manifest"])
            ),
            "public_correctness": cls._member(
                root, str(raw["workload"]["public_correctness"])
            ),
            "public_profile": cls._member(
                root, str(raw["workload"]["public_profile"])
            ),
            "hidden_contract": cls._member(
                root, str(raw["workload"]["hidden_contract"])
            ),
            "measurement_profile": cls._member(
                root, str(raw["evaluation"]["measurement_profile"])
            ),
            "scoring_profile": cls._member(
                root, str(raw["evaluation"]["scoring_profile"])
            ),
            "quality_profile": cls._member(
                root, str(raw["evaluation"]["quality_profile"])
            ),
            "quality_policy": cls._member(
                root, str(raw["evaluation"]["quality_policy"])
            ),
        }
        transitive = {
            name: digest_file(path) for name, path in declared_files.items()
        }
        manifest_digest = digest_value(
            {
                "manifest": raw,
                "transitive_files": transitive,
            }
        )
        campaign = cls(root, raw, transitive, manifest_digest)
        campaign.validate_reference_candidate()
        hidden_contract = campaign.hidden_contract()
        measurement_profile = campaign.measurement_profile()
        scoring_profile = campaign.scoring_profile()
        quality_profile = campaign.quality_profile()
        quality_policy = campaign.quality_policy()
        scoring_profile.validate_against(
            campaign.benchmark_plan(),
            measurement_profile_digest=measurement_profile.digest,
            measurement_repetitions=measurement_profile.repetitions,
        )
        quality_policy.validate_against(quality_profile)
        hidden_quality = hidden_contract["quality_contract"]
        expected_quality_digests = {
            "quality_profile_digest": quality_profile.digest,
            "quality_policy_digest": quality_policy.digest,
            "quality_workload_digest": quality_policy.quality_workload_digest,
        }
        if any(
            hidden_quality.get(key) != value
            for key, value in expected_quality_digests.items()
        ):
            raise ManifestValidationError("hidden quality digests differ")
        return campaign

    @property
    def manifest_digest(self) -> str:
        return self._manifest_digest

    @property
    def target_model_id(self) -> str:
        return str(self.raw["target_model"]["id"])

    @property
    def target_model_revision(self) -> str:
        return str(self.raw["target_model"]["revision"])

    @property
    def reference_candidate_path(self) -> Path:
        return self._member(
            self.root, str(self.raw["reference"]["candidate_manifest"])
        )

    @property
    def measurement_profile_path(self) -> Path:
        return self._member(
            self.root, str(self.raw["evaluation"]["measurement_profile"])
        )

    @property
    def scoring_profile_path(self) -> Path:
        return self._member(
            self.root, str(self.raw["evaluation"]["scoring_profile"])
        )

    @property
    def quality_profile_path(self) -> Path:
        return self._member(
            self.root, str(self.raw["evaluation"]["quality_profile"])
        )

    @property
    def quality_policy_path(self) -> Path:
        return self._member(
            self.root, str(self.raw["evaluation"]["quality_policy"])
        )

    def measurement_profile(self):
        """Load the evaluator profile lazily to keep campaign modules decoupled."""

        from .serving_measurement import MeasurementProfile

        return MeasurementProfile.load(self.measurement_profile_path)

    def scoring_profile(self):
        """Load the score policy without coupling it to a compute adapter."""

        from .serving_scoring import ScoringProfile

        return ScoringProfile.load(self.scoring_profile_path)

    def quality_profile(self):
        """Load the served-generation request profile."""

        from .serving_quality import QualityProfile

        return QualityProfile.load(self.quality_profile_path)

    def quality_policy(self):
        """Load the served-generation non-inferiority policy."""

        from .serving_quality import QualityPolicy

        return QualityPolicy.load(self.quality_policy_path)

    def hidden_contract(self) -> Mapping[str, Any]:
        path = self._member(
            self.root, str(self.raw["workload"]["hidden_contract"])
        )
        try:
            with path.open("r", encoding="utf-8") as source:
                contract = load_json(source)
        except (json.JSONDecodeError, DuplicateKeyError) as error:
            raise ManifestValidationError(
                "hidden evaluator contract is not unambiguous JSON"
            ) from error
        self._validate_hidden_contract(contract)
        return contract

    @staticmethod
    def _validate_hidden_contract(contract: Any) -> None:
        if not isinstance(contract, dict):
            raise ManifestValidationError(
                "hidden evaluator contract must be a JSON object"
            )
        ModelServingCampaign._exact_keys(
            contract,
            {
                "schema_version",
                "materialization",
                "required_digests",
                "required_gates",
                "required_diagnostics",
                "quality_contract",
                "primary_metric",
                "agent_visible",
            },
            "hidden evaluator contract",
        )
        expected_scalars = {
            "schema_version": "model-serving-hidden-input/v0alpha1",
            "materialization": "external_after_submission_closure",
            "primary_metric": "goodput_requests_per_second",
        }
        if (
            any(contract.get(key) != value for key, value in expected_scalars.items())
            or contract.get("agent_visible") is not False
        ):
            raise ManifestValidationError(
                "unsupported hidden evaluator contract profile"
            )
        if contract.get("required_digests") != [
            "correctness_requests",
            "quality_requests",
            "quality_profile",
            "quality_policy",
            "quality_workload",
            "performance_profile",
        ]:
            raise ManifestValidationError(
                "hidden evaluator contract has invalid required digests"
            )
        if contract.get("required_gates") != [
            "artifact_integrity",
            "cold_start",
            "api_schema",
            "correctness",
            "downstream_generation_noninferiority",
            "quality_relative_to_reference",
            "stability",
            "prohibited_shortcuts",
        ]:
            raise ManifestValidationError(
                "hidden evaluator contract has invalid required gates"
            )
        if contract.get("required_diagnostics") != ["teacher_forced_quality"]:
            raise ManifestValidationError(
                "hidden evaluator contract has invalid required diagnostics"
            )

        quality = ModelServingCampaign._mapping(contract, "quality_contract")
        expected_quality = {
            "decision_rule": "paired_reference_relative_noninferiority",
            "authoritative_evidence": (
                "observed_outputs_from_held_out_served_generation"
            ),
            "teacher_forced_metric_role": "diagnostic_only",
            "downstream_generation_evaluation": "required",
            "decoding_profiles": "pinned_target_model_recommended",
            "lossless_identity_metric_role": "optional_claim_diagnostic",
            "candidate_implementation_policy": (
                "unrestricted_within_campaign_policy"
            ),
            "task_mix_status": "calibration_v2_materialized_evaluator_private",
            "threshold_status": "frozen_quality_policy_v0alpha1",
        }
        ModelServingCampaign._exact_keys(
            quality,
            {
                *expected_quality,
                "quality_profile_digest",
                "quality_policy_digest",
                "quality_workload_digest",
            },
            "hidden quality contract",
        )
        if any(quality.get(key) != value for key, value in expected_quality.items()):
            raise ManifestValidationError("unsupported hidden quality contract")
        for key in (
            "quality_profile_digest",
            "quality_policy_digest",
            "quality_workload_digest",
        ):
            if not isinstance(quality.get(key), str) or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", quality[key]
            ):
                raise ManifestValidationError("hidden quality digest is invalid")

    def materialize(self, task_seed: int) -> MaterializedJobs:
        if task_seed < 0:
            raise ManifestValidationError("task_seed must be non-negative")
        mission_path = self._member(self.root, str(self.raw["mission_file"]))
        mission = mission_path.read_text(encoding="utf-8")
        material = {
            "campaign_manifest_digest": self.manifest_digest,
            "task_seed": task_seed,
            "mission_digest": self.transitive_digests["mission"],
            "submission_schema_digest": self.transitive_digests[
                "submission_schema"
            ],
            "public_correctness_digest": self.transitive_digests[
                "public_correctness"
            ],
            "public_profile_digest": self.transitive_digests["public_profile"],
            "measurement_profile_digest": self.transitive_digests[
                "measurement_profile"
            ],
            "scoring_profile_digest": self.transitive_digests[
                "scoring_profile"
            ],
            "quality_profile_digest": self.transitive_digests[
                "quality_profile"
            ],
            "quality_policy_digest": self.transitive_digests["quality_policy"],
        }
        material_digest = digest_value(material)
        job = Job(
            job_id="optimize-serving",
            mission=mission,
            materials_digest=material_digest,
            public_materials={
                "submission_schema": str(self.raw["submission_schema"]),
                "correctness_workload": str(
                    self.raw["workload"]["public_correctness"]
                ),
                "benchmark_profile": str(
                    self.raw["workload"]["public_profile"]
                ),
                "reference_candidate": str(
                    self.raw["reference"]["candidate_manifest"]
                ),
                "measurement_profile": str(
                    self.raw["evaluation"]["measurement_profile"]
                ),
                "scoring_profile": str(
                    self.raw["evaluation"]["scoring_profile"]
                ),
            },
        )
        return MaterializedJobs((job,), material_digest)

    def benchmark_buckets(self) -> tuple[BenchmarkBucket, ...]:
        return self.benchmark_plan().buckets

    def benchmark_plan(self) -> BenchmarkPlan:
        profile_path = self._member(
            self.root, str(self.raw["workload"]["public_profile"])
        )
        with profile_path.open("rb") as source:
            profile = tomllib.load(source)
        if profile.get("schema_version") != "serving-workload/v0alpha1":
            raise ManifestValidationError("unsupported public workload schema")
        self._exact_keys(
            profile,
            {"schema_version", "seed", "metric_percentiles", "buckets"},
            "public workload",
        )
        seed = profile.get("seed")
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            raise ManifestValidationError("workload seed must be non-negative")
        percentiles = tuple(profile.get("metric_percentiles", []))
        if (
            not percentiles
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 1 <= value <= 100
                for value in percentiles
            )
            or tuple(sorted(set(percentiles))) != percentiles
        ):
            raise ManifestValidationError(
                "metric percentiles must be unique ascending integers from 1 to 100"
            )
        buckets: list[BenchmarkBucket] = []
        seen: set[str] = set()
        for item in profile.get("buckets", []):
            if not isinstance(item, dict):
                raise ManifestValidationError("workload bucket must be a mapping")
            self._exact_keys(
                item,
                {"id", "input_tokens", "output_tokens", "request_rates", "num_prompts"},
                "workload bucket",
            )
            bucket_id = str(item.get("id", ""))
            if not _IDENTIFIER.fullmatch(bucket_id) or bucket_id in seen:
                raise ManifestValidationError("invalid or duplicate workload bucket id")
            seen.add(bucket_id)
            bucket = BenchmarkBucket(
                bucket_id=bucket_id,
                input_tokens=self._positive_int(item, "input_tokens"),
                output_tokens=self._positive_int(item, "output_tokens"),
                request_rates=tuple(item.get("request_rates", [])),
                num_prompts=self._positive_int(item, "num_prompts"),
            )
            if not bucket.request_rates or any(
                not isinstance(rate, int) or isinstance(rate, bool) or rate < 1
                for rate in bucket.request_rates
            ):
                raise ManifestValidationError(
                    f"bucket {bucket_id} has invalid request rates"
                )
            buckets.append(bucket)
        if not buckets:
            raise ManifestValidationError("at least one workload bucket is required")
        return BenchmarkPlan(seed, percentiles, tuple(buckets))

    def validate_candidate(self, candidate_path: Path) -> CandidateDescriptor:
        return self.validate_candidate_document(self._load_candidate(candidate_path))

    def validate_reference_candidate(self) -> CandidateDescriptor:
        candidate = self._load_candidate(self.reference_candidate_path)
        descriptor = self.validate_candidate_document(candidate)
        reference = self._mapping(self.raw, "reference")
        build = self._mapping(candidate, "build")
        server = self._mapping(candidate, "server")
        if descriptor.engine != "vllm":
            raise ManifestValidationError("reference candidate engine must be vllm")
        if descriptor.engine_version != reference["vllm_version"]:
            raise ManifestValidationError("reference vLLM version mismatch")
        if build["image_ref"] != reference["cuda_image"]:
            raise ManifestValidationError("reference CUDA image mismatch")
        if build["image_digest"] != reference["cuda_image_digest"]:
            raise ManifestValidationError("reference CUDA image digest mismatch")
        if build["dependency_lock"] != f"vllm=={reference['vllm_version']}":
            raise ManifestValidationError("reference dependency lock mismatch")

        entrypoint = server["entrypoint"]
        if entrypoint[:3] != ["vllm", "serve", self.target_model_id]:
            raise ManifestValidationError("reference entrypoint must serve the target model")
        required_flags = {
            "--revision": self.target_model_revision,
            "--served-model-name": str(reference["served_model_name"]),
            "--generation-config": str(reference["generation_config"]),
            "--port": str(server["port"]),
        }
        for flag, expected in required_flags.items():
            if entrypoint.count(flag) != 1:
                raise ManifestValidationError(
                    f"reference entrypoint must contain exactly one {flag}"
                )
            index = entrypoint.index(flag)
            if index + 1 == len(entrypoint) or entrypoint[index + 1] != expected:
                raise ManifestValidationError(
                    f"reference entrypoint has an invalid {flag} value"
                )
        return descriptor

    @staticmethod
    def _load_candidate(candidate_path: Path) -> Mapping[str, Any]:
        try:
            with candidate_path.open("r", encoding="utf-8") as source:
                candidate = load_json(source)
        except (json.JSONDecodeError, DuplicateKeyError) as error:
            raise ManifestValidationError("candidate is not unambiguous JSON") from error
        if not isinstance(candidate, dict):
            raise ManifestValidationError("candidate must be a JSON object")
        return candidate

    def validate_candidate_document(
        self, candidate: Mapping[str, Any]
    ) -> CandidateDescriptor:
        if not isinstance(candidate, dict):
            raise ManifestValidationError("candidate must be a JSON object")

        allowed_top = {
            "schema_version",
            "candidate_id",
            "model",
            "resource",
            "build",
            "server",
        }
        self._exact_keys(candidate, allowed_top, "candidate")
        if candidate["schema_version"] != CANDIDATE_SCHEMA:
            raise ManifestValidationError("unsupported candidate schema")
        candidate_id_value = candidate["candidate_id"]
        if not isinstance(candidate_id_value, str) or not _IDENTIFIER.fullmatch(
            candidate_id_value
        ):
            raise ManifestValidationError("invalid candidate_id")
        candidate_id = candidate_id_value

        model = self._mapping(candidate, "model")
        self._exact_keys(model, {"id", "revision"}, "candidate.model")
        if model["id"] != self.target_model_id:
            raise ManifestValidationError("candidate changes the target model")
        if model["revision"] != self.target_model_revision:
            raise ManifestValidationError("candidate changes the model revision")

        resource = self._mapping(candidate, "resource")
        self._exact_keys(resource, {"gpu_type", "gpu_count"}, "candidate.resource")
        if resource["gpu_type"] != self.raw["hardware"]["gpu_type"]:
            raise ManifestValidationError("candidate changes the GPU type")
        if resource["gpu_count"] != self.raw["hardware"]["gpu_count"]:
            raise ManifestValidationError("candidate changes the GPU count")

        build = self._mapping(candidate, "build")
        self._exact_keys(
            build,
            {"image_ref", "image_digest", "dependency_lock"},
            "candidate.build",
        )
        for key in ("image_ref", "dependency_lock"):
            if not isinstance(build[key], str) or not build[key].strip():
                raise ManifestValidationError(f"candidate.build.{key} is required")
        image_digest = build["image_digest"]
        if image_digest is not None and not re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(image_digest)
        ):
            raise ManifestValidationError("invalid candidate image digest")

        server = self._mapping(candidate, "server")
        self._exact_keys(
            server,
            {
                "engine",
                "engine_version",
                "entrypoint",
                "port",
                "health_path",
                "chat_path",
                "served_model_name",
                "generation_config",
            },
            "candidate.server",
        )
        engine_value = server["engine"]
        engine_version_value = server["engine_version"]
        if not isinstance(engine_value, str) or not isinstance(
            engine_version_value, str
        ):
            raise ManifestValidationError("candidate engine and version must be strings")
        engine = engine_value
        engine_version = engine_version_value
        if not _IDENTIFIER.fullmatch(engine) or not engine_version.strip():
            raise ManifestValidationError("candidate engine and version are required")
        entrypoint = server["entrypoint"]
        if not isinstance(entrypoint, list) or not entrypoint or any(
            not isinstance(part, str) or not part.strip() or "\x00" in part
            for part in entrypoint
        ):
            raise ManifestValidationError("candidate entrypoint must be argv strings")
        port = server["port"]
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ManifestValidationError("candidate server port is invalid")
        for key in ("health_path", "chat_path"):
            if not isinstance(server[key], str) or not server[key].startswith("/"):
                raise ManifestValidationError(f"candidate.server.{key} must be a path")
        if server["served_model_name"] != self.raw["reference"]["served_model_name"]:
            raise ManifestValidationError("candidate changes the served model name")
        if server["generation_config"] not in {"vllm", "explicit"}:
            raise ManifestValidationError("candidate generation configuration is ambiguous")

        return CandidateDescriptor(
            candidate_id=candidate_id,
            engine=engine,
            engine_version=engine_version,
            manifest_digest=digest_value(candidate),
        )

    @staticmethod
    def _validate_manifest(raw: Mapping[str, Any]) -> None:
        ModelServingCampaign._exact_keys(
            raw,
            {
                "schema_version",
                "campaign_id",
                "status",
                "mission_file",
                "submission_schema",
                "target_model",
                "hardware",
                "reference",
                "workload",
                "evaluation",
                "development_limits",
            },
            "campaign",
        )
        if raw.get("schema_version") != CAMPAIGN_SCHEMA:
            raise ManifestValidationError("unsupported campaign schema")
        if raw.get("status") != "calibration":
            raise ManifestValidationError("V0 implementation accepts calibration packs only")
        campaign_id = raw.get("campaign_id")
        if not isinstance(campaign_id, str) or not _IDENTIFIER.fullmatch(campaign_id):
            raise ManifestValidationError("invalid campaign_id")
        target = ModelServingCampaign._mapping(raw, "target_model")
        ModelServingCampaign._exact_keys(
            target, {"id", "revision", "license"}, "target_model"
        )
        if not isinstance(target.get("id"), str) or not target["id"]:
            raise ManifestValidationError("target model id is required")
        revision = target.get("revision")
        if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
            raise ManifestValidationError("target revision must be a 40-character commit")
        if not isinstance(target.get("license"), str) or not target["license"]:
            raise ManifestValidationError("target model license is required")
        hardware = ModelServingCampaign._mapping(raw, "hardware")
        ModelServingCampaign._exact_keys(
            hardware,
            {"provider", "environment", "gpu_type", "gpu_count"},
            "hardware",
        )
        if hardware.get("provider") != "modal":
            raise ManifestValidationError("the calibration compute provider must be modal")
        if hardware.get("environment") != "dev":
            raise ManifestValidationError("calibration must use the Modal dev environment")
        if hardware.get("gpu_type") != "L4" or hardware.get("gpu_count") != 1:
            raise ManifestValidationError("the calibration target is exactly one L4")
        reference = ModelServingCampaign._mapping(raw, "reference")
        ModelServingCampaign._exact_keys(
            reference,
            {
                "candidate_manifest",
                "adapter",
                "vllm_version",
                "cuda_image",
                "cuda_image_digest",
                "generation_config",
                "served_model_name",
            },
            "reference",
        )
        if reference.get("generation_config") != "vllm":
            raise ManifestValidationError("reference must disable model generation defaults")
        for key in (
            "candidate_manifest",
            "adapter",
            "vllm_version",
            "cuda_image",
            "cuda_image_digest",
            "served_model_name",
        ):
            if not isinstance(reference.get(key), str) or not reference[key]:
                raise ManifestValidationError(f"reference.{key} is required")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", reference["cuda_image_digest"]):
            raise ManifestValidationError("reference CUDA image digest is invalid")
        for key in ("mission_file", "submission_schema"):
            if not isinstance(raw.get(key), str) or not raw[key]:
                raise ManifestValidationError(f"{key} is required")
        workload = ModelServingCampaign._mapping(raw, "workload")
        ModelServingCampaign._exact_keys(
            workload,
            {"public_correctness", "public_profile", "hidden_contract"},
            "workload",
        )
        for key in workload:
            if not isinstance(workload[key], str) or not workload[key]:
                raise ManifestValidationError(f"workload.{key} is required")

        evaluation = ModelServingCampaign._mapping(raw, "evaluation")
        ModelServingCampaign._exact_keys(
            evaluation,
            {
                "primary_metric",
                "quality_gate",
                "slo_status",
                "hidden_data_status",
                "measurement_profile",
                "scoring_profile",
                "quality_profile",
                "quality_policy",
            },
            "evaluation",
        )
        expected_evaluation = {
            "primary_metric": "goodput_requests_per_second",
            "quality_gate": "reference_relative",
            "slo_status": "calibrated_for_candidate_sensitivity",
            "hidden_data_status": "quality_materialized_other_hidden_external",
        }
        if any(evaluation.get(key) != value for key, value in expected_evaluation.items()):
            raise ManifestValidationError("unsupported calibration evaluation profile")
        if not isinstance(evaluation.get("measurement_profile"), str) or not evaluation[
            "measurement_profile"
        ]:
            raise ManifestValidationError("evaluation.measurement_profile is required")
        if not isinstance(evaluation.get("scoring_profile"), str) or not evaluation[
            "scoring_profile"
        ]:
            raise ManifestValidationError("evaluation.scoring_profile is required")
        for key in ("quality_profile", "quality_policy"):
            if not isinstance(evaluation.get(key), str) or not evaluation[key]:
                raise ManifestValidationError(f"evaluation.{key} is required")

        limits = ModelServingCampaign._mapping(raw, "development_limits")
        ModelServingCampaign._exact_keys(
            limits,
            {
                "modal_usd_cap",
                "openrouter_usd_cap",
                "gpu_seconds_per_solo_run",
                "candidate_submissions",
            },
            "development_limits",
        )
        for key in ("modal_usd_cap", "openrouter_usd_cap"):
            ModelServingCampaign._positive_money(limits, key)
        for key in ("gpu_seconds_per_solo_run", "candidate_submissions"):
            ModelServingCampaign._positive_int(limits, key)

    @staticmethod
    def _member(root: Path, relative: str) -> Path:
        path = Path(relative)
        if path.is_absolute():
            raise ManifestValidationError("campaign file references must be relative")
        resolved_root = root.resolve()
        resolved = (resolved_root / path).resolve()
        try:
            resolved.relative_to(resolved_root)
        except ValueError as error:
            raise ManifestValidationError("campaign file escapes its pack") from error
        if not resolved.is_file():
            raise ManifestValidationError(f"campaign file does not exist: {relative}")
        return resolved

    @staticmethod
    def _mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
        item = value.get(key)
        if not isinstance(item, dict):
            raise ManifestValidationError(f"{key} must be a mapping")
        return item

    @staticmethod
    def _exact_keys(
        value: Mapping[str, Any], expected: set[str], location: str
    ) -> None:
        actual = set(value)
        if actual != expected:
            missing = sorted(expected - actual)
            unknown = sorted(actual - expected)
            raise ManifestValidationError(
                f"{location} keys differ; missing={missing}, unknown={unknown}"
            )

    @staticmethod
    def _positive_int(value: Mapping[str, Any], key: str) -> int:
        item = value.get(key)
        if not isinstance(item, int) or isinstance(item, bool) or item < 1:
            raise ManifestValidationError(f"{key} must be a positive integer")
        return item

    @staticmethod
    def _positive_money(value: Mapping[str, Any], key: str) -> Decimal:
        item = value.get(key)
        if not isinstance(item, str) or not re.fullmatch(r"[0-9]+\.[0-9]{2}", item):
            raise ManifestValidationError(f"{key} must be a decimal currency string")
        try:
            amount = Decimal(item)
        except InvalidOperation as error:
            raise ManifestValidationError(f"{key} is invalid") from error
        if amount <= 0:
            raise ManifestValidationError(f"{key} must be positive")
        return amount
