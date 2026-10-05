"""Three bounded performance-only comparisons after an aborted solo pilot."""

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path
import subprocess
import time

from .adapters.compute_candidate_evaluator import ComputeCandidateEvaluationProfile, ComputeCandidateEvaluator
from .adapters.compute_quality_backend import ComputeQualityRepetitionProfile, ComputeQualityRepetitionBackend
from .adapters.modal_paired_performance import ModalPairedPerformanceEvidence, ModalPairedPerformanceTransport, profiles
from .adapters.sqlite_compute_spend import SqliteComputeSpendAuthorizationService
from .adapters.sqlite_execution_backend import SqliteComputeBackend
from .campaigns.model_serving import load_benchmark_plan
from .campaigns.serving_paired_performance import FUNCTION_SECONDS, pair_spec, summarize_pairs
from .campaigns.serving_quality import QualityPolicy, evaluate_quality_series
from .campaigns.serving_scoring import ScoringProfile
from .canonical import digest_bytes, digest_file, digest_value, parse_json
from .compute_backend import ComputeExecutionRequest, ComputeExecutionStatus, FrozenComputeRunManifest
from .evaluation import EvaluationScope
from .modal_pilot_cost import modal_paired_cost
from .pilot_evidence import retain_bytes, retain_document
from .pilot_spend import PilotSpendEnvelope
from .solo_evaluation_continuation import _ORDER, _pinned_document, EvaluationContinuation
from .solo_live_configuration import LivePilotConfiguration


def reference(path):
    return {"file": str(Path(path).resolve(strict=True)), "digest": digest_file(path)}


def capture_retained_inputs(previous, output):
    """Inspect the old frozen plan and reconcile its three completed quality jobs."""
    previous._check_manifest(previous.digest)
    backend = previous.inventory.backend("quality")
    receipts = backend.reconcile(previous.document["run_id"])
    if len(receipts) != 3 or any(r.status is not ComputeExecutionStatus.COMPLETE for r in receipts):
        raise RuntimeError("previous continuation has not completed its three quality jobs")
    previous.inventory.require_authorized(tuple(previous.requests[s] for s in _ORDER[6:9]), consumed=True)
    retained = []
    for slot in _ORDER[:9]:
        if slot in previous.requests:
            request = previous.requests[slot]
            _, evidence = backend.resolve(request)
            path = previous.root / "completed" / (request.request_digest[7:] + ".json")
            if parse_json(path.read_text()) != evidence:
                raise RuntimeError("completed quality snapshot differs from durable evidence")
        else:
            entry = previous.entries[slot]
            request, evidence = previous._source_request(slot), previous._retained(entry)
            path = previous.recovery_path.parent / entry["retained_evidence"]["file"]
        retained.append({"slot": slot, "request": request.document, "evidence": reference(path)})
    document = {"schema_version": "solo-performance-retained-inputs/v1",
        "previous_manifest": reference(previous.root / "manifest.json"),
        "recovery_plan": reference(previous.recovery_path), "source_audit_digest": previous.recovery["source_audit_digest"],
        "selected_candidate_digest": previous.document["selected_candidate_digest"],
        "quality_profile": previous.document["quality_profile"],
        "correctness_profile": previous.document["phase_profiles"]["correctness"], "evidence": retained}
    retain_document(output, document)
    return document


