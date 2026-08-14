"""Append-only local event sink for development and fake runs."""

from __future__ import annotations

import fcntl
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Mapping

from ..canonical import DuplicateKeyError, canonical_json_bytes, parse_json


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}")


class LocalEventSink:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._lock = threading.Lock()

    def append(
        self, campaign_run_id: str, kind: str, payload: Mapping[str, Any]
    ) -> int:
        self._validate_id(campaign_run_id)
        if not kind:
            raise ValueError("event kind is required")
        with self._lock:
            path = self._path(campaign_run_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                path,
                os.O_APPEND | os.O_CREAT | os.O_RDWR,
                0o600,
            )
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                os.lseek(descriptor, 0, os.SEEK_SET)
                existing = bytearray()
                while chunk := os.read(descriptor, 1024 * 1024):
                    existing.extend(chunk)
                sequence = len(self._parse(bytes(existing), campaign_run_id)) + 1
                event = {
                    "sequence": sequence,
                    "campaign_run_id": campaign_run_id,
                    "kind": kind,
                    "payload": dict(payload),
                }
                content = canonical_json_bytes(event) + b"\n"
                written = 0
                while written < len(content):
                    count = os.write(descriptor, content[written:])
                    if count == 0:
                        raise OSError("event log write made no progress")
                    written += count
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return sequence

    def read(self, campaign_run_id: str) -> tuple[dict[str, Any], ...]:
        self._validate_id(campaign_run_id)
        path = self._path(campaign_run_id)
        if not path.exists():
            return ()
        descriptor = os.open(path, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH)
            content = bytearray()
            while chunk := os.read(descriptor, 1024 * 1024):
                content.extend(chunk)
        finally:
            os.close(descriptor)
        return self._parse(bytes(content), campaign_run_id)

    @staticmethod
    def _parse(content: bytes, campaign_run_id: str) -> tuple[dict[str, Any], ...]:
        if not content:
            return ()
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("event log is not valid UTF-8") from error
        events: list[dict[str, Any]] = []
        expected = 1
        for line in text.splitlines():
            if not line:
                raise ValueError("event log contains an empty record")
            try:
                event = parse_json(line)
            except (json.JSONDecodeError, DuplicateKeyError) as error:
                raise ValueError("event log contains ambiguous JSON") from error
            if not isinstance(event, dict) or set(event) != {
                "sequence",
                "campaign_run_id",
                "kind",
                "payload",
            }:
                raise ValueError("event log record does not match the schema")
            if event["sequence"] != expected:
                raise ValueError("event log has a non-monotonic sequence")
            if event["campaign_run_id"] != campaign_run_id:
                raise ValueError("event log campaign identifier mismatch")
            if not isinstance(event["kind"], str) or not event["kind"]:
                raise ValueError("event log kind is invalid")
            if not isinstance(event["payload"], dict):
                raise ValueError("event log payload is invalid")
            events.append(event)
            expected += 1
        return tuple(events)

    def _path(self, campaign_run_id: str) -> Path:
        return self._root / campaign_run_id / "events.jsonl"

    @staticmethod
    def _validate_id(campaign_run_id: str) -> None:
        if not _SAFE_ID.fullmatch(campaign_run_id):
            raise ValueError("invalid campaign_run_id")
