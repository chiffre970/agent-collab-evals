"""Offline composition of live pilot adapters; this module grants no spend."""

from dataclasses import dataclass
from pathlib import Path
import tempfile

from .adapters.compute_candidate_evaluator import ComputeCandidateEvaluationProfile
from .adapters.modal_vllm_compute import ModalVllmCliTransport, ModalVllmComputeProfile, ModalVllmEvidenceResolver
from .adapters.modal_vllm_correctness_compute import ModalVllmCorrectnessCliTransport, ModalVllmCorrectnessEvidenceResolver, ModalVllmCorrectnessProfile
from .adapters.modal_vllm_performance_compute import ModalVllmHiddenPerformanceEvidenceResolver, ModalVllmHiddenPerformanceProfile
from .adapters.modal_vllm_quality_compute import ModalVllmQualityCliTransport, ModalVllmQualityEvidenceResolver, ModalVllmQualityProfile
from .adapters.opencode_harness import OpenCodeRuntimeProfile
from .adapters.openrouter import OpenRouterUpstream
from .adapters.sqlite_compute_routes import ComputeRouteAdapter
from .adapters.sqlite_compute_spend import SqliteComputeSpendAuthorizationService
from .adapters.sqlite_execution_backend import SqliteComputeBackend
from .artifacts import ArtifactRef
from .campaigns.model_serving import ModelServingCampaign
from .campaigns.serving_scoring import ScoringProfile
from .campaigns.serving_workload import HiddenWorkloadExpectations, load_hidden_workload
from .canonical import digest_bytes, digest_value, parse_json
from .compute_backend import FrozenComputeRunManifest
from .evaluation import EvaluationReservation, EvaluationReservationStatus, EvaluationScope
from .model_gateway import ModelGatewayProfile
from .sandbox import SandboxProfile
from .solo_pilot_stack import compose_pilot_stack


@dataclass(frozen=True)
class LivePilotConfiguration:
    """Experiment inputs outside the credential environment, pending approval."""

    document: dict
    repository: Path
    campaign: ModelServingCampaign
    gateway: ModelGatewayProfile
    runtime: OpenCodeRuntimeProfile
    sandbox: SandboxProfile
    public_compute: ModalVllmComputeProfile
    hidden_manifest: Path
    modal_cli: Path

    @classmethod
    def load(cls, path: Path, repository: Path):
        document = parse_json(path.read_text())
        expected = {"schema_version", "execution_mode", "execution_authorized", "campaign",
            "gateway_profile", "runtime_profile", "sandbox_profile", "public_compute_profile",
            "hidden_manifest", "hidden_manifest_digest", "modal_cli", "phase_seconds",
            "model_limit_usd_nanos", "modal_limit_usd_nanos", "task_seed"}
        if not isinstance(document, dict) or set(document) != expected:
            raise ValueError("live pilot configuration fields differ")
        if document["schema_version"] != "solo-live-configuration/v1" or document["execution_mode"] != "live":
            raise ValueError("unsupported live pilot configuration")
        if document["execution_authorized"] is not False:
            raise ValueError("live pilot execution is disabled pending approval and deployment")
        # Approval will require a separate binding to this configuration digest.
        for field in ("model_limit_usd_nanos", "modal_limit_usd_nanos"):
            value = document[field]
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f"{field} must be a positive integer or null")
        if type(document["task_seed"]) is not int or document["task_seed"] < 0:
            raise ValueError("task_seed must be a nonnegative integer")
        seconds = document["phase_seconds"]
        if not isinstance(seconds, dict) or set(seconds) != {"public", "correctness", "quality", "performance"}:
            raise ValueError("live pilot phase allocations differ")
        if any(type(value) is not int or value < 1 for value in seconds.values()):
            raise ValueError("phase allocations must be positive integer seconds")

        def repo_path(field):
            if not isinstance(document[field], str):
                raise ValueError(f"{field} must be a path")
            result = (repository / document[field]).resolve(strict=True)
            result.relative_to(repository.resolve())
            return result

        campaign = ModelServingCampaign.load(repo_path("campaign"))
        gateway = ModelGatewayProfile.load(repo_path("gateway_profile"), repository_root=repository)
        runtime = OpenCodeRuntimeProfile.load(repo_path("runtime_profile"), repository_root=repository)
        sandbox = SandboxProfile.load(repo_path("sandbox_profile"))
        public = ModalVllmComputeProfile.load(repo_path("public_compute_profile"), repository_root=repository)
        if gateway.status != "development" or runtime.status != "development":
            raise ValueError("live exploratory pilot requires development model profiles")
        if runtime.model_id != gateway.requested_model or runtime.agent_inference_digest != gateway.model_profile_digest:
            raise ValueError("runtime and gateway model/provider profiles differ")
        if public.campaign_manifest_digest != campaign.manifest_digest:
            raise ValueError("public compute campaign differs")
        if public.modal_environment != campaign.raw["hardware"]["environment"]:
            raise ValueError("Modal environment differs from the campaign")
        # Private inputs may be outside the repository; they never enter an actor workspace.
        if not isinstance(document["hidden_manifest"], str):
            raise ValueError("hidden_manifest must be a path")
        hidden = (repository / document["hidden_manifest"]).resolve(strict=True)
        return cls(document, repository.resolve(), campaign, gateway, runtime, sandbox,
            public, hidden, repo_path("modal_cli"))

    def model_upstream(self, api_key: str) -> OpenRouterUpstream:
        """Construct the existing provider adapter; credentials are never retained."""
        return OpenRouterUpstream.from_profile(self.gateway, api_key)

    def hidden_bundle(self):
        campaign = self.campaign
        expectations = HiddenWorkloadExpectations(
            campaign_manifest_digest=campaign.manifest_digest,
            hidden_contract_digest=campaign.transitive_digests["hidden_contract"],
            quality_profile_digest=campaign.transitive_digests["quality_profile"],
            quality_policy_digest=campaign.transitive_digests["quality_policy"],
            quality_workload_digest=campaign.quality_policy().quality_workload_digest,
            public_correctness_digest=campaign.transitive_digests["public_correctness"],
            public_performance_digest=campaign.transitive_digests["public_profile"],
            required_gates=tuple(campaign.hidden_contract()["required_gates"]))
        return load_hidden_workload(self.hidden_manifest, expectations, campaign.benchmark_plan(),
            registered_manifest_digest=self.document["hidden_manifest_digest"])