def validate_retained_inputs(path, campaign):
    """Recheck frozen selection, nine evidence pins and the original quality rules."""
    document = parse_json(Path(path).read_text())
    prior = _pinned_document(Path(document["previous_manifest"]["file"]), document["previous_manifest"]["digest"])
    recovery = _pinned_document(Path(document["recovery_plan"]["file"]), document["recovery_plan"]["digest"])
    root = Path(recovery["source_root"])
    audit = _pinned_document(root / "audit.json", document["source_audit_digest"])
    _pinned_document(root / "run-config.json", recovery["source_run_config_digest"])
    if (document.get("schema_version") != "solo-performance-retained-inputs/v1"
        or audit.get("status") != "aborted" or audit.get("scoreable") is not False
        or prior.get("source_audit_digest") != document["source_audit_digest"]
        or prior.get("selected_candidate_digest") != document["selected_candidate_digest"]
        or document["quality_profile"] != prior["quality_profile"]
        or document["correctness_profile"] != prior["phase_profiles"]["correctness"]
        or [e["slot"] for e in document["evidence"]] != list(_ORDER[:9])):
        raise RuntimeError("retained performance inputs differ from the aborted source")
    evidence = {}
    jobs = {j["slot"]: j for j in prior["jobs"]}
    for entry in document["evidence"]:
        slot = entry["slot"]
        request = ComputeExecutionRequest.from_document(entry["request"])
        expected_request = jobs[slot]["replacement_request"] or jobs[slot]["source"]["request"]
        if entry["request"] != expected_request:
            raise RuntimeError("retained source request differs")
        value = _pinned_document(Path(entry["evidence"]["file"]), entry["evidence"]["digest"])
        expected = {"request_digest": request.request_digest, "candidate_digest": request.candidate_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "evaluator_profile_digest": request.evaluator_profile_digest, "status": "complete", "failure": None}
        if (any(value.get(k) != v for k, v in expected.items()) or type(value.get("used_seconds")) is not int
            or not 0 <= value["used_seconds"] <= request.maximum_seconds):
            raise RuntimeError("retained evidence identity or duration differs")
        evidence[slot] = value
    scores = {slot: EvaluationContinuation._public_score(evidence[slot]) for slot in _ORDER[:2]}
    ref, cand = scores["public-reference"], scores["public-candidate"]
    winner = "public-reference" if ref[0] and (not cand[0] or ref[1] >= cand[1]) else "public-candidate"
    if not scores[winner][0] or evidence[winner]["candidate_digest"] != document["selected_candidate_digest"]:
        raise RuntimeError("retained public selection differs")
    reference_digest = digest_bytes(campaign.reference_candidate_path.read_bytes())
    if evidence["public-reference"]["candidate_digest"] != reference_digest:
        raise RuntimeError("retained public reference differs")
    quality_profile = ComputeQualityRepetitionProfile(**document["quality_profile"])
    correctness_profile = ComputeCandidateEvaluationProfile(**document["correctness_profile"])
    if evidence["correctness"]["candidate_digest"] != document["selected_candidate_digest"]:
        raise RuntimeError("retained correctness candidate differs")
    correctness = ComputeCandidateEvaluator.validate_evidence(correctness_profile, evidence["correctness"],
        ComputeExecutionRequest.from_document(jobs["correctness"]["source"]["request"]))
    runs = {"reference": {}, "candidate": {}}
    for entry in document["evidence"][3:]:
        slot = entry["slot"]
        _, repetition, role = slot.split("-")
        expected_candidate = reference_digest if role == "reference" else document["selected_candidate_digest"]
        if evidence[slot]["candidate_digest"] != expected_candidate:
            raise RuntimeError("retained quality candidate differs")
        runs[role][int(repetition)] = ComputeQualityRepetitionBackend.validate_evidence(quality_profile,
            evidence[slot], ComputeExecutionRequest.from_document(entry["request"]), role, int(repetition))
    # Reuse the policy actually frozen for the completed runs, not a policy
    # reconstructed with new workload or uncertainty settings.
    policy_fields = dict(prior["quality_policy"])
    for key in ("families", "reference_receipt_digests", "clean_control_receipt_digests"):
        policy_fields[key] = tuple(policy_fields[key])
    policy = QualityPolicy(path=campaign.quality_policy().path, **policy_fields)
    policy.validate_against(campaign.quality_profile())
    if policy.quality_workload_digest != quality_profile.quality_workload_digest:
        raise RuntimeError("retained quality policy and workload differ")
    quality = evaluate_quality_series(policy,
        [runs["reference"][r] for r in range(1, 4)], [runs["candidate"][r] for r in range(1, 4)])
    return {"correctness": correctness, "quality": quality, "evidence_digests":
        {e["slot"]: e["evidence"]["digest"] for e in document["evidence"]}}


