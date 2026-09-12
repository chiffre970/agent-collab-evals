"""Shared evaluator composition and synthetic transport wiring for the pilot."""

from dataclasses import dataclass, replace
from pathlib import Path

from .adapters.composite_hidden_evaluator import CompositeHiddenEvaluationProfile, CompositeHiddenServingEvaluator, HiddenEvaluationPhaseProfile
from .adapters.compute_candidate_evaluator import ComputeCandidateEvaluationProfile, ComputeCandidateEvaluator
from .adapters.compute_quality_backend import ComputeQualityRepetitionBackend, ComputeQualityRepetitionProfile
from .adapters.modal_serving_evaluator import ModalServingDevelopmentEvaluator
from .adapters.modal_vllm_compute import ModalVllmComputeProfile
from .adapters.performance_series_evaluator import PerformanceSeriesEvaluator, PerformanceSeriesProfile
from .adapters.quality_series_evaluator import PairedQualitySeriesEvaluator, QualitySeriesProfile, quality_policy_authority_digest
from .adapters.split_scope_evaluator import EvaluationLaneProfile, RegisteredEvaluationProfile, SplitScopeServingEvaluator
from .adapters.sqlite_compute_routes import ComputeRouteAdapter, SqliteComputeRouteInventory
from .adapters.synthetic_pilot_compute import SyntheticPilotEvidence, SyntheticPilotTransport
from .canonical import digest_bytes, digest_value
from .campaigns.serving_scoring import ScoringProfile
from .evaluation import EvaluationScope
from .pilot_evidence import retain_document
from .solo_pilot_runner import PilotComputeRoute


@dataclass
class SoloPilotStack:
    inventory: SqliteComputeRouteInventory
    public: ModalServingDevelopmentEvaluator
    hidden: CompositeHiddenServingEvaluator
    evaluator: SplitScopeServingEvaluator
    hidden_seconds: int
    adapter_for_profile: dict[str, str]
    profile_document: dict

    def public_plan(self, item):
        return (PilotComputeRoute("public", (self.public.prepare_visible_request(item.candidate, item.reservation, item.evaluation_key),)),)

    def hidden_plan(self, item):
        requests = self.hidden.prepare_hidden_requests(item.candidate, item.reservation, item.evaluation_key)
        groups = {}
        for request in requests:
            groups.setdefault(self.adapter_for_profile[request.evaluator_profile_digest], []).append(request)
        return tuple(PilotComputeRoute(key, tuple(value)) for key, value in groups.items())


def build_no_spend_stack(root: Path, campaign, run_id: str, repository: Path,
                        *, candidate_public_ppm: int = 1100000) -> SoloPilotStack:
    """Use real evaluator contracts with synthetic data, never a live transport."""
    hidden_digest = digest_value({"synthetic_hidden_bundle": 1, "campaign": campaign.manifest_digest})
    policy = replace(campaign.quality_policy(),
        quality_workload_digest=digest_value({"synthetic_quality_cases": 64}), bootstrap_resamples=100)
    scoring = ScoringProfile.load(repository / "campaigns/model_serving_v0/evaluator/scoring_hidden_v1.toml")
    common = {"campaign_manifest_digest": campaign.manifest_digest, "hidden_workload_manifest_digest": hidden_digest}
    recipes = {
        "public": {"phase": "public", "candidate_public_ppm": candidate_public_ppm},
        "correctness": {**common, "phase": "correctness", "workload_digest": digest_value({"synthetic_correctness": 1})},
        "quality": {**common, "phase": "quality", "quality_policy_authority": quality_policy_authority_digest(policy)},
        **{f"performance-{r}": {**common, "phase": "performance", "repetition": r,
            "workload_digest": digest_value({"synthetic_performance": 1})} for r in range(1, 4)},
    }
    adapters = {key: ComputeRouteAdapter(
        SyntheticPilotTransport.profile_digest_for(recipe), SyntheticPilotEvidence.profile_digest,
        lambda route_root, spend, recipe=recipe: SyntheticPilotTransport(route_root / "evidence", spend, recipe, policy),
        lambda route_root: SyntheticPilotEvidence(route_root / "evidence"),
    ) for key, recipe in recipes.items()}
    return compose_pilot_stack(root, campaign, run_id, adapters,
        public_profile=ModalVllmComputeProfile.load(repository / "config/compute/modal-vllm-development.json", repository_root=repository),
        hidden_digest=hidden_digest, policy=policy, scoring=scoring,
        correctness_workload=recipes["correctness"]["workload_digest"],
        performance_workload=recipes["performance-1"]["workload_digest"],
        phase_seconds={"correctness": 60, "quality": 60, "performance": 60},
        collection_seconds=1, execution_mode="no_spend",
        authority_digest=digest_value({"no_spend": True}),
        details={"synthetic_recipes": recipes})


