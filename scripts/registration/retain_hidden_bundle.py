"""Retain and verify the registered evaluator-private hidden bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_collab_evals.hidden_bundle_retention import (
    HiddenBundleRetentionProfile,
    HiddenBundleRetentionService,
    ModalVolumeObjectStore,
    write_retention_receipt_once,
)
from agent_collab_evals.study_registration import StudyCompositionCandidate


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--composition",
        type=Path,
        default=Path(
            "config/studies/model-serving-flash-v0.registration-candidate.json"
        ),
    )
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path(
            "config/retention_profiles/model-serving-hidden-study-v1.json"
        ),
    )
    parser.add_argument(
        "--hidden-manifest",
        type=Path,
        default=Path(
            "tmp/evaluator-private/model-serving-hidden-study-v1/manifest.json"
        ),
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        default=Path(
            "evidence/hidden_workloads/model-serving-hidden-study-v1.json"
        ),
    )
    parser.add_argument("--create-volume", action="store_true")
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    composition = StudyCompositionCandidate.load(
        (REPOSITORY_ROOT / arguments.composition).resolve(),
        repository_root=REPOSITORY_ROOT,
    )
    bundle = composition.verify_hidden_bundle(
        (REPOSITORY_ROOT / arguments.hidden_manifest).resolve()
    )
    profile = HiddenBundleRetentionProfile.load(
        (REPOSITORY_ROOT / arguments.profile).resolve()
    )
    store = ModalVolumeObjectStore(
        profile.volume_name,
        profile.modal_environment,
        create_if_missing=arguments.create_volume,
    )
    receipt = HiddenBundleRetentionService(profile, store).retain(bundle)
    write_retention_receipt_once(
        (REPOSITORY_ROOT / arguments.receipt).resolve(), receipt
    )
    print(
        json.dumps(
            {
                "ok": True,
                "profile_digest": profile.digest,
                "receipt_digest": receipt.digest,
                "manifest_digest": bundle.manifest_digest,
                "volume_name": profile.volume_name,
                "namespace": profile.namespace,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