def prepare(configuration, retained_path, root, run_id):
    """Freeze three matched-pair requests without granting or consuming authority."""
    root, repository = Path(root).resolve(), configuration.repository
    retained_path = Path(retained_path).resolve(strict=True)
    retained = parse_json(retained_path.read_text())
    validate_retained_inputs(retained_path, configuration.campaign)
    prior_root = Path(retained["previous_manifest"]["file"]).parent
    if root.is_relative_to(prior_root) or prior_root.is_relative_to(root):
        raise ValueError("performance follow-up state must be separate from prior evidence")
    candidate_path = prior_root / "candidates" / (retained["selected_candidate_digest"][7:] + ".json")
    candidate = candidate_path.read_bytes()
    if digest_bytes(candidate) != retained["selected_candidate_digest"]:
        raise RuntimeError("selected candidate changed")
    descriptor = configuration.campaign.validate_candidate_document(parse_json(candidate.decode()))
    reference_bytes = configuration.campaign.reference_candidate_path.read_bytes()
    retain_bytes(root / "candidate.json", candidate)
    retain_bytes(root / "reference.json", reference_bytes)
    hidden = configuration.hidden_bundle()
    performance = hidden.resource_paths["performance_profile"]
    plan = load_benchmark_plan(performance)
    scoring_path = repository / "campaigns/model_serving_v0/evaluator/scoring_hidden_v1.toml"
    scoring = ScoringProfile.load(scoring_path)
    scoring.validate_against(plan, measurement_profile_digest=configuration.campaign.measurement_profile().digest,
        measurement_repetitions=3)
    script = configuration.public_compute.modal_script
    estimate = modal_paired_cost(script, repository / "config/compute/modal-pilot-cost-v1.json")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    namespace = digest_value({"run_id": run_id, "state_root": str(root), "git_commit": commit})[7:]
    jobs = []
    for r in range(1, 4):
        spec = pair_spec(configuration.campaign, plan, scoring, digest_file(performance), r,
            f"model-serving-paired/{namespace}/repetition-{r:04d}")
        request = ComputeExecutionRequest(execution_key=f"{run_id}:paired-performance:{r}",
            campaign_run_id=run_id, reservation_id=f"paired-{namespace[:32]}-{r}", scope=EvaluationScope.HIDDEN,
            candidate_digest=digest_bytes(candidate), candidate_manifest_digest=descriptor.manifest_digest,
            evaluator_profile_digest=digest_value({"paired_spec": spec, "retained_inputs": digest_file(retained_path)}),
            maximum_seconds=FUNCTION_SECONDS)
        jobs.append({"request": request.document, "spec": spec})
    binding = {"repository": str(repository), "git_commit": commit, "jobs": jobs,
        "campaign_manifest": reference(configuration.public_compute.campaign_manifest),
        "performance_profile": reference(performance), "scoring_profile": reference(scoring_path),
        "modal_script": reference(script), "bridge_script": reference(repository / "scripts/runtime/modal_paired_performance.py"),
        "candidate": reference(root / "candidate.json"), "reference": reference(root / "reference.json"),
        "retained_inputs": reference(retained_path), "source": {str(p.relative_to(repository)): digest_file(p)
            for p in sorted((repository / "src/agent_collab_evals").rglob("*.py")) if p.is_file()}}
    transport, evidence = profiles(binding)
    authority = FrozenComputeRunManifest.load_or_create(root / "compute-manifest.json", campaign_run_id=run_id,
        compute_enabled=True, transport_profile_digest=transport,
        backend_profile_digest=SqliteComputeBackend.profile_digest_for(transport, evidence),
        requests=tuple(ComputeExecutionRequest.from_document(j["request"]) for j in jobs))
    document = {"schema_version": "solo-paired-performance-followup/v1", "run_id": run_id,
        "scoreable": False, "agent_reruns": 0, "model_calls": 0, "new_gpu_calls": 3,
        "interpretation": "exploratory_same_gpu_pairs_reuses_quality_from_original_environment",
        "binding": binding, "estimate": estimate, "compute_manifest_digest": authority.manifest_digest}
    retain_document(root / "manifest.json", document)
    return document


