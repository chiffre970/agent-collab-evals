"""Fetch digest-pinned public sources for the private quality materializer."""

from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
import tomllib
import urllib.request
from pathlib import Path


MAX_SOURCE_BYTES = 32 * 1024 * 1024


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sources",
        type=Path,
        default=Path("campaigns/model_serving_v0/evaluator/quality_sources.toml"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("tmp/evaluator-private/model-serving-quality/sources"),
    )
    arguments = parser.parse_args()
    with arguments.sources.open("rb") as source:
        profile = tomllib.load(source)
    if set(profile) != {"schema_version", "sources"} or profile.get(
        "schema_version"
    ) != "model-serving-quality-sources/v0alpha1":
        raise RuntimeError("unsupported quality source profile")
    sources = profile.get("sources")
    if not isinstance(sources, list):
        raise RuntimeError("quality source list is invalid")
    arguments.output_root.mkdir(parents=True, exist_ok=True)
    for value in sources:
        _fetch_one(arguments.output_root, value)


def _fetch_one(root: Path, value: object) -> None:
    expected_keys = {"id", "format", "filename", "url", "revision", "sha256"}
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise RuntimeError("quality source fields differ")
    filename = value["filename"]
    expected = value["sha256"]
    url = value["url"]
    if (
        not isinstance(filename, str)
        or Path(filename).name != filename
        or not isinstance(expected, str)
        or len(expected) != 64
        or not isinstance(url, str)
        or not url.startswith("https://")
    ):
        raise RuntimeError("quality source identity is invalid")
    destination = (root / filename).resolve()
    if destination.exists():
        _verify(destination, expected)
        print(f"verified {value['id']}: {destination}")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{filename}.", suffix=".download", dir=root
    )
    temporary = Path(temporary_name)
    try:
        received = 0
        hasher = hashlib.sha256()
        with os.fdopen(descriptor, "wb") as target:
            request = urllib.request.Request(url, headers={"User-Agent": "agent-collab-evals/0.1"})
            with urllib.request.urlopen(request, timeout=60) as response:
                while chunk := response.read(1024 * 1024):
                    received += len(chunk)
                    if received > MAX_SOURCE_BYTES:
                        raise RuntimeError("quality source exceeds the size limit")
                    hasher.update(chunk)
                    target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        if hasher.hexdigest() != expected:
            raise RuntimeError(f"quality source {value['id']} digest differs")
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    print(f"fetched {value['id']}: {destination}")


def _verify(path: Path, expected: str) -> None:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    if hasher.hexdigest() != expected:
        raise RuntimeError(f"existing quality source {path.name} digest differs")


if __name__ == "__main__":
    main()
