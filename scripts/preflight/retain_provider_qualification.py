#!/usr/bin/env python3
"""Retain one passing route-qualification record and its exact receipts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_collab_evals.canonical import digest_file, load_json


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TEMP_ROOT = (REPOSITORY_ROOT / "tmp/provider-qualification").resolve()
EVIDENCE_ROOT = REPOSITORY_ROOT / "evidence/provider_qualification"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    if source.parent != TEMP_ROOT or not source.is_file():
        raise ValueError(f"source must be a record directly under {TEMP_ROOT}")
    with source.open("r", encoding="utf-8") as stream:
        record = load_json(stream)
    if record.get("qualified") is not True or record.get("conformance_failures") != []:
        raise ValueError("only a passing qualification record can be retained")
    charges = record.get("charges")
    if not isinstance(charges, list) or len(charges) != 3:
        raise ValueError("qualification record must contain exactly three charges")

    record_directory = EVIDENCE_ROOT / "records"
    receipt_directory = EVIDENCE_ROOT / "receipts"
    record_directory.mkdir(parents=True, exist_ok=True)
    receipt_directory.mkdir(parents=True, exist_ok=True)
    retained_record = record_directory / source.name
    _copy_exact(source, retained_record)
    receipts: list[dict[str, str]] = []
    stem = source.stem.removeprefix("provider-route-").replace("-", "")
    for index, charge in enumerate(charges):
        if not isinstance(charge, dict):
            raise ValueError("qualification charge must be an object")
        receipt: dict[str, str] = {}
        for kind in ("stream", "metadata"):
            original = (REPOSITORY_ROOT / str(charge[f"{kind}_file"])).resolve()
            expected_parent = source.with_suffix(".receipts").resolve()
            if original.parent != expected_parent or not original.is_file():
                raise ValueError("qualification receipt is outside its evidence directory")
            suffix = "sse" if kind == "stream" else "json"
            destination = receipt_directory / f"{stem}-{index:02d}.{kind}.{suffix}"
            _copy_exact(original, destination)
            if digest_file(destination) != charge[f"{kind}_digest"]:
                raise ValueError("retained qualification receipt digest differs")
            receipt[f"{kind}_digest"] = str(charge[f"{kind}_digest"])
            receipt[f"{kind}_file"] = str(destination.relative_to(REPOSITORY_ROOT))
        receipts.append(receipt)
    print(
        json.dumps(
            {
                "qualification_record_file": str(
                    retained_record.relative_to(REPOSITORY_ROOT)
                ),
                "qualification_record_digest": digest_file(retained_record),
                "receipts": receipts,
            },
            indent=2,
        )
    )
    return 0


def _copy_exact(source: Path, destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(source.read_bytes())
    temporary.replace(destination)


if __name__ == "__main__":
    raise SystemExit(main())
