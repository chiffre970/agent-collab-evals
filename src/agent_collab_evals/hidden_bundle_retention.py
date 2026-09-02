"""Digest-addressed retention for evaluator-private workload bundles."""

from __future__ import annotations

import io
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Protocol

from .campaigns.serving_workload import HiddenWorkloadBundle
from .canonical import (
    canonical_json_bytes,
    digest_bytes,
    digest_file,
    digest_value,
    load_json,
)


RETENTION_PROFILE_SCHEMA = "hidden-bundle-retention-profile/v1"
RETENTION_RECEIPT_SCHEMA = "hidden-bundle-retention-receipt/v1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}")
_FILES = {
    "manifest": "manifest.json",
    "correctness_requests": "correctness.jsonl",
    "performance_profile": "performance.toml",
    "quality_requests": "quality-requests.json",
    "quality_workload": "quality-workload.json",
}


class HiddenBundleRetentionError(ValueError):
    """The private bundle retention authority or evidence is invalid."""


class ImmutableObjectStore(Protocol):
    """Minimal write-once object store used by private bundle retention."""

    def put_once(self, path: str, content: bytes) -> None: ...

    def get(self, path: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class HiddenBundleRetentionProfile:
    path: Path
    digest: str
    profile_id: str
    modal_environment: str
    volume_name: str
    namespace: str
    hidden_workload_manifest_digest: str

    @classmethod
    def load(cls, path: Path) -> "HiddenBundleRetentionProfile":
        resolved = path.resolve(strict=True)
        with resolved.open("r", encoding="utf-8") as source:
            value = load_json(source)
        expected = {
            "schema_version",
            "profile_id",
            "status",
            "backend",
            "modal_environment",
            "volume_name",
            "namespace",
            "hidden_workload_manifest_digest",
            "write_policy",
            "verification_policy",
            "seed_retention",
            "deletion_policy",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise HiddenBundleRetentionError("retention profile fields differ")
        fixed = {
            "schema_version": RETENTION_PROFILE_SCHEMA,
            "status": "registered",
            "backend": "modal_volume",
            "write_policy": "write_once_digest_addressed",
            "verification_policy": "complete_read_back_sha256",
            "seed_retention": "excluded",
            "deletion_policy": "manual_after_study_audit_release",
        }
        if any(value.get(key) != item for key, item in fixed.items()):
            raise HiddenBundleRetentionError("retention profile policy differs")
        for name in (
            "profile_id",
            "modal_environment",
            "volume_name",
            "namespace",
        ):
            if not isinstance(value[name], str) or not _SAFE_ID.fullmatch(value[name]):
                raise HiddenBundleRetentionError(f"retention {name} is invalid")
        manifest_digest = value["hidden_workload_manifest_digest"]
        if not isinstance(manifest_digest, str) or not _DIGEST.fullmatch(
            manifest_digest
        ):
            raise HiddenBundleRetentionError("retention manifest digest is invalid")
        expected_namespace = "hidden-workload-" + manifest_digest[7:23]
        if value["namespace"] != expected_namespace:
            raise HiddenBundleRetentionError("retention namespace is not derived")
        return cls(
            path=resolved,
            digest=digest_file(resolved),
            profile_id=value["profile_id"],
            modal_environment=value["modal_environment"],
            volume_name=value["volume_name"],
            namespace=value["namespace"],
            hidden_workload_manifest_digest=manifest_digest,
        )


@dataclass(frozen=True, slots=True)
class HiddenBundleRetentionReceipt:
    profile_digest: str
    hidden_workload_manifest_digest: str
    selection_seed_commitment: str
    volume_name: str
    namespace: str
    object_digests: Mapping[str, str]

    @property
    def document(self) -> dict[str, Any]:
        return {
            "schema_version": RETENTION_RECEIPT_SCHEMA,
            "profile_digest": self.profile_digest,
            "hidden_workload_manifest_digest": (
                self.hidden_workload_manifest_digest
            ),
            "selection_seed_commitment": self.selection_seed_commitment,
            "backend": "modal_volume",
            "volume_name": self.volume_name,
            "namespace": self.namespace,
            "verification": "complete_read_back_sha256",
            "object_digests": dict(self.object_digests),
        }

    @property
    def digest(self) -> str:
        return digest_value(self.document)

    @classmethod
    def load(cls, path: Path) -> "HiddenBundleRetentionReceipt":
        with path.resolve(strict=True).open("r", encoding="utf-8") as source:
            value = load_json(source)
        expected = {
            "schema_version",
            "profile_digest",
            "hidden_workload_manifest_digest",
            "selection_seed_commitment",
            "backend",
            "volume_name",
            "namespace",
            "verification",
            "object_digests",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise HiddenBundleRetentionError("retention receipt fields differ")
        if (
            value["schema_version"] != RETENTION_RECEIPT_SCHEMA
            or value["backend"] != "modal_volume"
            or value["verification"] != "complete_read_back_sha256"
        ):
            raise HiddenBundleRetentionError("retention receipt policy differs")
        objects = value["object_digests"]
        if not isinstance(objects, dict) or set(objects) != set(_FILES.values()):
            raise HiddenBundleRetentionError("retention receipt objects differ")
        for digest in (
            value["profile_digest"],
            value["hidden_workload_manifest_digest"],
            value["selection_seed_commitment"],
            *objects.values(),
        ):
            if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
                raise HiddenBundleRetentionError("retention receipt digest is invalid")
        for name in ("volume_name", "namespace"):
            if not isinstance(value[name], str) or not _SAFE_ID.fullmatch(value[name]):
                raise HiddenBundleRetentionError("retention receipt locator is invalid")
        return cls(
            profile_digest=value["profile_digest"],
            hidden_workload_manifest_digest=value[
                "hidden_workload_manifest_digest"
            ],
            selection_seed_commitment=value["selection_seed_commitment"],
            volume_name=value["volume_name"],
            namespace=value["namespace"],
            object_digests=dict(objects),
        )


class HiddenBundleRetentionService:
    """Retain and reverify one registered hidden bundle without its seed."""

    def __init__(
        self,
        profile: HiddenBundleRetentionProfile,
        object_store: ImmutableObjectStore,
    ) -> None:
        self._profile = profile
        self._store = object_store

    def retain(self, bundle: HiddenWorkloadBundle) -> HiddenBundleRetentionReceipt:
        if bundle.manifest_digest != self._profile.hidden_workload_manifest_digest:
            raise HiddenBundleRetentionError("retained bundle manifest differs")
        objects = self._objects(bundle)
        observed: dict[str, str] = {}
        for filename, content in sorted(objects.items()):
            remote_path = self._remote_path(filename)
            self._store.put_once(remote_path, content)
            retained = self._store.get(remote_path)
            if retained != content:
                raise HiddenBundleRetentionError("retained hidden object differs")
            observed[filename] = digest_bytes(retained)
        receipt = HiddenBundleRetentionReceipt(
            profile_digest=self._profile.digest,
            hidden_workload_manifest_digest=bundle.manifest_digest,
            selection_seed_commitment=bundle.selection_seed_commitment,
            volume_name=self._profile.volume_name,
            namespace=self._profile.namespace,
            object_digests=observed,
        )
        self.verify(bundle, receipt)
        return receipt

    def verify(
        self,
        bundle: HiddenWorkloadBundle,
        receipt: HiddenBundleRetentionReceipt,
    ) -> None:
        expected = self._objects(bundle)
        identity = (
            self._profile.digest,
            bundle.manifest_digest,
            bundle.selection_seed_commitment,
            self._profile.volume_name,
            self._profile.namespace,
        )
        observed_identity = (
            receipt.profile_digest,
            receipt.hidden_workload_manifest_digest,
            receipt.selection_seed_commitment,
            receipt.volume_name,
            receipt.namespace,
        )
        if identity != observed_identity:
            raise HiddenBundleRetentionError("retention receipt identity differs")
        expected_digests = {
            name: digest_bytes(content) for name, content in expected.items()
        }
        if dict(receipt.object_digests) != expected_digests:
            raise HiddenBundleRetentionError("retention receipt digests differ")
        for filename, expected_digest in expected_digests.items():
            retained = self._store.get(self._remote_path(filename))
            if digest_bytes(retained) != expected_digest:
                raise HiddenBundleRetentionError("retained hidden object was modified")

    def _remote_path(self, filename: str) -> str:
        return str(PurePosixPath(self._profile.namespace) / filename)

    @staticmethod
    def _objects(bundle: HiddenWorkloadBundle) -> dict[str, bytes]:
        objects = {"manifest.json": bundle.manifest_path.read_bytes()}
        for resource_name, filename in _FILES.items():
            if resource_name == "manifest":
                continue
            path = bundle.resource_paths[resource_name]
            content = path.read_bytes()
            if digest_bytes(content) != bundle.resource_digests[resource_name]:
                raise HiddenBundleRetentionError("local hidden resource differs")
            objects[filename] = content
        return objects


class ModalVolumeObjectStore:
    """Write-once object store backed by an evaluator-owned Modal Volume."""

    def __init__(
        self,
        volume_name: str,
        modal_environment: str,
        *,
        create_if_missing: bool = False,
    ) -> None:
        import modal

        self._volume = modal.Volume.from_name(
            volume_name,
            environment_name=modal_environment,
            create_if_missing=create_if_missing,
        )

    def put_once(self, path: str, content: bytes) -> None:
        try:
            existing = self.get(path)
        except FileNotFoundError:
            with self._volume.batch_upload(force=False) as batch:
                batch.put_file(io.BytesIO(content), path, mode=0o400)
            existing = self.get(path)
        if existing != content:
            raise HiddenBundleRetentionError(
                "retention path already contains different bytes"
            )

    def get(self, path: str) -> bytes:
        return b"".join(self._volume.read_file(path))


def write_retention_receipt_once(
    path: Path, receipt: HiddenBundleRetentionReceipt
) -> None:
    """Atomically write one canonical receipt without replacement."""

    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = canonical_json_bytes(receipt.document) + b"\n"
    if destination.exists():
        if destination.read_bytes() != content:
            raise HiddenBundleRetentionError(
                "retention receipt already exists with different bytes"
            )
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".hidden-retention-", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, destination)
        directory = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
