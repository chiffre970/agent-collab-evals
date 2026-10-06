"""Replay retained calibration controls without dispatching or revealing cases."""

from pathlib import Path

from .adapters.local_measurements import LocalMeasurementBundleStore
from .adapters.modal_vllm_quality_compute import _validate_durable_evidence
from .campaigns.serving_correctness import load_correctness_workload, score_correctness_responses
from .campaigns.serving_quality import evaluate_quality_series, load_quality_workload, score_quality_outputs
from .campaigns.serving_workload import HiddenWorkloadExpectations, load_hidden_workload
from .canonical import canonical_json_bytes, digest_bytes, digest_file, parse_json


def load_control_hidden(campaign, path, digest):
    """Load control inputs against an externally pinned bundle digest."""
    expectations = HiddenWorkloadExpectations(
        campaign_manifest_digest=campaign.manifest_digest,
        hidden_contract_digest=campaign.transitive_digests["hidden_contract"],
        quality_profile_digest=campaign.transitive_digests["quality_profile"],
        quality_policy_digest=campaign.transitive_digests["quality_policy"],
        quality_workload_digest=campaign.quality_policy().quality_workload_digest,
        public_correctness_digest=campaign.transitive_digests["public_correctness"],
        public_performance_digest=campaign.transitive_digests["public_profile"],
        required_gates=tuple(campaign.hidden_contract()["required_gates"]))
    return load_hidden_workload(Path(path), expectations, campaign.benchmark_plan(),
        registered_manifest_digest=digest)


def _load_receipt(path, expected_digest):
    path = Path(path).resolve(strict=True)
    if digest_file(path) != expected_digest:
        raise RuntimeError("reference control receipt digest differs")
    document = parse_json(path.read_text())
    store = LocalMeasurementBundleStore(path.parents[2])
    bundle = store.load(document["measurement_id"], document["repetition"], attempt=document["attempt"])
    if canonical_json_bytes(bundle.receipt) != canonical_json_bytes(document):
        raise RuntimeError("reference control receipt identity differs")
    normalized = bundle.receipt["normalized"]
    if normalized.get("valid") is not True or normalized.get("validation_errors") != []:
        raise RuntimeError("reference control execution is invalid")
    _validate_durable_evidence(normalized, "agent-collab-evals-evaluator-evidence-v2")
    remote, durable = normalized["remote_receipt"], normalized["durable_evidence"]
    if (remote.get("ok") is not True or remote.get("error") is not None
        or durable.get("raw_digests") != bundle.receipt["raw_digests"]
        or durable.get("remote_receipt_digest") != digest_bytes(canonical_json_bytes(remote) + b"\n")):
        raise RuntimeError("reference control raw evidence seal differs")
    if any(remote.get(k) != normalized.get(k) for k in (
        "campaign_manifest_digest", "candidate_id", "repetition", "attempt")):
        raise RuntimeError("reference control remote identity differs")
    return normalized, bundle.raw_documents


def _environment(normalized, campaign):
    remote = normalized["remote_receipt"]
    measurement = campaign.measurement_profile()
    expected_gpu = {"name": "NVIDIA L4", "memory_mib": str(measurement.gpu_memory_mib),
        "driver_version": measurement.gpu_driver_version, "power_limit_watts": measurement.gpu_power_limit_watts}
    if any(remote.get(location, {}).get(k) != v
        for location in ("gpu_before", "gpu_after") for k, v in expected_gpu.items()):
        raise RuntimeError("reference control GPU environment differs")
    expected = {"model_id": campaign.target_model_id, "model_revision": campaign.target_model_revision,
        "served_model_name": "target-model", "vllm_version": campaign.validate_reference_candidate().engine_version}
    if any(remote.get(k) != v for k, v in expected.items()):
        raise RuntimeError("reference control model identity differs")
    software = {"base_image_ref": "nvidia/cuda:12.9.0-devel-ubuntu22.04",
        "base_image_digest": measurement.base_image_digest, "package_set_digest": measurement.resolved_package_digest}
    if any(remote.get("environment", {}).get(k) != v for k, v in software.items()):
        raise RuntimeError("reference control software environment differs")
    for key in ("canary_before", "canary_after"):
        if (remote.get(key, {}).get("content") != "READY"
            or remote[key].get("returned_model") != "target-model"):
            raise RuntimeError("reference control server canary failed")
    return expected_gpu["driver_version"]


