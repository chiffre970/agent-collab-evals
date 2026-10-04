"""Reconstruct read-only Modal evidence profiles from an original checkout."""

from pathlib import Path
from contextlib import closing
import sqlite3

from .adapters.modal_vllm_compute import ModalVllmEvidenceResolver
from .adapters.modal_vllm_correctness_compute import ModalVllmCorrectnessEvidenceResolver, ModalVllmCorrectnessProfile
from .adapters.modal_vllm_performance_compute import ModalVllmHiddenPerformanceEvidenceResolver, ModalVllmHiddenPerformanceProfile
from .adapters.modal_vllm_quality_compute import ModalVllmQualityEvidenceResolver, ModalVllmQualityProfile
from .adapters.sqlite_execution_backend import SqliteComputeBackend
from .campaigns.serving_scoring import ScoringProfile
from .canonical import digest_bytes, digest_file, digest_value, parse_json
from .solo_evaluation_recovery import plan_evaluation_recovery
from .solo_live_configuration import LivePilotConfiguration, make_modal_adapters


class _RetainedV1QualityResolver(ModalVllmQualityEvidenceResolver):
    """Read legacy evidence under its original pin; never use for new execution."""

    @staticmethod
    def profile_digest_for(quality_profile_digest: str) -> str:
        return digest_value({"adapter": "modal-vllm-quality-evidence-resolver/v0alpha1",
            "quality_profile_digest": quality_profile_digest,
            "source": "digest_verified_local_mirror_of_modal_volume"})


def plan_modal_evaluation_recovery(source_root: Path, audit_digest: str, config_path: Path,
                                 source_repository: Path, output_root: Path) -> dict:
    """Inspect original evidence without constructing a spend service or runtime."""
    configuration = LivePilotConfiguration.load(config_path, source_repository)
    run_config = parse_json((source_root / "run-config.json").read_text())
    # Current profiles are not a substitute for original evaluation provenance.
    if configuration.document != run_config.get("config"):
        raise RuntimeError("recovery configuration differs from the original run")
    public = configuration.public_compute
    if (digest_file(public.modal_script) != run_config["spend_admission"]["estimate"]["modal_script_digest"]
        or configuration.campaign.manifest_digest != run_config["campaign_manifest_digest"]):
        raise RuntimeError("recovery source checkout differs from the original run")
    hidden = configuration.hidden_bundle()
    common = dict(campaign=configuration.campaign, campaign_manifest=public.campaign_manifest,
        hidden_workload=hidden, modal_script=public.modal_script, modal_environment=public.modal_environment,
        modal_client_version=public.modal_client_version, attempt=1,
        maximum_collection_seconds=public.maximum_collection_seconds, evidence_volume=public.evidence_volume)
    scoring = ScoringProfile.load(source_repository / "campaigns/model_serving_v0/evaluator/scoring_hidden_v1.toml")
    profiles = {"public": public,
        "correctness": ModalVllmCorrectnessProfile.create(profile_id="solo-pilot-correctness", **common),
        "quality": ModalVllmQualityProfile.create(profile_id="solo-pilot-quality", **common),
        **{f"performance-{r}": ModalVllmHiddenPerformanceProfile.create(profile_id=f"solo-pilot-performance-{r}",
            scoring_profile=scoring.path, repetition=r, **common) for r in range(1, 4)}}

    def factory(adapter_id, route, manifest):
        profile = profiles[adapter_id]
        if adapter_id == "quality":
            for resolver_type in (ModalVllmQualityEvidenceResolver, _RetainedV1QualityResolver):
                evidence_digest = resolver_type.profile_digest_for(profile.digest)
                if SqliteComputeBackend.profile_digest_for(manifest.transport_profile_digest, evidence_digest) == manifest.backend_profile_digest:
                    return resolver_type(profile, route, manifest.transport_profile_digest)
            raise RuntimeError("unsupported original quality evidence profile")
        if adapter_id == "correctness":
            return ModalVllmCorrectnessEvidenceResolver(profile, route, manifest.transport_profile_digest)
        resolver_type = ModalVllmEvidenceResolver if adapter_id == "public" else ModalVllmHiddenPerformanceEvidenceResolver
        return resolver_type(profile, source_repository, route, manifest.transport_profile_digest)

    return plan_evaluation_recovery(source_root, audit_digest, factory, output_root)