def build_live_stack(root, configuration, run_id, hidden, policy):
    """Install real Modal factories without issuing authority or executing work."""
    campaign, repository = configuration.campaign, configuration.repository
    public = configuration.public_compute
    common = dict(campaign=campaign, campaign_manifest=public.campaign_manifest,
        hidden_workload=hidden, modal_script=public.modal_script,
        modal_environment=public.modal_environment, modal_client_version=public.modal_client_version,
        attempt=1, maximum_collection_seconds=public.maximum_collection_seconds,
        evidence_volume=public.evidence_volume)
    scoring = ScoringProfile.load(repository / "campaigns/model_serving_v0/evaluator/scoring_hidden_v1.toml")
    modals = {"public": public,
        "correctness": ModalVllmCorrectnessProfile.create(profile_id="solo-pilot-correctness", **common),
        "quality": ModalVllmQualityProfile.create(profile_id="solo-pilot-quality", **common),
        **{f"performance-{r}": ModalVllmHiddenPerformanceProfile.create(
            profile_id=f"solo-pilot-performance-{r}", scoring_profile=scoring.path, repetition=r, **common)
            for r in range(1, 4)}}
    transports = {"public": ModalVllmCliTransport, "correctness": ModalVllmCorrectnessCliTransport,
        "quality": ModalVllmQualityCliTransport,
        **{f"performance-{r}": ModalVllmCliTransport for r in range(1, 4)}}
    resolvers = {"public": ModalVllmEvidenceResolver, "correctness": ModalVllmCorrectnessEvidenceResolver,
        "quality": ModalVllmQualityEvidenceResolver,
        **{f"performance-{r}": ModalVllmHiddenPerformanceEvidenceResolver for r in range(1, 4)}}
    adapters = {}
    for key, profile in modals.items():
        transport_type, resolver_type = transports[key], resolvers[key]
        transport_digest = transport_type.profile_digest_for(profile.digest, configuration.modal_cli,
            SqliteComputeSpendAuthorizationService.profile_digest_for())
        evidence_digest = resolver_type.profile_digest_for(profile.digest)
        evaluator_digest = None
        if key == "correctness" or key.startswith("performance-"):
            phase = "correctness" if key == "correctness" else "performance"
            workload = profile.correctness_workload_digest if phase == "correctness" else profile.performance_profile_digest
            evaluator_digest = ComputeCandidateEvaluationProfile(f"pilot-{key}", phase,
                campaign.manifest_digest, hidden.manifest_digest, workload,
                SqliteComputeBackend.profile_digest_for(transport_digest, evidence_digest),
                public.maximum_collection_seconds).digest

        def evidence(route_root, key=key, profile=profile, resolver_type=resolver_type, transport_digest=transport_digest):
            if key in {"correctness", "quality"}:
                return resolver_type(profile, route_root, transport_digest)
            return resolver_type(profile, repository, route_root, transport_digest)

        def transport(route_root, spend, key=key, profile=profile, transport_type=transport_type,
                      evaluator_digest=evaluator_digest, evidence=evidence):
            options = {}
            if evaluator_digest is not None:
                options["evaluator_profile_digest"] = evaluator_digest
            if key.startswith("performance-"):
                options["evidence_resolver"] = evidence(route_root)
            return transport_type(profile, repository, route_root, configuration.modal_cli, spend, **options)

        adapters[key] = ComputeRouteAdapter(transport_digest, evidence_digest, transport, evidence)
    stack = compose_pilot_stack(root, campaign, run_id, adapters, public_profile=public,
        hidden_digest=hidden.manifest_digest, policy=policy, scoring=scoring,
        correctness_workload=modals["correctness"].correctness_workload_digest,
        performance_workload=modals["performance-1"].performance_profile_digest,
        phase_seconds=configuration.document["phase_seconds"], collection_seconds=public.maximum_collection_seconds,
        execution_mode="live", authority_digest=digest_value(configuration.document),
        details={"execution_authorized": False, "modal_profiles": {key: value.digest for key, value in modals.items()}})
    return stack, adapters


