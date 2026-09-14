"""Optional, no-spend acceptance of the existing pilot inside rootless OCI."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_collab_evals.adapters.oci_sandbox import OciSandboxExec, OciSandboxProfile
from agent_collab_evals.adapters.opencode_harness import OpenCodeRuntimeProfile, _runtime_config
from agent_collab_evals.canonical import digest_file, digest_value
from agent_collab_evals.solo_pilot_command import make_opencode_runtime_dependencies, run_solo_pilot


REPOSITORY = Path(__file__).resolve().parents[1]
CONFIG = REPOSITORY / "config/pilots/solo-no-spend-oci-v1.json"
PROFILE = REPOSITORY / "config/enforcement_profiles/oci-opencode-podman-development-v1.json"


class OciPilotConfigurationTests(unittest.TestCase):
    def test_retained_observation_resolves_audit_and_runtime_bindings(self):
        record = json.loads((REPOSITORY / "evidence/deployment/oci-solo-conformance-20260913.json").read_text())
        for name in ("pilot_audit", "run_configuration"):
            self.assertEqual(digest_value(record[name]), "sha256:" + record[name + "_raw_sha256"])
        self.assertEqual(record["pilot_audit"]["run_config_digest"], digest_value(record["run_configuration"]))
        profile = OciSandboxProfile.load(PROFILE, repository_root=REPOSITORY)
        self.assertEqual(OciSandboxExec.profile_digest_for(profile, digest_value(record["engine_identity"])),
            record["run_configuration"]["runtime_sandbox_evidence"]["sandbox_profile_digest"])
        self.assertFalse(record["registered_conformance_complete"])
        self.assertEqual(record["pilot_audit"]["actual_spend_usd_nanos"], 0)
        self.assertEqual(record["new_containers_remaining"], [])

    def test_conformance_profile_does_not_claim_registration(self):
        profile = OciSandboxProfile.load(PROFILE, repository_root=REPOSITORY)
        self.assertEqual(profile.status, "development_conformance")
        self.assertTrue(profile.execution_authorized)
        self.assertTrue(profile.unresolved_gates)
        self.assertEqual(profile.engine, "podman-rootless-keep-id")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "explicit host runtime"):
                run_solo_pilot(CONFIG, Path(directory), "no-implicit-engine")
            self.assertFalse((Path(directory) / "no-implicit-engine").exists())

    def test_runtime_sidecars_use_container_paths(self):
        profile = OpenCodeRuntimeProfile.load(REPOSITORY / "config/runtime_profiles/opencode-deepseek-v4-flash-development.json")
        access = SimpleNamespace(endpoint="http://127.0.0.1:4319/v1/call", token="synthetic-token")
        assets = Path("/opt/agent-collab/runtime")
        config = _runtime_config(profile, "http://127.0.0.1:4317/v1", "synthetic-model-token", True,
            access, access, access, runtime_assets_root=assets)
        self.assertEqual(config["mcp"]["candidate"]["command"], ["node", str(assets / "candidate_tool_server.mjs")])
        self.assertEqual(config["mcp"]["peer"]["command"], ["node", str(assets / "peer_tool_server.mjs")])
        self.assertEqual(config["plugin"][0][0], (assets / "native_admission_plugin.mjs").as_uri())
        self.assertNotIn(str(REPOSITORY), json.dumps(config))


@unittest.skipUnless(os.environ.get("RUN_OCI_PILOT_INTEGRATION") == "1", "enable local Linux/Podman integration")
class OciPilotIntegrationTests(unittest.TestCase):
    def test_complete_existing_pilot_without_model_or_gpu_spend(self):
        engine = Path("/usr/bin/podman")
        environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}

        def command(*args):
            return subprocess.run([str(engine), *args], env=environment,
                check=True, text=True, capture_output=True, timeout=30).stdout

        info = json.loads(command("info", "--format", "json"))
        self.assertTrue(info["host"]["security"]["rootless"])
        self.assertEqual(info["host"]["cgroupVersion"], "v2")
        self.assertEqual(info["host"]["cgroupManager"], "systemd")
        profile = OciSandboxProfile.load(PROFILE, repository_root=REPOSITORY)
        image = f"{profile.image_reference}@{profile.image_digest}"
        command("image", "inspect", image)
        before = set(command("ps", "--all", "--quiet", "--no-trunc").splitlines())
        identity = {"engine_binary_digest": digest_file(engine), "version": info["version"],
            "kernel": info["host"]["kernel"], "security": info["host"]["security"],
            "runtime": info["host"]["ociRuntime"], "id_mappings": info["host"]["idMappings"]}
        sandbox = OciSandboxExec(profile, engine, digest_value(identity))
        runtime = OpenCodeRuntimeProfile.load(REPOSITORY / "config/runtime_profiles/opencode-deepseek-v4-flash-development.json")
        dependencies = make_opencode_runtime_dependencies(runtime, profile, sandbox, timeout_seconds=120)
        with tempfile.TemporaryDirectory(prefix="ace-", dir="/tmp") as directory:
            root = Path(directory)
            try:
                result = run_solo_pilot(CONFIG, root, "pilot", runtime_dependencies=dependencies)
                audit = json.loads(Path(result["audit_path"]).read_text())
                self.assertEqual(audit["status"], "complete")
                self.assertEqual(audit["execution_mode"], "no_spend")
                self.assertFalse(audit["live_execution_authorized"])
                self.assertFalse(audit["scoreable"])
                self.assertGreater(audit["synthetic_model_calls"], 0)
                self.assertEqual(audit["synthetic_compute_executions"], 12)
                self.assertEqual(audit["external_model_calls"], 0)
                self.assertEqual(audit["external_compute_executions"], 0)
                self.assertEqual(audit["actual_spend_usd_nanos"], 0)
                self.assertTrue(audit["budget_reconciliation"]["valid"])
                bindings = json.loads((root / "pilot/run-config.json").read_text())
                self.assertEqual(bindings["runtime_sandbox_evidence"]["user_namespace"], "keep_id")
            finally:
                # Preserve generated diagnostics privately, including failed runs.
                retained = REPOSITORY / "tmp/oci-pilot-conformance" / root.name
                retained.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(root, retained)
                (retained / "engine-identity.json").write_text(json.dumps(identity, indent=2) + "\n")
                after = set(command("ps", "--all", "--quiet", "--no-trunc").splitlines())
                print(json.dumps({"retained_evidence": str(retained), "new_containers_remaining": sorted(after - before)}))
                self.assertEqual(after - before, set(), "pilot left a container behind")


if __name__ == "__main__":
    unittest.main()
