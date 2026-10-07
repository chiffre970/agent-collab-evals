"""CPU-only context verification using the pinned model's actual chat template."""

import importlib.metadata
from pathlib import Path
from urllib.request import urlopen

from .canonical import digest_file, digest_value, parse_json
from .campaigns.serving_correctness import load_correctness_workload
from .campaigns.serving_quality import build_quality_requests, load_quality_workload
from .pilot_evidence import retain_bytes, retain_document
from .solo_live_configuration import LivePilotConfiguration


VERSIONS = {"tokenizers": "0.22.2", "Jinja2": "3.1.6"}


def calibration_context_report(configuration_path, repository, tokenizer_root, *, fetch=False):
    """Rederive lengths; fetching reads public model metadata, never model weights."""
    from jinja2.sandbox import ImmutableSandboxedEnvironment
    from tokenizers import Tokenizer

    versions = {name: importlib.metadata.version(name) for name in VERSIONS}
    if versions != VERSIONS:
        raise ValueError("calibration tokenizer dependencies differ from their pins")
    configuration = LivePilotConfiguration.load(Path(configuration_path), Path(repository))
    campaign, hidden = configuration.campaign, configuration.hidden_bundle()
    if (campaign.raw["campaign_id"] != "model-serving-calibration-v3"
        or campaign.quality_policy().calibration_status != "pending_current_control"):
        raise ValueError("context check requires the pending V3 calibration")
    root = Path(tokenizer_root)
    base = f"https://huggingface.co/{campaign.target_model_id}/resolve/{campaign.target_model_revision}/"
    pins = {}
    for filename in ("tokenizer.json", "tokenizer_config.json"):
        path = root / filename
        if not path.exists():
            if not fetch:
                raise ValueError("pinned tokenizer snapshot is missing; explicit fetch is required")
            with urlopen(base + filename, timeout=60) as response:
                content = response.read(32 * 1024 * 1024 + 1)
            if len(content) > 32 * 1024 * 1024:
                raise ValueError("tokenizer snapshot exceeds its size limit")
            retain_bytes(path, content)
        if path.is_symlink():
            raise ValueError("tokenizer snapshot must not be a symlink")
        pins[filename] = {"url": base + filename, "digest": digest_file(path)}
    tokenizer = Tokenizer.from_file(str(root / "tokenizer.json"))
    tokenizer_config = parse_json((root / "tokenizer_config.json").read_text())
    template_source = tokenizer_config.get("chat_template")
    if not isinstance(template_source, str) or not template_source:
        raise ValueError("pinned tokenizer has no single chat template")
    environment = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)
    def reject(message):
        raise ValueError(message)
    environment.globals["raise_exception"] = reject
    template = environment.from_string(template_source)
    special = {k: v for k, v in tokenizer_config.items() if k.endswith("_token") and isinstance(v, str)}
    profile = campaign.quality_profile()
    workload = load_quality_workload(hidden.resource_paths["quality_workload"], profile)
    correctness = load_correctness_workload(hidden.resource_paths["correctness_requests"])
    groups = {
        "correctness": tuple({"case_id": c.case_id, "body": c.request("target-model")} for c in correctness.cases),
        "quality": build_quality_requests(profile, workload, served_model_name="target-model"),
    }
    context = parse_json(campaign.reference_candidate_path.read_text())["server"]["engine_args"]["max_model_len"]
    measurements = {}
    for phase, requests in groups.items():
        rows = []
        for request in requests:
            body = request["body"]
            rendered = template.render(messages=body["messages"], tools=None,
                add_generation_prompt=True, **special, **body["chat_template_kwargs"])
            prompt_tokens = len(tokenizer.encode(rendered, add_special_tokens=False).ids)
            maximum = prompt_tokens + body["max_tokens"]
            if maximum > context:
                raise ValueError("calibration request exceeds the pinned stock context window")
            rows.append({"case_id": request["case_id"], "prompt_tokens": prompt_tokens,
                "max_completion_tokens": body["max_tokens"], "maximum_total_tokens": maximum})
        measurements[phase] = {"case_count": len(rows), "max_prompt_tokens": max(r["prompt_tokens"] for r in rows),
            "max_total_tokens": max(r["maximum_total_tokens"] for r in rows),
            "lengths_digest": digest_value(rows)}
    return {"schema_version": "serving-calibration-context/v1", "execution_authorized": False,
        "new_model_calls": 0, "new_gpu_calls": 0, "configuration_digest": digest_file(Path(configuration_path)),
        "hidden_manifest_digest": hidden.manifest_digest, "model_id": campaign.target_model_id,
        "model_revision": campaign.target_model_revision, "dependency_versions": versions,
        "verifier_source_digest": digest_file(Path(__file__)), "tokenizer_snapshots": pins,
        "reference_context_tokens": context, "all_case_contexts_fit": True, "phases": measurements,
        "function_timeout_seconds": 3000, "quality_request_timeout_seconds": profile.request_timeout_seconds,
        "quality_request_timeout_envelope_seconds": 2 * ((len(workload.cases) + profile.max_concurrency - 1)
            // profile.max_concurrency) * profile.request_timeout_seconds,
        "completion_guaranteed_by_timeouts": False,
        "timing_interpretation": "function_timeout_bounds_spend_not_success_at_every_request_deadline"}


def check_calibration_context(root, repository, *, fetch=False):
    root = Path(root).resolve(strict=True)
    if not root.is_relative_to(Path(repository).resolve() / ".private"):
        raise ValueError("calibration context evidence must remain evaluator-private")
    report = calibration_context_report(root / "configuration.json", repository, root / "tokenizer", fetch=fetch)
    retain_document(root / "context-check.json", report)
    return report
