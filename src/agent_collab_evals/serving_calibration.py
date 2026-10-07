"""Prepare a new, private calibration pack without grants or external calls."""

from copy import deepcopy
from pathlib import Path
import os
import re

from .canonical import canonical_json_bytes, digest_bytes, digest_file, parse_json
from .campaigns.model_serving import ModelServingCampaign
from .campaigns.serving_quality import QualityProfile, build_quality_requests, load_quality_workload
from .campaigns.serving_workload import (
    HIDDEN_WORKLOAD_SCHEMA, _quality_request_bytes,
)
from .peer_live_configuration import prepare_paired_stock_control
from .pilot_evidence import retain_bytes, retain_document
from .solo_live_configuration import LivePilotConfiguration


DIAGNOSTIC_SLOTS = ("correctness-1", "quality-1")


def _member(repository, name):
    if not isinstance(name, str) or Path(name).is_absolute():
        raise ValueError("calibration input must be repository-relative")
    path = (repository / name).resolve(strict=True)
    path.relative_to(repository)
    return path


def _private(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    retain_bytes(path, content)
    os.chmod(path, 0o600)


def _semantic_correctness(content):
    """Keep prompts and case selection; change only the declared arithmetic check."""
    cases = [parse_json(line) for line in content.decode().splitlines()]
    for case in cases:
        if case["check"]["kind"] == "exact":
            continue
        match = re.fullmatch(r"What is ([0-9]+) \+ ([0-9]+)\? Reply with only the integer\.",
            case["messages"][0]["content"])
        if match is None or case["check"] != {"kind": "regex", "value": f"^{sum(map(int, match.groups()))}$"}:
            raise ValueError("calibration arithmetic source differs")
        operands = list(map(int, match.groups()))
        case["check"] = {"kind": "integer_sum", "value": str(sum(operands)), "operands": operands}
    if (len(cases) != 8 or sum(c["check"]["kind"] == "integer_sum" for c in cases) != 4
        or sum(c["check"]["kind"] == "exact" for c in cases) != 4):
        raise ValueError("calibration correctness population differs")
    return b"".join(canonical_json_bytes(case) + b"\n" for case in cases)


def prepare_serving_calibration(recipe_path, repository, root, run_id):
    """Snapshot shared assets, derive V3 inputs, and freeze exactly two unused jobs.

    The old pack and all source resources remain unchanged. Historical quality
    receipts are not copied into the new policy as calibration evidence.
    """
    repository, root = Path(repository).resolve(strict=True), Path(root).resolve()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", run_id):
        raise ValueError("calibration run ID is invalid")
    if not root.is_relative_to(repository / ".private") or root == repository / ".private":
        raise ValueError("calibration state must be a dedicated evaluator-private repository directory")
    recipe = parse_json(Path(recipe_path).read_text())
    fields = {"schema_version", "revision_id", "execution_authorized", "scoreable", "source_configuration",
        "source_quality_profile_digest", "source_quality_policy_digest", "source_outcome_digest", "quality_profile",
        "arithmetic_check", "arithmetic_format", "reference_context_tokens", "diagnostic_slots"}
    if (not isinstance(recipe, dict) or set(recipe) != fields
        or recipe.get("execution_authorized") is not False or recipe.get("scoreable") is not False
        or type(recipe.get("reference_context_tokens")) is not int
        or any(recipe.get(k) != v for k, v in {
            "schema_version": "serving-calibration-revision/v1", "revision_id": "model-serving-calibration-v3",
            "execution_authorized": False, "scoreable": False, "arithmetic_check": "integer_sum",
            "arithmetic_format": "diagnostic_only", "reference_context_tokens": 16384,
            "diagnostic_slots": list(DIAGNOSTIC_SLOTS)}.items())
        or any(not isinstance(recipe[k], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", recipe[k])
            for k in ("source_quality_profile_digest", "source_quality_policy_digest", "source_outcome_digest"))):
        raise ValueError("calibration revision differs")
    source_config = _member(repository, recipe["source_configuration"])
    source = LivePilotConfiguration.load(source_config, repository)
    source_hidden = source.hidden_bundle()
    old_profile, old_policy = source.campaign.quality_profile(), source.campaign.quality_policy()
    if old_profile.digest != recipe["source_quality_profile_digest"] or old_policy.digest != recipe["source_quality_policy_digest"]:
        raise ValueError("calibration predecessor pins differ")
    new_profile_path = _member(repository, recipe["quality_profile"])
    profile = QualityProfile.load(new_profile_path)
    if profile.decoding["thinking"].max_tokens != 8192 or profile.request_timeout_seconds != 600:
        raise ValueError("V3 quality profile differs")
    source_paths = tuple(p for p in source.campaign.root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    if any(p.is_symlink() for p in source_paths):
        raise ValueError("calibration assets must not be symlinks")
    source_pins = {str(p.relative_to(source.campaign.root)): digest_file(p) for p in source_paths}
    derivation = {"schema_version": "serving-calibration-derivation/v1", "recipe": recipe,
        "recipe_digest": digest_file(Path(recipe_path)), "source_configuration_digest": digest_file(source_config),
        "source_hidden_manifest_digest": source_hidden.manifest_digest, "source_assets": source_pins,
        "source_hidden_resources": dict(source_hidden.resource_digests),
        "new_quality_profile_digest": profile.digest, "run_id": run_id, "execution_authorized": False}
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    retain_document(root / "derivation.json", derivation)
    pack = root / "pack"
    replaced = {"campaign.toml", "reference/candidate.json", "evaluator/quality_calibration.toml",
        "evaluator/quality_policy.toml", "evaluator/hidden_contract.json"}
    for path in source_paths:
        name = str(path.relative_to(source.campaign.root))
        if name not in replaced:
            retain_bytes(pack / name, path.read_bytes())
    retain_bytes(pack / "evaluator/quality_calibration.toml", new_profile_path.read_bytes())
    profile = QualityProfile.load(pack / "evaluator/quality_calibration.toml")
    workload = load_quality_workload(source_hidden.resource_paths["quality_workload"], old_profile)
    document = deepcopy(workload.document)
    document["profile_digest"] = profile.digest
    hidden_root = root / "hidden"
    _private(hidden_root / "quality-workload.json", canonical_json_bytes(document) + b"\n")
    new_workload = load_quality_workload(hidden_root / "quality-workload.json", profile)
    policy_text = old_policy.path.read_text().split("[calibration]", 1)[0]
    policy_text = policy_text.replace('phase = "calibration_frozen_v2"', 'phase = "calibration_pending_v3"')
    policy_text = policy_text.replace(old_profile.digest, profile.digest).replace(workload.digest, new_workload.digest)
    policy_text += ('[calibration]\nstatus = "pending_current_control"\n'
        f'predecessor_policy_digest = "{old_policy.digest}"\n'
        f'trigger_outcome_digest = "{recipe["source_outcome_digest"]}"\n')
    retain_bytes(pack / "evaluator/quality_policy.toml", policy_text.encode())
    contract = deepcopy(source.campaign.hidden_contract())
    contract["quality_contract"].update(quality_profile_digest=profile.digest,
        quality_policy_digest=digest_file(pack / "evaluator/quality_policy.toml"),
        quality_workload_digest=new_workload.digest, task_mix_status="calibration_v3_same_cases_evaluator_private",
        threshold_status="frozen_rule_calibration_pending")
    retain_document(pack / "evaluator/hidden_contract.json", contract)
    reference = parse_json(source.campaign.reference_candidate_path.read_text())
    reference["candidate_id"] = "stock-vllm-0.21.0-calibration-v3"
    reference["server"]["engine_args"]["max_model_len"] = recipe["reference_context_tokens"]
    retain_bytes(pack / "reference/candidate.json", canonical_json_bytes(reference) + b"\n")
    campaign_text = (source.campaign.root / "campaign.toml").read_text()
    campaign_text = campaign_text.replace('campaign_id = "model-serving-v0"', 'campaign_id = "model-serving-calibration-v3"')
    retain_bytes(pack / "campaign.toml", campaign_text.encode())
    campaign = ModelServingCampaign.load(pack / "campaign.toml")
    resources = {
        "correctness_requests": ("correctness.jsonl", _semantic_correctness(source_hidden.resource_paths["correctness_requests"].read_bytes())),
        "performance_profile": ("performance.toml", source_hidden.resource_paths["performance_profile"].read_bytes()),
        "quality_requests": ("quality-requests.json", _quality_request_bytes(build_quality_requests(profile, new_workload, served_model_name="target-model"))),
        "quality_workload": ("quality-workload.json", (hidden_root / "quality-workload.json").read_bytes()),
    }
    for filename, content in resources.values():
        _private(hidden_root / filename, content)
    digests = {k: digest_bytes(content) for k, (_, content) in resources.items()}
    hidden_document = {"schema_version": HIDDEN_WORKLOAD_SCHEMA, "scope": "hidden", "agent_visible": False,
        "campaign_manifest_digest": campaign.manifest_digest, "hidden_contract_digest": campaign.transitive_digests["hidden_contract"],
        "selection_seed_commitment": source_hidden.selection_seed_commitment, "required_gates": contract["required_gates"],
        "resources": {k: {"path": name, "digest": digests[k]} for k, (name, _) in resources.items()},
        "digests": {**digests, "quality_profile": profile.digest, "quality_policy": campaign.quality_policy().digest}}
    _private(hidden_root / "manifest.json", canonical_json_bytes(hidden_document) + b"\n")
    # Reuse the actual configuration loader and compute factories, not a fake
    # object with a different evidence or request shape.
    public = parse_json(_member(repository, source.document["public_compute_profile"]).read_text())
    public.update(profile_id="modal-calibration-v3-public", campaign_manifest=str((pack / "campaign.toml").relative_to(repository)),
        campaign_manifest_digest=campaign.manifest_digest,
        performance_profile=str((pack / campaign.raw["workload"]["public_profile"]).relative_to(repository)))
    retain_document(root / "public-compute.json", public)
    configuration = {**source.document, "campaign": str((pack / "campaign.toml").relative_to(repository)),
        "public_compute_profile": str((root / "public-compute.json").relative_to(repository)),
        "hidden_manifest": str((hidden_root / "manifest.json").relative_to(repository)),
        "hidden_manifest_digest": digest_file(hidden_root / "manifest.json"),
        "model_limit_usd_nanos": None, "modal_limit_usd_nanos": None,
        "phase_seconds": {k: 3000 for k in source.document["phase_seconds"]}}
    retain_document(root / "configuration.json", configuration)
    target = LivePilotConfiguration.load(root / "configuration.json", repository)
    hidden = target.hidden_bundle()
    result = prepare_paired_stock_control(root / "control", target, run_id,
        hidden, campaign.quality_policy(), diagnostic=True)
    result = {"schema_version": "serving-calibration-preflight/v1", "run_id": run_id,
        "execution_authorized": False, "scoreable": False, "new_model_calls": 0, "new_gpu_calls": 0,
        "revision_digest": digest_file(root / "derivation.json"), "configuration_digest": digest_file(root / "configuration.json"),
        "preparation_digest": digest_file(root / "control/stock-control.json"),
        "source_inputs_unchanged": (all(digest_file(p) == source_pins[str(p.relative_to(source.campaign.root))] for p in source_paths)
            and digest_file(source_config) == derivation["source_configuration_digest"]
            and digest_file(source_hidden.manifest_path) == source_hidden.manifest_digest
            and all(digest_file(path) == source_hidden.resource_digests[name]
                for name, path in source_hidden.resource_paths.items())),
        "same_quality_cases_and_seeds": new_workload.document["cases"] == workload.document["cases"],
        "calibration_status": campaign.quality_policy().calibration_status,
        "thinking_max_tokens": 8192, "reference_context_tokens": 16384, "request_timeout_seconds": 600,
        "preparation": result,
        "remaining_gates": ["clean committed deployment", "exact rendered prompt token counts",
            "current pricing and provider gross-usage check", "existing journal reconciliation and fresh two-job approval"]}
    if not result["source_inputs_unchanged"] or not result["same_quality_cases_and_seeds"]:
        raise RuntimeError("calibration derivation changed predecessor inputs or cases")
    retain_document(root / "preflight.json", result)
    return result
