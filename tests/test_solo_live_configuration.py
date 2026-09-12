"""Live adapter composition without provider calls or compute authority."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agent_collab_evals.adapters.openrouter import OpenRouterUpstream
from agent_collab_evals.adapters.sqlite_compute_spend import SqliteComputeSpendAuthorizationService
from agent_collab_evals.adapters.sqlite_execution_backend import SqliteComputeBackend
from agent_collab_evals.canonical import digest_value
from agent_collab_evals.compute_backend import ComputeExecutionStatus, FrozenComputeRunManifest
from agent_collab_evals.pilot_evidence import retain_document
from agent_collab_evals.solo_live_configuration import LivePilotConfiguration, build_live_stack, prepare_offline_requests
from agent_collab_evals.solo_pilot_command import run_solo_pilot
from tests.quality_fixture import REPOSITORY_ROOT, real_hidden_quality_bundle


class SoloLiveConfigurationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.campaign, self.bundle, self.policy = real_hidden_quality_bundle(self.root / "bundle")
        self.document = json.loads((REPOSITORY_ROOT / "config/pilots/solo-live-v1.json").read_bytes())
        self.document.update(hidden_manifest=str(self.bundle.manifest_path), hidden_manifest_digest=self.bundle.manifest_digest)

    def config(self, **changes):
        path = self.root / f"config-{len(list(self.root.glob('config-*.json')))}.json"
        retain_document(path, {**self.document, **changes})
        return path

    def test_live_factories_plan_every_phase_with_real_profiles_and_no_authority(self):
        configuration = LivePilotConfiguration.load(self.config(), REPOSITORY_ROOT)
        # Only fixture data differs. Use the production profile factories,
        # request planners, durable backends, and authorization service unchanged.
        with (patch("subprocess.run", side_effect=AssertionError("no subprocess expected")) as process,
              patch("http.client.HTTPSConnection", side_effect=AssertionError("no network expected")) as network):
            stack, adapters = build_live_stack(self.root / "stack", configuration, "offline-check", self.bundle, self.policy)
            groups = prepare_offline_requests(stack, configuration)
            self.assertEqual(len(adapters), 6)
            self.assertEqual(sum(len(group) for _, group in groups), 12)
            self.assertEqual(stack.hidden_seconds, 9600)
            self.assertEqual(sum(request.maximum_seconds for _, group in groups for request in group), 13200)
            self.assertIsInstance(configuration.model_upstream("test-only-credential"), OpenRouterUpstream)
            for index, (key, requests) in enumerate(groups):
                adapter = adapters[key]
                route = self.root / f"route-{index}"
                manifest = FrozenComputeRunManifest.load_or_create(route / "manifest.json",
                    campaign_run_id=requests[0].campaign_run_id, compute_enabled=True,
                    transport_profile_digest=adapter.transport_profile_digest,
                    backend_profile_digest=adapter.backend_profile_digest, requests=requests)
                spend = SqliteComputeSpendAuthorizationService(route / "spend.sqlite3", manifest)
                transport, evidence = adapter.transport(route, spend), adapter.evidence(route)
                backend = SqliteComputeBackend(route / "compute.sqlite3", transport, evidence, manifest)
                self.assertEqual(backend.profile_digest, adapter.backend_profile_digest)
                for request in requests:
                    with self.subTest(phase=key, request=request.execution_key):
                        result = backend.submit(request, self.campaign.reference_candidate_path.read_bytes())
                        self.assertEqual(result.status, ComputeExecutionStatus.FAILED)
                        self.assertIn("authorization", result.failure)
                        self.assertIsNone(spend.request_status(request, transport.profile_digest))
            process.assert_not_called()
            network.assert_not_called()

    def test_approval_flag_cannot_enable_live_execution(self):
        with self.assertRaisesRegex(ValueError, "execution is disabled"):
            LivePilotConfiguration.load(self.config(execution_authorized=True), REPOSITORY_ROOT)
        with self.assertRaisesRegex(ValueError, "execution is disabled"):
            run_solo_pilot(self.config(), self.root, "paid-run")
        self.assertFalse((self.root / "paid-run").exists())

    def test_invalid_budgets_and_phase_allocations_fail_closed(self):
        for changes in ({"model_limit_usd_nanos": True}, {"modal_limit_usd_nanos": -1},
                        {"phase_seconds": {**self.document["phase_seconds"], "quality": 0}},
                        {"task_seed": False}, {"api_key": "not-a-config-field"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                LivePilotConfiguration.load(self.config(**changes), REPOSITORY_ROOT)

    def test_synthetic_model_profile_is_not_a_live_provider(self):
        with self.assertRaisesRegex(ValueError, "development model profiles"):
            LivePilotConfiguration.load(self.config(gateway_profile="config/gateway_profiles/openrouter-deepinfra-local-conformance-v0.json"), REPOSITORY_ROOT)

    def test_runtime_and_gateway_must_resolve_the_same_model_profile(self):
        original = LivePilotConfiguration.load(self.config(), REPOSITORY_ROOT)
        with patch("agent_collab_evals.solo_live_configuration.OpenCodeRuntimeProfile.load",
                   return_value=replace(original.runtime, agent_inference_digest=digest_value("different"))):
            with self.assertRaisesRegex(ValueError, "model/provider profiles differ"):
                LivePilotConfiguration.load(self.config(), REPOSITORY_ROOT)

    def test_hidden_loader_rejects_a_bundle_outside_the_fixed_quality_policy(self):
        configuration = LivePilotConfiguration.load(self.config(), REPOSITORY_ROOT)
        with self.assertRaises(ValueError):
            configuration.hidden_bundle()


if __name__ == "__main__":
    unittest.main()
