"""Real V3 preparation, context checks, and two-job ledger composition."""

from datetime import UTC, datetime, timedelta
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from agent_collab_evals.adapters.modal_paired_serving import ModalPairedServingTransport
from agent_collab_evals.adapters.sqlite_compute_spend import SqliteComputeSpendAuthorizationService
from agent_collab_evals.calibration_context import check_calibration_context
from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.campaigns.serving_quality import build_quality_requests, load_quality_workload
from agent_collab_evals.campaigns.serving_workload import HiddenWorkloadExpectations, materialize_hidden_workload
from agent_collab_evals.canonical import digest_file, parse_json
from agent_collab_evals.campaigns.serving_paired_result import score_paired_execution
from agent_collab_evals.compute_backend import FrozenComputeRunManifest
from agent_collab_evals.peer_live_configuration import stock_control_components
from agent_collab_evals.peer_stock_control import run_stock_control, stock_batch
from agent_collab_evals.pilot_evidence import retain_bytes, retain_document
from agent_collab_evals.pilot_spend import PilotSpendEnvelope
from agent_collab_evals.serving_calibration import prepare_serving_calibration
from agent_collab_evals.solo_live_configuration import LivePilotConfiguration
from tests.quality_fixture import REPOSITORY_ROOT, real_hidden_quality_bundle
from tests.test_paired_serving_stack import _RetainedPairedTransport, paired_fixture


HAS_TOKENIZER = all(importlib.util.find_spec(name) for name in ("tokenizers", "jinja2"))


