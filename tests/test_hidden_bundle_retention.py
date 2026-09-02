from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_collab_evals.hidden_bundle_retention import (
    HiddenBundleRetentionError,
    HiddenBundleRetentionProfile,
    HiddenBundleRetentionReceipt,
    HiddenBundleRetentionService,
    write_retention_receipt_once,
)
from tests.quality_fixture import REPOSITORY_ROOT, real_hidden_quality_bundle


PROFILE_PATH = (
    REPOSITORY_ROOT
    / "config/retention_profiles/model-serving-hidden-study-v1.json"
)


class _MemoryObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_once(self, path: str, content: bytes) -> None:
        existing = self.objects.setdefault(path, content)
        if existing != content:
            raise HiddenBundleRetentionError("object differs")

    def get(self, path: str) -> bytes:
        try:
            return self.objects[path]
        except KeyError as error:
            raise FileNotFoundError(path) from error


class HiddenBundleRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        _, self.bundle, _ = real_hidden_quality_bundle(self.root / "bundle")
        source = PROFILE_PATH.read_text(encoding="utf-8").replace(
            "sha256:d4ef783ed35d7418f0e1b5a61a4d5045d1c00781f873a8882ddf0c02ab62ffa8",
            self.bundle.manifest_digest,
        ).replace(
            "hidden-workload-d4ef783ed35d7418",
            "hidden-workload-" + self.bundle.manifest_digest[7:23],
        )
        self.profile_path = self.root / "retention.json"
        self.profile_path.write_text(source, encoding="utf-8")
        self.profile = HiddenBundleRetentionProfile.load(self.profile_path)
        self.store = _MemoryObjectStore()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_retention_is_idempotent_and_excludes_the_seed(self) -> None:
        service = HiddenBundleRetentionService(self.profile, self.store)

        first = service.retain(self.bundle)
        second = service.retain(self.bundle)

        self.assertEqual(first, second)
        self.assertEqual(set(first.object_digests), {
            "manifest.json",
            "correctness.jsonl",
            "performance.toml",
            "quality-requests.json",
            "quality-workload.json",
        })
        self.assertFalse(any("seed" in path for path in self.store.objects))

    def test_remote_tampering_fails_reverification(self) -> None:
        service = HiddenBundleRetentionService(self.profile, self.store)
        receipt = service.retain(self.bundle)
        path = f"{self.profile.namespace}/performance.toml"
        self.store.objects[path] += b"changed"

        with self.assertRaisesRegex(
            HiddenBundleRetentionError, "object was modified"
        ):
            service.verify(self.bundle, receipt)

    def test_receipt_is_canonical_write_once_and_reloadable(self) -> None:
        receipt = HiddenBundleRetentionService(
            self.profile, self.store
        ).retain(self.bundle)
        path = self.root / "receipt.json"

        write_retention_receipt_once(path, receipt)
        write_retention_receipt_once(path, receipt)

        self.assertEqual(HiddenBundleRetentionReceipt.load(path), receipt)


if __name__ == "__main__":
    unittest.main()
