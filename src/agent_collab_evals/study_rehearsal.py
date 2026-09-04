"""Fail-closed, no-spend rehearsal of registered study orchestration."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .adapters.fake_harness import FakeHarnessRuntime
from .adapters.local_events import LocalEventSink
from .adapters.no_compute_reconciliation import NoComputeExecutionReconciler
from .adapters.no_model_budget import NoModelBudgetReconciler
from .canonical import (
    canonical_json_bytes,
    digest_bytes,
    digest_file,
    digest_value,
    load_json,
    parse_json,
)
from .compute_backend import FrozenComputeRunManifest
from .controller import CampaignController
from .domain import OrganisationSpec, top_level_actor_count
from .study_registration import StudyCompositionCandidate
from .study_schedule import RandomizedBlockPlan, ResolvedRunManifest


NO_SPEND_AUTHORITY_SCHEMA = "no-spend-study-authority/v1"
NO_SPEND_AUDIT_SCHEMA = "no-spend-study-audit/v1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}")
_AUTHORITY_NAMES = (
    "analysis",
    "block_plan",
    "budget",
    "compute",
    "enforcement",
    "platform_build",
    "provider_runtime",
    "stability_and_shortcuts",
)


class StudyRehearsalError(RuntimeError):
    """A structural rehearsal differs from its immutable authority."""


@dataclass(frozen=True, slots=True)
class NoSpendStudyAuthority:
    """Immutable authority for a non-scoreable structural rehearsal only."""

    path: Path
    digest: str
    rehearsal_id: str
    composition_digest: str
    resolved_configuration_digest: str
    block_plan_digest: str
    platform_source_digest: str
    authorities: Mapping[str, Mapping[str, Any]]
    source_root: Path

    @classmethod
    def create(
        cls,
        path: Path,
        *,
        rehearsal_id: str,
        composition: StudyCompositionCandidate,
        block_plan: RandomizedBlockPlan,
        repository_root: Path,
    ) -> "NoSpendStudyAuthority":
        _identifier(rehearsal_id, "rehearsal ID")
        source_digest = _source_tree_digest(repository_root / "src")
        document = {
            "schema_version": NO_SPEND_AUTHORITY_SCHEMA,
            "status": "structural_rehearsal",
            "execution_authorized": True,
            "execution_class": "no_spend",
            "scoreable": False,
            "treatment_surfaces_exercised": False,
            "external_model_calls": False,
            "external_compute": False,
            "rehearsal_id": rehearsal_id,
            "composition_digest": composition.digest,
            "resolved_configuration_digest": (
                composition.resolved_configuration_digest
            ),
            "block_plan_digest": block_plan.digest,
            "platform_source_digest": source_digest,
            "authorities": _authority_records(
                composition=composition,
                block_plan=block_plan,
                platform_source_digest=source_digest,
            ),
        }
        content = canonical_json_bytes(document)
        _write_once(path, content)
        return cls.load(
            path,
            expected_digest=digest_bytes(content),
            composition=composition,
            block_plan=block_plan,
            repository_root=repository_root,
        )

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        expected_digest: str,
        composition: StudyCompositionCandidate,
        block_plan: RandomizedBlockPlan,
        repository_root: Path,
    ) -> "NoSpendStudyAuthority":
        _digest(expected_digest, "no-spend authority")
        resolved = path.resolve(strict=True)
        content = resolved.read_bytes()
        if digest_bytes(content) != expected_digest:
            raise StudyRehearsalError("no-spend authority digest differs")
        try:
            with resolved.open("r", encoding="utf-8") as source:
                document = load_json(source)
        except ValueError as error:
            raise StudyRehearsalError("no-spend authority JSON is invalid") from error
        expected_fields = {
            "schema_version",
            "status",
            "execution_authorized",
            "execution_class",
            "scoreable",
            "treatment_surfaces_exercised",
            "external_model_calls",
            "external_compute",
            "rehearsal_id",
            "composition_digest",
            "resolved_configuration_digest",
            "block_plan_digest",
            "platform_source_digest",
            "authorities",
        }
        if not isinstance(document, dict) or set(document) != expected_fields:
            raise StudyRehearsalError("no-spend authority fields differ")
        required_semantics = {
            "schema_version": NO_SPEND_AUTHORITY_SCHEMA,
            "status": "structural_rehearsal",
            "execution_authorized": True,
            "execution_class": "no_spend",
            "scoreable": False,
            "treatment_surfaces_exercised": False,
            "external_model_calls": False,
            "external_compute": False,
        }
        if any(document.get(key) != value for key, value in required_semantics.items()):
            raise StudyRehearsalError("no-spend authority semantics differ")
        rehearsal_id = document["rehearsal_id"]
        _identifier(rehearsal_id, "rehearsal ID")
        current_source_digest = _source_tree_digest(repository_root / "src")
        expected_values = {
            "composition_digest": composition.digest,
            "resolved_configuration_digest": (
                composition.resolved_configuration_digest
            ),
            "block_plan_digest": block_plan.digest,
            "platform_source_digest": current_source_digest,
        }
        changed = [
            name
            for name, expected in expected_values.items()
            if document.get(name) != expected
        ]
        if changed:
            raise StudyRehearsalError(
                "no-spend authority inputs differ: " + ", ".join(changed)
            )
        authorities = document["authorities"]
        if not isinstance(authorities, dict) or tuple(sorted(authorities)) != (
            _AUTHORITY_NAMES
        ):
            raise StudyRehearsalError("no-spend authority set differs")
        expected_authorities = _authority_records(
            composition=composition,
            block_plan=block_plan,
            platform_source_digest=current_source_digest,
        )
        if authorities != expected_authorities:
            raise StudyRehearsalError("no-spend component authorities differ")
        if canonical_json_bytes(document) != content:
            raise StudyRehearsalError("no-spend authority is not canonical")
        return cls(
            path=resolved,
            digest=expected_digest,
            rehearsal_id=rehearsal_id,
            composition_digest=document["composition_digest"],
            resolved_configuration_digest=document[
                "resolved_configuration_digest"
            ],
            block_plan_digest=document["block_plan_digest"],
            platform_source_digest=document["platform_source_digest"],
            authorities={
                name: dict(record) for name, record in authorities.items()
            },
            source_root=(repository_root / "src").resolve(strict=True),
        )

    def assert_no_spend(self) -> None:
        """Document the only execution class this authority can permit."""

        if digest_file(self.path) != self.digest:
            raise StudyRehearsalError("no-spend authority changed after loading")
        if _source_tree_digest(self.source_root) != self.platform_source_digest:
            raise StudyRehearsalError("platform source changed after authority loading")


@dataclass(frozen=True, slots=True)
class StudyRehearsalResult:
    rehearsal_id: str
    authority_digest: str
    block_plan_digest: str
    audit_path: Path
    audit_digest: str
    block_count: int
    run_count: int


class NoSpendStudyRunner:
    """Execute assigned campaign lifecycles without agents, models, or compute."""

    def __init__(
        self,
        *,
        composition: StudyCompositionCandidate,
        block_plan: RandomizedBlockPlan,
        authority: NoSpendStudyAuthority,
        state_root: Path,
    ) -> None:
        authority.assert_no_spend()
        if authority.composition_digest != composition.digest:
            raise StudyRehearsalError("rehearsal composition differs")
        if authority.block_plan_digest != block_plan.digest:
            raise StudyRehearsalError("rehearsal block plan differs")
        if (
            authority.resolved_configuration_digest
            != composition.resolved_configuration_digest
        ):
            raise StudyRehearsalError("rehearsal configuration differs")
        self._composition = composition
        self._block_plan = block_plan
        self._authority = authority
        self._state_root = state_root.resolve()

    def run(self) -> StudyRehearsalResult:
        audit_path = self._state_root / "study-audit.json"
        if audit_path.exists():
            raise StudyRehearsalError("rehearsal audit already exists")
        block_audits: list[dict[str, object]] = []
        for block in self._block_plan.blocks:
            materialized = self._composition.campaign.materialize(block.task_seed)
            if materialized.material_digest != block.task_material_digest:
                raise StudyRehearsalError(
                    f"block material digest differs: {block.block_id}"
                )
            run_audits: list[dict[str, object]] = []
            for assigned in block.runs:
                resolved = self._block_plan.resolve(
                    study_manifest_digest=self._authority.digest,
                    run_id=assigned.run_id,
                    resolved_configuration_digest=(
                        self._composition.resolved_configuration_digest
                    ),
                )
                run_root = self._state_root / "runs" / resolved.run_id
                resolved_path = resolved.write_once(run_root / "resolved-run.json")
                compute_authority = FrozenComputeRunManifest.load_or_create(
                    run_root / "compute-run.json",
                    campaign_run_id=resolved.run_id,
                    compute_enabled=False,
                    transport_profile_digest=None,
                    backend_profile_digest=None,
                    requests=(),
                )
                events = LocalEventSink(run_root / "events")
                runtime = FakeHarnessRuntime()
                controller = CampaignController(
                    runtime,
                    events,
                    NoModelBudgetReconciler(),
                    NoComputeExecutionReconciler(compute_authority),
                )
                handle = controller.start(
                    OrganisationSpec(
                        campaign_run_id=resolved.run_id,
                        condition=resolved.condition,
                        organisation_size=self._block_plan.organisation_size,
                        workspace_root=run_root / "workspace",
                        model_endpoint="fake://no-spend-rehearsal",
                    )
                )
                for job in materialized.jobs:
                    controller.deliver(handle, job)
                result = controller.close(handle, "no-spend structural rehearsal")
                event_log = events.read(resolved.run_id)
                expected_sessions = top_level_actor_count(
                    resolved.condition, self._block_plan.organisation_size
                )
                if len(handle.sessions) != expected_sessions:
                    raise StudyRehearsalError("top-level session count differs")
                if tuple(result.delivered_job_ids) != tuple(
                    job.job_id for job in materialized.jobs
                ):
                    raise StudyRehearsalError("delivered job set differs")
                if not event_log or event_log[-1]["kind"] != "campaign.closed":
                    raise StudyRehearsalError("campaign audit is incomplete")
                snapshot_document = {
                    "organisation_id": result.final_harness_snapshot.organisation_id,
                    "payload": result.final_harness_snapshot.payload,
                }
                snapshot_content = canonical_json_bytes(snapshot_document)
                snapshot_path = _write_once(
                    run_root / "final-harness-snapshot.json", snapshot_content
                )
                run_audit = {
                    "run_id": resolved.run_id,
                    "execution_position": resolved.execution_position,
                    "condition": resolved.condition.value,
                    "resolved_run_path": str(
                        resolved_path.relative_to(self._state_root)
                    ),
                    "resolved_run_digest": resolved.digest,
                    "compute_authority_digest": compute_authority.manifest_digest,
                    "task_material_digest": resolved.task_material_digest,
                    "top_level_session_count": len(handle.sessions),
                    "delivered_job_ids": list(result.delivered_job_ids),
                    "event_count": len(event_log),
                    "event_log_digest": digest_value(event_log),
                    "final_harness_snapshot_path": str(
                        snapshot_path.relative_to(self._state_root)
                    ),
                    "final_harness_snapshot_digest": digest_bytes(snapshot_content),
                    "model_calls": 0,
                    "compute_executions": 0,
                    "scoreable": False,
                }
                _write_once(
                    run_root / "run-audit.json", canonical_json_bytes(run_audit)
                )
                run_audits.append(run_audit)
            block_audits.append(
                {
                    "block_id": block.block_id,
                    "replicate_id": block.replicate_id,
                    "variant_id": block.variant_id,
                    "task_seed": block.task_seed,
                    "task_material_digest": block.task_material_digest,
                    "runs": run_audits,
                }
            )
        document = {
            "schema_version": NO_SPEND_AUDIT_SCHEMA,
            "rehearsal_id": self._authority.rehearsal_id,
            "authority_digest": self._authority.digest,
            "composition_digest": self._composition.digest,
            "resolved_configuration_digest": (
                self._composition.resolved_configuration_digest
            ),
            "block_plan_digest": self._block_plan.digest,
            "execution_class": "no_spend",
            "scoreable": False,
            "treatment_surfaces_exercised": False,
            "external_model_calls": 0,
            "external_compute_executions": 0,
            "blocks": block_audits,
        }
        content = canonical_json_bytes(document)
        _write_once(audit_path, content)
        return StudyRehearsalResult(
            rehearsal_id=self._authority.rehearsal_id,
            authority_digest=self._authority.digest,
            block_plan_digest=self._block_plan.digest,
            audit_path=audit_path,
            audit_digest=digest_bytes(content),
            block_count=len(block_audits),
            run_count=sum(len(block["runs"]) for block in block_audits),
        )


def verify_no_spend_study_audit(
    path: Path,
    *,
    expected_digest: str,
    composition: StudyCompositionCandidate,
    block_plan: RandomizedBlockPlan,
    authority: NoSpendStudyAuthority,
) -> StudyRehearsalResult:
    """Verify a completed rehearsal from retained evidence without rerunning it."""

    authority.assert_no_spend()
    _digest(expected_digest, "study audit")
    resolved_path = path.resolve(strict=True)
    state_root = resolved_path.parent
    content = resolved_path.read_bytes()
    if digest_bytes(content) != expected_digest:
        raise StudyRehearsalError("study audit digest differs")
    document = _canonical_document(content, "study audit")
    expected_fields = {
        "schema_version",
        "rehearsal_id",
        "authority_digest",
        "composition_digest",
        "resolved_configuration_digest",
        "block_plan_digest",
        "execution_class",
        "scoreable",
        "treatment_surfaces_exercised",
        "external_model_calls",
        "external_compute_executions",
        "blocks",
    }
    if set(document) != expected_fields:
        raise StudyRehearsalError("study audit fields differ")
    fixed = {
        "schema_version": NO_SPEND_AUDIT_SCHEMA,
        "rehearsal_id": authority.rehearsal_id,
        "authority_digest": authority.digest,
        "composition_digest": composition.digest,
        "resolved_configuration_digest": composition.resolved_configuration_digest,
        "block_plan_digest": block_plan.digest,
        "execution_class": "no_spend",
        "scoreable": False,
        "treatment_surfaces_exercised": False,
        "external_model_calls": 0,
        "external_compute_executions": 0,
    }
    if any(document.get(name) != value for name, value in fixed.items()):
        raise StudyRehearsalError("study audit authority differs")
    blocks = document["blocks"]
    if not isinstance(blocks, list) or len(blocks) != len(block_plan.blocks):
        raise StudyRehearsalError("study audit block set differs")
    run_count = 0
    for block_document, block in zip(blocks, block_plan.blocks, strict=True):
        if not isinstance(block_document, dict) or set(block_document) != {
            "block_id",
            "replicate_id",
            "variant_id",
            "task_seed",
            "task_material_digest",
            "runs",
        }:
            raise StudyRehearsalError("block audit fields differ")
        expected_block = {
            "block_id": block.block_id,
            "replicate_id": block.replicate_id,
            "variant_id": block.variant_id,
            "task_seed": block.task_seed,
            "task_material_digest": block.task_material_digest,
        }
        if any(
            block_document.get(name) != value
            for name, value in expected_block.items()
        ):
            raise StudyRehearsalError("block audit authority differs")
        runs = block_document["runs"]
        if not isinstance(runs, list) or len(runs) != len(block.runs):
            raise StudyRehearsalError("run audit set differs")
        materialized = composition.campaign.materialize(block.task_seed)
        if materialized.material_digest != block.task_material_digest:
            raise StudyRehearsalError("block material digest differs during audit")
        expected_jobs = [job.job_id for job in materialized.jobs]
        for run_document, assigned in zip(runs, block.runs, strict=True):
            _verify_run_audit(
                run_document,
                assigned_run_id=assigned.run_id,
                expected_jobs=expected_jobs,
                state_root=state_root,
                composition=composition,
                block_plan=block_plan,
                authority=authority,
            )
            run_count += 1
    return StudyRehearsalResult(
        rehearsal_id=authority.rehearsal_id,
        authority_digest=authority.digest,
        block_plan_digest=block_plan.digest,
        audit_path=resolved_path,
        audit_digest=expected_digest,
        block_count=len(blocks),
        run_count=run_count,
    )


def _verify_run_audit(
    value: Any,
    *,
    assigned_run_id: str,
    expected_jobs: list[str],
    state_root: Path,
    composition: StudyCompositionCandidate,
    block_plan: RandomizedBlockPlan,
    authority: NoSpendStudyAuthority,
) -> None:
    expected_fields = {
        "run_id",
        "execution_position",
        "condition",
        "resolved_run_path",
        "resolved_run_digest",
        "compute_authority_digest",
        "task_material_digest",
        "top_level_session_count",
        "delivered_job_ids",
        "event_count",
        "event_log_digest",
        "final_harness_snapshot_path",
        "final_harness_snapshot_digest",
        "model_calls",
        "compute_executions",
        "scoreable",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise StudyRehearsalError("run audit fields differ")
    expected_resolved = block_plan.resolve(
        study_manifest_digest=authority.digest,
        run_id=assigned_run_id,
        resolved_configuration_digest=composition.resolved_configuration_digest,
    )
    expected_values = {
        "run_id": expected_resolved.run_id,
        "execution_position": expected_resolved.execution_position,
        "condition": expected_resolved.condition.value,
        "resolved_run_path": f"runs/{expected_resolved.run_id}/resolved-run.json",
        "resolved_run_digest": expected_resolved.digest,
        "task_material_digest": expected_resolved.task_material_digest,
        "top_level_session_count": top_level_actor_count(
            expected_resolved.condition, block_plan.organisation_size
        ),
        "delivered_job_ids": expected_jobs,
        "model_calls": 0,
        "compute_executions": 0,
        "scoreable": False,
        "final_harness_snapshot_path": (
            f"runs/{expected_resolved.run_id}/final-harness-snapshot.json"
        ),
    }
    if any(value.get(name) != expected for name, expected in expected_values.items()):
        raise StudyRehearsalError("run audit authority differs")
    run_root = state_root / "runs" / expected_resolved.run_id
    retained_run_audit = _canonical_document(
        (run_root / "run-audit.json").read_bytes(), "retained run audit"
    )
    if retained_run_audit != value:
        raise StudyRehearsalError("retained run audit differs")
    loaded_resolved = ResolvedRunManifest.load(
        run_root / "resolved-run.json",
        plan=block_plan,
        study_manifest_digest=authority.digest,
        resolved_configuration_digest=composition.resolved_configuration_digest,
    )
    if loaded_resolved.digest != value["resolved_run_digest"]:
        raise StudyRehearsalError("resolved run digest differs")
    compute_digest = value["compute_authority_digest"]
    _digest(compute_digest, "compute authority")
    compute = FrozenComputeRunManifest.load(
        run_root / "compute-run.json", expected_digest=compute_digest
    )
    compute.assert_no_compute(expected_resolved.run_id)
    event_log = LocalEventSink(run_root / "events").read(expected_resolved.run_id)
    if len(event_log) != value["event_count"]:
        raise StudyRehearsalError("event count differs")
    if digest_value(event_log) != value["event_log_digest"]:
        raise StudyRehearsalError("event log digest differs")
    snapshot_path = run_root / "final-harness-snapshot.json"
    snapshot_content = snapshot_path.read_bytes()
    _canonical_document(snapshot_content, "final harness snapshot")
    if digest_bytes(snapshot_content) != value["final_harness_snapshot_digest"]:
        raise StudyRehearsalError("final harness snapshot digest differs")


def _authority_records(
    *,
    composition: StudyCompositionCandidate,
    block_plan: RandomizedBlockPlan,
    platform_source_digest: str,
) -> dict[str, dict[str, Any]]:
    documents = {
        "analysis": {
            "mode": "none",
            "reason": "structural_rehearsal_has_no_outcomes",
        },
        "block_plan": {
            "algorithm": block_plan.algorithm,
            "digest": block_plan.digest,
        },
        "budget": {
            "adapter": "no-model-budget-reconciler/v1",
            "accounting_mode": "no_model_calls",
        },
        "compute": {
            "adapter": "no-compute-execution-reconciler/v1",
            "compute_enabled": False,
        },
        "enforcement": {
            "mode": "in_process_fake_only",
            "external_processes": False,
            "external_network": False,
            "external_credentials": False,
        },
        "platform_build": {
            "source_tree_digest": platform_source_digest,
            "composition_schema": NO_SPEND_AUTHORITY_SCHEMA,
        },
        "provider_runtime": {
            "adapter": "fake-harness/v1",
            "model_endpoint": "fake://no-spend-rehearsal",
            "external_model_calls": False,
        },
        "stability_and_shortcuts": {
            "mode": "not_evaluated",
            "reason": "structural_rehearsal_is_not_scoreable",
            "hidden_evaluation_profile_digest": composition.profiles[
                "hidden_evaluation"
            ].digest,
        },
    }
    return {
        name: {
            "profile": documents[name],
            "digest": digest_value(documents[name]),
        }
        for name in _AUTHORITY_NAMES
    }


def _source_tree_digest(source_root: Path) -> str:
    root = source_root.resolve(strict=True)
    files = sorted(path for path in root.rglob("*.py") if path.is_file())
    if not files:
        raise StudyRehearsalError("platform source tree is empty")
    return digest_value(
        {
            str(path.relative_to(root)): digest_file(path)
            for path in files
        }
    )


def _canonical_document(content: bytes, label: str) -> dict[str, Any]:
    try:
        text = content.decode("utf-8")
        value = parse_json(text)
    except (UnicodeDecodeError, ValueError) as error:
        raise StudyRehearsalError(f"{label} is invalid JSON") from error
    if not isinstance(value, dict):
        raise StudyRehearsalError(f"{label} must be an object")
    if canonical_json_bytes(value) != content:
        raise StudyRehearsalError(f"{label} is not canonical")
    return value


def _identifier(value: Any, label: str) -> None:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise StudyRehearsalError(f"{label} is invalid")


def _digest(value: Any, label: str) -> None:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise StudyRehearsalError(f"{label} digest is invalid")


def _write_once(path: Path, content: bytes) -> Path:
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = destination.read_bytes()
    except FileNotFoundError:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{destination.name}-", dir=destination.parent
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if destination.read_bytes() != content:
                    raise StudyRehearsalError("rehearsal record already differs")
            finally:
                temporary.unlink(missing_ok=True)
            _fsync_directory(destination.parent)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    else:
        if existing != content:
            raise StudyRehearsalError("rehearsal record already differs")
    return destination


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