def check_live_pilot(config_path: Path, repository: Path) -> dict:
    """Validate local inputs and construct every adapter without network or keys."""
    configuration = LivePilotConfiguration.load(config_path, repository)
    hidden = configuration.hidden_bundle()
    with tempfile.TemporaryDirectory(prefix="solo-live-check-") as directory:
        root = Path(directory)
        stack, adapters = build_live_stack(root, configuration, "offline-check", hidden, configuration.campaign.quality_policy())
        requests = prepare_offline_requests(stack, configuration)
        # Construction proves constructor and profile compatibility. No grants,
        # HTTP connections, or Modal subprocesses are created here.
        for index, (key, planned) in enumerate(requests):
            adapter = adapters[key]
            route_root = root / f"constructor-check-{index}"
            manifest = FrozenComputeRunManifest.load_or_create(route_root / "manifest.json",
                campaign_run_id=planned[0].campaign_run_id, compute_enabled=True,
                transport_profile_digest=adapter.transport_profile_digest,
                backend_profile_digest=adapter.backend_profile_digest, requests=planned)
            spend = SqliteComputeSpendAuthorizationService(route_root / "spend.sqlite3", manifest)
            if adapter.transport(route_root, spend).profile_digest != adapter.transport_profile_digest:
                raise RuntimeError("Modal transport factory profile differs")
            if adapter.evidence(route_root).profile_digest != adapter.evidence_profile_digest:
                raise RuntimeError("Modal evidence factory profile differs")
        configuration.model_upstream("offline-construction-only")
        reserved_seconds = sum(request.maximum_seconds for _, group in requests for request in group)
        evaluation_digest = stack.evaluator.profile_digest
    return {"ok": True, "mode": "offline_configuration_check", "execution_authorized": False,
        "scoreable": False, "config_digest": digest_value(configuration.document),
        "model": configuration.gateway.requested_model, "provider": configuration.gateway.expected_provider,
        "gateway_profile_digest": configuration.gateway.resolved_digest,
        "runtime_profile_digest": configuration.runtime.resolved_digest,
        "evaluation_profile_digest": evaluation_digest, "hidden_manifest_digest": hidden.manifest_digest,
        "modal_environment": configuration.public_compute.modal_environment,
        "gpu": configuration.campaign.raw["hardware"]["gpu_type"],
        "compute_adapter_count": len(adapters), "planned_compute_executions": sum(len(group) for _, group in requests),
        "planned_reserved_seconds": reserved_seconds, "hidden_reserved_seconds": stack.hidden_seconds,
        "actual_spend_usd_nanos": 0,
        "remaining_gates": ["live command execution and abort/remote-cleanup integration",
            "explicit run-bound model and Modal dollar-budget approval",
            "current provider route and billing qualification",
            "isolated deployment: current Darwin sandbox does not restrict filesystem or local services"],
        "billing_note": "Function allowances are not provider-billed time or a dollar spending cap."}


def prepare_offline_requests(stack, configuration):
    """Exercise the production planners with reference bytes, without admission."""
    reference = configuration.campaign.reference_candidate_path.read_bytes()
    artifact = ArtifactRef("artifact-" + digest_bytes(reference)[7:39])
    visible = EvaluationReservation("evaluation-" + "1" * 32, "visible:offline-check",
        "offline-check", "actor-0", artifact, EvaluationScope.VISIBLE,
        configuration.document["phase_seconds"]["public"], EvaluationReservationStatus.RESERVED)
    hidden = EvaluationReservation("evaluation-" + "2" * 32, "hidden:offline-check",
        "offline-check", None, artifact, EvaluationScope.HIDDEN,
        stack.hidden_seconds, EvaluationReservationStatus.RESERVED)
    groups = {}
    for request in stack.hidden.prepare_hidden_requests(reference, hidden, "hidden:offline-check"):
        groups.setdefault(stack.adapter_for_profile[request.evaluator_profile_digest], []).append(request)
    return (("public", (stack.public.prepare_visible_request(reference, None, "visible:reference"),)),
        ("public", (stack.public.prepare_visible_request(reference, visible, "visible:offline-check"),)),
        *((key, tuple(value)) for key, value in groups.items()))
