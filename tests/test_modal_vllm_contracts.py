from __future__ import annotations

import copy
import hashlib
import importlib.util
import math
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
