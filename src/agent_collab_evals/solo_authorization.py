"""Operator-owned authority for one exploratory solo run, never registration.

The operator approves a digest-pinned document after reviewing its retained
readiness evidence. This is a trusted-controller boundary, not a signature or
an automatic claim that arbitrary evidence proves deployment conformance.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import re
import subprocess
import sys

from .adapters.oci_sandbox import OciSandboxExec, OciSandboxProfile
from .canonical import digest_file, digest_value, parse_json
from .pilot_spend import PilotSpendEnvelope
from .provider_qualification import QualifiedProviderRoute


def configuration_binding(configuration):
    """Bind transitive experiment inputs and controller code, not just filenames."""
    root = configuration.repository
    configuration.hidden_bundle()
    return digest_value({
        "configuration": configuration.document,
        "campaign": configuration.campaign.manifest_digest,
        "gateway": configuration.gateway.resolved_digest,
        "runtime": configuration.runtime.resolved_digest,
        "sandbox": configuration.sandbox.resolved_digest,
        "compute": configuration.public_compute.digest,
        "supporting_files": {name: digest_file(root / name) for name in (
            "campaigns/model_serving_v0/evaluator/scoring_hidden_v1.toml",
            "config/compute/modal-pilot-cost-v1.json", "package.json", "package-lock.json")},
        "source": {str(path.relative_to(root)): digest_file(path)
            for base in (root / "src/agent_collab_evals", root / "scripts/runtime")
            for path in sorted(base.rglob("*")) if path.is_file() and path.suffix in {".py", ".mjs"}},
    })


@dataclass(frozen=True)
class ExploratorySoloAuthorization:
    document: dict
    digest: str

    @classmethod
    def load(cls, path: Path, expected_digest: str):
        if not isinstance(expected_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_digest):
            raise ValueError("an explicit operator authorization digest is required")
        raw = path.read_bytes()
        from .canonical import digest_bytes
        if digest_bytes(raw) != expected_digest:
            raise ValueError("operator authorization digest differs")
        value = parse_json(raw.decode())
        fields = {"schema_version", "scope", "run_id", "expires_at", "configuration_digest",
            "spend_plan_digest", "spend_journal", "state_root", "engine_executable",
            "engine_identity_digest", "provider_selection", "readiness_evidence", "qualification_admissions_digest"}
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("solo authorization fields differ")
        if value["schema_version"] != "exploratory-solo-authorization/v1" or value["scope"] != "one_exploratory_solo_attempt":
            raise ValueError("authorization is not scoped to one exploratory solo attempt")
        if not isinstance(value["run_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", value["run_id"]):
            raise ValueError("authorization run ID is invalid")
        for field in ("spend_journal", "state_root", "engine_executable"):
            if not isinstance(value[field], str) or not Path(value[field]).is_absolute():
                raise ValueError("authorization controller paths must be absolute")
        for field in ("configuration_digest", "spend_plan_digest", "engine_identity_digest", "qualification_admissions_digest"):
            if not isinstance(value[field], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value[field]):
                raise ValueError("authorization binding digest is invalid")
        result = cls(value, expected_digest)
        result.require_current()
        return result

    def require_current(self):
        try:
            expires = datetime.fromisoformat(self.document["expires_at"])
        except (ValueError, TypeError):
            raise ValueError("authorization expiry is invalid") from None
        if expires.tzinfo is None or datetime.now(UTC) >= expires:
            raise PermissionError("solo authorization expired or has no timezone")
        if expires > datetime.now(UTC) + timedelta(hours=24):
            raise PermissionError("solo authorization must expire within 24 hours")

    def validate(self, configuration, envelope, run_id, state_root):
        self.require_current()
        value = self.document
        if value["run_id"] != run_id or Path(value["state_root"]).resolve() != state_root.resolve():
            raise PermissionError("solo authorization run or state root differs")
        if (Path(value["spend_journal"]).resolve() != envelope.root
            or value["spend_plan_digest"] != envelope.plan_digest):
            raise PermissionError("solo authorization spending journal differs")
        state = state_root.resolve()
        if (state.is_relative_to(envelope.root) or envelope.root.is_relative_to(state)
            or configuration.hidden_manifest.resolve().is_relative_to(state)):
            raise PermissionError("pilot state must be separate from the journal and private workload")
        if value["configuration_digest"] != configuration_binding(configuration):
            raise PermissionError("solo authorization configuration differs")
        sandbox = configuration.sandbox
        if (not isinstance(sandbox, OciSandboxProfile) or sandbox.status != "development_conformance"
            or not sandbox.execution_authorized or sandbox.engine != "podman-rootless-keep-id"
            or configuration.document.get("sandbox_engine_identity_digest") != value["engine_identity_digest"]):
            raise PermissionError("exploratory authorization requires the pinned development OCI boundary")
        selection_path = self._evidence_path(configuration.repository, value["provider_selection"])
        selection = QualifiedProviderRoute.load(selection_path, repository_root=configuration.repository)
        if selection.gateway_profile_digest != configuration.gateway.resolved_digest:
            raise PermissionError("qualified provider differs from the authorized gateway")
        readiness = value["readiness_evidence"]
        if not isinstance(readiness, dict) or set(readiness) != {"deployment", "evaluator", "billing", "modal_cancellation"}:
            raise ValueError("operator must retain every readiness assessment")
        for reference in readiness.values():
            self._evidence_path(configuration.repository, reference)
        snapshot = envelope.snapshot()
        if any(receipt["purpose"] != "qualification" for receipt in snapshot["receipts"]):
            raise PermissionError("the pilot journal already admitted an attempt or overhead")
        if digest_value(snapshot["receipts"]) != value["qualification_admissions_digest"]:
            raise PermissionError("qualification admissions differ from the approved journal")
        receipts = {item["operation_key"]: item for item in snapshot["receipts"]}
        if not {"qualification:provider-route-v1", "qualification:modal-access-v1"}.issubset(receipts):
            raise PermissionError("the journal is missing required qualification admissions")
        expected_request = digest_value({"selection": selection.selection.resolved_digest,
            "gateway": configuration.gateway.resolved_digest, "probe_count": 3, "maximum_elapsed_ms": 60_000})
        if receipts["qualification:provider-route-v1"]["request_digest"] != expected_request:
            raise PermissionError("provider qualification admission differs from the selected route")
        cancellation = parse_json(self._evidence_path(configuration.repository, readiness["modal_cancellation"]).read_text())
        if (cancellation.get("status") != "device_and_cancellation_observed"
            or cancellation.get("admission_digest") != digest_value(receipts["qualification:modal-access-v1"])):
            raise PermissionError("Modal cancellation evidence differs from its admission")

    @staticmethod
    def _evidence_path(repository, reference):
        if not isinstance(reference, dict) or set(reference) != {"file", "digest"}:
            raise ValueError("readiness evidence requires a file and digest")
        if not isinstance(reference["file"], str) or not reference["file"]:
            raise ValueError("readiness evidence path is invalid")
        path = (repository / reference["file"]).resolve(strict=True)
        if not path.is_file() or digest_file(path) != reference["digest"]:
            raise ValueError("operator readiness evidence digest differs")
        return path

    def process_sandbox(self, configuration):
        if sys.platform != "linux" or os.getuid() == 0:
            raise PermissionError("exploratory live execution requires the non-root Linux controller")
        engine = Path(self.document["engine_executable"]).resolve(strict=True)
        environment = {"LANG": "C.UTF-8", "PATH": "/usr/bin:/bin", "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}
        result = subprocess.run([str(engine), "info", "--format", "json"], env=environment,
            check=True, capture_output=True, text=True, timeout=30)
        info = parse_json(result.stdout)
        host = info["host"]
        if host["security"]["rootless"] is not True or host["cgroupVersion"] != "v2" or host["cgroupManager"] != "systemd":
            raise PermissionError("live OCI resource enforcement differs")
        identity = {"engine_binary_digest": digest_file(engine), "version": info["version"],
            "kernel": host["kernel"], "security": host["security"], "runtime": host["ociRuntime"], "id_mappings": host["idMappings"]}
        if digest_value(identity) != self.document["engine_identity_digest"]:
            raise PermissionError("live OCI engine identity differs")
        image = f"{configuration.sandbox.image_reference}@{configuration.sandbox.image_digest}"
        subprocess.run([str(engine), "image", "inspect", image], env=environment,
            check=True, capture_output=True, timeout=30)
        return OciSandboxExec(configuration.sandbox, engine, digest_value(identity))


def run_authorized_solo(config_path, state_root, run_id, authorization_path, authorization_digest):
    """Check operator authority before handing off to the existing lifecycle."""
    from .solo_live_configuration import LivePilotConfiguration, make_live_dependencies
    from .pilot_spend_guard import PilotSpendGuard
    from .solo_pilot_command import _execute_solo_pilot

    repository = Path(__file__).resolve().parents[2]
    authority = ExploratorySoloAuthorization.load(authorization_path, authorization_digest)
    configuration = LivePilotConfiguration.load(config_path, repository)
    journal = Path(authority.document["spend_journal"])
    # Never initialize a new journal as an accidental way to reset admission.
    if not (journal / "plan.json").is_file():
        raise PermissionError("the approved qualification spending journal is missing")
    plan = parse_json((repository / "config/pilots/solo-spend-envelope-v1.json").read_text())
    envelope = PilotSpendEnvelope(journal, plan)
    authority.validate(configuration, envelope, run_id, state_root)
    sandbox = authority.process_sandbox(configuration)
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise ValueError("OPENROUTER_API_KEY is missing")
    guard = PilotSpendGuard(configuration, envelope, run_id)
    guard.operator_authorization = authority
    remaining = envelope.snapshot()["remaining_usd_nanos"]
    required_modal = 12 * guard.estimate["per_execution_allowance_usd_nanos"] + guard.estimate["shared_overhead_allowance_usd_nanos"]
    if remaining["modal"] < required_modal or remaining["openrouter"] < guard.model_limit:
        raise PermissionError("remaining allowance cannot cover the complete solo pilot")
    live = make_live_dependencies(configuration, api_key=key, process_sandbox=sandbox,
        spend_guard=guard, exploratory_authorization=authority, state_root=state_root)
    return _execute_solo_pilot(configuration.document, state_root, run_id, repository,
        configuration.campaign, configuration.gateway, configuration.runtime, configuration.sandbox, live=live)