def run(root, authorization_path, authorization_digest):
    """Run the exact three-job inventory; do not release any historical reserve."""
    root = Path(root).resolve(strict=True)
    authorization = _pinned_document(Path(authorization_path), authorization_digest)
    fields = {"schema_version", "scope", "run_id", "expires_at", "manifest_digest", "retry_amendment",
        "spend_journal", "state_root", "git_commit"}
    if (not isinstance(authorization, dict) or set(authorization) != fields
        or authorization["schema_version"] != "paired-performance-authorization/v1"):
        raise PermissionError("paired operator authority fields differ")
    journal = Path(authorization["spend_journal"])
    if (not journal.is_absolute() or str(journal.resolve(strict=True)) != str(journal)
        or journal.is_relative_to(root) or root.is_relative_to(journal)):
        raise PermissionError("paired spend journal must be absolute and separate from run state")
    manifest_path = root / "manifest.json"
    document = _pinned_document(manifest_path, authorization["manifest_digest"])
    binding = document["binding"]
    def check(*, require_unexpired=True):
        expires = datetime.fromisoformat(authorization["expires_at"])
        if (expires.tzinfo is None
            or (require_unexpired and not datetime.now(UTC) < expires <= datetime.now(UTC) + timedelta(hours=24))
            or authorization.get("scope") != "one_exploratory_paired_performance_followup"
            or authorization.get("run_id") != document["run_id"] or authorization.get("state_root") != str(root)
            or authorization.get("git_commit") != binding["git_commit"]
            or digest_file(manifest_path) != authorization["manifest_digest"]):
            raise PermissionError("paired operator authority is expired or differs")
        repository = Path(binding["repository"])
        if (subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip() != binding["git_commit"]
            or subprocess.check_output(["git", "status", "--porcelain"], cwd=repository, text=True).strip()
            or any(digest_file(repository / name) != digest for name, digest in binding["source"].items())):
            raise PermissionError("paired follow-up requires the approved clean build")
        resolver.check_inputs()
        validate_retained_inputs(Path(binding["retained_inputs"]["file"]), resolver.campaign)
    resolver = ModalPairedPerformanceEvidence(root, binding)
    check()
    from .pilot_retry import _resolve
    _, amendment = _resolve(authorization["retry_amendment"])
    if amendment.get("followup_manifest") != reference(manifest_path) or amendment.get("run_id") != document["run_id"]:
        raise PermissionError("paired amendment differs from authority")
    repository = Path(binding["repository"])
    estimate = modal_paired_cost(Path(binding["modal_script"]["file"]), repository / "config/compute/modal-pilot-cost-v1.json")
    if estimate != document["estimate"]:
        raise PermissionError("paired admission estimate changed")
    plan = parse_json((repository / "config/pilots/solo-spend-envelope-v1.json").read_text())
    envelope = PilotSpendEnvelope(Path(authorization["spend_journal"]), plan, retry=amendment)
    retain_document(root / "operator-authorization.json", authorization)
    requests = tuple(ComputeExecutionRequest.from_document(j["request"]) for j in binding["jobs"])
    overhead = envelope.reserve(operation_key="pilot:retry-" + digest_value(amendment)[7:] + ":modal:base",
        provider="modal", purpose="overhead", request_digest=digest_file(manifest_path),
        maximum_usd_nanos=estimate["shared_overhead_allowance_usd_nanos"])
    admissions = [envelope.reserve(operation_key=f"pilot:{document['run_id']}:compute:{r.request_digest[7:]}",
        provider="modal", purpose="pilot", request_digest=r.request_digest,
        maximum_usd_nanos=estimate["per_execution_allowance_usd_nanos"]) for r in requests]
    authority = FrozenComputeRunManifest.load(root / "compute-manifest.json", expected_digest=document["compute_manifest_digest"])
    spend = SqliteComputeSpendAuthorizationService(root / "spend.sqlite3", authority)
    transport = ModalPairedPerformanceTransport(root, binding, manifest_path, spend)
    backend = SqliteComputeBackend(root / "executions.sqlite3", transport, resolver, authority=authority)
    try:
        for request, admission in zip(requests, admissions, strict=True):
            check()
            spend.issue(request, transport.profile_digest, "pilot-admission:" + digest_value(admission))
            execution = backend.submit(request, Path(binding["candidate"]["file"]).read_bytes())
            deadline = time.monotonic() + FUNCTION_SECONDS + estimate["gpu_startup_timeout_seconds"] + 60
            while execution.status is ComputeExecutionStatus.DISPATCHED:
                if time.monotonic() >= deadline:
                    raise RuntimeError("paired collection deadline exceeded; inspect existing call, never redispatch")
                execution = backend.collect(request, timeout_seconds=60)
            if execution.status is not ComputeExecutionStatus.COMPLETE:
                raise RuntimeError("paired evaluation stopped: " + str(execution.failure))
            _, evidence = backend.resolve(request)
            retain_document(root / "completed" / (request.request_digest[7:] + ".json"), evidence)
        receipts = backend.reconcile(document["run_id"])
        if len(receipts) != 3 or any(r.status is not ComputeExecutionStatus.COMPLETE for r in receipts):
            raise RuntimeError("paired closure inventory is incomplete")
        if any(spend.status(spend._authorization(r, transport.profile_digest).authorization_id) != "consumed" for r in requests):
            raise RuntimeError("paired closure lacks consumed authority")
        check(require_unexpired=False)
        source = validate_retained_inputs(Path(binding["retained_inputs"]["file"]), resolver.campaign)
        performance = summarize_pairs([backend.resolve(r)[1]["result"]["paired_performance"] for r in requests])
        quality_drivers_match = all(driver == resolver.campaign.measurement_profile().gpu_driver_version
            for driver in performance["drivers"])
        outcome = {"schema_version": "solo-paired-performance-outcome/v1", "status": "complete",
            "run_id": document["run_id"], "manifest_digest": digest_file(manifest_path), "scoreable": False,
            "original_campaign_status": "aborted", "previous_continuation_status": "stopped",
            "new_gpu_calls": 3, "agent_reruns": 0, "model_calls": 0,
            "eligible": source["correctness"].eligible and source["quality"]["eligible"] and performance["eligible"] and quality_drivers_match,
            "quality_verified_on_performance_drivers": quality_drivers_match,
            "quality_interpretation": "retained_original_environment_only_no_cross_driver_quality_claim",
            "retained": source, "performance": performance, "compute_receipts": receipts,
            "spend_admission": envelope.snapshot()}
        resolver.check_inputs()
        retain_document(root / "outcome.json", outcome)
        return outcome
    except Exception as error:
        from .adapters.modal_cleanup import ModalCallCanceller
        canceller = ModalCallCanceller(repository, repository / ".venv/bin/modal")
        cleanup = []
        for request in requests:
            path = root / "dispatch" / (request.request_digest[7:] + ".json")
            if path.exists():
                try:
                    current = backend.inspect(request)
                    if current is not None and current.status not in {ComputeExecutionStatus.COMPLETE, ComputeExecutionStatus.FAILED}:
                        call = current.external_call_id
                        resolver.resolve_dispatch(request, call)
                        cleanup.append({"call_id": call, "cancellation": canceller.cancel(call)})
                except Exception as cleanup_error:
                    cleanup.append({"error_type": type(cleanup_error).__name__, "terminal_confirmed": False})
        stop = {"schema_version": "solo-paired-performance-stop/v1", "status": "stopped", "scoreable": False,
            "observed_at": datetime.now(UTC).isoformat(), "error_type": type(error).__name__, "error": str(error)[-2500:],
            "manifest_digest": digest_file(manifest_path), "cleanup": cleanup, "spend_admission": envelope.snapshot()}
        retain_document(root / "stops" / (digest_value(stop)[7:] + ".json"), stop)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "run"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--configuration", type=Path)
    parser.add_argument("--retained-inputs", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--authorization-digest")
    args = parser.parse_args()
    if args.mode == "prepare":
        if args.configuration is None or args.retained_inputs is None or not args.run_id:
            parser.error("prepare requires configuration, retained inputs and run ID")
        configuration = LivePilotConfiguration.load(args.configuration, Path.cwd())
        prepare(configuration, args.retained_inputs, args.root, args.run_id)
        print(digest_file(args.root / "manifest.json"))
    else:
        if args.authorization is None or args.authorization_digest is None:
            parser.error("run requires independently pinned operator authority")
        result = run(args.root, args.authorization, args.authorization_digest)
        print("complete; eligible=" + str(result["eligible"]) + "; scoreable=False")


if __name__ == "__main__":
    main()
