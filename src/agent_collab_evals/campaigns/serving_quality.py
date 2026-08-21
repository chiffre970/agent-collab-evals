"""Evaluator-private, served-generation quality calibration for model serving."""

from __future__ import annotations

import csv
import hashlib
import json
import random
import re
import tomllib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..canonical import (
    DuplicateKeyError,
    canonical_json_bytes,
    digest_bytes,
    digest_file,
    load_json,
)


QUALITY_PROFILE_SCHEMA = "model-serving-quality-profile/v0alpha1"
QUALITY_SOURCES_SCHEMA = "model-serving-quality-sources/v0alpha1"
QUALITY_WORKLOAD_SCHEMA = "model-serving-quality-workload/v0alpha1"
QUALITY_RUN_SCHEMA = "model-serving-quality-run/v0alpha1"
QUALITY_POLICY_SCHEMA = "model-serving-quality-policy/v0alpha1"
QUALITY_DECISION_SCHEMA = "model-serving-quality-decision/v0alpha1"
_ANSWER = re.compile(r"<answer>\s*(.*?)\s*</answer>", re.IGNORECASE | re.DOTALL)
_HEX = set("0123456789abcdef")


class QualityValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DecodingProfile:
    mode: str
    enable_thinking: bool
    temperature_milli: int
    top_p_milli: int
    top_k: int
    min_p_milli: int
    max_tokens: int

    def request_fields(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature_milli / 1000,
            "top_p": self.top_p_milli / 1000,
            "top_k": self.top_k,
            "min_p": self.min_p_milli / 1000,
            "max_tokens": self.max_tokens,
            "chat_template_kwargs": {"enable_thinking": self.enable_thinking},
        }


@dataclass(frozen=True, slots=True)
class QualityFamily:
    family_id: str
    mode: str
    scorer: str


@dataclass(frozen=True, slots=True)
class QualityProfile:
    path: Path
    digest: str
    target_model: str
    target_revision: str
    repetitions: int
    cases_per_family: int
    seed_bytes: int
    max_concurrency: int
    request_timeout_seconds: int
    decoding: Mapping[str, DecodingProfile]
    families: Mapping[str, QualityFamily]

    @classmethod
    def load(cls, path: Path) -> "QualityProfile":
        resolved = path.resolve()
        try:
            with resolved.open("rb") as source:
                raw = tomllib.load(source)
        except tomllib.TOMLDecodeError as error:
            raise QualityValidationError("quality profile is not valid TOML") from error
        _validate_quality_profile(raw)
        materialization = _mapping(raw, "materialization")
        execution = _mapping(raw, "execution")
        decoding_raw = _mapping(raw, "decoding")
        decoding: dict[str, DecodingProfile] = {}
        for mode in ("non_thinking", "thinking"):
            value = _mapping(decoding_raw, mode)
            decoding[mode] = DecodingProfile(
                mode=mode,
                enable_thinking=_boolean(value, "enable_thinking"),
                temperature_milli=_nonnegative_int(value, "temperature_milli"),
                top_p_milli=_positive_int(value, "top_p_milli"),
                top_k=_positive_int(value, "top_k"),
                min_p_milli=_nonnegative_int(value, "min_p_milli"),
                max_tokens=_positive_int(value, "max_tokens"),
            )
        families = {
            str(value["id"]): QualityFamily(
                family_id=str(value["id"]),
                mode=str(value["mode"]),
                scorer=str(value["scorer"]),
            )
            for value in raw["families"]
        }
        return cls(
            path=resolved,
            digest=digest_file(resolved),
            target_model=str(raw["target_model"]),
            target_revision=str(raw["target_revision"]),
            repetitions=_positive_int(raw, "repetitions"),
            cases_per_family=_positive_int(materialization, "cases_per_family"),
            seed_bytes=_positive_int(materialization, "seed_bytes"),
            max_concurrency=_positive_int(execution, "max_concurrency"),
            request_timeout_seconds=_positive_int(
                execution, "request_timeout_seconds"
            ),
            decoding=decoding,
            families=families,
        )


@dataclass(frozen=True, slots=True)
class QualityCase:
    case_id: str
    family_id: str
    mode: str
    scorer: str
    prompt: str
    expected: str
    seed: int
    source_id: str
    source_index: int

    def to_document(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "family_id": self.family_id,
            "mode": self.mode,
            "scorer": self.scorer,
            "prompt": self.prompt,
            "expected": self.expected,
            "seed": self.seed,
            "source_id": self.source_id,
            "source_index": self.source_index,
        }


@dataclass(frozen=True, slots=True)
class QualityWorkload:
    document: Mapping[str, Any]
    digest: str
    cases: tuple[QualityCase, ...]


@dataclass(frozen=True, slots=True)
class QualityPolicy:
    path: Path
    digest: str
    quality_profile_digest: str
    quality_workload_digest: str
    repetitions: int
    case_count: int
    families: tuple[str, ...]
    aggregate_margin_ppm: int
    family_margin_ppm: int
    confidence_ppm: int
    bootstrap_resamples: int
    bootstrap_seed: int
    reference_measurement_id: str
    reference_receipt_digests: tuple[str, ...]
    clean_control_measurement_id: str
    clean_control_receipt_digests: tuple[str, ...]

    @classmethod
    def load(cls, path: Path) -> "QualityPolicy":
        resolved = path.resolve()
        try:
            with resolved.open("rb") as source:
                raw = tomllib.load(source)
        except tomllib.TOMLDecodeError as error:
            raise QualityValidationError("quality policy is not valid TOML") from error
        _validate_quality_policy(raw)
        noninferiority = _mapping(raw, "noninferiority")
        uncertainty = _mapping(raw, "uncertainty")
        calibration = _mapping(raw, "calibration")
        return cls(
            path=resolved,
            digest=digest_file(resolved),
            quality_profile_digest=str(raw["quality_profile_digest"]),
            quality_workload_digest=str(raw["quality_workload_digest"]),
            repetitions=_positive_int(raw, "repetitions"),
            case_count=_positive_int(raw, "case_count"),
            families=tuple(raw["families"]),
            aggregate_margin_ppm=_positive_int(
                noninferiority, "aggregate_margin_ppm"
            ),
            family_margin_ppm=_positive_int(noninferiority, "family_margin_ppm"),
            confidence_ppm=_positive_int(uncertainty, "confidence_ppm"),
            bootstrap_resamples=_positive_int(uncertainty, "resamples"),
            bootstrap_seed=_positive_int(uncertainty, "seed"),
            reference_measurement_id=str(calibration["reference_measurement_id"]),
            reference_receipt_digests=tuple(
                calibration["reference_receipt_digests"]
            ),
            clean_control_measurement_id=str(
                calibration["clean_control_measurement_id"]
            ),
            clean_control_receipt_digests=tuple(
                calibration["clean_control_receipt_digests"]
            ),
        )

    def validate_against(self, profile: QualityProfile) -> None:
        if self.quality_profile_digest != profile.digest:
            raise QualityValidationError("quality policy names a different profile")
        if self.repetitions != profile.repetitions:
            raise QualityValidationError("quality policy repetition count differs")
        if self.case_count != profile.cases_per_family * len(profile.families):
            raise QualityValidationError("quality policy case count differs")
        if self.families != tuple(sorted(profile.families)):
            raise QualityValidationError("quality policy family set differs")


