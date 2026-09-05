"""Read-only deployment and registration inventory; never an execution authority."""

from __future__ import annotations

import platform
import shutil
from pathlib import Path

from .adapters.oci_sandbox import OciSandboxProfile
from .study_registration import StudyCompositionCandidate


def readiness_report(repository: Path, composition_path: Path) -> dict[str, object]:
    profile = OciSandboxProfile.load(
        repository / "config/enforcement_profiles/oci-opencode-v0-candidate.json",
        repository_root=repository,
    )
    candidate = StudyCompositionCandidate.load(composition_path, repository_root=repository)
    engines = {name: shutil.which(name) for name in ("docker", "podman")}
    local_gaps = []
    if platform.system() != "Linux":
        local_gaps.append("linux_gateway_and_container_deployment_required")
    if not any(engines.values()):
        local_gaps.append("container_engine_not_installed")
    if profile.image_digest is None:
        local_gaps.append("pinned_runtime_image")
    local_gaps.extend(profile.unresolved_gates)
    return {
        "execution_authorized": False,
        "kind": "read_only_inventory_not_conformance_evidence",
        "platform": platform.system(),
        "engine_paths": engines,
        "engine_daemon_checked": False,
        "oci_profile_digest": profile.resolved_digest,
        "deployment_gaps": sorted(set(local_gaps)),
        "registration_gaps": list(candidate.unresolved_gates),
        "runtime_qualification_gaps": [
            "native_admission_interception_and_containment_qualification",
            "candidate_oci_relay_and_matched_peer_wiring",
            "registered_candidate_recovery_qualification",
            "live_agent_to_evaluator_composition",
            "registered_capability_denial_audit",
        ],
        "study_composition_digest": candidate.digest,
    }
