"""Control replay, scope checks, and coherent score-tampering regressions."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from agent_collab_evals.adapters.local_measurements import LocalMeasurementBundleStore
from agent_collab_evals.campaigns.serving_correctness import load_correctness_workload, score_correctness_responses
from agent_collab_evals.campaigns.serving_quality import load_quality_workload, score_quality_outputs
from agent_collab_evals.canonical import canonical_json_bytes, digest_bytes, digest_file, parse_json
from agent_collab_evals.reference_controls import verify_reference_controls
from tests.quality_fixture import real_hidden_quality_bundle


class ReferenceControlTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.campaign, self.hidden, self.policy = real_hidden_quality_bundle(self.root / "fixture")
        profile = self.campaign.quality_profile()
        self.workload = load_quality_workload(self.hidden.resource_paths["quality_workload"], profile)
        self.store = LocalMeasurementBundleStore(self.root / "quality")
        self.anchors = {}
        for role, measurement_id in (("reference", self.policy.reference_measurement_id),
            ("clean_control", self.policy.clean_control_measurement_id)):
            self.anchors[role] = []
            for repetition in range(1, 4):
                outputs = {c.case_id: f"<answer>{c.expected}</answer>" for c in self.workload.cases}
                raw = {c.case_id + ".json": self.response(outputs[c.case_id]) for c in self.workload.cases}
                normalized = {"role": role, "repetition": repetition, "attempt": 1,
                    "quality_profile_digest": profile.digest, "quality_workload_digest": self.workload.digest,
                    "campaign_manifest_digest": self.campaign.manifest_digest,
                    "quality_score": score_quality_outputs(profile, self.workload, outputs, repetition=repetition, role=role)}
                destination = self.store.save(measurement_id, repetition, self.seal(normalized, raw), raw)
                self.anchors[role].append(digest_file(destination / "receipt.json"))
        self.policy = replace(self.policy, reference_receipt_digests=tuple(self.anchors["reference"]),
            clean_control_receipt_digests=tuple(self.anchors["clean_control"]))
        workload = load_correctness_workload(self.hidden.resource_paths["correctness_requests"])
        raw = {c.case_id + ".json": self.response(c.expected.strip("^$")
            if c.check_kind == "regex" else c.expected) for c in workload.cases}
        normalized = {"role": "reference", "repetition": 1, "attempt": 1,
            "campaign_manifest_digest": self.campaign.manifest_digest,
            "candidate_id": self.campaign.validate_reference_candidate().candidate_id,
            "candidate_manifest_digest": self.campaign.validate_reference_candidate().manifest_digest,
            "hidden_workload_manifest_digest": self.hidden.manifest_digest,
            "correctness_workload_digest": workload.digest,
            "correctness_result": score_correctness_responses(workload,
                {c.case_id: raw[c.case_id + ".json"] for c in workload.cases}, served_model_name="target-model").to_document()}
        self.correctness = LocalMeasurementBundleStore(self.root / "correctness").save("stock", 1,
            self.seal(normalized, raw), raw) / "receipt.json"
        self.arguments = dict(campaign=self.campaign, policy=self.policy,
            quality_workload=self.hidden.resource_paths["quality_workload"], quality_store=self.root / "quality",
            correctness_receipt=self.correctness, correctness_receipt_digest=digest_file(self.correctness),
            source_hidden=self.hidden, target_hidden=self.hidden)

    @staticmethod
    def response(content):
        return canonical_json_bytes({"model": "target-model", "choices": [{"finish_reason": "stop",
            "message": {"role": "assistant", "content": content}}]})

    def seal(self, normalized, raw):
        measurement = self.campaign.measurement_profile()
        gpu = {"name": "NVIDIA L4", "memory_mib": str(measurement.gpu_memory_mib),
            "power_limit_watts": measurement.gpu_power_limit_watts, "driver_version": measurement.gpu_driver_version}
        remote = {"ok": True, "error": None, "model_id": self.campaign.target_model_id,
            "model_revision": self.campaign.target_model_revision, "served_model_name": "target-model",
            "vllm_version": "0.21.0", "gpu_before": gpu, "gpu_after": dict(gpu),
            "canary_before": {"content": "READY", "returned_model": "target-model"},
            "canary_after": {"content": "READY", "returned_model": "target-model"},
            "environment": {"base_image_ref": "nvidia/cuda:12.9.0-devel-ubuntu22.04",
                "base_image_digest": measurement.base_image_digest, "package_set_digest": measurement.resolved_package_digest}}
        remote.update({k: normalized.get(k) for k in ("campaign_manifest_digest", "candidate_id", "repetition", "attempt")})
        remote.update(quality_profile_digest=normalized.get("quality_profile_digest"),
            quality_workload_digest=normalized.get("quality_workload_digest", normalized.get("correctness_workload_digest")))
        unsealed = {**normalized, "valid": True, "validation_errors": [], "remote_receipt": remote,
            "durable_evidence": {"volume_name": "agent-collab-evals-evaluator-evidence-v2",
                "raw_digests": {k: digest_bytes(v) for k, v in raw.items()},
                "remote_receipt_digest": digest_bytes(canonical_json_bytes(remote) + b"\n")}}
        digest = digest_bytes(canonical_json_bytes(unsealed) + b"\n")
        return {**unsealed, "durable_evidence": {**unsealed["durable_evidence"], "normalized_digest": digest}}

    def test_replays_raw_controls_and_returns_no_cases_or_execution_authority(self):
        report = verify_reference_controls(**self.arguments)
        self.assertTrue(report["quality"]["eligible"])
        self.assertTrue(report["correctness"]["eligible"])
        self.assertEqual(report["correctness"]["passed_cases"], 8)
        self.assertTrue(report["correctness"]["matches_target_workload"])
        self.assertFalse(report["physical_gpu_pairing_verified"])
        self.assertFalse(report["execution_authorized"])
        self.assertFalse(report["scoreable"])
        content = canonical_json_bytes(report).decode()
        self.assertNotIn('"cases"', content)
        self.assertNotIn("<answer>", content)
        self.assertNotIn('"response_digests"', content)

    def test_different_target_workload_does_not_inherit_control_qualification(self):
        target = replace(self.hidden, resource_digests={**self.hidden.resource_digests,
            "correctness_requests": digest_bytes(b"different workload")})
        report = verify_reference_controls(**{**self.arguments, "target_hidden": target})
        self.assertTrue(report["correctness"]["stock_control_verified"])
        self.assertFalse(report["correctness"]["matches_target_workload"])

    def test_changed_receipt_fails_closed(self):
        self.correctness.write_bytes(self.correctness.read_bytes() + b" ")
        with self.assertRaisesRegex(RuntimeError, "receipt digest"):
            verify_reference_controls(**self.arguments)

    def test_changed_raw_response_fails_before_scoring(self):
        path = next((self.correctness.parent / "raw").iterdir())
        path.write_bytes(self.response("corrupted response"))
        with self.assertRaisesRegex(ValueError, "digest"):
            verify_reference_controls(**self.arguments)

    def test_resealed_wrong_score_is_recomputed_even_with_an_updated_receipt_anchor(self):
        original = parse_json(self.correctness.read_text())
        bundle = LocalMeasurementBundleStore(self.root / "correctness").load("stock", 1)
        normalized = dict(original["normalized"])
        normalized["correctness_result"] = {**normalized["correctness_result"], "passed_cases": 0}
        changed = {**original, "normalized": self.seal(normalized, bundle.raw_documents)}
        # This deliberately bypasses write-once storage to simulate corruption.
        self.correctness.write_bytes(canonical_json_bytes(changed) + b"\n")
        with self.assertRaisesRegex(RuntimeError, "score differs from raw"):
            verify_reference_controls(**{**self.arguments, "correctness_receipt_digest": digest_file(self.correctness)})

    def test_resealed_quality_score_cannot_replace_raw_scoring(self):
        path = self.root / "quality" / self.policy.reference_measurement_id / "repetition-0001-attempt-01/receipt.json"
        original = parse_json(path.read_text())
        bundle = self.store.load(self.policy.reference_measurement_id, 1)
        normalized = dict(original["normalized"])
        normalized["quality_score"] = {**normalized["quality_score"], "pass_count": 0}
        changed = {**original, "normalized": self.seal(normalized, bundle.raw_documents)}
        path.write_bytes(canonical_json_bytes(changed) + b"\n")
        policy = replace(self.policy, reference_receipt_digests=(digest_file(path), *self.policy.reference_receipt_digests[1:]))
        with self.assertRaisesRegex(RuntimeError, "quality score differs from raw"):
            verify_reference_controls(**{**self.arguments, "policy": policy})