class ServingCalibrationTests(unittest.TestCase):
    def setUp(self):
        (REPOSITORY_ROOT / ".private").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="calibration-test-", dir=REPOSITORY_ROOT / ".private")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        _, original, _ = real_hidden_quality_bundle(self.root / "sources")
        source_pack = self.root / "source-pack"
        shutil.copytree(REPOSITORY_ROOT / "campaigns/model_serving_v0", source_pack,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        policy_file = source_pack / "evaluator/quality_policy.toml"
        policy_file.write_text(policy_file.read_text().replace(
            "sha256:9f129b49dda34d6af30d3e6a59f6ea945756b67fae32fbefca0ed9444492e8f4",
            original.resource_digests["quality_workload"]))
        contract_file = source_pack / "evaluator/hidden_contract.json"
        contract = parse_json(contract_file.read_text())
        contract["quality_contract"].update(quality_policy_digest=digest_file(policy_file),
            quality_workload_digest=original.resource_digests["quality_workload"])
        contract_file.write_bytes(json.dumps(contract).encode())
        campaign = ModelServingCampaign.load(source_pack / "campaign.toml")
        profile = campaign.quality_profile()
        workload = load_quality_workload(original.resource_paths["quality_workload"], profile)
        expectations = HiddenWorkloadExpectations(campaign.manifest_digest,
            campaign.transitive_digests["hidden_contract"], profile.digest, campaign.quality_policy().digest,
            workload.digest, campaign.transitive_digests["public_correctness"],
            campaign.transitive_digests["public_profile"], tuple(contract["required_gates"]))
        hidden = materialize_hidden_workload(self.root / "source-hidden", expectations=expectations,
            public_plan=campaign.benchmark_plan(), selection_seed=bytes(reversed(range(32))), seed_bytes=32,
            quality_workload_path=original.resource_paths["quality_workload"],
            quality_requests=build_quality_requests(profile, workload, served_model_name="target-model"))
        public = parse_json((REPOSITORY_ROOT / "config/compute/modal-vllm-development.json").read_text())
        public.update(campaign_manifest=str((source_pack / "campaign.toml").relative_to(REPOSITORY_ROOT)),
            campaign_manifest_digest=campaign.manifest_digest,
            performance_profile=str((source_pack / "workloads/public/profile.toml").relative_to(REPOSITORY_ROOT)))
        retain_document(self.root / "source-compute.json", public)
        config = parse_json((REPOSITORY_ROOT / "config/pilots/solo-live-oci-v1.json").read_text())
        config.update(campaign=public["campaign_manifest"],
            public_compute_profile=str((self.root / "source-compute.json").relative_to(REPOSITORY_ROOT)),
            hidden_manifest=str(hidden.manifest_path.relative_to(REPOSITORY_ROOT)), hidden_manifest_digest=hidden.manifest_digest)
        retain_document(self.root / "source-configuration.json", config)
        recipe = parse_json((REPOSITORY_ROOT / "config/calibration/model-serving-v3.json").read_text())
        recipe.update(source_configuration=str((self.root / "source-configuration.json").relative_to(REPOSITORY_ROOT)),
            source_quality_policy_digest=campaign.quality_policy().digest)
        self.recipe = self.root / "recipe.json"
        retain_document(self.recipe, recipe)
        self.target = self.root / "v3"
        self.before = {p: digest_file(p) for p in (*source_pack.rglob("*"), *hidden.manifest_path.parent.rglob("*")) if p.is_file()}
        _RetainedPairedTransport.dispatches = []
        _RetainedPairedTransport.wrong_correctness = False
        _RetainedPairedTransport.wrong_reference = False
        _RetainedPairedTransport.speedup = 1

    def prepare(self):
        return prepare_serving_calibration(self.recipe, REPOSITORY_ROOT, self.target, "v3-diagnostic")

    def statuses(self, preparation):
        pin, = preparation["compute_manifests"]
        manifest = FrozenComputeRunManifest.load(Path(pin["file"]), expected_digest=pin["digest"])
        service = SqliteComputeSpendAuthorizationService(manifest.path.parent / "spend.sqlite3", manifest)
        return [service.request_status(r, manifest.transport_profile_digest) for r in manifest.requests()]

    def test_real_factory_preserves_cases_seeds_and_source_and_grants_nothing(self):
        with patch.object(ModalPairedServingTransport, "dispatch", side_effect=AssertionError("no dispatch allowed")):
            result = self.prepare()
            self.assertEqual(self.prepare(), result)
        preparation = result["preparation"]
        self.assertEqual(preparation["planned_gpu_calls"], 2)
        self.assertEqual(preparation["reservation"]["reserved_seconds"], 6000)
        self.assertEqual(preparation["reserved_function_seconds"], 6000)
        self.assertEqual(preparation["modal_allowance_usd_nanos"], 3_275_968_000)
        self.assertEqual(self.statuses(preparation), [None, None])
        self.assertFalse(result["execution_authorized"])
        self.assertTrue(result["same_quality_cases_and_seeds"])
        self.assertTrue(all(digest_file(p) == d for p, d in self.before.items()))
        configuration = LivePilotConfiguration.load(self.target / "configuration.json", REPOSITORY_ROOT)
        stack, _, requests, _ = stock_control_components(self.target / "control", configuration,
            "v3-diagnostic", configuration.hidden_bundle(), configuration.campaign.quality_policy(), diagnostic=True)
        spec = stack.hidden.profile.spec(requests[1])
        self.assertEqual(spec["benchmark"]["request_timeout_seconds"], 600)
        self.assertEqual({r["body"]["max_tokens"] for r in spec["benchmark"]["requests"]}, {512, 8192})
        self.assertEqual(configuration.campaign.quality_policy().reference_receipt_digests, ())
        self.assertEqual(configuration.campaign.quality_policy().clean_control_receipt_digests, ())

    def test_revision_rejects_wrong_pins_state_root_and_changed_existing_inputs(self):
        with self.assertRaisesRegex(ValueError, "evaluator-private"):
            prepare_serving_calibration(self.recipe, REPOSITORY_ROOT, self.root.parent.parent / "public-calibration", "bad")
        wrong = parse_json(self.recipe.read_text())
        wrong["source_quality_policy_digest"] = "sha256:" + "0" * 64
        retain_document(self.root / "wrong-recipe.json", wrong)
        with self.assertRaisesRegex(ValueError, "predecessor"):
            prepare_serving_calibration(self.root / "wrong-recipe.json", REPOSITORY_ROOT, self.target, "bad")
        self.prepare()
        (self.target / "pack/reference/candidate.json").write_text("tampered")
        with self.assertRaisesRegex(RuntimeError, "different content"):
            self.prepare()

    def test_v3_raw_truncation_never_passes_even_with_an_answer_tag(self):
        self.prepare()
        configuration = LivePilotConfiguration.load(self.target / "configuration.json", REPOSITORY_ROOT)
        stack, _, requests, _ = stock_control_components(self.target / "control", configuration,
            "v3-diagnostic", configuration.hidden_bundle(), configuration.campaign.quality_policy(), diagnostic=True)
        request = requests[1]
        candidate = configuration.campaign.reference_candidate_path.read_bytes()
        profile = stack.hidden.profile
        remote, raw = paired_fixture(profile, request, candidate, profile.spec(request))
        name = next(n for n in raw if n.startswith("candidate-"))
        response = parse_json(raw[name].decode())
        response["choices"][0]["finish_reason"] = "length"
        raw[name] = json.dumps(response).encode()
        result = score_paired_execution(profile, request, remote, raw, parse_json(candidate.decode()))
        self.assertEqual(result["scores"]["candidate"]["pass_count"], 63)
        self.assertEqual(result["generation"]["candidate"]["truncated_responses"], 1)
        self.assertEqual(result["generation"]["candidate"]["missing_final_answers"], 1)

    def tokenizer_fixture(self):
        from tokenizers import Tokenizer, models, pre_tokenizers
        tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
        tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
        retain_bytes(self.target / "tokenizer/tokenizer.json", tokenizer.to_str().encode())
        retain_document(self.target / "tokenizer/tokenizer_config.json", {
            "chat_template": "{% for message in messages %}{{ message.content }}{% endfor %}{% if enable_thinking %} think{% endif %}",
        })

    @unittest.skipUnless(HAS_TOKENIZER, "install the calibration optional dependencies")
    def test_context_replays_actual_template_lengths_without_fetch_or_model_calls(self):
        self.prepare()
        with patch("agent_collab_evals.calibration_context.urlopen", side_effect=AssertionError("no network allowed")):
            with self.assertRaisesRegex(ValueError, "snapshot is missing"):
                check_calibration_context(self.target, REPOSITORY_ROOT)
            self.tokenizer_fixture()
            result = check_calibration_context(self.target, REPOSITORY_ROOT)
            self.assertEqual(check_calibration_context(self.target, REPOSITORY_ROOT), result)
        self.assertEqual(result["phases"]["quality"]["case_count"], 64)
        self.assertEqual(result["phases"]["correctness"]["case_count"], 8)
        self.assertTrue(result["all_case_contexts_fit"])
        self.assertFalse(result["completion_guaranteed_by_timeouts"])
        self.assertEqual(result["quality_request_timeout_envelope_seconds"], 9600)

    @unittest.skipUnless(HAS_TOKENIZER, "install the calibration optional dependencies")
    def test_two_jobs_real_authority_budget_raw_resolution_and_restart_never_qualify(self):
        result = self.prepare()
        self.tokenizer_fixture()
        check_calibration_context(self.target, REPOSITORY_ROOT)
        plan = parse_json((REPOSITORY_ROOT / "config/pilots/solo-spend-envelope-v1.json").read_text())
        envelope = PilotSpendEnvelope(self.root / "journal", plan)
        state = self.target / "control"
        authorization = {"schema_version": "paired-stock-control-authorization/v1",
            "scope": "one_bounded_calibration_diagnostic", "run_id": "v3-diagnostic",
            "expires_at": (datetime.now(UTC) + timedelta(hours=12)).isoformat(),
            "preparation_digest": digest_file(state / "stock-control.json"),
            "configuration": {"file": str(self.target / "configuration.json"), "digest": digest_file(self.target / "configuration.json")},
            "context_check": {"file": str(self.target / "context-check.json"), "digest": digest_file(self.target / "context-check.json")},
            "state_root": str(state), "repository": str(REPOSITORY_ROOT), "git_commit": result["preparation"]["git_commit"],
            "journal": {"root": str(envelope.root), "plan_digest": envelope.plan_digest, "retry_amendment": None, "prior_batches": []},
            "batch_approval": stock_batch(result["preparation"], envelope.snapshot(),
                envelope.snapshot()["provider_limits_usd_nanos"])}
        retain_document(self.root / "authority.json", authorization)
        with (patch("agent_collab_evals.peer_stock_control.require_clean_build", return_value=None),
            patch.object(ModalPairedServingTransport, "dispatch", _RetainedPairedTransport.dispatch)):
            wrong_scope = {**authorization, "scope": "one_bounded_wrapper_conformance_and_stock_control"}
            wrong_scope.pop("context_check")
            retain_document(self.root / "wrong-scope.json", wrong_scope)
            with self.assertRaisesRegex(PermissionError, "scope differs"):
                run_stock_control(state, self.root / "wrong-scope.json", digest_file(self.root / "wrong-scope.json"))
            self.assertEqual(self.statuses(result["preparation"]), [None, None])
            self.assertFalse((envelope.root / "batches").exists())
            outcome = run_stock_control(state, self.root / "authority.json", digest_file(self.root / "authority.json"))
            self.assertEqual(run_stock_control(state, self.root / "authority.json", digest_file(self.root / "authority.json")), outcome)
        self.assertEqual(outcome["status"], "complete")
        self.assertEqual(outcome["new_gpu_calls"], 2)
        self.assertEqual(outcome["used_seconds"], 242)
        self.assertIsNone(outcome["evaluation_receipt"])
        self.assertFalse(outcome["result"]["calibration_qualified"])
        self.assertEqual(outcome["result"]["quality_noninferiority"], "not_evaluated_single_repetition")
        self.assertNotIn("eligible", outcome["result"])
        self.assertEqual(self.statuses(result["preparation"]), ["consumed"] * 2)
        self.assertEqual(len(_RetainedPairedTransport.dispatches), 2)
        self.assertEqual(outcome["spend_admission"]["reserved_usd_nanos"]["modal"], 3_275_968_000)