def materialize_quality_workload(
    profile: QualityProfile,
    sources_path: Path,
    source_root: Path,
    selection_seed: bytes,
) -> dict[str, Any]:
    """Build a deterministic private workload from digest-verified sources."""

    if len(selection_seed) != profile.seed_bytes:
        raise QualityValidationError("selection seed length differs from profile")
    sources, source_profile_digest = _load_sources(sources_path, source_root)
    cases: list[QualityCase] = []
    cases.extend(_materialize_mmlu(profile, sources["mmlu"], selection_seed))
    cases.extend(_materialize_gsm8k(profile, sources["gsm8k"], selection_seed))
    cases.extend(
        _materialize_bbh(
            profile,
            (sources["bbh_date_understanding"], sources["bbh_logical_deduction"]),
            selection_seed,
        )
    )
    cases.extend(_materialize_structured(profile, selection_seed))
    cases.sort(key=lambda case: (case.family_id, case.case_id))
    _validate_case_population(profile, cases)
    source_receipts = [
        {
            "id": source["id"],
            "revision": source["revision"],
            "sha256": source["sha256"],
        }
        for source in sorted(sources.values(), key=lambda value: value["id"])
    ]
    return {
        "schema_version": QUALITY_WORKLOAD_SCHEMA,
        "profile_digest": profile.digest,
        "source_profile_digest": source_profile_digest,
        "selection_seed_commitment": digest_bytes(selection_seed),
        "case_count": len(cases),
        "source_receipts": source_receipts,
        "cases": [case.to_document() for case in cases],
    }


def load_quality_workload(path: Path, profile: QualityProfile) -> QualityWorkload:
    resolved = path.resolve()
    try:
        with resolved.open("r", encoding="utf-8") as source:
            raw = load_json(source)
    except (json.JSONDecodeError, DuplicateKeyError) as error:
        raise QualityValidationError("quality workload is not unambiguous JSON") from error
    if not isinstance(raw, dict):
        raise QualityValidationError("quality workload must be an object")
    expected = {
        "schema_version",
        "profile_digest",
        "source_profile_digest",
        "selection_seed_commitment",
        "case_count",
        "source_receipts",
        "cases",
    }
    if set(raw) != expected:
        raise QualityValidationError("quality workload fields differ")
    if raw["schema_version"] != QUALITY_WORKLOAD_SCHEMA:
        raise QualityValidationError("unsupported quality workload schema")
    if raw["profile_digest"] != profile.digest:
        raise QualityValidationError("quality workload names a different profile")
    if not _is_digest(raw["source_profile_digest"]) or not _is_digest(
        raw["selection_seed_commitment"]
    ):
        raise QualityValidationError("quality workload digests are invalid")
    if not isinstance(raw["source_receipts"], list) or not raw["source_receipts"]:
        raise QualityValidationError("quality workload source receipts are invalid")
    cases_raw = raw["cases"]
    if not isinstance(cases_raw, list) or raw["case_count"] != len(cases_raw):
        raise QualityValidationError("quality workload case count differs")
    cases = tuple(_parse_case(value, profile) for value in cases_raw)
    _validate_case_population(profile, cases)
    return QualityWorkload(raw, digest_file(resolved), cases)


def build_quality_requests(
    profile: QualityProfile,
    workload: QualityWorkload,
    *,
    served_model_name: str,
) -> tuple[dict[str, Any], ...]:
    requests: list[dict[str, Any]] = []
    for case in workload.cases:
        decoding = profile.decoding[case.mode]
        requests.append(
            {
                "case_id": case.case_id,
                "body": {
                    "model": served_model_name,
                    "messages": [{"role": "user", "content": case.prompt}],
                    "seed": case.seed,
                    "stream": False,
                    **decoding.request_fields(),
                },
            }
        )
    return tuple(requests)


def score_quality_outputs(
    profile: QualityProfile,
    workload: QualityWorkload,
    outputs: Mapping[str, str],
    *,
    repetition: int,
    role: str,
) -> dict[str, Any]:
    if role not in {"reference", "candidate", "clean_control"}:
        raise QualityValidationError("invalid quality run role")
    if not 1 <= repetition <= profile.repetitions:
        raise QualityValidationError("quality repetition is invalid")
    expected_ids = {case.case_id for case in workload.cases}
    if set(outputs) != expected_ids:
        raise QualityValidationError("quality output case set differs")
    results: list[dict[str, Any]] = []
    family_passes = {family_id: 0 for family_id in profile.families}
    family_counts = {family_id: 0 for family_id in profile.families}
    for case in workload.cases:
        content = outputs[case.case_id]
        if not isinstance(content, str):
            raise QualityValidationError("quality output must be text")
        extracted = _extract_answer(content)
        normalized_expected = _normalize_answer(case.scorer, case.expected)
        normalized_actual = (
            None if extracted is None else _normalize_answer(case.scorer, extracted)
        )
        passed = normalized_actual == normalized_expected
        family_counts[case.family_id] += 1
        family_passes[case.family_id] += int(passed)
        results.append(
            {
                "case_id": case.case_id,
                "family_id": case.family_id,
                "passed": passed,
                "extracted": extracted,
                "content_digest": digest_bytes(content.encode("utf-8")),
            }
        )
    total_passes = sum(family_passes.values())
    total = len(workload.cases)
    return {
        "schema_version": QUALITY_RUN_SCHEMA,
        "profile_digest": profile.digest,
        "workload_digest": workload.digest,
        "role": role,
        "repetition": repetition,
        "case_count": total,
        "pass_count": total_passes,
        "score_ppm": _ratio_ppm(total_passes, total),
        "family_scores": {
            family_id: {
                "case_count": family_counts[family_id],
                "pass_count": family_passes[family_id],
                "score_ppm": _ratio_ppm(
                    family_passes[family_id], family_counts[family_id]
                ),
            }
            for family_id in sorted(profile.families)
        },
        "cases": results,
    }


