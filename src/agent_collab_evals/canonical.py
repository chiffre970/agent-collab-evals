"""Small deterministic serialization helpers used at experiment boundaries."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, TextIO


class CanonicalizationError(ValueError):
    """Raised when a value has no unambiguous canonical representation."""


class DuplicateKeyError(ValueError):
    """Raised when a JSON object repeats a key."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def load_json(source: TextIO) -> Any:
    """Parse JSON while rejecting duplicate object keys."""

    return json.load(source, object_pairs_hook=_unique_object)


def parse_json(value: str) -> Any:
    """Parse a JSON string while rejecting duplicate object keys."""

    return json.loads(value, object_pairs_hook=_unique_object)


def _normalize(value: Any) -> Any:
    if is_dataclass(value):
        return _normalize(asdict(value))
    if isinstance(value, Enum):
        return _normalize(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError("canonical mappings require string keys")
            normalized[key] = _normalize(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, float):
        raise CanonicalizationError(
            "floating-point values are forbidden; encode measurements and money "
            "as integer units or decimal strings"
        )
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise CanonicalizationError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return a stable UTF-8 JSON representation with no insignificant spacing."""

    return json.dumps(
        _normalize(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def digest_value(value: Any) -> str:
    return digest_bytes(canonical_json_bytes(value))


def digest_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return f"sha256:{hasher.hexdigest()}"
