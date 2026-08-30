"""Frozen four-condition assignment and resolved-run manifests."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .canonical import canonical_json_bytes, digest_value, load_json
from .domain import CoordinationCondition


BLOCK_PLAN_SCHEMA = "randomized-block-plan/v0alpha1"
RESOLVED_RUN_SCHEMA = "resolved-run-manifest/v0alpha1"
ASSIGNMENT_ALGORITHM = "sha256-condition-sort-v1"
_CONDITIONS = tuple(CoordinationCondition)
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}")


@dataclass(frozen=True, slots=True)
class BlockInput:
    """Pre-randomization task identity shared by all four positions."""

    block_id: str
    replicate_id: str
    variant_id: str
    task_seed: int
    task_material_digest: str

    def __post_init__(self) -> None:
        for value in (self.block_id, self.replicate_id, self.variant_id):
            if not _SAFE_ID.fullmatch(value):
                raise ValueError("block input identifier is invalid")
        _seed(self.task_seed, "task seed")
        _digest(self.task_material_digest, "task material")


@dataclass(frozen=True, slots=True)
class RunAssignment:
    """One predeclared execution position and its randomized condition."""

    run_id: str
    execution_position: int
    run_stochastic_seed: int
    actor_stochastic_seeds: tuple[int, ...]
    assigned_condition: CoordinationCondition

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.run_id):
            raise ValueError("run assignment identifier is invalid")
        if type(self.execution_position) is not int or not 1 <= (
            self.execution_position
        ) <= len(_CONDITIONS):
            raise ValueError("execution position is invalid")
        _seed(self.run_stochastic_seed, "run stochastic seed")
        if not self.actor_stochastic_seeds:
            raise ValueError("actor stochastic seeds are required")
        for value in self.actor_stochastic_seeds:
            _seed(value, "actor stochastic seed")
        if not isinstance(self.assigned_condition, CoordinationCondition):
            raise ValueError("assigned condition is invalid")

    @property
    def actor_seed_map(self) -> Mapping[int, int]:
        return {
            ordinal: seed
            for ordinal, seed in enumerate(self.actor_stochastic_seeds)
        }

    @property
    def document(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "execution_position": self.execution_position,
            "run_stochastic_seed": self.run_stochastic_seed,
            "actor_stochastic_seeds": {
                str(ordinal): seed
                for ordinal, seed in self.actor_seed_map.items()
            },
            "assigned_condition": self.assigned_condition.value,
        }


@dataclass(frozen=True, slots=True)
class BlockAssignment:
    """One complete randomized block with shared task material."""

    block_id: str
    replicate_id: str
    variant_id: str
    task_seed: int
    task_material_digest: str
    runs: tuple[RunAssignment, ...]

    def __post_init__(self) -> None:
        BlockInput(
            self.block_id,
            self.replicate_id,
            self.variant_id,
            self.task_seed,
            self.task_material_digest,
        )
        if len(self.runs) != len(_CONDITIONS):
            raise ValueError("a block must contain exactly four runs")
        if tuple(run.execution_position for run in self.runs) != (1, 2, 3, 4):
            raise ValueError("block positions must be complete and ordered")
        if {run.assigned_condition for run in self.runs} != set(_CONDITIONS):
            raise ValueError("a block must assign every condition exactly once")
        if len({run.run_id for run in self.runs}) != len(self.runs):
            raise ValueError("run identifiers repeat within a block")

    @property
    def document(self) -> dict[str, object]:
        return {
            "block_id": self.block_id,
            "replicate_id": self.replicate_id,
            "variant_id": self.variant_id,
            "task_seed": self.task_seed,
            "task_material_digest": self.task_material_digest,
            "runs": [run.document for run in self.runs],
        }


@dataclass(frozen=True, slots=True)
class RandomizedBlockPlan:
    """Versioned, content-addressed assignment for complete four-run blocks."""

    master_seed: int
    organisation_size: int
    blocks: tuple[BlockAssignment, ...]
    algorithm: str = ASSIGNMENT_ALGORITHM

    def __post_init__(self) -> None:
        _seed(self.master_seed, "master seed", maximum=2**63 - 1)
        if type(self.organisation_size) is not int or self.organisation_size < 1:
            raise ValueError("organisation size must be positive")
        if self.algorithm != ASSIGNMENT_ALGORITHM:
            raise ValueError("assignment algorithm is unsupported")
        if not self.blocks:
            raise ValueError("a randomized block plan requires at least one block")
        if len({block.block_id for block in self.blocks}) != len(self.blocks):
            raise ValueError("block identifiers repeat")
        run_ids = [run.run_id for block in self.blocks for run in block.runs]
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("run identifiers repeat across blocks")
        if any(
            len(run.actor_stochastic_seeds) != self.organisation_size
            for block in self.blocks
            for run in block.runs
        ):
            raise ValueError("actor seed count differs from organisation size")

    @classmethod
    def create(
        cls,
        *,
        master_seed: int,
        organisation_size: int,
        blocks: Sequence[BlockInput],
    ) -> "RandomizedBlockPlan":
        _seed(master_seed, "master seed", maximum=2**63 - 1)
        if type(organisation_size) is not int or organisation_size < 1:
            raise ValueError("organisation size must be positive")
        if not blocks:
            raise ValueError("at least one block input is required")
        assignments: list[BlockAssignment] = []
        for block in blocks:
            order = _condition_order(master_seed, block.block_id)
            runs = tuple(
                RunAssignment(
                    run_id=f"{block.block_id}:position:{position}",
                    execution_position=position,
                    run_stochastic_seed=_derived_seed(
                        master_seed, block.block_id, position, "run"
                    ),
                    actor_stochastic_seeds=tuple(
                        _derived_seed(
                            master_seed,
                            block.block_id,
                            position,
                            f"actor:{ordinal}",
                        )
                        for ordinal in range(organisation_size)
                    ),
                    assigned_condition=order[position - 1],
                )
                for position in range(1, len(_CONDITIONS) + 1)
            )
            assignments.append(
                BlockAssignment(
                    block_id=block.block_id,
                    replicate_id=block.replicate_id,
                    variant_id=block.variant_id,
                    task_seed=block.task_seed,
                    task_material_digest=block.task_material_digest,
                    runs=runs,
                )
            )
        return cls(master_seed, organisation_size, tuple(assignments))

    @classmethod
    def load(cls, path: Path) -> "RandomizedBlockPlan":
        with path.open("r", encoding="utf-8") as source:
            document = load_json(source)
        if not isinstance(document, dict) or set(document) != {
            "schema_version",
            "algorithm",
            "master_seed",
            "organisation_size",
            "blocks",
        }:
            raise ValueError("randomized block plan fields differ")
        if document["schema_version"] != BLOCK_PLAN_SCHEMA:
            raise ValueError("randomized block plan schema differs")
        raw_blocks = document["blocks"]
        if not isinstance(raw_blocks, list):
            raise ValueError("randomized block plan blocks are invalid")
        blocks = tuple(_block(value) for value in raw_blocks)
        plan = cls(
            master_seed=document["master_seed"],
            organisation_size=document["organisation_size"],
            blocks=blocks,
            algorithm=str(document["algorithm"]),
        )
        expected = cls.create(
            master_seed=plan.master_seed,
            organisation_size=plan.organisation_size,
            blocks=tuple(
                BlockInput(
                    block.block_id,
                    block.replicate_id,
                    block.variant_id,
                    block.task_seed,
                    block.task_material_digest,
                )
                for block in plan.blocks
            ),
        )
        if plan != expected:
            raise ValueError("randomized block plan differs from its algorithm")
        return plan

    @property
    def document(self) -> dict[str, object]:
        return {
            "schema_version": BLOCK_PLAN_SCHEMA,
            "algorithm": self.algorithm,
            "master_seed": self.master_seed,
            "organisation_size": self.organisation_size,
            "blocks": [block.document for block in self.blocks],
        }

    @property
    def digest(self) -> str:
        return digest_value(self.document)

    def write_once(self, path: Path) -> Path:
        return _write_once(path, canonical_json_bytes(self.document) + b"\n")

    def assignment(self, run_id: str) -> tuple[BlockAssignment, RunAssignment]:
        matches = tuple(
            (block, run)
            for block in self.blocks
            for run in block.runs
            if run.run_id == run_id
        )
        if len(matches) != 1:
            raise KeyError("run assignment is unavailable")
        return matches[0]

    def resolve(
        self,
        *,
        study_manifest_digest: str,
        run_id: str,
        resolved_configuration_digest: str,
    ) -> "ResolvedRunManifest":
        _digest(study_manifest_digest, "study manifest")
        _digest(resolved_configuration_digest, "resolved configuration")
        block, run = self.assignment(run_id)
        return ResolvedRunManifest(
            study_manifest_digest=study_manifest_digest,
            block_plan_digest=self.digest,
            block_id=block.block_id,
            replicate_id=block.replicate_id,
            variant_id=block.variant_id,
            run_id=run.run_id,
            execution_position=run.execution_position,
            task_seed=block.task_seed,
            task_material_digest=block.task_material_digest,
            run_stochastic_seed=run.run_stochastic_seed,
            actor_stochastic_seeds=run.actor_stochastic_seeds,
            condition=run.assigned_condition,
            resolved_configuration_digest=resolved_configuration_digest,
        )


@dataclass(frozen=True, slots=True)
class ResolvedRunManifest:
    """Mechanically resolved authority for one assigned campaign run."""

    study_manifest_digest: str
    block_plan_digest: str
    block_id: str
    replicate_id: str
    variant_id: str
    run_id: str
    execution_position: int
    task_seed: int
    task_material_digest: str
    run_stochastic_seed: int
    actor_stochastic_seeds: tuple[int, ...]
    condition: CoordinationCondition
    resolved_configuration_digest: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.study_manifest_digest, "study manifest"),
            (self.block_plan_digest, "block plan"),
            (self.task_material_digest, "task material"),
            (self.resolved_configuration_digest, "resolved configuration"),
        ):
            _digest(value, name)
        for value in (self.block_id, self.replicate_id, self.variant_id, self.run_id):
            if not _SAFE_ID.fullmatch(value):
                raise ValueError("resolved run identifier is invalid")
        if type(self.execution_position) is not int or not 1 <= (
            self.execution_position
        ) <= len(_CONDITIONS):
            raise ValueError("resolved execution position is invalid")
        _seed(self.task_seed, "task seed")
        _seed(self.run_stochastic_seed, "run stochastic seed")
        if not self.actor_stochastic_seeds:
            raise ValueError("resolved actor seeds are required")
        for value in self.actor_stochastic_seeds:
            _seed(value, "actor stochastic seed")
        if not isinstance(self.condition, CoordinationCondition):
            raise ValueError("resolved condition is invalid")

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        plan: RandomizedBlockPlan,
        study_manifest_digest: str,
        resolved_configuration_digest: str,
    ) -> "ResolvedRunManifest":
        with path.open("r", encoding="utf-8") as source:
            document = load_json(source)
        expected_fields = {
            "schema_version",
            "study_manifest_digest",
            "block_plan_digest",
            "block_id",
            "replicate_id",
            "variant_id",
            "run_id",
            "execution_position",
            "task_seed",
            "task_material_digest",
            "run_stochastic_seed",
            "actor_stochastic_seeds",
            "condition",
            "resolved_configuration_digest",
        }
        if not isinstance(document, dict) or set(document) != expected_fields:
            raise ValueError("resolved run manifest fields differ")
        if document["schema_version"] != RESOLVED_RUN_SCHEMA:
            raise ValueError("resolved run manifest schema differs")
        run_id = document["run_id"]
        if not isinstance(run_id, str):
            raise ValueError("resolved run identifier is invalid")
        expected = plan.resolve(
            study_manifest_digest=study_manifest_digest,
            run_id=run_id,
            resolved_configuration_digest=resolved_configuration_digest,
        )
        if document != expected.document:
            raise ValueError("resolved run manifest differs from its block plan")
        return expected

    @property
    def document(self) -> dict[str, object]:
        return {
            "schema_version": RESOLVED_RUN_SCHEMA,
            "study_manifest_digest": self.study_manifest_digest,
            "block_plan_digest": self.block_plan_digest,
            "block_id": self.block_id,
            "replicate_id": self.replicate_id,
            "variant_id": self.variant_id,
            "run_id": self.run_id,
            "execution_position": self.execution_position,
            "task_seed": self.task_seed,
            "task_material_digest": self.task_material_digest,
            "run_stochastic_seed": self.run_stochastic_seed,
            "actor_stochastic_seeds": {
                str(ordinal): seed
                for ordinal, seed in enumerate(self.actor_stochastic_seeds)
            },
            "condition": self.condition.value,
            "resolved_configuration_digest": self.resolved_configuration_digest,
        }

    @property
    def digest(self) -> str:
        return digest_value(self.document)

    def write_once(self, path: Path) -> Path:
        return _write_once(path, canonical_json_bytes(self.document) + b"\n")


def _condition_order(
    master_seed: int, block_id: str
) -> tuple[CoordinationCondition, ...]:
    return tuple(
        sorted(
            _CONDITIONS,
            key=lambda condition: (
                digest_value(
                    {
                        "algorithm": ASSIGNMENT_ALGORITHM,
                        "master_seed": master_seed,
                        "block_id": block_id,
                        "condition": condition.value,
                    }
                ),
                condition.value,
            ),
        )
    )


def _derived_seed(
    master_seed: int, block_id: str, execution_position: int, stream: str
) -> int:
    digest = digest_value(
        {
            "algorithm": ASSIGNMENT_ALGORITHM,
            "master_seed": master_seed,
            "block_id": block_id,
            "execution_position": execution_position,
            "stream": stream,
        }
    )
    return int(digest[7:23], 16) % (2**31)


def _block(value: object) -> BlockAssignment:
    if not isinstance(value, dict) or set(value) != {
        "block_id",
        "replicate_id",
        "variant_id",
        "task_seed",
        "task_material_digest",
        "runs",
    }:
        raise ValueError("block assignment fields differ")
    raw_runs = value["runs"]
    if not isinstance(raw_runs, list):
        raise ValueError("block runs are invalid")
    for name in ("block_id", "replicate_id", "variant_id", "task_material_digest"):
        if not isinstance(value[name], str):
            raise ValueError("block assignment identity is invalid")
    return BlockAssignment(
        block_id=value["block_id"],
        replicate_id=value["replicate_id"],
        variant_id=value["variant_id"],
        task_seed=value["task_seed"],
        task_material_digest=value["task_material_digest"],
        runs=tuple(_run(run) for run in raw_runs),
    )


def _run(value: object) -> RunAssignment:
    if not isinstance(value, dict) or set(value) != {
        "run_id",
        "execution_position",
        "run_stochastic_seed",
        "actor_stochastic_seeds",
        "assigned_condition",
    }:
        raise ValueError("run assignment fields differ")
    actor_seeds = value["actor_stochastic_seeds"]
    if not isinstance(actor_seeds, dict) or set(actor_seeds) != {
        str(index) for index in range(len(actor_seeds))
    }:
        raise ValueError("actor stochastic seed ordinals differ")
    if not isinstance(value["run_id"], str) or not isinstance(
        value["assigned_condition"], str
    ):
        raise ValueError("run assignment identity is invalid")
    try:
        condition = CoordinationCondition(value["assigned_condition"])
    except ValueError as error:
        raise ValueError("assigned condition is invalid") from error
    return RunAssignment(
        run_id=value["run_id"],
        execution_position=value["execution_position"],
        run_stochastic_seed=value["run_stochastic_seed"],
        actor_stochastic_seeds=tuple(
            actor_seeds[str(index)] for index in range(len(actor_seeds))
        ),
        assigned_condition=condition,
    )


def _seed(value: object, name: str, *, maximum: int = 2**31 - 1) -> None:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{name} is invalid")


def _digest(value: object, name: str) -> None:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError(f"{name} digest is invalid")


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
                    raise RuntimeError("registered manifest already differs")
            finally:
                temporary.unlink(missing_ok=True)
            _fsync_directory(destination.parent)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    else:
        if existing != content:
            raise RuntimeError("registered manifest already differs")
    return destination


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