def prepare_modal_evaluation_continuation(configuration, recovery_path: Path,
                                         recovery_digest: str, output_root: Path, run_id: str):
    """Freeze six production Modal requests; do not issue authority or call APIs."""
    from .adapters.compute_candidate_evaluator import ComputeCandidateEvaluationProfile
    from .adapters.compute_quality_backend import ComputeQualityRepetitionProfile
    from .solo_evaluation_continuation import EvaluationContinuation

    if digest_file(recovery_path) != recovery_digest:
        raise RuntimeError("recovery plan digest differs")
    recovery = parse_json(recovery_path.read_text())
    source = Path(recovery["source_root"])
    campaign, repository = configuration.campaign, configuration.repository
    hidden = configuration.hidden_bundle()
    policy = campaign.quality_policy()
    modals, adapters = make_modal_adapters(configuration, hidden)
    collection = configuration.public_compute.maximum_collection_seconds
    quality = ComputeQualityRepetitionProfile("pilot-quality-repetitions", campaign.manifest_digest,
        hidden.manifest_digest, policy.quality_profile_digest, policy.quality_workload_digest,
        adapters["quality"].backend_profile_digest, 3, collection)
    phases = {key: ComputeCandidateEvaluationProfile(f"pilot-{key}",
        "correctness" if key == "correctness" else "performance", campaign.manifest_digest,
        hidden.manifest_digest, modals[key].correctness_workload_digest if key == "correctness"
        else modals[key].performance_profile_digest, adapters[key].backend_profile_digest, collection)
        for key in ("correctness", "performance-1", "performance-2", "performance-3")}
    reference = campaign.reference_candidate_path.read_bytes()
    candidates = {digest_bytes(reference): reference}
    storage = source / "candidate-services/artifacts"
    with closing(sqlite3.connect((storage / "artifacts.sqlite3").resolve(strict=True).as_uri() + "?mode=ro", uri=True)) as connection:
        for digest in {entry["request"]["candidate_digest"] for entry in recovery["entries"]} - set(candidates):
            rows = connection.execute("SELECT artifact_ref, size_bytes FROM artifacts WHERE campaign_run_id=? AND digest=?",
                (recovery["source_run_id"], digest[7:])).fetchall()
            if not rows:
                raise RuntimeError("selected recovery artifact is missing")
            contents = []
            for artifact, size in rows:
                from .artifacts import ArtifactRef
                path = storage / "blobs" / ArtifactRef(artifact).value
                content = path.read_bytes()
                if len(content) != size or digest_bytes(content) != digest:
                    raise RuntimeError("selected recovery artifact bytes differ")
                contents.append(content)
            candidates[digest] = contents[0]
    with closing(sqlite3.connect((source / "candidate-services/submissions.sqlite3").resolve(strict=True).as_uri() + "?mode=ro", uri=True)) as connection:
        rows = connection.execute("SELECT selection_json FROM selections WHERE campaign_run_id=? AND job_id=?",
            (recovery["source_run_id"], "optimize-serving")).fetchall()
    if len(rows) != 1:
        raise RuntimeError("source selection receipt is missing")
    selection = parse_json(rows[0][0])
    if (selection.get("campaign_run_id") != recovery["source_run_id"]
        or selection.get("job_id") != "optimize-serving"
        or selection.get("selection_receipt") != "selection-" + selection["selection_digest"][7:39]):
        raise RuntimeError("source selection receipt identity differs")
    selected_artifact = selection["selected_artifact_ref"]
    from .artifacts import ArtifactRef
    selected_content = (storage / "blobs" / ArtifactRef(selected_artifact).value).read_bytes()
    hidden_candidates = {entry["request"]["candidate_digest"] for entry in recovery["entries"]
        if entry["adapter_id"] == "correctness"}
    if hidden_candidates != {digest_bytes(selected_content)}:
        raise RuntimeError("source selection does not name the frozen hidden candidate")
    binding = {"campaign_manifest_digest": campaign.manifest_digest,
        "hidden_manifest_digest": hidden.manifest_digest,
        "public_compute_profile_digest": configuration.public_compute.digest,
        "modal_profiles": {key: profile.digest for key, profile in modals.items()},
        "source_selection": selection,
        "cost_profile_digest": digest_file(repository / "config/compute/modal-pilot-cost-v1.json"),
        "source": {str(path.relative_to(repository)): digest_file(path)
            for base in (repository / "src/agent_collab_evals", repository / "campaigns/model_serving_v0/reference")
            for path in sorted(base.rglob("*.py")) if path.is_file()}}
    scoring = ScoringProfile.load(repository / "campaigns/model_serving_v0/evaluator/scoring_hidden_v1.toml")
    return EvaluationContinuation(output_root, recovery_path, recovery_digest, run_id, campaign=campaign,
        adapters=adapters, quality_profile=quality, phase_profiles=phases, policy=policy, scoring=scoring,
        candidates=candidates, build_binding=binding, execution_mode="live")
