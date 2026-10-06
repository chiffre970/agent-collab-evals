"""Exploratory, same-GPU goodput comparisons; never registered-study scoring."""

from dataclasses import replace
from pathlib import Path
from statistics import median

from ..canonical import canonical_json_bytes, digest_bytes, digest_value
from .serving_benchmark import build_vllm_benchmark_invocations
from .serving_measurement import parse_vllm_benchmark_result, replay_vllm_goodput
from .serving_scoring import score_repetition
from .serving_pair_policy import ALLOWED_DRIVERS as _DRIVERS, validate_pair_environment


ALLOWED_DRIVERS = list(_DRIVERS)
FUNCTION_SECONDS = 3000
MODEL_SOURCE = "/cache/huggingface/hub/models--Qwen--Qwen3-4B/snapshots/1cfa9a7208912126459214e8b04321603b3df60c"
RESULT_ROOT = Path("/tmp/reference-benchmark")


def invocations(campaign, plan, scoring):
    return build_vllm_benchmark_invocations(plan, base_url="http://127.0.0.1:8000",
        model_source=MODEL_SOURCE, served_model_name="target-model", result_directory=RESULT_ROOT,
        warmup_requests=campaign.measurement_profile().point_warmups,
        goodput_slos_ms_by_bucket=scoring.goodput_slos_ms_by_bucket)


def pair_spec(campaign, plan, scoring, performance_digest, repetition, evidence_root):
    measurement = campaign.measurement_profile()
    benchmark = {"campaign_manifest_digest": campaign.manifest_digest,
        "measurement_profile_digest": measurement.digest, "scoring_profile_digest": scoring.digest,
        "performance_profile_digest": performance_digest, "repetition": repetition, "attempt": 1,
        "evidence_root": evidence_root, "point_timeout_seconds": measurement.point_timeout_seconds,
        "invocations": [{"bucket_id": i.bucket_id, "request_rate": i.request_rate,
            "result_filename": i.result_file.name, "argv": list(i.argv)} for i in invocations(campaign, plan, scoring)]}
    policy = {"benchmark": benchmark, "allowed_drivers": ALLOWED_DRIVERS,
        "expected_gpu": {"name": "NVIDIA L4", "memory_mib": str(measurement.gpu_memory_mib),
            "power_limit_watts": measurement.gpu_power_limit_watts},
        "order": ["reference", "candidate"] if repetition % 2 else ["candidate", "reference"]}
    return {**policy, "pair_digest": digest_value(policy)}


def evaluate_pair(campaign, plan, scoring, spec, reference, candidate, receipt, raw):
    """Recompute each score from sealed raw outputs and a matched reference."""
    driver = validate_pair_environment(spec, receipt, campaign.measurement_profile())
    if receipt.get("schema_version") != "modal-paired-serving-repetition/v1" or receipt.get("pair_digest") != spec["pair_digest"]:
        raise RuntimeError("paired receipt identity differs")
    if receipt.get("order") != spec["order"]:
        raise RuntimeError("paired role order differs")
    for role, document in (("reference", reference), ("candidate", candidate)):
        campaign.validate_candidate_document(document)
        if receipt.get(role + "_document_digest") != digest_bytes(canonical_json_bytes(document) + b"\n"):
            raise RuntimeError("paired candidate identity differs")
    roles = receipt.get("roles", {})
    if receipt.get("ok") is not True or receipt.get("errors") != [] or set(roles) != {"reference", "candidate"}:
        raise RuntimeError("paired execution failed: " + str(receipt.get("errors")))
    environment = receipt.get("environment", {})
    replayed, points = {}, {}
    benchmark_invocations = invocations(campaign, plan, scoring)
    expected_names = {f"{role}-{i.result_file.name}" for role in roles for i in benchmark_invocations}
    if set(raw) != expected_names:
        raise RuntimeError("paired raw benchmark point set differs")
    for role, document in (("reference", reference), ("candidate", candidate)):
        r = roles[role]
        expected = {**{k: spec["benchmark"][k] for k in ("campaign_manifest_digest", "measurement_profile_digest",
            "scoring_profile_digest", "performance_profile_digest", "repetition", "attempt")},
            "candidate_id": document["candidate_id"], "model_id": campaign.target_model_id,
            "model_revision": campaign.target_model_revision, "served_model_name": "target-model",
            "vllm_version": document["server"]["engine_version"], "ok": True, "error": None}
        if any(r.get(k) != v for k, v in expected.items()) or r.get("environment") != environment:
            raise RuntimeError("paired role receipt differs")
        for k in ("canary_before", "canary_after"):
            if r.get(k, {}).get("content") != "READY" or r[k].get("returned_model") != "target-model":
                raise RuntimeError("paired server canary failed")
        points[role], replayed[role] = [], []
        for i in benchmark_invocations:
            content = raw[f"{role}-{i.result_file.name}"]
            point = parse_vllm_benchmark_result(content, invocation=i, model_source=MODEL_SOURCE,
                metric_percentiles=plan.metric_percentiles)
            rule = scoring.bucket_rules[i.bucket_id]
            replay = replay_vllm_goodput(content, invocation=i, model_source=MODEL_SOURCE,
                goodput_slos_ms={"ttft": rule.ttft_slo_ms, "tpot": rule.tpot_slo_ms},
                legacy_classification_guard_us=scoring.legacy_classification_guard_us,
                aggregate_tolerance_us=scoring.legacy_aggregate_tolerance_us)
            points[role].append({**point.to_document(), "goodput": replay.to_document()})
            replayed[role].append(replay)
    refs = {(r.bucket_id, r.request_rate): r for r in replayed["reference"]}
    rules = {key: replace(rule, reference_goodput_micro_rps=refs[(key, rule.selected_request_rate)].goodput_micro_rps)
        for key, rule in scoring.bucket_rules.items()}
    if any(rule.reference_goodput_micro_rps <= 0 for rule in rules.values()):
        raise RuntimeError("paired reference has no measurable goodput")
    paired_scoring = replace(scoring, bucket_rules=rules)
    scores = {role: score_repetition(paired_scoring, plan, values,
        repetition=spec["benchmark"]["repetition"], role="candidate").to_document()
        for role, values in replayed.items()}
    return {"schema_version": "exploratory-paired-performance-result/v1",
        "eligible": all(s["eligible"] for s in scores.values()), "scoreable": False,
        "pair_digest": spec["pair_digest"], "driver_version": driver,
        "repetition": spec["benchmark"]["repetition"], "order": spec["order"],
        "scores": scores, "points": points, "timing": receipt["timing"]}


def summarize_pairs(values):
    if len(values) != 3 or sorted(v["repetition"] for v in values) != [1, 2, 3]:
        raise ValueError("three distinct matched performance repetitions are required")
    if any(v.get("scoreable") is not False or type(v.get("eligible")) is not bool for v in values):
        raise ValueError("paired series result fields differ")
    ratios = [v["scores"]["candidate"]["scalar_ppm"] for v in values]
    return {"eligible": all(v["eligible"] for v in values), "scoreable": False,
        "median_candidate_reference_ratio_ppm": median(ratios),
        "minimum_observed_candidate_reference_ratio_ppm": min(ratios),
        "ratio_ppm_by_repetition": ratios, "drivers": [v["driver_version"] for v in values],
        "interpretation": "exploratory_same_gpu_pairs_not_a_registered_score_or_confidence_bound"}