def compose_pilot_stack(root, campaign, run_id, adapters, *, public_profile,
                        hidden_digest, policy, scoring, correctness_workload,
                        performance_workload, phase_seconds, collection_seconds,
                        execution_mode, authority_digest, details) -> SoloPilotStack:
    """Share evaluator and reservation construction across transport choices."""
    reference = campaign.reference_candidate_path.read_bytes()
    inventory = SqliteComputeRouteInventory(root / "compute", run_id, adapters)
    public = ModalServingDevelopmentEvaluator(root / "public.sqlite3", campaign,
        public_profile, inventory.backend("public"))
    correctness_profile = ComputeCandidateEvaluationProfile("pilot-correctness", "correctness",
        campaign.manifest_digest, hidden_digest, correctness_workload,
        adapters["correctness"].backend_profile_digest, collection_seconds)
    correctness = ComputeCandidateEvaluator(root / "correctness.sqlite3", campaign, correctness_profile, inventory.backend("correctness"))
    quality_profile = ComputeQualityRepetitionProfile("pilot-quality-repetitions", campaign.manifest_digest,
        hidden_digest, policy.quality_profile_digest, policy.quality_workload_digest,
        adapters["quality"].backend_profile_digest, 3, collection_seconds)
    quality_backend = ComputeQualityRepetitionBackend(root / "quality-repetitions.sqlite3", campaign, quality_profile, inventory.backend("quality"))
    quality_series = QualitySeriesProfile("pilot-quality", campaign.manifest_digest, hidden_digest,
        policy.quality_profile_digest, policy.digest, quality_policy_authority_digest(policy), policy.quality_workload_digest,
        "artifact-" + digest_bytes(reference)[7:39], digest_bytes(reference), quality_profile.digest, 3, phase_seconds["quality"],
        (("reference", "candidate"), ("candidate", "reference"), ("reference", "candidate")))
    quality = PairedQualitySeriesEvaluator(root / "quality.sqlite3", quality_series, policy, reference, quality_backend)
    performance_profiles = {r: ComputeCandidateEvaluationProfile(f"pilot-performance-{r}", "performance",
        campaign.manifest_digest, hidden_digest, performance_workload,
        adapters[f"performance-{r}"].backend_profile_digest, collection_seconds) for r in range(1, 4)}
    performance_series = PerformanceSeriesProfile("pilot-performance", campaign.manifest_digest, hidden_digest,
        performance_workload, scoring.digest,
        tuple(performance_profiles[r].digest for r in range(1, 4)), phase_seconds["performance"])
    performance = PerformanceSeriesEvaluator(root / "performance.sqlite3", performance_series, scoring,
        {r: ComputeCandidateEvaluator(root / f"performance-{r}.sqlite3", campaign, performance_profiles[r], inventory.backend(f"performance-{r}")) for r in range(1, 4)})
    hidden_profile = CompositeHiddenEvaluationProfile("pilot-hidden", campaign.manifest_digest, hidden_digest,
        HiddenEvaluationPhaseProfile("correctness", correctness.profile_digest, correctness_workload, phase_seconds["correctness"]),
        HiddenEvaluationPhaseProfile("quality", quality.profile_digest, policy.quality_workload_digest, quality_series.reserved_seconds),
        HiddenEvaluationPhaseProfile("performance", performance.profile_digest, performance_workload, performance_series.reserved_seconds))
    hidden = CompositeHiddenServingEvaluator(root / "hidden.sqlite3", hidden_profile,
        {"correctness": correctness, "quality": quality, "performance": performance})
    lanes = {scope: EvaluationLaneProfile(scope,
        public.profile_digest if scope is EvaluationScope.VISIBLE else hidden.profile_digest,
        adapters["public"].backend_profile_digest if scope is EvaluationScope.VISIBLE else digest_value({key: value.backend_profile_digest for key, value in adapters.items() if key != "public"}),
        public_profile.performance_profile_digest if scope is EvaluationScope.VISIBLE else hidden_digest, f"pilot-{scope.value}",
        digest_value({"pilot_schedule": scope.value, "authority": authority_digest}), f"pilot-evidence-{scope.value}") for scope in EvaluationScope}
    profile = RegisteredEvaluationProfile(f"{execution_mode}-pilot", campaign.manifest_digest, authority_digest, lanes[EvaluationScope.VISIBLE], lanes[EvaluationScope.HIDDEN])
    evaluator = SplitScopeServingEvaluator(root / "split.sqlite3", profile, public, hidden)
    document = {"execution_mode": execution_mode, **details,
        "quality_policy": policy, "hidden_profile": hidden_profile, "evaluation_profile": profile,
        "adapter_profiles": {key: value.backend_profile_digest for key, value in adapters.items()}}
    retain_document(root / "profiles.json", document)
    return SoloPilotStack(inventory, public, hidden, evaluator, hidden_profile.reserved_seconds,
        {correctness.profile_digest: "correctness", quality_profile.digest: "quality",
         **{performance_profiles[r].digest: f"performance-{r}" for r in range(1, 4)}}, document)
