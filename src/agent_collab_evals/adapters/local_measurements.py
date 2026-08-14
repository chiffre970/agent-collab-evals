"""Atomic evaluator-private persistence for raw measurement bundles."""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..canonical import (
    DuplicateKeyError,
    canonical_json_bytes,
    digest_bytes,
    load_json,
)


MEASUREMENT_BUNDLE_SCHEMA = "measurement-bundle/v0alpha1"
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}")
_SAFE_RAW_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,191}\.json")


class MeasurementBundleError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StoredMeasurementBundle:
    receipt: Mapping[str, Any]
    raw_documents: Mapping[str, bytes]


class LocalMeasurementBundleStore:
    """Commit a complete repetition directory with no partial visibility."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def save(
        self,
        measurement_id: str,
        repetition: int,
        normalized: Mapping[str, Any],
        raw_documents: Mapping[str, bytes],
        *,
        attempt: int = 1,
    ) -> Path:
        _validate_identity(measurement_id, repetition, attempt)
        raw_digests = _validate_raw_documents(raw_documents)
        receipt = {
            "schema_version": MEASUREMENT_BUNDLE_SCHEMA,
            "measurement_id": measurement_id,
            "repetition": repetition,
            "attempt": attempt,
            "normalized": normalized,
            "raw_digests": raw_digests,
        }
        receipt_bytes = canonical_json_bytes(receipt) + b"\n"
        parent = self._root / measurement_id
        destination = parent / f"repetition-{repetition:04d}-attempt-{attempt:02d}"
        parent.mkdir(parents=True, exist_ok=True)

        lock_path = parent / ".bundle.lock"
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            if destination.exists():
                existing = self.load(measurement_id, repetition, attempt=attempt)
                if (
                    canonical_json_bytes(existing.receipt) + b"\n" == receipt_bytes
                    and dict(existing.raw_documents) == dict(raw_documents)
                ):
                    return destination
                raise MeasurementBundleError(
                    "measurement repetition already exists with different content"
                )

            temporary = Path(
                tempfile.mkdtemp(prefix=".measurement-", dir=parent)
            )
            try:
                raw_root = temporary / "raw"
                raw_root.mkdir()
                for name, content in sorted(raw_documents.items()):
                    _write_fsynced(raw_root / name, content)
                _write_fsynced(temporary / "receipt.json", receipt_bytes)
                _fsync_directory(raw_root)
                _fsync_directory(temporary)
                os.replace(temporary, destination)
                _fsync_directory(parent)
            except BaseException:
                shutil.rmtree(temporary, ignore_errors=True)
                raise
        return destination

    def load(
        self, measurement_id: str, repetition: int, *, attempt: int = 1
    ) -> StoredMeasurementBundle:
        _validate_identity(measurement_id, repetition, attempt)
        root = (
            self._root
            / measurement_id
            / f"repetition-{repetition:04d}-attempt-{attempt:02d}"
        )
        try:
            with (root / "receipt.json").open("r", encoding="utf-8") as source:
                receipt = load_json(source)
        except FileNotFoundError as error:
            raise KeyError("measurement repetition does not exist") from error
        except (json.JSONDecodeError, DuplicateKeyError) as error:
            raise MeasurementBundleError(
                "measurement receipt is not unambiguous JSON"
            ) from error
        if not isinstance(receipt, dict):
            raise MeasurementBundleError("measurement receipt must be an object")
        expected = {
            "schema_version",
            "measurement_id",
            "repetition",
            "attempt",
            "normalized",
            "raw_digests",
        }
        if set(receipt) != expected:
            raise MeasurementBundleError("measurement receipt fields differ")
        if (
            receipt["schema_version"] != MEASUREMENT_BUNDLE_SCHEMA
            or receipt["measurement_id"] != measurement_id
            or receipt["repetition"] != repetition
            or receipt["attempt"] != attempt
            or not isinstance(receipt["normalized"], dict)
            or not isinstance(receipt["raw_digests"], dict)
        ):
            raise MeasurementBundleError("measurement receipt identity is invalid")

        raw_root = root / "raw"
        try:
            raw_entries = tuple(raw_root.iterdir())
        except FileNotFoundError as error:
            raise MeasurementBundleError("measurement raw directory is missing") from error
        if any(entry.is_symlink() or not entry.is_file() for entry in raw_entries):
            raise MeasurementBundleError("measurement raw directory is invalid")
        if {entry.name for entry in raw_entries} != set(receipt["raw_digests"]):
            raise MeasurementBundleError("measurement raw result set differs")

        raw_documents: dict[str, bytes] = {}
        for name, expected_digest in receipt["raw_digests"].items():
            if not isinstance(name, str) or not _SAFE_RAW_NAME.fullmatch(name):
                raise MeasurementBundleError("measurement raw filename is invalid")
            if not isinstance(expected_digest, str):
                raise MeasurementBundleError("measurement raw digest is invalid")
            try:
                content = (raw_root / name).read_bytes()
            except FileNotFoundError as error:
                raise MeasurementBundleError("measurement raw result is missing") from error
            if digest_bytes(content) != expected_digest:
                raise MeasurementBundleError("measurement raw result digest mismatch")
            raw_documents[name] = content
        return StoredMeasurementBundle(receipt, raw_documents)


def _validate_identity(measurement_id: str, repetition: int, attempt: int) -> None:
    if not _SAFE_ID.fullmatch(measurement_id):
        raise MeasurementBundleError("invalid measurement_id")
    if not isinstance(repetition, int) or isinstance(repetition, bool) or repetition < 1:
        raise MeasurementBundleError("repetition must be a positive integer")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise MeasurementBundleError("attempt must be a positive integer")


def _validate_raw_documents(raw_documents: Mapping[str, bytes]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, content in raw_documents.items():
        if not isinstance(name, str) or not _SAFE_RAW_NAME.fullmatch(name):
            raise MeasurementBundleError("invalid raw result filename")
        if not isinstance(content, bytes):
            raise MeasurementBundleError("raw results must be bytes")
        result[name] = digest_bytes(content)
    return result


def _write_fsynced(path: Path, content: bytes) -> None:
    with path.open("xb") as target:
        target.write(content)
        target.flush()
        os.fsync(target.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
