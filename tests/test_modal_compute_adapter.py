from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_collab_evals.adapters.local_measurements import LocalMeasurementBundleStore
from agent_collab_evals.adapters.modal_vllm_compute import (
    ModalVllmCliTransport,
    ModalVllmComputeProfile,
    ModalVllmEvidenceResolver,
    _measurement_id,
    _minimal_modal_environment,
    _used_seconds,
)
from agent_collab_evals.adapters.sqlite_execution_backend import (
    SqliteComputeBackend,
)
from agent_collab_evals.adapters.sqlite_compute_spend import (
    SqliteComputeSpendAuthorizationService,
)
from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.canonical import digest_bytes
from agent_collab_evals.compute_backend import (
    ComputeExecutionRequest,
    ComputeExecutionStatus,
    FrozenComputeRunManifest,
)
from agent_collab_evals.evaluation import EvaluationScope


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = REPOSITORY_ROOT / "config/compute/modal-vllm-development.json"
CAMPAIGN_PATH = REPOSITORY_ROOT / "campaigns/model_serving_v0/campaign.toml"


class ModalComputeAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.state_root = Path(self._temporary.name)
        self.profile = ModalVllmComputeProfile.load(
            PROFILE_PATH, repository_root=REPOSITORY_ROOT
        )
        self.campaign = ModelServingCampaign.load(CAMPAIGN_PATH)
        self.candidate = self.campaign.reference_candidate_path.read_bytes()
        descriptor = self.campaign.validate_reference_candidate()
        self.request = ComputeExecutionRequest(
            execution_key="modal-development-reference",
            campaign_run_id="modal-development-run",
            reservation_id="evaluation-" + "2" * 32,
            scope=EvaluationScope.VISIBLE,
            candidate_digest=digest_bytes(self.candidate),
            candidate_manifest_digest=descriptor.manifest_digest,
            evaluator_profile_digest=self.profile.evaluator_profile_digest,
            maximum_seconds=1_800,
        )
        authorization_profile = (
            SqliteComputeSpendAuthorizationService.profile_digest_for()
        )
        transport_profile = ModalVllmCliTransport.profile_digest_for(
            self.profile.digest,
            REPOSITORY_ROOT / ".venv/bin/modal",
            authorization_profile,
        )
        evidence_profile = ModalVllmEvidenceResolver.profile_digest_for(
            self.profile.digest
        )
        self.manifest = FrozenComputeRunManifest.load_or_create(
            self.state_root / "compute-run-manifest.json",
            campaign_run_id=self.request.campaign_run_id,
            compute_enabled=True,
            transport_profile_digest=transport_profile,
            backend_profile_digest=SqliteComputeBackend.profile_digest_for(
                transport_profile, evidence_profile
            ),
            requests=(self.request,),
        )
        self.authorizations = SqliteComputeSpendAuthorizationService(
            self.state_root / "compute-spend.sqlite3", self.manifest
        )
        self.transport = ModalVllmCliTransport(
            self.profile,
            REPOSITORY_ROOT,
            self.state_root,
            REPOSITORY_ROOT / ".venv/bin/modal",
            self.authorizations,
        )
        self.authorization = self.authorizations.issue(
            self.request,
            self.transport.profile_digest,
            "unit-test-approved",
        )
        self.resolver = ModalVllmEvidenceResolver(
            self.profile,
            REPOSITORY_ROOT,
            self.state_root,
            self.transport.profile_digest,
        )
        self.backend = SqliteComputeBackend(
            self.state_root / "executions.sqlite3",
            self.transport,
            self.resolver,
            self.manifest,
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_profile_pins_script_campaign_client_and_environment(self) -> None:
        self.assertEqual(self.profile.modal_environment, "dev")
        self.assertEqual(self.profile.modal_client_version, "1.5.4")
        self.assertEqual(
            self.profile.campaign_manifest_digest,
            self.campaign.manifest_digest,
        )
        environment = _minimal_modal_environment()
        self.assertNotIn("OPENROUTER_API_KEY", environment)
        self.assertNotIn("HF_TOKEN", environment)

    def test_transport_detaches_the_app_around_the_spawned_function(self) -> None:
        command = self.transport._command(
            self.state_root / "candidate.json",
            "measurement",
            dispatch_only=True,
        )
        self.assertEqual(command[1:5], ("run", "--detach", "-e", "dev"))
        self.assertIn("--dispatch-only", command)

    def test_transport_rejects_dispatch_without_spend_authorization(self) -> None:
        root = self.state_root / "unauthorized"
        manifest = FrozenComputeRunManifest.load_or_create(
            root / "compute-run-manifest.json",
            campaign_run_id=self.request.campaign_run_id,
            compute_enabled=True,
            transport_profile_digest=self.transport.profile_digest,
            backend_profile_digest=self.backend.profile_digest,
            requests=(self.request,),
        )
        authorizations = SqliteComputeSpendAuthorizationService(
            root / "compute-spend.sqlite3", manifest
        )
        transport = ModalVllmCliTransport(
            self.profile,
            REPOSITORY_ROOT,
            root,
            REPOSITORY_ROOT / ".venv/bin/modal",
            authorizations,
        )
        with self.assertRaisesRegex(RuntimeError, "spend authorization"):
            transport.dispatch(self.request, self.candidate)

    def test_spend_authorization_is_durable_and_single_use(self) -> None:
        restarted = SqliteComputeSpendAuthorizationService(
            self.state_root / "compute-spend.sqlite3", self.manifest
        )
        consumed = restarted.consume(
            self.request, self.transport.profile_digest
        )
        self.assertEqual(consumed, self.authorization)
        self.assertEqual(restarted.status(consumed.authorization_id), "consumed")
        with self.assertRaisesRegex(RuntimeError, "unused durable"):
            restarted.consume(self.request, self.transport.profile_digest)

    def test_real_transport_contract_composes_with_durable_backend(self) -> None:
        measurement_id = _measurement_id(self.request)
        function_call_id = "fc-development-contract"

        def fake_dispatch(*args, **kwargs):
            path = (
                self.state_root
                / "measurements/.dispatch"
                / measurement_id
                / "repetition-0001-attempt-01.json"
            )
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "measurement_id": measurement_id,
                        "campaign_manifest_digest": self.campaign.manifest_digest,
                        "candidate_manifest_digest": (
                            self.request.candidate_manifest_digest
                        ),
                        "repetition": 1,
                        "attempt": 1,
                        "function_call_id": function_call_id,
                        "git_commit": "f" * 40,
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args[0], 0, stdout="dispatched")

        with patch(
            "agent_collab_evals.adapters.modal_vllm_compute.subprocess.run",
            side_effect=fake_dispatch,
        ):
            dispatched = self.backend.submit(self.request, self.candidate)
        self.assertEqual(dispatched.status, ComputeExecutionStatus.DISPATCHED)
        self.assertEqual(dispatched.external_call_id, function_call_id)

        normalized = {
            "campaign_manifest_digest": self.campaign.manifest_digest,
            "candidate_manifest_digest": self.request.candidate_manifest_digest,
            "modal_function_call_id": function_call_id,
            "repetition": 1,
            "attempt": 1,
            "valid": True,
            "failure": None,
            "parse_errors": [],
            "environment_errors": [],
            "remote_receipt": {"timing": {"function_body_ms": 7_001}},
            "performance_score": {
                "eligible": True,
                "scalar_ppm": 1_002_000,
            },
            "platform_build": {
                "git_commit": "f" * 40,
                "modal_client_version": self.profile.modal_client_version,
            },
            "durable_evidence": {
                "volume_name": self.profile.evidence_volume,
            },
        }
        normalized["durable_evidence"]["normalized_digest"] = digest_bytes(
            json.dumps(
                normalized,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
        LocalMeasurementBundleStore(self.state_root / "measurements").save(
            measurement_id,
            1,
            normalized,
            {"point.json": b'{"ok":true}'},
        )
        completed = self.backend.collect(self.request, timeout_seconds=0)
        self.assertEqual(completed.status, ComputeExecutionStatus.COMPLETE)
        self.assertEqual(completed.used_seconds, 8)
        _, evidence = self.backend.resolve(self.request)
        self.assertEqual(
            evidence["result"]["performance_score"]["scalar_ppm"],
            1_002_000,
        )

    def test_profile_rejects_changed_script_digest(self) -> None:
        changed = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        changed["modal_script_digest"] = "sha256:" + "0" * 64
        path = self.state_root / "changed-profile.json"
        path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "script digest differs"):
            ModalVllmComputeProfile.load(path, repository_root=REPOSITORY_ROOT)

    def test_terminal_remote_failure_is_reconcilable_without_scored_evidence(
        self,
    ) -> None:
        measurement_id = _measurement_id(self.request)
        function_call_id = "fc-terminal-failure"
        dispatch_path = (
            self.state_root
            / "measurements/.dispatch"
            / measurement_id
            / "repetition-0001-attempt-01.json"
        )
        dispatch_path.parent.mkdir(parents=True)
        dispatch_path.write_text(
            json.dumps(
                {
                    "measurement_id": measurement_id,
                    "campaign_manifest_digest": self.campaign.manifest_digest,
                    "candidate_manifest_digest": (
                        self.request.candidate_manifest_digest
                    ),
                    "repetition": 1,
                    "attempt": 1,
                    "function_call_id": function_call_id,
                    "git_commit": "f" * 40,
                }
            ),
            encoding="utf-8",
        )
        failure = {
            "campaign_manifest_digest": self.campaign.manifest_digest,
            "candidate_manifest_digest": self.request.candidate_manifest_digest,
            "modal_function_call_id": function_call_id,
            "repetition": 1,
            "attempt": 1,
            "valid": False,
            "failure": {
                "stage": "remote_invocation",
                "type": "RemoteError",
                "message": "app stopped",
            },
            "platform_build": {
                "git_commit": "f" * 40,
                "modal_client_version": self.profile.modal_client_version,
            },
            "remote_receipt": None,
            "performance_score": None,
        }
        LocalMeasurementBundleStore(self.state_root / "measurements").save(
            measurement_id,
            1,
            failure,
            {},
        )

        pointer, status, used_seconds, message = self.resolver.pointer(
            self.request, function_call_id
        )

        self.assertEqual(status, ComputeExecutionStatus.FAILED)
        self.assertEqual(used_seconds, self.request.maximum_seconds)
        self.assertIn("RemoteError", message or "")
        repeated = self.resolver.pointer(self.request, function_call_id)
        self.assertEqual(repeated[0], pointer)

    def test_observed_compute_overrun_is_not_clamped(self) -> None:
        normalized = {"remote_receipt": {"timing": {"function_body_ms": 120_001}}}
        self.assertEqual(_used_seconds(normalized, 60), 121)


if __name__ == "__main__":
    unittest.main()