def compare_quality_runs(
    reference: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    for key in ("profile_digest", "workload_digest", "repetition", "case_count"):
        if reference.get(key) != candidate.get(key):
            raise QualityValidationError(f"paired quality run {key} differs")
    reference_cases = {
        value["case_id"]: value for value in _list(reference, "cases")
    }
    candidate_cases = {
        value["case_id"]: value for value in _list(candidate, "cases")
    }
    if set(reference_cases) != set(candidate_cases):
        raise QualityValidationError("paired quality case sets differ")
    transitions = {"pass_pass": 0, "pass_fail": 0, "fail_pass": 0, "fail_fail": 0}
    for case_id in reference_cases:
        reference_passed = _case_passed(reference_cases[case_id])
        candidate_passed = _case_passed(candidate_cases[case_id])
        transitions[
            ("pass" if reference_passed else "fail")
            + "_"
            + ("pass" if candidate_passed else "fail")
        ] += 1
    return {
        "reference_score_ppm": _integer(reference, "score_ppm"),
        "candidate_score_ppm": _integer(candidate, "score_ppm"),
        "delta_ppm": _integer(candidate, "score_ppm")
        - _integer(reference, "score_ppm"),
        "paired_transitions": transitions,
    }


def evaluate_quality_series(
    policy: QualityPolicy,
    reference_runs: Iterable[Mapping[str, Any]],
    candidate_runs: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply the frozen paired non-inferiority rule to two served-output series."""

    references = _validate_quality_series(policy, reference_runs, {"reference"})
    candidates = _validate_quality_series(
        policy, candidate_runs, {"candidate", "clean_control"}
    )
    candidate_roles = {str(run["role"]) for run in candidates}
    if len(candidate_roles) != 1:
        raise QualityValidationError("candidate quality roles differ")

    clusters: dict[str, dict[str, list[int]]] = {
        family_id: {} for family_id in policy.families
    }
    transitions = {"pass_pass": 0, "pass_fail": 0, "fail_pass": 0, "fail_fail": 0}
    reference_passes = 0
    candidate_passes = 0
    family_reference_passes = {family_id: 0 for family_id in policy.families}
    family_candidate_passes = {family_id: 0 for family_id in policy.families}

    for reference, candidate in zip(references, candidates, strict=True):
        reference_cases = {value["case_id"]: value for value in reference["cases"]}
        candidate_cases = {value["case_id"]: value for value in candidate["cases"]}
        if set(reference_cases) != set(candidate_cases):
            raise QualityValidationError("paired quality case sets differ")
        for case_id in sorted(reference_cases):
            reference_case = reference_cases[case_id]
            candidate_case = candidate_cases[case_id]
            if reference_case["family_id"] != candidate_case["family_id"]:
                raise QualityValidationError("paired quality case families differ")
            family_id = str(reference_case["family_id"])
            reference_passed = _case_passed(reference_case)
            candidate_passed = _case_passed(candidate_case)
            reference_passes += int(reference_passed)
            candidate_passes += int(candidate_passed)
            family_reference_passes[family_id] += int(reference_passed)
            family_candidate_passes[family_id] += int(candidate_passed)
            clusters[family_id].setdefault(case_id, []).append(
                int(candidate_passed) - int(reference_passed)
            )
            transition = (
                ("pass" if reference_passed else "fail")
                + "_"
                + ("pass" if candidate_passed else "fail")
            )
            transitions[transition] += 1

    for family_id, cases in clusters.items():
        if len(cases) * policy.repetitions != (
            policy.case_count // len(policy.families)
        ) * policy.repetitions:
            raise QualityValidationError(
                f"quality family {family_id} case count differs"
            )
        if any(len(values) != policy.repetitions for values in cases.values()):
            raise QualityValidationError("quality case repetition count differs")

    paired_observations = policy.case_count * policy.repetitions
    aggregate_delta_ppm = _ratio_ppm(
        candidate_passes - reference_passes, paired_observations
    )
    family_observations = (
        policy.case_count // len(policy.families)
    ) * policy.repetitions
    family_delta_ppm = {
        family_id: _ratio_ppm(
            family_candidate_passes[family_id]
            - family_reference_passes[family_id],
            family_observations,
        )
        for family_id in policy.families
    }
    aggregate_lower_bound_ppm, family_lower_bound_ppm = _bootstrap_lower_bounds(
        policy, clusters
    )

    failures: list[str] = []
    if aggregate_delta_ppm < -policy.aggregate_margin_ppm:
        failures.append("aggregate observed quality delta exceeds margin")
    if aggregate_lower_bound_ppm < -policy.aggregate_margin_ppm:
        failures.append("aggregate quality lower bound exceeds margin")
    family_documents: dict[str, dict[str, Any]] = {}
    for family_id in policy.families:
        observed_passes = family_delta_ppm[family_id] >= -policy.family_margin_ppm
        lower_bound_passes = (
            family_lower_bound_ppm[family_id] >= -policy.family_margin_ppm
        )
        if not observed_passes:
            failures.append(f"{family_id} observed quality delta exceeds margin")
        if not lower_bound_passes:
            failures.append(f"{family_id} quality lower bound exceeds margin")
        family_documents[family_id] = {
            "reference_passes": family_reference_passes[family_id],
            "candidate_passes": family_candidate_passes[family_id],
            "paired_observations": family_observations,
            "delta_ppm": family_delta_ppm[family_id],
            "lower_bound_ppm": family_lower_bound_ppm[family_id],
            "margin_ppm": policy.family_margin_ppm,
            "observed_passes": observed_passes,
            "lower_bound_passes": lower_bound_passes,
        }

    return {
        "schema_version": QUALITY_DECISION_SCHEMA,
        "quality_policy_digest": policy.digest,
        "quality_profile_digest": policy.quality_profile_digest,
        "quality_workload_digest": policy.quality_workload_digest,
        "candidate_role": next(iter(candidate_roles)),
        "repetitions": policy.repetitions,
        "case_count": policy.case_count,
        "paired_observations": paired_observations,
        "eligible": not failures,
        "aggregate": {
            "reference_passes": reference_passes,
            "candidate_passes": candidate_passes,
            "delta_ppm": aggregate_delta_ppm,
            "lower_bound_ppm": aggregate_lower_bound_ppm,
            "margin_ppm": policy.aggregate_margin_ppm,
            "observed_passes": aggregate_delta_ppm
            >= -policy.aggregate_margin_ppm,
            "lower_bound_passes": aggregate_lower_bound_ppm
            >= -policy.aggregate_margin_ppm,
        },
        "families": family_documents,
        "paired_transitions": transitions,
        "uncertainty": {
            "method": "stratified_paired_case_cluster_percentile_bootstrap",
            "confidence_ppm": policy.confidence_ppm,
            "resamples": policy.bootstrap_resamples,
            "seed": policy.bootstrap_seed,
            "prng": "splitmix64_modulo",
            "quantile": "lower_order_statistic_floor",
        },
        "failures": failures,
    }


def write_private_workload(path: Path, document: Mapping[str, Any]) -> Path:
    """Create a private workload once; never replace an existing selection."""

    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = canonical_json_bytes(document) + b"\n"
    try:
        with destination.open("xb") as target:
            target.write(content)
            target.flush()
    except FileExistsError:
        if destination.read_bytes() != content:
            raise QualityValidationError(
                "private quality workload already exists with different content"
            )
    return destination


def _load_sources(
    sources_path: Path, source_root: Path
) -> tuple[dict[str, dict[str, Any]], str]:
    resolved = sources_path.resolve()
    try:
        with resolved.open("rb") as source:
            raw = tomllib.load(source)
    except tomllib.TOMLDecodeError as error:
        raise QualityValidationError("quality sources are not valid TOML") from error
    if set(raw) != {"schema_version", "sources"}:
        raise QualityValidationError("quality source profile fields differ")
    if raw["schema_version"] != QUALITY_SOURCES_SCHEMA:
        raise QualityValidationError("unsupported quality source profile")
    if not isinstance(raw["sources"], list):
        raise QualityValidationError("quality source list is invalid")
    sources: dict[str, dict[str, Any]] = {}
    expected_keys = {"id", "format", "filename", "url", "revision", "sha256"}
    for value in raw["sources"]:
        if not isinstance(value, dict) or set(value) != expected_keys:
            raise QualityValidationError("quality source fields differ")
        source_id = value["id"]
        filename = value["filename"]
        if (
            not isinstance(source_id, str)
            or source_id in sources
            or not isinstance(filename, str)
            or Path(filename).name != filename
        ):
            raise QualityValidationError("quality source identity is invalid")
        path = source_root.resolve() / filename
        expected_digest = f"sha256:{value['sha256']}"
        if digest_file(path) != expected_digest:
            raise QualityValidationError(f"quality source {source_id} digest differs")
        sources[source_id] = {**value, "path": path}
    expected_ids = {
        "mmlu",
        "gsm8k",
        "bbh_date_understanding",
        "bbh_logical_deduction",
    }
    if set(sources) != expected_ids:
        raise QualityValidationError("quality source set differs")
    return sources, digest_file(resolved)


def _materialize_mmlu(
    profile: QualityProfile, source: Mapping[str, Any], seed: bytes
) -> list[QualityCase]:
    with Path(source["path"]).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    required = {"Question", "A", "B", "C", "D", "Answer", "Subject"}
    if not rows or not required.issubset(rows[0]):
        raise QualityValidationError("MMLU source fields differ")
    by_subject: dict[str, list[tuple[int, Mapping[str, str]]]] = {}
    for index, row in enumerate(rows):
        by_subject.setdefault(row["Subject"], []).append((index, row))
    rng = _rng(seed, "mmlu")
    subjects = sorted(by_subject)
    rng.shuffle(subjects)
    if len(subjects) < profile.cases_per_family:
        raise QualityValidationError("MMLU has too few subjects")
    result: list[QualityCase] = []
    for subject in subjects[: profile.cases_per_family]:
        index, row = rng.choice(by_subject[subject])
        prompt = (
            f"{row['Question']}\n\n"
            f"A. {row['A']}\nB. {row['B']}\nC. {row['C']}\nD. {row['D']}\n\n"
            "Give the best answer. End with one line in the form "
            "<answer>X</answer>, replacing X with the single option letter."
        )
        result.append(
            _case(seed, "mmlu", "non_thinking", "choice", prompt, row["Answer"], source["id"], index)
        )
    return result


def _materialize_gsm8k(
    profile: QualityProfile, source: Mapping[str, Any], seed: bytes
) -> list[QualityCase]:
    rows: list[tuple[int, Mapping[str, Any]]] = []
    with Path(source["path"]).open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise QualityValidationError("GSM8K source is invalid JSONL") from error
            if not isinstance(value, dict) or set(value) != {"question", "answer"}:
                raise QualityValidationError("GSM8K source fields differ")
            rows.append((index, value))
    selected = _sample(rows, profile.cases_per_family, _rng(seed, "gsm8k"))
    result: list[QualityCase] = []
    for index, row in selected:
        match = re.search(r"####\s*([^\n]+)\s*$", str(row["answer"]))
        if match is None:
            raise QualityValidationError("GSM8K answer has no final value")
        prompt = (
            f"Solve this problem carefully and concisely:\n\n{row['question']}\n\n"
            "End with one line in the form <answer>X</answer>, replacing X "
            "with only the numeric answer and no units."
        )
        result.append(
            _case(seed, "gsm8k", "thinking", "numeric", prompt, match.group(1), source["id"], index)
        )
    return result


def _materialize_bbh(
    profile: QualityProfile,
    sources: tuple[Mapping[str, Any], Mapping[str, Any]],
    seed: bytes,
) -> list[QualityCase]:
    per_source = profile.cases_per_family // len(sources)
    if per_source * len(sources) != profile.cases_per_family:
        raise QualityValidationError("BBH family cannot be split evenly")
    result: list[QualityCase] = []
    for source in sources:
        try:
            with Path(source["path"]).open("r", encoding="utf-8") as handle:
                document = load_json(handle)
        except (json.JSONDecodeError, DuplicateKeyError) as error:
            raise QualityValidationError("BBH source is invalid JSON") from error
        if not isinstance(document, dict) or set(document) != {"canary", "examples"}:
            raise QualityValidationError("BBH source fields differ")
        examples = document["examples"]
        if not isinstance(examples, list):
            raise QualityValidationError("BBH examples are invalid")
        selected = _sample(
            list(enumerate(examples)), per_source, _rng(seed, str(source["id"]))
        )
        for index, row in selected:
            if not isinstance(row, dict) or set(row) != {"input", "target"}:
                raise QualityValidationError("BBH example fields differ")
            target = str(row["target"]).strip().strip("()")
            prompt = (
                f"{row['input']}\n\n"
                "Reason through the problem concisely. End with one line in the "
                "form <answer>X</answer>, replacing X with the single option letter."
            )
            result.append(
                _case(seed, "bbh_reasoning", "thinking", "choice", prompt, target, str(source["id"]), index)
            )
    return result


def _materialize_structured(
    profile: QualityProfile, seed: bytes
) -> list[QualityCase]:
    rng = _rng(seed, "structured_transform")
    categories = ("amber", "blue", "green")
    result: list[QualityCase] = []
    for index in range(profile.cases_per_family):
        records = []
        for record_index in range(7):
            records.append(
                {
                    "id": f"r{record_index + 1}",
                    "category": rng.choice(categories),
                    "score": rng.randrange(10, 100),
                }
            )
        category = rng.choice(categories)
        threshold = rng.randrange(25, 75)
        selected = sorted(
            record["id"]
            for record in records
            if record["category"] == category and record["score"] >= threshold
        )
        expected = ",".join(selected) if selected else "NONE"
        prompt = (
            "Given these records:\n"
            + "\n".join(
                f"- {record['id']}: category={record['category']}, score={record['score']}"
                for record in records
            )
            + f"\n\nSelect IDs whose category is {category} and score is at least {threshold}. "
            "Sort IDs lexicographically and join them with commas; use NONE if empty. "
            "End with one line in the form <answer>X</answer>, replacing X with "
            "only that comma-joined value."
        )
        result.append(
            _case(seed, "structured_transform", "non_thinking", "exact", prompt, expected, "evaluator_generated", index)
        )
    return result


def _case(
    seed: bytes,
    family_id: str,
    mode: str,
    scorer: str,
    prompt: str,
    expected: str,
    source_id: str,
    source_index: int,
) -> QualityCase:
    identity = hashlib.sha256(
        seed + f"\0{family_id}\0{source_id}\0{source_index}".encode("utf-8")
    ).hexdigest()
    request_seed = int(identity[16:24], 16) & 0x7FFFFFFF
    return QualityCase(
        case_id=f"{family_id}-{identity[:16]}",
        family_id=family_id,
        mode=mode,
        scorer=scorer,
        prompt=prompt,
        expected=str(expected),
        seed=request_seed,
        source_id=source_id,
        source_index=source_index,
    )


def _parse_case(value: Any, profile: QualityProfile) -> QualityCase:
    expected_keys = {
        "case_id",
        "family_id",
        "mode",
        "scorer",
        "prompt",
        "expected",
        "seed",
        "source_id",
        "source_index",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise QualityValidationError("quality case fields differ")
    family_id = value["family_id"]
    if family_id not in profile.families:
        raise QualityValidationError("quality case family is unknown")
    family = profile.families[family_id]
    if value["mode"] != family.mode or value["scorer"] != family.scorer:
        raise QualityValidationError("quality case family contract differs")
    for key in ("case_id", "prompt", "expected", "source_id"):
        if not isinstance(value[key], str) or not value[key]:
            raise QualityValidationError(f"quality case {key} is invalid")
    seed = value["seed"]
    source_index = value["source_index"]
    if (
        not isinstance(seed, int)
        or isinstance(seed, bool)
        or not 0 <= seed <= 0x7FFFFFFF
        or not isinstance(source_index, int)
        or isinstance(source_index, bool)
        or source_index < 0
    ):
        raise QualityValidationError("quality case numeric identity is invalid")
    return QualityCase(
        case_id=value["case_id"],
        family_id=family_id,
        mode=value["mode"],
        scorer=value["scorer"],
        prompt=value["prompt"],
        expected=value["expected"],
        seed=seed,
        source_id=value["source_id"],
        source_index=source_index,
    )


def _validate_case_population(
    profile: QualityProfile, cases: Iterable[QualityCase]
) -> None:
    values = tuple(cases)
    if len({case.case_id for case in values}) != len(values):
        raise QualityValidationError("quality case IDs repeat")
    counts = {family_id: 0 for family_id in profile.families}
    for case in values:
        if case.family_id not in counts:
            raise QualityValidationError("quality case family is unknown")
        counts[case.family_id] += 1
    if any(count != profile.cases_per_family for count in counts.values()):
        raise QualityValidationError("quality family case count differs")


def _validate_quality_series(
    policy: QualityPolicy,
    runs: Iterable[Mapping[str, Any]],
    allowed_roles: set[str],
) -> tuple[Mapping[str, Any], ...]:
    supplied = tuple(runs)
    if any(not isinstance(value, Mapping) for value in supplied):
        raise QualityValidationError("quality series runs must be mappings")
    values = tuple(
        sorted(supplied, key=lambda value: _integer(value, "repetition"))
    )
    if len(values) != policy.repetitions:
        raise QualityValidationError("quality series repetition count differs")
    if tuple(_integer(value, "repetition") for value in values) != tuple(
        range(1, policy.repetitions + 1)
    ):
        raise QualityValidationError("quality series repetitions differ")
    expected_run_keys = {
        "schema_version",
        "profile_digest",
        "workload_digest",
        "role",
        "repetition",
        "case_count",
        "pass_count",
        "score_ppm",
        "family_scores",
        "cases",
    }
    expected_case_keys = {
        "case_id",
        "family_id",
        "passed",
        "extracted",
        "content_digest",
    }
    canonical_case_families: dict[str, str] | None = None
    for run in values:
        if set(run) != expected_run_keys:
            raise QualityValidationError("quality run fields differ")
        if run["schema_version"] != QUALITY_RUN_SCHEMA:
            raise QualityValidationError("quality run schema differs")
        if run["profile_digest"] != policy.quality_profile_digest:
            raise QualityValidationError("quality run profile digest differs")
        if run["workload_digest"] != policy.quality_workload_digest:
            raise QualityValidationError("quality run workload digest differs")
        if run["role"] not in allowed_roles:
            raise QualityValidationError("quality run role differs")
        if _positive_int(run, "case_count") != policy.case_count:
            raise QualityValidationError("quality run case count differs")
        cases = _list(run, "cases")
        if len(cases) != policy.case_count:
            raise QualityValidationError("quality run case list differs")
        case_families: dict[str, str] = {}
        family_passes = {family_id: 0 for family_id in policy.families}
        family_counts = {family_id: 0 for family_id in policy.families}
        for case in cases:
            if not isinstance(case, dict) or set(case) != expected_case_keys:
                raise QualityValidationError("quality case score fields differ")
            case_id = case["case_id"]
            family_id = case["family_id"]
            if (
                not isinstance(case_id, str)
                or not case_id
                or case_id in case_families
                or family_id not in policy.families
                or not _is_digest(case["content_digest"])
                or (
                    case["extracted"] is not None
                    and not isinstance(case["extracted"], str)
                )
            ):
                raise QualityValidationError("quality case score is invalid")
            passed = _case_passed(case)
            case_families[case_id] = str(family_id)
            family_counts[str(family_id)] += 1
            family_passes[str(family_id)] += int(passed)
        if canonical_case_families is None:
            canonical_case_families = case_families
        elif case_families != canonical_case_families:
            raise QualityValidationError("quality series case identities differ")
        expected_family_count = policy.case_count // len(policy.families)
        if any(count != expected_family_count for count in family_counts.values()):
            raise QualityValidationError("quality run family count differs")
        pass_count = sum(family_passes.values())
        if _nonnegative_int(run, "pass_count") != pass_count:
            raise QualityValidationError("quality run pass count differs")
        if _nonnegative_int(run, "score_ppm") != _ratio_ppm(
            pass_count, policy.case_count
        ):
            raise QualityValidationError("quality run score differs")
        family_scores = _mapping(run, "family_scores")
        if set(family_scores) != set(policy.families):
            raise QualityValidationError("quality run family scores differ")
        for family_id in policy.families:
            score = _mapping(family_scores, family_id)
            if set(score) != {"case_count", "pass_count", "score_ppm"}:
                raise QualityValidationError("quality family score fields differ")
            if (
                _positive_int(score, "case_count") != expected_family_count
                or _nonnegative_int(score, "pass_count")
                != family_passes[family_id]
                or _nonnegative_int(score, "score_ppm")
                != _ratio_ppm(family_passes[family_id], expected_family_count)
            ):
                raise QualityValidationError("quality family score differs")
    return values


def _bootstrap_lower_bounds(
    policy: QualityPolicy,
    clusters: Mapping[str, Mapping[str, list[int]]],
) -> tuple[int, dict[str, int]]:
    rng = _SplitMix64(policy.bootstrap_seed)
    aggregate_samples: list[int] = []
    family_samples: dict[str, list[int]] = {
        family_id: [] for family_id in policy.families
    }
    clusters_by_family = {
        family_id: tuple(
            sum(values) for _, values in sorted(clusters[family_id].items())
        )
        for family_id in policy.families
    }
    family_observations = (
        policy.case_count // len(policy.families)
    ) * policy.repetitions
    aggregate_observations = policy.case_count * policy.repetitions
    for _ in range(policy.bootstrap_resamples):
        aggregate_delta = 0
        for family_id in policy.families:
            values = clusters_by_family[family_id]
            family_delta = sum(values[rng.index(len(values))] for _ in values)
            family_samples[family_id].append(
                _ratio_ppm(family_delta, family_observations)
            )
            aggregate_delta += family_delta
        aggregate_samples.append(_ratio_ppm(aggregate_delta, aggregate_observations))
    tail_index = (
        (policy.bootstrap_resamples - 1) * (1_000_000 - policy.confidence_ppm)
    ) // 1_000_000
    aggregate_samples.sort()
    lower_by_family: dict[str, int] = {}
    for family_id, samples in family_samples.items():
        samples.sort()
        lower_by_family[family_id] = samples[tail_index]
    return aggregate_samples[tail_index], lower_by_family


class _SplitMix64:
    """Small fixed PRNG used only to make bootstrap resampling reproducible."""

    _MASK = (1 << 64) - 1

    def __init__(self, seed: int) -> None:
        self._state = seed & self._MASK

    def index(self, upper: int) -> int:
        if upper <= 0:
            raise QualityValidationError("bootstrap cluster population is empty")
        self._state = (self._state + 0x9E3779B97F4A7C15) & self._MASK
        value = self._state
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & self._MASK
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & self._MASK
        value ^= value >> 31
        return value % upper


def _validate_quality_policy(raw: Mapping[str, Any]) -> None:
    expected_top = {
        "schema_version",
        "phase",
        "quality_profile_digest",
        "quality_workload_digest",
        "pairing",
        "repetitions",
        "case_count",
        "families",
        "decision_rule",
        "malformed_response_policy",
        "noninferiority",
        "uncertainty",
        "calibration",
    }
    if set(raw) != expected_top:
        raise QualityValidationError("quality policy fields differ")
    expected_literals = {
        "schema_version": QUALITY_POLICY_SCHEMA,
        "phase": "calibration_frozen_v2",
        "pairing": "same_case_seed_and_repetition",
        "decision_rule": "paired_aggregate_and_family_noninferiority",
        "malformed_response_policy": "fail_case",
    }
    if any(raw.get(key) != value for key, value in expected_literals.items()):
        raise QualityValidationError("unsupported quality policy")
    if not _is_digest(raw.get("quality_profile_digest")) or not _is_digest(
        raw.get("quality_workload_digest")
    ):
        raise QualityValidationError("quality policy digests are invalid")
    if _positive_int(raw, "repetitions") != 3 or _positive_int(
        raw, "case_count"
    ) != 64:
        raise QualityValidationError("quality policy population differs")
    expected_families = [
        "bbh_reasoning",
        "gsm8k",
        "mmlu",
        "structured_transform",
    ]
    if raw.get("families") != expected_families:
        raise QualityValidationError("quality policy families differ")

    noninferiority = _mapping(raw, "noninferiority")
    if set(noninferiority) != {
        "comparison",
        "aggregate_margin_ppm",
        "family_margin_ppm",
        "inclusive",
    }:
        raise QualityValidationError("quality noninferiority fields differ")
    if (
        noninferiority.get("comparison")
        != "candidate_minus_contemporaneous_reference"
        or _positive_int(noninferiority, "aggregate_margin_ppm") != 31_250
        or _positive_int(noninferiority, "family_margin_ppm") != 62_500
        or _boolean(noninferiority, "inclusive") is not True
    ):
        raise QualityValidationError("quality noninferiority contract differs")

    uncertainty = _mapping(raw, "uncertainty")
    expected_uncertainty_literals = {
        "method": "stratified_paired_case_cluster_percentile_bootstrap",
        "prng": "splitmix64_modulo",
        "quantile": "lower_order_statistic_floor",
        "cluster": "case_id_with_three_repetitions",
        "stratify": "family_id",
    }
    if set(uncertainty) != {
        *expected_uncertainty_literals,
        "confidence_ppm",
        "resamples",
        "seed",
    } or any(
        uncertainty.get(key) != value
        for key, value in expected_uncertainty_literals.items()
    ):
        raise QualityValidationError("quality uncertainty contract differs")
    if (
        _positive_int(uncertainty, "confidence_ppm") != 950_000
        or _positive_int(uncertainty, "resamples") != 100_000
        or _positive_int(uncertainty, "seed") != 20_260_821
    ):
        raise QualityValidationError("quality uncertainty parameters differ")

    calibration = _mapping(raw, "calibration")
    expected_calibration_keys = {
        "reference_measurement_id",
        "reference_receipt_digests",
        "clean_control_measurement_id",
        "clean_control_receipt_digests",
        "reference_passes",
        "clean_control_passes",
        "paired_observations",
        "observed_delta_ppm",
        "aggregate_lower_bound_ppm",
        "worst_family_observed_delta_ppm",
        "worst_family_lower_bound_ppm",
        "clean_control_expected_noninferior",
    }
    if set(calibration) != expected_calibration_keys:
        raise QualityValidationError("quality calibration fields differ")
    for key in ("reference_measurement_id", "clean_control_measurement_id"):
        if not isinstance(calibration.get(key), str) or not calibration[key]:
            raise QualityValidationError("quality calibration identity is invalid")
    for key in ("reference_receipt_digests", "clean_control_receipt_digests"):
        digests = calibration.get(key)
        if (
            not isinstance(digests, list)
            or len(digests) != 3
            or len(set(digests)) != 3
            or not all(_is_digest(value) for value in digests)
        ):
            raise QualityValidationError("quality calibration receipts are invalid")
    reference_passes = _nonnegative_int(calibration, "reference_passes")
    clean_control_passes = _nonnegative_int(calibration, "clean_control_passes")
    paired_observations = _positive_int(calibration, "paired_observations")
    if (
        paired_observations != 192
        or reference_passes > paired_observations
        or clean_control_passes > paired_observations
        or _integer(calibration, "observed_delta_ppm")
        != _ratio_ppm(clean_control_passes - reference_passes, paired_observations)
        or _integer(calibration, "aggregate_lower_bound_ppm") != -20_833
        or _integer(calibration, "worst_family_observed_delta_ppm") != -20_833
        or _integer(calibration, "worst_family_lower_bound_ppm") != -62_500
        or _boolean(calibration, "clean_control_expected_noninferior") is not True
    ):
        raise QualityValidationError("quality calibration result differs")


def _validate_quality_profile(raw: Mapping[str, Any]) -> None:
    expected_top = {
        "schema_version",
        "phase",
        "target_model",
        "target_revision",
        "workload_schema",
        "repetitions",
        "pairing",
        "authoritative_evidence",
        "teacher_forced_role",
        "materialization",
        "execution",
        "decoding",
        "families",
        "decision",
    }
    if set(raw) != expected_top:
        raise QualityValidationError("quality profile fields differ")
    expected_literals = {
        "schema_version": QUALITY_PROFILE_SCHEMA,
        "phase": "qwen_quality_calibration_v2",
        "target_model": "Qwen/Qwen3-4B",
        "target_revision": "1cfa9a7208912126459214e8b04321603b3df60c",
        "workload_schema": QUALITY_WORKLOAD_SCHEMA,
        "pairing": "same_case_and_seed_reference_candidate",
        "authoritative_evidence": "served_generation",
        "teacher_forced_role": "diagnostic_only",
    }
    for key, expected in expected_literals.items():
        if raw.get(key) != expected:
            raise QualityValidationError(f"quality profile {key} differs")
    if _positive_int(raw, "repetitions") != 3:
        raise QualityValidationError("quality calibration requires three repetitions")
    materialization = _mapping(raw, "materialization")
    if set(materialization) != {
        "cases_per_family",
        "seed_bytes",
        "seed_visibility",
        "source_policy",
    }:
        raise QualityValidationError("quality materialization fields differ")
    if (
        _positive_int(materialization, "cases_per_family") != 16
        or _positive_int(materialization, "seed_bytes") != 32
        or materialization.get("seed_visibility") != "evaluator_private"
        or materialization.get("source_policy") != "content_digest_verified"
    ):
        raise QualityValidationError("quality materialization contract differs")
    execution = _mapping(raw, "execution")
    if set(execution) != {"max_concurrency", "request_timeout_seconds"}:
        raise QualityValidationError("quality execution fields differ")
    if (
        _positive_int(execution, "max_concurrency") != 8
        or _positive_int(execution, "request_timeout_seconds") != 300
    ):
        raise QualityValidationError("quality execution contract differs")
    decoding = _mapping(raw, "decoding")
    if set(decoding) != {"non_thinking", "thinking"}:
        raise QualityValidationError("quality decoding modes differ")
    expected_decoding = {
        "non_thinking": (False, 700, 800, 20, 0, 512),
        "thinking": (True, 600, 950, 20, 0, 4096),
    }
    for mode, expected in expected_decoding.items():
        value = _mapping(decoding, mode)
        if set(value) != {
            "enable_thinking",
            "temperature_milli",
            "top_p_milli",
            "top_k",
            "min_p_milli",
            "max_tokens",
        }:
            raise QualityValidationError("quality decoding fields differ")
        observed = (
            _boolean(value, "enable_thinking"),
            _nonnegative_int(value, "temperature_milli"),
            _positive_int(value, "top_p_milli"),
            _positive_int(value, "top_k"),
            _nonnegative_int(value, "min_p_milli"),
            _positive_int(value, "max_tokens"),
        )
        if observed != expected:
            raise QualityValidationError(f"quality decoding profile {mode} differs")
    families = raw.get("families")
    if not isinstance(families, list):
        raise QualityValidationError("quality families are invalid")
    observed_families: dict[str, tuple[str, str]] = {}
    for value in families:
        if not isinstance(value, dict) or set(value) != {"id", "mode", "scorer"}:
            raise QualityValidationError("quality family fields differ")
        family_id = value["id"]
        if not isinstance(family_id, str) or family_id in observed_families:
            raise QualityValidationError("quality family identity is invalid")
        observed_families[family_id] = (value["mode"], value["scorer"])
    expected_families = {
        "mmlu": ("non_thinking", "choice"),
        "structured_transform": ("non_thinking", "exact"),
        "gsm8k": ("thinking", "numeric"),
        "bbh_reasoning": ("thinking", "choice"),
    }
    if observed_families != expected_families:
        raise QualityValidationError("quality family set differs")
    decision = _mapping(raw, "decision")
    if decision != {
        "rule": "paired_family_and_aggregate_noninferiority",
        "margin_status": "unset_until_reference_and_clean-control_calibration",
        "uncertainty": "paired_case_bootstrap_to_be_frozen",
        "failure_policy": "any_missing_or_malformed_response_fails_its_case",
    }:
        raise QualityValidationError("quality decision contract differs")


def _extract_answer(content: str) -> str | None:
    matches = _ANSWER.findall(content)
    return matches[-1].strip() if matches else None


def _normalize_answer(scorer: str, value: str) -> str:
    normalized = " ".join(value.strip().split())
    if scorer == "choice":
        normalized = normalized.strip().strip("()[]{}.").upper()
        return normalized
    if scorer == "exact":
        return normalized.casefold()
    if scorer == "numeric":
        candidate = normalized.replace(",", "").replace("$", "")
        try:
            number = Decimal(candidate)
        except InvalidOperation:
            return candidate.casefold()
        if not number.is_finite():
            raise QualityValidationError("non-finite numeric quality answer")
        return format(number.normalize(), "f")
    raise QualityValidationError("unknown quality scorer")


def _rng(seed: bytes, namespace: str) -> random.Random:
    digest = hashlib.sha256(seed + b"\0" + namespace.encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest, "big"))


def _sample(values: list[Any], count: int, rng: random.Random) -> list[Any]:
    if len(values) < count:
        raise QualityValidationError("quality source has too few examples")
    indices = sorted(rng.sample(range(len(values)), count))
    return [values[index] for index in indices]


def _ratio_ppm(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise QualityValidationError("quality score denominator must be positive")
    return int(
        (Decimal(numerator) * Decimal(1_000_000) / Decimal(denominator)).to_integral_value(
            rounding=ROUND_HALF_EVEN
        )
    )


def _mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    result = value.get(key)
    if not isinstance(result, dict):
        raise QualityValidationError(f"quality {key} must be a table")
    return result


def _list(value: Mapping[str, Any], key: str) -> list[Any]:
    result = value.get(key)
    if not isinstance(result, list):
        raise QualityValidationError(f"quality {key} must be a list")
    return result


def _integer(value: Mapping[str, Any], key: str) -> int:
    result = value.get(key)
    if not isinstance(result, int) or isinstance(result, bool):
        raise QualityValidationError(f"quality {key} must be an integer")
    return result


def _positive_int(value: Mapping[str, Any], key: str) -> int:
    result = _integer(value, key)
    if result <= 0:
        raise QualityValidationError(f"quality {key} must be positive")
    return result


def _nonnegative_int(value: Mapping[str, Any], key: str) -> int:
    result = _integer(value, key)
    if result < 0:
        raise QualityValidationError(f"quality {key} must be nonnegative")
    return result


def _boolean(value: Mapping[str, Any], key: str) -> bool:
    result = value.get(key)
    if not isinstance(result, bool):
        raise QualityValidationError(f"quality {key} must be boolean")
    return result


def _is_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 71
        and all(character in _HEX for character in value[7:])
    )


def _case_passed(value: Any) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("passed"), bool):
        raise QualityValidationError("quality case score is invalid")
    return value["passed"]
