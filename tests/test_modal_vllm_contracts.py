from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path("campaigns/model_serving_v0/reference/modal_vllm.py")
SPEC = importlib.util.spec_from_file_location("modal_vllm_contracts", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODAL_VLLM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODAL_VLLM)


def _quality_spec() -> dict[str, object]:
    return {
        "campaign_manifest_digest": "sha256:" + "1" * 64,
        "quality_profile_digest": "sha256:" + "2" * 64,
        "quality_workload_digest": "sha256:" + "3" * 64,
        "repetition": 1,
        "attempt": 1,
        "evidence_root": "model-serving-quality/abc/repetition-0001-attempt-01",
        "max_concurrency": 8,
        "request_timeout_seconds": 300,
        "requests": [
            {
                "case_id": "mmlu-abc123",
                "body": {
                    "model": "target-model",
                    "messages": [{"role": "user", "content": "Question"}],
                    "seed": 7,
                    "stream": False,
                    "temperature": 0.7,
                    "top_p": 0.8,
                    "top_k": 20,
                    "min_p": 0.0,
                    "max_tokens": 512,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            }
        ],
    }


class _ReadOnlyVolume:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def read_file(self, path: str):
        try:
            yield self.files[path]
        except KeyError as error:
            raise FileNotFoundError(path) from error


class ModalVllmContractTests(unittest.TestCase):
    def test_server_command_is_built_from_typed_settings(self) -> None:
        candidate = json.loads(
            Path("campaigns/model_serving_v0/reference/candidate.json").read_text(
                encoding="utf-8"
            )
        )
        command = MODAL_VLLM._server_command(candidate)
        self.assertEqual(
            command[:3],
            ("vllm", "serve", MODAL_VLLM._pinned_model_snapshot_path()),
        )
        self.assertIn(MODAL_VLLM.MODEL_REVISION, command[2])
        self.assertNotIn("python", command)
        self.assertEqual(command.count("--stream-interval"), 1)

        candidate["server"]["entrypoint"] = ["python", "-c", "print('secret')"]
        with self.assertRaisesRegex(ValueError, "fields differ"):
            MODAL_VLLM._server_command(candidate)

    def test_scored_server_environment_is_minimal_and_offline(self) -> None:
        environment = MODAL_VLLM._server_environment(offline=True)
        self.assertEqual(environment["HF_HUB_OFFLINE"], "1")
        self.assertEqual(environment["TRANSFORMERS_OFFLINE"], "1")
        self.assertNotIn("HF_TOKEN", environment)
        self.assertNotIn("OPENROUTER_API_KEY", environment)

    def test_scored_functions_have_no_secret_or_writable_volume(self) -> None:
        source = Path(
            "campaigns/model_serving_v0/reference/modal_vllm.py"
        ).read_text(encoding="utf-8")
        for function_name in (
            "benchmark_serving_repetition",
            "quality_serving_repetition",
        ):
            start = source.index(f"def {function_name}(")
            decorator = source[source.rfind("@app.function(", 0, start) : start]
            self.assertIn("block_network=True", decorator)
            self.assertIn("with_mount_options(read_only=True)", decorator)
            self.assertNotIn("secrets=", decorator)
            self.assertNotIn("EVIDENCE_MOUNT_PATH", decorator)

    def test_inline_evidence_bundle_is_bounded_and_digest_verified(self) -> None:
        receipt = {"ok": True, "candidate_id": "candidate"}
        raw = {"point.json": b'{"result":"ok"}'}
        bundle = MODAL_VLLM._encode_evidence_bundle(receipt, raw)
        self.assertEqual(MODAL_VLLM._decode_evidence_bundle(bundle), (receipt, raw))

        changed = dict(bundle)
        changed["compressed_digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(RuntimeError, "identity differs"):
            MODAL_VLLM._decode_evidence_bundle(changed)

    def test_security_conformance_uses_the_hardened_quality_boundary(self) -> None:
        candidate = json.loads(
            Path("campaigns/model_serving_v0/reference/candidate.json").read_text(
                encoding="utf-8"
            )
        )
        spec = MODAL_VLLM._security_conformance_spec(
            candidate, "sha256:" + "1" * 64, "a" * 32
        )

        requests = MODAL_VLLM._validate_quality_spec(spec)

        self.assertEqual(len(requests), 1)
        self.assertEqual(spec["evidence_root"], "security-conformance/" + "a" * 32)
        self.assertEqual(requests[0]["body"]["max_tokens"], 8)

    def test_evidence_persistence_is_idempotent_without_candidate_mount(self) -> None:
        receipt = {"ok": True, "candidate_id": "candidate"}
        raw = {"point.json": b'{"result":"ok"}'}
        with tempfile.TemporaryDirectory() as directory:
            original_root = MODAL_VLLM.EVIDENCE_MOUNT_PATH
            MODAL_VLLM.EVIDENCE_MOUNT_PATH = Path(directory)
            try:
                first = MODAL_VLLM._persist_remote_evidence(
                    "model-serving/test/repetition-0001-attempt-01",
                    receipt,
                    raw,
                )
                repeated = MODAL_VLLM._persist_remote_evidence(
                    "model-serving/test/repetition-0001-attempt-01",
                    receipt,
                    raw,
                )
                self.assertEqual(first, repeated)
                with self.assertRaisesRegex(RuntimeError, "already differs"):
                    MODAL_VLLM._persist_remote_evidence(
                        "model-serving/test/repetition-0001-attempt-01",
                        receipt,
                        {"point.json": b"tampered"},
                    )
            finally:
                MODAL_VLLM.EVIDENCE_MOUNT_PATH = original_root

    def test_quality_spec_rejects_unknown_fields_and_path_traversal(self) -> None:
        valid = _quality_spec()
        self.assertEqual(len(MODAL_VLLM._validate_quality_spec(valid)), 1)

        changed = copy.deepcopy(valid)
        changed["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "fields differ"):
            MODAL_VLLM._validate_quality_spec(changed)

        changed = copy.deepcopy(valid)
        changed["evidence_root"] = "../outside"
        with self.assertRaisesRegex(ValueError, "evidence root"):
            MODAL_VLLM._validate_quality_spec(changed)

    def test_quality_spec_rejects_nonfinite_or_out_of_range_sampling(self) -> None:
        for key, value in (
            ("temperature", math.nan),
            ("temperature", 2.1),
            ("top_p", 0.0),
            ("min_p", 1.1),
            ("top_k", -1),
        ):
            changed = copy.deepcopy(_quality_spec())
            changed["requests"][0]["body"][key] = value
            with self.subTest(key=key, value=value):
                with self.assertRaisesRegex(ValueError, "sampling|temperature|top_k"):
                    MODAL_VLLM._validate_quality_spec(changed)

        changed = copy.deepcopy(_quality_spec())
        changed["max_concurrency"] = 0
        with self.assertRaisesRegex(ValueError, "concurrency"):
            MODAL_VLLM._validate_quality_spec(changed)

    def test_durable_evidence_is_reloaded_and_digest_verified(self) -> None:
        root = "model-serving/abc/repetition-0001-attempt-01"
        raw = b'{"result":"ok"}'
        receipt = {"ok": True, "candidate_id": "candidate"}
        receipt_bytes = MODAL_VLLM._stable_json_bytes(receipt)
        evidence = {
            "schema_version": "modal-evaluator-evidence/v0alpha1",
            "volume_name": MODAL_VLLM.EVIDENCE_VOLUME_NAME,
            "root": root,
            "remote_receipt_digest": "sha256:"
            + hashlib.sha256(receipt_bytes).hexdigest(),
            "raw_digests": {
                "point.json": "sha256:" + hashlib.sha256(raw).hexdigest()
            },
        }
        files = {
            f"{root}/manifest.json": MODAL_VLLM._stable_json_bytes(evidence),
            f"{root}/remote-receipt.json": receipt_bytes,
            f"{root}/raw/point.json": raw,
        }
        original_volume = MODAL_VLLM.evidence_volume
        MODAL_VLLM.evidence_volume = _ReadOnlyVolume(files)
        try:
            pointer = MODAL_VLLM._evidence_pointer(evidence)
            loaded_receipt, loaded, observed = MODAL_VLLM._collect_remote_evidence(
                pointer, expected_root=root
            )
            self.assertEqual(loaded_receipt, receipt)
            self.assertEqual(loaded, {"point.json": raw})
            self.assertEqual(observed, evidence)

            files[f"{root}/raw/point.json"] = b"tampered"
            with self.assertRaisesRegex(RuntimeError, "digest differs"):
                MODAL_VLLM._collect_remote_evidence(pointer, expected_root=root)
        finally:
            MODAL_VLLM.evidence_volume = original_volume


if __name__ == "__main__":
    unittest.main()
