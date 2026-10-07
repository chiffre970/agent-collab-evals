"""Raw-evidence scoring for the co-located exploratory evaluation stack."""

from pathlib import Path

from ..canonical import canonical_json_bytes, digest_bytes, parse_json
from .serving_correctness import load_correctness_workload, score_correctness_responses
from .serving_paired_performance import evaluate_pair
from .serving_pair_policy import validate_pair_environment
from .serving_quality import load_quality_workload, score_quality_outputs


def score_paired_execution(profile, request, receipt, raw, candidate, *, namespace=None):
    """Resolve both roles from the same physical call; never trust stored scores."""
    spec = profile.spec(request, namespace=namespace)
    phase = profile.slot(request).rsplit("-", 1)[0]
    reference = parse_json(Path(profile.pins["reference"]["file"]).read_text())
    if phase in {"public", "performance"}:
        plan, scoring, _ = profile.performance_inputs(request.scope)
        return evaluate_pair(profile.campaign, plan, scoring, spec, reference, candidate, receipt, raw)
    driver = validate_pair_environment(spec, receipt, profile.campaign.measurement_profile())
    if (receipt.get("schema_version") != "modal-paired-serving-repetition/v1"
        or receipt.get("pair_digest") != spec["pair_digest"] or receipt.get("order") != spec["order"]
        or receipt.get("ok") is not True or receipt.get("errors") != []):
        raise RuntimeError("paired served-response receipt differs")
    for role, document in (("reference", reference), ("candidate", candidate)):
        if receipt.get(role + "_document_digest") != digest_bytes(canonical_json_bytes(document) + b"\n"):
            raise RuntimeError("paired served-response candidate identity differs")
        child = receipt["roles"][role]
        expected = {k: spec["benchmark"][k] for k in ("campaign_manifest_digest", "quality_profile_digest",
            "quality_workload_digest", "repetition", "attempt")}
        expected.update(candidate_id=document["candidate_id"], model_id=profile.campaign.target_model_id,
            model_revision=profile.campaign.target_model_revision, vllm_version=document["server"]["engine_version"],
            served_model_name="target-model", ok=True, error=None,
            execution={k: spec["benchmark"][k] for k in ("max_concurrency", "request_timeout_seconds")})
        if any(child.get(k) != v for k, v in expected.items()):
            raise RuntimeError("paired served-response role differs")
        for key in ("canary_before", "canary_after"):
            if (child.get(key, {}).get("content") != "READY"
                or child[key].get("returned_model") != "target-model"):
                raise RuntimeError("paired served-response canary failed")
    if phase == "quality":
        workload = load_quality_workload(Path(profile.pins["quality_workload"]["file"]), profile.campaign.quality_profile())
    else:
        workload = load_correctness_workload(Path(profile.pins["correctness_requests"]["file"]))
    names = {f"{role}-{case.case_id}.json" for role in ("reference", "candidate") for case in workload.cases}
    if set(raw) != names:
        raise RuntimeError("paired served-response raw case set differs")
    scores, generation = {}, {}
    pending_calibration = profile.quality_policy.calibration_status == "pending_current_control"
    for role in ("reference", "candidate"):
        responses = {case.case_id: raw[f"{role}-{case.case_id}.json"] for case in workload.cases}
        if phase == "quality":
            outputs = {}
            truncated = 0
            for case_id, content in responses.items():
                # Preserve the frozen quality scorer: malformed answers fail
                # their case. No new favorable interpretation or threshold.
                try:
                    response = parse_json(content.decode())
                    if (not isinstance(response, dict) or not isinstance(response.get("choices"), list)
                        or not response["choices"] or not isinstance(response["choices"][0], dict)):
                        raise ValueError("quality response choices differ")
                    if response["choices"][0].get("finish_reason") == "length":
                        truncated += 1
                    answer = response["choices"][0]["message"]["content"]
                    if response.get("model") != "target-model" or not isinstance(answer, str):
                        raise ValueError("quality response schema differs")
                    if pending_calibration and (len(response["choices"]) != 1
                        or response["choices"][0].get("finish_reason") != "stop"
                        or response["choices"][0]["message"].get("role") != "assistant"):
                        raise ValueError("V3 quality generation is unfinished or malformed")
                except (ValueError, KeyError, IndexError, TypeError):
                    answer = ""
                outputs[case_id] = answer
            scores[role] = score_quality_outputs(profile.campaign.quality_profile(), workload, outputs,
                repetition=spec["benchmark"]["repetition"], role=role)
            if pending_calibration:
                generation[role] = {"truncated_responses": truncated,
                    "missing_final_answers": sum(c["extracted"] is None for c in scores[role]["cases"])}
        else:
            scores[role] = score_correctness_responses(workload, responses, served_model_name="target-model").to_document()
    result = {"schema_version": "exploratory-paired-responses-result/v1", "phase": phase,
        "pair_digest": spec["pair_digest"], "scoreable": False, "driver_version": driver,
        "repetition": spec["benchmark"]["repetition"], "scores": scores}
    if generation:
        result["generation"] = generation
    return result
