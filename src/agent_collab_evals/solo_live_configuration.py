"""Offline composition of live pilot adapters; this module grants no spend."""

from dataclasses import dataclass
from pathlib import Path
import re
import tempfile

from .adapters.compute_candidate_evaluator import ComputeCandidateEvaluationProfile
from .adapters.modal_vllm_compute import ModalVllmCliTransport, ModalVllmComputeProfile, ModalVllmEvidenceResolver
from .adapters.modal_vllm_correctness_compute import ModalVllmCorrectnessCliTransport, ModalVllmCorrectnessEvidenceResolver, ModalVllmCorrectnessProfile
from .adapters.modal_vllm_performance_compute import ModalVllmHiddenPerformanceEvidenceResolver, ModalVllmHiddenPerformanceProfile
from .adapters.modal_vllm_quality_compute import ModalVllmQualityCliTransport, ModalVllmQualityEvidenceResolver, ModalVllmQualityProfile
from .adapters.opencode_harness import OpenCodeRuntimeProfile
from .adapters.oci_sandbox import OciSandboxExec, OciSandboxProfile
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
from .modal_pilot_cost import modal_pilot_cost
from .pilot_spend_guard import PilotSpendGuard
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
    sandbox: SandboxProfile | OciSandboxProfile
    public_compute: ModalVllmComputeProfile
    hidden_manifest: Path
    modal_cli: Path

    @staticmethod
    def required_sandbox_lifetime_seconds(document: dict) -> int:
        """Bound two agent jobs and every planned public and hidden GPU slot."""
        seconds = document["phase_seconds"]
        return (
            seconds["public"]
            + seconds["correctness"]
            + 6 * seconds["quality"]
            + 3 * seconds["performance"]
            + 2 * document["runtime_timeout_seconds"]
            + 600  # Controller, collection, and cleanup margin.
        )

    @classmethod
    def load(cls, path: Path, repository: Path):
        document = parse_json(path.read_text())
        expected = {"schema_version", "execution_mode", "execution_authorized", "campaign",
            "gateway_profile", "runtime_profile", "sandbox_profile", "public_compute_profile",
            "hidden_manifest", "hidden_manifest_digest", "modal_cli", "phase_seconds",
            "model_limit_usd_nanos", "modal_limit_usd_nanos", "task_seed", "runtime_timeout_seconds"}
        if isinstance(document, dict) and document.get("schema_version") == "solo-live-configuration/v2":
            expected.add("sandbox_engine_identity_digest")
        if not isinstance(document, dict) or set(document) != expected:
            raise ValueError("live pilot configuration fields differ")
        if document["schema_version"] not in {"solo-live-configuration/v1", "solo-live-configuration/v2"} or document["execution_mode"] != "live":
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
        if type(document["runtime_timeout_seconds"]) is not int or not 1 <= document["runtime_timeout_seconds"] <= 3600:
            raise ValueError("runtime timeout must be between 1 and 3600 seconds")
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
        sandbox_path = repo_path("sandbox_profile")
        sandbox_document = parse_json(sandbox_path.read_text())
        if not isinstance(sandbox_document, dict):
            raise ValueError("sandbox profile must be an object")
        if sandbox_document.get("schema_version") == "oci-process-sandbox-profile/v2":
            if document["schema_version"] != "solo-live-configuration/v2":
                raise ValueError("OCI sandbox requires live configuration v2")
            sandbox = OciSandboxProfile.load(sandbox_path, repository_root=repository)
        else:
            sandbox = SandboxProfile.load(sandbox_path)
        engine_digest = document.get("sandbox_engine_identity_digest")
        if engine_digest is not None:
            if not isinstance(sandbox, OciSandboxProfile):
                raise ValueError("engine identity is only valid for an OCI sandbox")
            if not isinstance(engine_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", engine_digest):
                raise ValueError("sandbox engine identity digest is invalid")
        public = ModalVllmComputeProfile.load(repo_path("public_compute_profile"), repository_root=repository)
        if gateway.status != "development" or runtime.status != "development":
            raise ValueError("live exploratory pilot requires development model profiles")
        if runtime.model_id != gateway.requested_model or runtime.agent_inference_digest != gateway.model_profile_digest:
            raise ValueError("runtime and gateway model/provider profiles differ")
        if public.campaign_manifest_digest != campaign.manifest_digest:
            raise ValueError("public compute campaign differs")
        if public.modal_environment != campaign.raw["hardware"]["environment"]:
            raise ValueError("Modal environment differs from the campaign")
        if isinstance(sandbox, OciSandboxProfile) and sandbox.execution_authorized:
            if sandbox.timeout_seconds < cls.required_sandbox_lifetime_seconds(document):
                raise ValueError("OCI sandbox lifetime is shorter than the pilot schedule")
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


def make_live_dependencies(configuration, *, api_key, process_sandbox, spend_guard=None,
                           exploratory_authorization=None, state_root=None):
    """Compose the live lifecycle below the still-closed operator authority gate.

    The concrete spend guard reserves allowances before issuing request-bound
    authority. Provider cap verification still belongs to the closed operator gate.
    """
    from .adapters.modal_cleanup import ModalCallCanceller
    from .solo_pilot_command import LivePilotDependencies, make_opencode_runtime_dependencies

    if any(configuration.document[field] is None for field in ("model_limit_usd_nanos", "modal_limit_usd_nanos")):
        raise ValueError("live execution requires explicit model and Modal dollar limits")
    sandbox = configuration.sandbox
    if exploratory_authorization is not None and not isinstance(sandbox, OciSandboxProfile):
        raise ValueError("exploratory authorization requires OCI isolation")
    expected_sandbox_digest = sandbox.resolved_digest
    if isinstance(sandbox, OciSandboxProfile):
        engine_digest = configuration.document.get("sandbox_engine_identity_digest")
        if engine_digest is None:
            raise ValueError("live OCI execution requires a pinned engine identity")
        if exploratory_authorization is None:
            if not sandbox.execution_authorized or sandbox.status != "registered":
                raise ValueError("live OCI sandbox is not execution-authorized")
        else:
            from .solo_authorization import ExploratorySoloAuthorization
            if not isinstance(exploratory_authorization, ExploratorySoloAuthorization) or not isinstance(spend_guard, PilotSpendGuard) or state_root is None:
                raise ValueError("exploratory live execution requires concrete operator authority")
            exploratory_authorization.validate(configuration, spend_guard.envelope, spend_guard.run_id, state_root)
            spend_guard.operator_authorization = exploratory_authorization
        expected_sandbox_digest = OciSandboxExec.profile_digest_for(sandbox, engine_digest)
    if process_sandbox.profile_digest != expected_sandbox_digest:
        raise ValueError("live runtime sandbox differs from configuration")
    if not isinstance(spend_guard, PilotSpendGuard):
        raise ValueError("live execution requires a shared pilot spend guard")
    if isinstance(sandbox, OciSandboxProfile):
        if sandbox.timeout_seconds < configuration.required_sandbox_lifetime_seconds(configuration.document):
            raise ValueError("OCI sandbox lifetime is shorter than the pilot schedule")
    if spend_guard.configuration_digest != digest_value(configuration.document):
        raise ValueError("pilot spend guard configuration differs")
    hidden = configuration.hidden_bundle()
    policy = configuration.campaign.quality_policy()
    canceller = ModalCallCanceller(configuration.repository, configuration.modal_cli)

    def stack(root, run_id):
        return build_live_stack(root, configuration, run_id, hidden, policy)[0]

    runtime = make_opencode_runtime_dependencies(configuration.runtime, sandbox, process_sandbox,
        timeout_seconds=configuration.document["runtime_timeout_seconds"])

    return LivePilotDependencies(stack, lambda: configuration.model_upstream(api_key), spend_guard.authorize,
        lambda stack: stack.inventory.cleanup(canceller), runtime.harness,
        configuration.document["model_limit_usd_nanos"], configuration.document["phase_seconds"]["public"],
        gateway_options=runtime.gateway_options, sandbox_evidence=runtime.sandbox_evidence,
        spend_guard=spend_guard, operator_authorization=(
            {"digest": exploratory_authorization.digest, "document": exploratory_authorization.document}
            if exploratory_authorization is not None else None))


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
    cost = modal_pilot_cost(configuration.public_compute.modal_script,
        repository / "config/compute/modal-pilot-cost-v1.json")
    if any(value != cost["function_timeout_seconds"] for value in configuration.document["phase_seconds"].values()):
        raise ValueError("pilot phase allowances must match the pinned function timeout")
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
        "modal_admission_estimate": cost,
        "planned_modal_allowance_usd_nanos": cost["shared_overhead_allowance_usd_nanos"]
            + sum(len(group) for _, group in requests) * cost["per_execution_allowance_usd_nanos"],
        "actual_spend_usd_nanos": 0,
        "remaining_gates": ["live execution requires a separate digest-pinned exploratory solo authorization",
            "shared admission journal must cover qualification and pilot; provider gross-usage cap must be verified",
            "current provider route and billing qualification",
            "isolated deployment: pinned OCI image/engine and retained readiness assessments"],
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
