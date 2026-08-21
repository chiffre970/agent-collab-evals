#!/usr/bin/env python3
"""Freeze raw OpenRouter route catalogs and derive the normalized candidates."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from agent_collab_evals.canonical import (
    canonical_json_bytes,
    digest_bytes,
    digest_file,
    load_json,
)
from agent_collab_evals.provider_qualification import (
    ENDPOINT_CATALOG_URL,
    ZDR_CATALOG_URL,
    extract_candidate_snapshot,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = (
    REPOSITORY_ROOT
    / "config/provider_qualification/deepseek-v4-flash-development-policy.json"
)
SOURCE_MANIFEST_PATH = (
    REPOSITORY_ROOT
    / "config/provider_qualification/openrouter-deepseek-v4-flash-sources-20260822.json"
)
SNAPSHOT_PATH = (
    REPOSITORY_ROOT
    / "config/provider_qualification/openrouter-deepseek-v4-flash-zdr-20260822.json"
)
SOURCE_DIRECTORY = REPOSITORY_ROOT / "evidence/provider_qualification/sources"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        action="store_true",
        help="fetch and replace the retained source bundle; otherwise spend nothing",
    )
    args = parser.parse_args()
    if not args.execute:
        print(
            json.dumps(
                {
                    "ok": True,
                    "executed": False,
                    "sources": [ENDPOINT_CATALOG_URL, ZDR_CATALOG_URL],
                    "next": "rerun with --execute to freeze a new source bundle",
                },
                indent=2,
            )
        )
        return 0
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is missing")
    with POLICY_PATH.open("r", encoding="utf-8") as stream:
        policy = load_json(stream)
    model_id = "deepseek/deepseek-v4-flash-0731"
    providers = tuple(policy["candidate_providers"])
    if not providers or not all(isinstance(value, str) for value in providers):
        raise ValueError("policy candidate providers are invalid")

    observed = datetime.now(UTC).replace(microsecond=0)
    timestamp = observed.strftime("%Y%m%dT%H%M%SZ")
    endpoints_raw = _fetch(ENDPOINT_CATALOG_URL, None)
    zdr_raw = _fetch(ZDR_CATALOG_URL, api_key)
    metadata_model, candidates = extract_candidate_snapshot(
        endpoints_raw,
        zdr_raw,
        model_id=model_id,
        candidate_providers=providers,
    )

    SOURCE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    source_rows: dict[str, dict[str, str]] = {}
    for source_id, url, raw in (
        ("endpoints", ENDPOINT_CATALOG_URL, endpoints_raw),
        ("zdr", ZDR_CATALOG_URL, zdr_raw),
    ):
        relative = Path(
            "evidence/provider_qualification/sources/"
            f"openrouter-{source_id}-{timestamp}.json.gz"
        )
        destination = REPOSITORY_ROOT / relative
        compressed = gzip.compress(raw, compresslevel=9, mtime=0)
        _write(destination, compressed)
        source_rows[source_id] = {
            "url": url,
            "file": str(relative),
            "content_encoding": "gzip",
            "raw_digest": digest_bytes(raw),
            "file_digest": digest_file(destination),
        }

    source_manifest = {
        "schema_version": "provider-source-bundle/v1",
        "bundle_id": f"openrouter-deepseek-v4-flash-{timestamp}",
        "status": "development",
        "observed_at": observed.isoformat().replace("+00:00", "Z"),
        "sources": source_rows,
    }
    _write(
        SOURCE_MANIFEST_PATH,
        canonical_json_bytes(source_manifest) + b"\n",
    )
    snapshot = {
        "schema_version": "provider-candidate-snapshot/v1",
        "snapshot_id": f"openrouter-deepseek-v4-flash-zdr-{timestamp}",
        "status": "development",
        "observed_at": source_manifest["observed_at"],
        "source_manifest": str(SOURCE_MANIFEST_PATH.relative_to(REPOSITORY_ROOT)),
        "source_manifest_digest": digest_file(SOURCE_MANIFEST_PATH),
        "model_id": model_id,
        "expected_metadata_model": metadata_model,
        "candidate_scope": "predeclared_reputable_routes",
        "candidate_providers": list(providers),
        "candidates": list(candidates),
    }
    _write(SNAPSHOT_PATH, canonical_json_bytes(snapshot) + b"\n")
    print(
        json.dumps(
            {
                "ok": True,
                "executed": True,
                "observed_at": source_manifest["observed_at"],
                "source_manifest": str(
                    SOURCE_MANIFEST_PATH.relative_to(REPOSITORY_ROOT)
                ),
                "source_manifest_digest": digest_file(SOURCE_MANIFEST_PATH),
                "candidate_snapshot": str(SNAPSHOT_PATH.relative_to(REPOSITORY_ROOT)),
                "candidate_snapshot_digest": digest_file(SNAPSHOT_PATH),
                "candidate_providers": list(providers),
            },
            indent=2,
        )
    )
    return 0


def _fetch(url: str, api_key: str | None) -> bytes:
    headers = {
        "Accept": "application/json",
        "User-Agent": "agent-collab-evals-provider-source/1",
    }
    if api_key is not None:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
        if response.status != 200 or not raw:
            raise RuntimeError(f"provider source request failed with {response.status}")
        return raw


def _write(path: Path, value: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(value)
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
