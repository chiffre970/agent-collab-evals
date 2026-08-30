from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.campaigns.serving_workload import (
    HiddenWorkloadError,
    HiddenWorkloadExpectations,
    load_hidden_workload,
    materialize_hidden_workload,
)
from agent_collab_evals.canonical import digest_bytes, digest_value


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class HiddenServingWorkloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.campaign = ModelServingCampaign.load(
            REPOSITORY_ROOT / "campaigns/model_serving_v0/campaign.toml"
        )
        self.quality_workload = self.root / "source-quality.json"
        self.quality_workload.write_bytes(b'{"private":"quality"}\n')
        self.expectations = HiddenWorkloadExpectations(
            campaign_manifest_digest=self.campaign.manifest_digest,
            hidden_contract_digest=self.campaign.transitive_digests[
                "hidden_contract"
            ],
            quality_profile_digest=digest_value({"profile": "quality"}),
            quality_policy_digest=digest_value({"policy": "quality"}),
            quality_workload_digest=digest_bytes(
                self.quality_workload.read_bytes()
            ),
            public_correctness_digest=self.campaign.transitive_digests[
                "public_correctness"
            ],
            public_performance_digest=self.campaign.transitive_digests[
                "public_profile"
            ],
            required_gates=tuple(self.campaign.hidden_contract()["required_gates"]),
        )
        self.seed = bytes(range(32))
        self.requests = (
            {
                "case_id": "private-case",
                "body": {
                    "model": "target-model",
                    "messages": [{"role": "user", "content": "private"}],
                    "seed": 42,
                    "stream": False,
                    "temperature": 0.6,
                    "top_p": 0.95,
                    "top_k": 20,
                    "min_p": 0.0,
                    "max_tokens": 512,
                    "chat_template_kwargs": {"enable_thinking": True},
                },
            },
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_materializes_and_resolves_a_disjoint_private_bundle(self) -> None:
        destination = self.root / "bundle"
        first = materialize_hidden_workload(
            destination,
            expectations=self.expectations,
            public_plan=self.campaign.benchmark_plan(),
            selection_seed=self.seed,
            seed_bytes=32,
            quality_workload_path=self.quality_workload,
            quality_requests=self.requests,
        )
        second = materialize_hidden_workload(
            destination,
            expectations=self.expectations,
            public_plan=self.campaign.benchmark_plan(),
            selection_seed=self.seed,
            seed_bytes=32,
            quality_workload_path=self.quality_workload,
            quality_requests=self.requests,
        )

        self.assertEqual(first, second)
        self.assertEqual(set(first.resource_paths), {
            "correctness_requests",
            "performance_profile",
            "quality_requests",
            "quality_workload",
        })
        self.assertNotEqual(
            first.resource_digests["correctness_requests"],
            self.expectations.public_correctness_digest,
        )
        self.assertNotEqual(
            first.resource_digests["performance_profile"],
            self.expectations.public_performance_digest,
        )
        manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
        self.assertFalse(manifest["agent_visible"])
        self.assertNotIn(self.seed.hex(), first.manifest_path.read_text())
        quality_requests = json.loads(
            first.resource_paths["quality_requests"].read_text(encoding="utf-8")
        )
        body = quality_requests["requests"][0]["body"]
        self.assertEqual(body["temperature_milli"], 600)
        self.assertEqual(body["top_p_milli"], 950)
        self.assertNotIn("temperature", body)
        for path in (*first.resource_paths.values(), first.manifest_path):
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        registered = load_hidden_workload(
            first.manifest_path,
            self.expectations,
            self.campaign.benchmark_plan(),
            registered_manifest_digest=first.manifest_digest,
        )
        self.assertEqual(registered, first)

    def test_tampering_and_public_workload_reuse_fail_closed(self) -> None:
        bundle = materialize_hidden_workload(
            self.root / "bundle",
            expectations=self.expectations,
            public_plan=self.campaign.benchmark_plan(),
            selection_seed=self.seed,
            seed_bytes=32,
            quality_workload_path=self.quality_workload,
            quality_requests=self.requests,
        )
        duplicate = replace(
            self.expectations,
            public_correctness_digest=bundle.resource_digests[
                "correctness_requests"
            ],
        )
        with self.assertRaisesRegex(HiddenWorkloadError, "duplicates public"):
            load_hidden_workload(
                bundle.manifest_path,
                duplicate,
                self.campaign.benchmark_plan(),
            )

        bundle.resource_paths["quality_requests"].write_bytes(b"tampered\n")
        with self.assertRaisesRegex(HiddenWorkloadError, "digest differs"):
            load_hidden_workload(
                bundle.manifest_path,
                self.expectations,
                self.campaign.benchmark_plan(),
            )

    def test_write_once_bundle_rejects_a_changed_private_seed(self) -> None:
        destination = self.root / "bundle"
        materialize_hidden_workload(
            destination,
            expectations=self.expectations,
            public_plan=self.campaign.benchmark_plan(),
            selection_seed=self.seed,
            seed_bytes=32,
            quality_workload_path=self.quality_workload,
            quality_requests=self.requests,
        )
        with self.assertRaisesRegex(HiddenWorkloadError, "already exists"):
            materialize_hidden_workload(
                destination,
                expectations=self.expectations,
                public_plan=self.campaign.benchmark_plan(),
                selection_seed=bytes(reversed(self.seed)),
                seed_bytes=32,
                quality_workload_path=self.quality_workload,
                quality_requests=self.requests,
            )

    def test_registered_manifest_digest_rejects_coherent_replacement(self) -> None:
        bundle = materialize_hidden_workload(
            self.root / "bundle",
            expectations=self.expectations,
            public_plan=self.campaign.benchmark_plan(),
            selection_seed=self.seed,
            seed_bytes=32,
            quality_workload_path=self.quality_workload,
            quality_requests=self.requests,
        )
        original_digest = bundle.manifest_digest
        manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
        changed = b'{"private":"replacement"}\n'
        bundle.resource_paths["quality_workload"].write_bytes(changed)
        replacement_digest = digest_bytes(changed)
        manifest["resources"]["quality_workload"]["digest"] = replacement_digest
        manifest["digests"]["quality_workload"] = replacement_digest
        bundle.manifest_path.write_text(
            json.dumps(manifest, separators=(",", ":"), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        changed_expectations = replace(
            self.expectations, quality_workload_digest=replacement_digest
        )
        with self.assertRaisesRegex(HiddenWorkloadError, "manifest digest differs"):
            load_hidden_workload(
                bundle.manifest_path,
                changed_expectations,
                self.campaign.benchmark_plan(),
                registered_manifest_digest=original_digest,
            )

    def test_private_bundle_rejects_broad_file_permissions(self) -> None:
        bundle = materialize_hidden_workload(
            self.root / "bundle",
            expectations=self.expectations,
            public_plan=self.campaign.benchmark_plan(),
            selection_seed=self.seed,
            seed_bytes=32,
            quality_workload_path=self.quality_workload,
            quality_requests=self.requests,
        )
        os.chmod(bundle.resource_paths["correctness_requests"], 0o644)
        with self.assertRaisesRegex(HiddenWorkloadError, "permissions are too broad"):
            load_hidden_workload(
                bundle.manifest_path,
                self.expectations,
                self.campaign.benchmark_plan(),
                registered_manifest_digest=bundle.manifest_digest,
            )


if __name__ == "__main__":
    unittest.main()