def verify_reference_controls(*, campaign, policy, quality_workload, quality_store,
    correctness_receipt, correctness_receipt_digest, source_hidden, target_hidden):
    """Verify historical controls and state whether their scope matches the target.

    Receipt anchors come from the frozen quality policy and the independently
    supplied correctness digest. Hidden bundles must already be loaded through
    their digest-pinned loader. A passing control is not authority to spend, or
    proof about another workload, build, driver, or physical GPU pairing.
    """
    profile = campaign.quality_profile()
    policy.validate_against(profile)
    workload = load_quality_workload(Path(quality_workload), profile)
    if workload.digest != policy.quality_workload_digest:
        raise RuntimeError("reference control quality workload differs")
    runs, receipts, historical_campaigns, drivers = {}, {}, set(), set()
    for role, measurement_id, anchors in (
        ("reference", policy.reference_measurement_id, policy.reference_receipt_digests),
        ("clean_control", policy.clean_control_measurement_id, policy.clean_control_receipt_digests)):
        runs[role], receipts[role] = [], []
        for repetition, anchor in enumerate(anchors, 1):
            path = Path(quality_store) / measurement_id / f"repetition-{repetition:04d}-attempt-01/receipt.json"
            normalized, raw = _load_receipt(path, anchor)
            expected = {"role": role, "repetition": repetition, "attempt": 1,
                "quality_profile_digest": profile.digest, "quality_workload_digest": workload.digest}
            if (any(normalized.get(k) != v for k, v in expected.items())
                or normalized["remote_receipt"].get("quality_profile_digest") != profile.digest
                or normalized["remote_receipt"].get("quality_workload_digest") != workload.digest
                or set(raw) != {f"{case.case_id}.json" for case in workload.cases}):
                raise RuntimeError("reference control quality identity differs")
            outputs = {}
            for case in workload.cases:
                response = parse_json(raw[f"{case.case_id}.json"].decode())
                content = response["choices"][0]["message"]["content"]
                if response.get("model") != "target-model" or not isinstance(content, str):
                    raise RuntimeError("reference control quality response differs")
                outputs[case.case_id] = content
            scored = score_quality_outputs(profile, workload, outputs, repetition=repetition, role=role)
            if scored != normalized.get("quality_score"):
                raise RuntimeError("reference control quality score differs from raw evidence")
            runs[role].append(scored)
            receipts[role].append(anchor)
            drivers.add(_environment(normalized, campaign))
            historical_campaigns.add(normalized["campaign_manifest_digest"])
    if len(historical_campaigns) != 1:
        raise RuntimeError("reference control calibration campaigns differ")
    quality = evaluate_quality_series(policy, runs["reference"], runs["clean_control"])
    normalized, raw = _load_receipt(correctness_receipt, correctness_receipt_digest)
    expected = {"campaign_manifest_digest": campaign.manifest_digest,
        "candidate_id": campaign.validate_reference_candidate().candidate_id,
        "candidate_manifest_digest": campaign.validate_reference_candidate().manifest_digest,
        "hidden_workload_manifest_digest": source_hidden.manifest_digest,
        "correctness_workload_digest": source_hidden.resource_digests["correctness_requests"],
        "role": "reference", "repetition": 1, "attempt": 1}
    if any(normalized.get(k) != v for k, v in expected.items()):
        raise RuntimeError("stock correctness control identity differs")
    if normalized["remote_receipt"].get("quality_workload_digest") != source_hidden.resource_digests["correctness_requests"]:
        raise RuntimeError("stock correctness control remote workload differs")
    workload = load_correctness_workload(source_hidden.resource_paths["correctness_requests"])
    if workload.digest != source_hidden.resource_digests["correctness_requests"]:
        raise RuntimeError("stock correctness control workload changed")
    if set(raw) != {f"{case.case_id}.json" for case in workload.cases}:
        raise RuntimeError("stock correctness control raw case set differs")
    correctness = score_correctness_responses(workload,
        {case.case_id: raw[f"{case.case_id}.json"] for case in workload.cases},
        served_model_name="target-model")
    if correctness.to_document() != normalized.get("correctness_result"):
        raise RuntimeError("stock correctness control score differs from raw evidence")
    drivers.add(_environment(normalized, campaign))
    return {"schema_version": "model-serving-reference-controls-review/v1",
        "execution_authorized": False, "scoreable": False, "new_model_calls": 0, "new_gpu_calls": 0,
        "campaign_manifest_digest": campaign.manifest_digest,
        "verifier_source_digest": digest_file(Path(__file__)),
        "target_hidden_manifest_digest": target_hidden.manifest_digest,
        "quality": {"calibration_verified": True, "eligible": quality["eligible"],
            "quality_policy_digest": policy.digest, "quality_workload_digest": policy.quality_workload_digest,
            "historical_campaign_manifest_digest": next(iter(historical_campaigns)),
            "matches_current_campaign": historical_campaigns == {campaign.manifest_digest},
            "matches_target_workload": policy.quality_workload_digest == target_hidden.resource_digests["quality_workload"],
            "aggregate": quality["aggregate"], "families": quality["families"],
            "failures": quality["failures"], "receipt_digests": receipts},
        "correctness": {"stock_control_verified": True, "eligible": correctness.eligible,
            "passed_cases": correctness.passed_cases, "total_cases": correctness.total_cases,
            "source_hidden_manifest_digest": source_hidden.manifest_digest,
            "source_workload_digest": workload.digest,
            "matches_target_workload": workload.digest == target_hidden.resource_digests["correctness_requests"],
            "receipt_digest": correctness_receipt_digest},
        "observed_drivers": sorted(drivers), "physical_gpu_pairing_verified": False,
        "interpretation": "historical_calibration_replay_not_current_same_gpu_control_qualification"}
