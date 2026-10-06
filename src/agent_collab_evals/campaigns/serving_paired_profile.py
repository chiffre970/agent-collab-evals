"""Frozen inputs for exploratory, co-located serving evaluation."""

from dataclasses import dataclass
import importlib.metadata
from pathlib import Path

from ..canonical import digest_file, digest_value, parse_json
from ..compute_backend import ComputeExecutionRequest
from ..evaluation import EvaluationScope
from .model_serving import load_benchmark_plan
from .serving_correctness import load_correctness_workload
from .serving_paired_performance import FUNCTION_SECONDS, pair_spec
from .serving_pair_policy import ALLOWED_DRIVERS
from .serving_quality import build_quality_requests, load_quality_workload
from .serving_scoring import ScoringProfile
from .serving_workload import verify_quality_request_specs


HIDDEN_SLOTS = ("correctness-1", "quality-1", "quality-2", "quality-3",
    "performance-1", "performance-2", "performance-3")


@dataclass(frozen=True)
class PairedServingProfile:
    repository: Path
    campaign: object
    hidden: object
    quality_policy: object
    pins: dict
    digest: str

    @classmethod
    def create(cls, repository, campaign, hidden, quality_policy):
        repository = Path(repository).resolve(strict=True)
        if importlib.metadata.version("modal") != campaign.measurement_profile().modal_client_version:
            raise ValueError("paired serving Modal client version differs from the measurement pin")
        quality_policy.validate_against(campaign.quality_profile())
        if (hidden.resource_digests["quality_workload"] != quality_policy.quality_workload_digest
            or hidden.resource_digests["quality_policy"] != quality_policy.digest):
            raise ValueError("paired quality policy and workload differ")
        workload = load_quality_workload(hidden.resource_paths["quality_workload"], campaign.quality_profile())
        verify_quality_request_specs(hidden.resource_paths["quality_requests"],
            build_quality_requests(campaign.quality_profile(), workload, served_model_name="target-model"),
            expected_digest=hidden.resource_digests["quality_requests"])
        files = {"manifest": campaign.root / "campaign.toml", "reference": campaign.reference_candidate_path,
            "base_script": campaign.root / "reference/modal_vllm.py",
            "paired_script": campaign.root / "reference/modal_paired.py",
            "bridge": repository / "scripts/runtime/modal_paired_serving.py",
            "pair_policy": repository / "src/agent_collab_evals/campaigns/serving_pair_policy.py",
            "profile_source": Path(__file__),
            "scorer_source": Path(__file__).with_name("serving_paired_result.py"),
            "measurement": campaign.measurement_profile_path,
            "public_plan": campaign.root / campaign.raw["workload"]["public_profile"],
            "public_scoring": campaign.scoring_profile_path,
            "hidden_manifest": hidden.manifest_path,
            "hidden_scoring": campaign.root / "evaluator/scoring_hidden_v1.toml",
            "quality_profile": campaign.quality_profile().path,
            **{name: path for name, path in hidden.resource_paths.items()},
            **{"source:" + str(path.relative_to(repository)): path
                for path in sorted((repository / "src/agent_collab_evals").rglob("*.py"))}}
        pins = {name: {"file": str(Path(path).resolve(strict=True)), "digest": digest_file(Path(path))}
            for name, path in files.items()}
        authority = {"schema_version": "exploratory-paired-serving-profile/v1", "pins": pins,
            "campaign_manifest_digest": campaign.manifest_digest,
            "quality_policy": quality_policy, "allowed_drivers": ALLOWED_DRIVERS,
            "function_seconds": FUNCTION_SECONDS, "scoreable": False,
            "pairing": "one_gpu_fresh_role_processes_fixed_alternating_order"}
        result = cls(repository, campaign, hidden, quality_policy, pins, digest_value(authority))
        result.check_inputs()
        for scope in EvaluationScope:
            plan, scoring, _ = result.performance_inputs(scope)
            scoring.validate_against(plan, measurement_profile_digest=campaign.measurement_profile().digest,
                measurement_repetitions=3)
        return result

    def check_inputs(self):
        if any(digest_file(Path(ref["file"])) != ref["digest"] for ref in self.pins.values()):
            raise RuntimeError("paired serving input changed")
        if self.campaign.manifest_digest != self.campaign.load(Path(self.pins["manifest"]["file"])).manifest_digest:
            raise RuntimeError("paired serving campaign changed")

    def performance_inputs(self, scope):
        plan_ref = self.pins["public_plan" if scope is EvaluationScope.VISIBLE else "performance_profile"]
        scoring = ScoringProfile.load(Path(self.pins["public_scoring" if scope is EvaluationScope.VISIBLE else "hidden_scoring"]["file"]))
        return load_benchmark_plan(Path(plan_ref["file"])), scoring, plan_ref["digest"]

    def slot(self, request):
        slot = request.execution_key.rsplit(":paired-", 1)[-1]
        allowed = ("public-1",) if request.scope is EvaluationScope.VISIBLE else HIDDEN_SLOTS
        if (slot not in allowed or request.maximum_seconds != FUNCTION_SECONDS
            or request.evaluator_profile_digest != self.evaluator_digest(request.scope)):
            raise PermissionError("paired compute request differs from its scope/profile")
        return slot

    def evaluator_digest(self, scope):
        return digest_value({"adapter": "paired-serving-evaluator/v1", "profile": self.digest, "scope": scope.value})

    def spec(self, request: ComputeExecutionRequest, *, namespace=None):
        self.check_inputs()
        slot = self.slot(request)
        phase, ordinal = slot.rsplit("-", 1)
        repetition = int(ordinal)
        root = "model-serving-paired-stack/" + (namespace or request.request_digest[7:])
        if phase in {"public", "performance"}:
            plan, scoring, plan_digest = self.performance_inputs(request.scope)
            return pair_spec(self.campaign, plan, scoring, plan_digest, repetition, root)
        if phase == "quality":
            profile = self.campaign.quality_profile()
            requests = parse_json(Path(self.pins["quality_requests"]["file"]).read_text())["requests"]
            workload_digest, profile_digest = self.hidden.resource_digests["quality_workload"], profile.digest
            concurrency, timeout = profile.max_concurrency, profile.request_timeout_seconds
        else:
            workload = load_correctness_workload(Path(self.pins["correctness_requests"]["file"]))
            requests = []
            for case in workload.cases:
                body = case.request("target-model")
                body.pop("temperature")
                body.pop("top_p")
                body.update(temperature_milli=0, top_p_milli=1000, top_k=0, min_p_milli=0)
                requests.append({"case_id": case.case_id, "body": body})
            workload_digest, profile_digest = workload.digest, self.digest
            concurrency, timeout = 1, 300
        measurement = self.campaign.measurement_profile()
        policy = {"benchmark": {"campaign_manifest_digest": self.campaign.manifest_digest,
            "quality_profile_digest": profile_digest, "quality_workload_digest": workload_digest,
            "repetition": repetition, "attempt": 1, "evidence_root": root,
            "max_concurrency": concurrency, "request_timeout_seconds": timeout, "requests": requests},
            "allowed_drivers": list(ALLOWED_DRIVERS), "order": ["reference", "candidate"] if repetition % 2 else ["candidate", "reference"],
            "expected_gpu": {"name": "NVIDIA L4", "memory_mib": str(measurement.gpu_memory_mib),
                "power_limit_watts": measurement.gpu_power_limit_watts}}
        return {**policy, "pair_digest": digest_value(policy)}
