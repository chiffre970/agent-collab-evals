"""Live adapter composition without provider calls or compute authority."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from agent_collab_evals.adapters.openrouter import OpenRouterUpstream
from agent_collab_evals.adapters.oci_sandbox import OciSandboxExec, OciSandboxProfile
from agent_collab_evals.adapters.sqlite_budget import SqliteBudgetAccount
from agent_collab_evals.adapters.sqlite_compute_spend import SqliteComputeSpendAuthorizationService
from agent_collab_evals.adapters.sqlite_execution_backend import SqliteComputeBackend
from agent_collab_evals.canonical import digest_value
from agent_collab_evals.candidate_gateway import CandidateToolGateway
from agent_collab_evals.compute_backend import ComputeExecutionStatus, FrozenComputeRunManifest
from agent_collab_evals.pilot_evidence import retain_document
from agent_collab_evals.pilot_spend import PilotSpendEnvelope
from agent_collab_evals.pilot_spend_guard import PilotSpendGuard
from agent_collab_evals.model_gateway import ModelBudgetGateway
from agent_collab_evals.session_identity import SessionIdentityRegistry
from agent_collab_evals.solo_live_configuration import LivePilotConfiguration, build_live_stack, make_live_dependencies, prepare_offline_requests
from agent_collab_evals.solo_pilot_command import run_solo_pilot
from tests.quality_fixture import REPOSITORY_ROOT, real_hidden_quality_bundle


class SoloLiveConfigurationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="ace-", dir="/tmp")
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
            self.assertEqual(stack.hidden_seconds, 18000)
            self.assertEqual(sum(request.maximum_seconds for _, group in groups for request in group), 21600)
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

    def test_live_dependency_factory_requires_budgets_before_loading_private_inputs(self):
        configuration = LivePilotConfiguration.load(self.config(), REPOSITORY_ROOT)
        with self.assertRaisesRegex(ValueError, "dollar limits"):
            make_live_dependencies(configuration, api_key="unused", process_sandbox=None)

    def oci_configuration(self, **changes):
        document = json.loads((REPOSITORY_ROOT / "config/pilots/solo-live-oci-v1.json").read_bytes())
        document.update(hidden_manifest=str(self.bundle.manifest_path), hidden_manifest_digest=self.bundle.manifest_digest)
        return LivePilotConfiguration.load(self.config(**{**document, **changes}), REPOSITORY_ROOT)

    def test_oci_configuration_is_loadable_but_does_not_enable_execution(self):
        configuration = self.oci_configuration()
        self.assertIsInstance(configuration.sandbox, OciSandboxProfile)
        self.assertFalse(configuration.sandbox.execution_authorized)
        self.assertFalse(configuration.document["execution_authorized"])
        with self.assertRaisesRegex(ValueError, "pinned engine identity"):
            make_live_dependencies(configuration, api_key="unused", process_sandbox=None)
        with self.assertRaisesRegex(ValueError, "not execution-authorized"):
            make_live_dependencies(self.oci_configuration(sandbox_engine_identity_digest=digest_value("engine")),
                api_key="unused", process_sandbox=None)
        with self.assertRaisesRegex(ValueError, "not execution-authorized"):
            make_live_dependencies(self.oci_configuration(
                sandbox_profile="config/enforcement_profiles/oci-opencode-podman-development-v2.json",
                sandbox_engine_identity_digest=digest_value("engine")),
                api_key="unused", process_sandbox=None)

    def test_executable_oci_sandbox_covers_the_whole_pilot_schedule(self):
        configuration = self.oci_configuration(
            sandbox_profile="config/enforcement_profiles/oci-opencode-podman-development-v2.json"
        )
        required = configuration.required_sandbox_lifetime_seconds(configuration.document)
        self.assertEqual(required, 22_200)
        self.assertGreaterEqual(configuration.sandbox.timeout_seconds, required)
        with self.assertRaisesRegex(ValueError, "lifetime is shorter"):
            self.oci_configuration(
                sandbox_profile="config/enforcement_profiles/oci-opencode-podman-development-v1.json"
            )
        with patch("agent_collab_evals.solo_live_configuration.OciSandboxProfile.load",
                   return_value=replace(configuration.sandbox, timeout_seconds=300)):
            with self.assertRaisesRegex(ValueError, "lifetime is shorter"):
                self.oci_configuration(
                    sandbox_profile="config/enforcement_profiles/oci-opencode-podman-development-v2.json"
                )

    def test_oci_binding_includes_engine_identity_and_uses_unix_gateways(self):
        engine_digest = digest_value("test-only-engine")
        configuration = self.oci_configuration(sandbox_engine_identity_digest=engine_digest)
        # Construction fixture only: no container or paid transport is started.
        profile = replace(configuration.sandbox, execution_authorized=True, status="registered",
            unresolved_gates=(), image_reference="test/runtime", image_digest=digest_value("image"),
            timeout_seconds=25_200)
        configuration = replace(configuration, sandbox=profile)
        sandbox = OciSandboxExec(profile, Path("/usr/bin/true"), engine_digest)
        different = OciSandboxExec(profile, Path("/usr/bin/true"), digest_value("different-engine"))
        with self.assertRaisesRegex(ValueError, "sandbox differs"):
            make_live_dependencies(configuration, api_key="unused", process_sandbox=different)
        with self.assertRaisesRegex(ValueError, "shared pilot spend guard"):
            make_live_dependencies(configuration, api_key="unused", process_sandbox=sandbox)
        plan = json.loads((REPOSITORY_ROOT / "config/pilots/solo-spend-envelope-v1.json").read_bytes())
        guard = PilotSpendGuard(configuration, PilotSpendEnvelope(self.root / "admission", plan), "pilot-test")
        with patch.object(LivePilotConfiguration, "hidden_bundle", return_value=self.bundle):
            dependencies = make_live_dependencies(configuration, api_key="unused", process_sandbox=sandbox, spend_guard=guard)
        self.assertIs(dependencies.spend_guard, guard)
        model, candidate = dependencies.gateway_options(self.root)
        for options, label in ((model, "model"), (candidate, "candidate")):
            self.assertIs(options["serve_http"], False)
            self.assertEqual(options["unix_socket_root"], self.root / "brokers" / label)
            self.assertEqual(options["advertised_endpoint"], getattr(profile, f"container_{label}_endpoint"))
        self.assertEqual(dependencies.sandbox_evidence["sandbox_profile_digest"], sandbox.profile_digest)
        self.assertNotEqual(sandbox.profile_digest, profile.resolved_digest)
        account = SqliteBudgetAccount(self.root / "budget.sqlite3", configuration.gateway.rate_card)
        with (patch("agent_collab_evals.model_gateway.ThreadingHTTPServer", side_effect=AssertionError("no TCP listener")),
              patch("agent_collab_evals.candidate_gateway.ThreadingHTTPServer", side_effect=AssertionError("no TCP listener"))):
            model_gateway = ModelBudgetGateway(configuration.gateway, account, configuration.model_upstream("unused"), **model)
            self.addCleanup(model_gateway.close)
            candidate_gateway = CandidateToolGateway(Mock(), SessionIdentityRegistry(), **candidate)
            self.addCleanup(candidate_gateway.close)
        self.assertEqual(model_gateway.endpoint, profile.container_model_endpoint)
        self.assertEqual(candidate_gateway.endpoint, profile.container_candidate_endpoint)

    def test_oci_engine_binding_rejects_invalid_or_inapplicable_values(self):
        for value in (True, "sha256:bad", 12):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "engine identity digest"):
                self.oci_configuration(sandbox_engine_identity_digest=value)
        with self.assertRaisesRegex(ValueError, "only valid for an OCI"):
            self.oci_configuration(sandbox_profile=self.document["sandbox_profile"],
                sandbox_engine_identity_digest=digest_value("engine"))
        with self.assertRaisesRegex(ValueError, "configuration v2"):
            LivePilotConfiguration.load(self.config(sandbox_profile="config/enforcement_profiles/oci-opencode-v0-candidate.json"), REPOSITORY_ROOT)

    def test_shared_spend_guard_precedes_real_durable_compute_authority(self):
        configuration = self.oci_configuration()
        plan = json.loads((REPOSITORY_ROOT / "config/pilots/solo-spend-envelope-v1.json").read_bytes())
        envelope = PilotSpendEnvelope(self.root / "admission", plan)
        envelope.reserve(operation_key="route-check", provider="openrouter", purpose="qualification",
            request_digest=digest_value("qualification"), maximum_usd_nanos=100_000_000)
        guard = PilotSpendGuard(configuration, envelope, "offline-check")
        binding = digest_value("retained-run-config")
        with patch("subprocess.run", side_effect=AssertionError("no subprocess expected")):
            stack, _ = build_live_stack(self.root / "stack", configuration, "offline-check", self.bundle, self.policy)
            groups = prepare_offline_requests(stack, configuration)
            key, requests = groups[0]
            request = requests[0]
            route = stack.inventory.register(key, requests)
            with self.assertRaisesRegex(PermissionError, "no matching run"):
                guard.authorize(stack, request, binding)
            guard.begin("offline-check", binding)
            with self.assertRaisesRegex(RuntimeError, "already admitted"):
                PilotSpendGuard(configuration, envelope, "offline-check").begin("offline-check", binding)
            grant = guard.authorize(stack, request, binding)
            source, spend = stack.inventory._source(route)
            self.assertEqual(spend.status(grant.authorization_id), "issued")
            spend.consume(request, source.manifest.transport_profile_digest)
            guard.authorize(stack, request, binding)  # No second debit or consumption.
            self.assertEqual(spend.status(grant.authorization_id), "consumed")
            self.assertEqual(len(envelope.snapshot()["receipts"]), 4)
            self.assertEqual(envelope.snapshot()["remaining_usd_nanos"]["openrouter"], 0)
            self.assertIsNone(envelope.snapshot()["actual_spend_usd_nanos"])
            for key, requests in groups:
                route = stack.inventory.register(key, requests)
                source, spend = stack.inventory._source(route)
                for request in requests:
                    grant = guard.authorize(stack, request, binding)
                    if spend.status(grant.authorization_id) == "issued":
                        spend.consume(request, source.manifest.transport_profile_digest)
            snapshot = envelope.snapshot()
            self.assertEqual(len(snapshot["receipts"]), 15)
            self.assertEqual(snapshot["reserved_usd_nanos"]["modal"], 10_192_960_000)
            self.assertEqual(snapshot["remaining_usd_nanos"]["modal"], 1_757_040_000)
            # A second run cannot reclaim the journal's single attempt.
            full = replace(configuration, document={**configuration.document, "model_limit_usd_nanos": 3_000_000_000})
            with self.assertRaisesRegex(RuntimeError, "retry differs"):
                PilotSpendGuard(full, envelope, "second-run").begin("second-run", digest_value("other"))

    def test_failed_compute_issuance_does_not_refund_its_admission(self):
        configuration = self.oci_configuration()
        plan = json.loads((REPOSITORY_ROOT / "config/pilots/solo-spend-envelope-v1.json").read_bytes())
        envelope = PilotSpendEnvelope(self.root / "admission", plan)
        guard = PilotSpendGuard(configuration, envelope, "offline-check")
        binding = digest_value("run-config")
        guard.begin("offline-check", binding)
        stack, _ = build_live_stack(self.root / "stack", configuration, "offline-check", self.bundle, self.policy)
        request = prepare_offline_requests(stack, configuration)[0][1][0]
        with patch.object(stack.inventory, "authorize", side_effect=RuntimeError("issuance interrupted")):
            with self.assertRaisesRegex(RuntimeError, "issuance interrupted"):
                guard.authorize(stack, request, binding)
        restarted = PilotSpendEnvelope(self.root / "admission", plan)
        self.assertEqual(restarted.snapshot()["reserved_usd_nanos"]["modal"], 1_766_080_000)


if __name__ == "__main__":
    unittest.main()
