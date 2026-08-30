"""Evaluator-private workload materialization for model-serving studies."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..canonical import (
    DuplicateKeyError,
    canonical_json_bytes,
    digest_bytes,
    digest_file,
    load_json,
    parse_json,
)
from .model_serving import BenchmarkPlan, ManifestValidationError, load_benchmark_plan


HIDDEN_WORKLOAD_SCHEMA = "model-serving-hidden-workload/v0alpha1"
QUALITY_REQUESTS_SCHEMA = "model-serving-quality-requests/v0alpha1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_RESOURCE_NAMES = {
    "correctness_requests": "correctness.jsonl",
    "performance_profile": "performance.toml",
    "quality_requests": "quality-requests.json",
    "quality_workload": "quality-workload.json",
}
_DIGEST_NAMES = {
    "correctness_requests",
    "performance_profile",
    "quality_requests",
    "quality_profile",
    "quality_policy",
    "quality_workload",
}


class HiddenWorkloadError(ValueError):
    """The evaluator-private workload is invalid or changed."""


@dataclass(frozen=True, slots=True)
class HiddenWorkloadExpectations:
    campaign_manifest_digest: str
    hidden_contract_digest: str
    quality_profile_digest: str
    quality_policy_digest: str
    quality_workload_digest: str
    public_correctness_digest: str
    public_performance_digest: str
    required_gates: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "campaign_manifest_digest",
            "hidden_contract_digest",
            "quality_profile_digest",
            "quality_policy_digest",
            "quality_workload_digest",
            "public_correctness_digest",
            "public_performance_digest",
        ):
            if not _DIGEST.fullmatch(getattr(self, name)):
                raise HiddenWorkloadError(f"{name} must be SHA-256")
        if not self.required_gates or any(
            not isinstance(gate, str) or not gate for gate in self.required_gates
        ):
            raise HiddenWorkloadError("required gates must be nonempty strings")


@dataclass(frozen=True, slots=True)
class HiddenWorkloadBundle:
    manifest_path: Path
    manifest_digest: str
    selection_seed_commitment: str
    resource_paths: Mapping[str, Path]
    resource_digests: Mapping[str, str]


def materialize_hidden_workload(
    destination: Path,
    *,
    expectations: HiddenWorkloadExpectations,
    public_plan: BenchmarkPlan,
    selection_seed: bytes,
    seed_bytes: int,
    quality_workload_path: Path,
    quality_requests: Sequence[Mapping[str, Any]],
) -> HiddenWorkloadBundle:
    """Create a write-once hidden bundle without exposing its seed in metadata."""

    if len(selection_seed) != seed_bytes or seed_bytes < 16:
        raise HiddenWorkloadError("selection seed length differs")
    quality_workload = quality_workload_path.read_bytes()
    if digest_bytes(quality_workload) != expectations.quality_workload_digest:
        raise HiddenWorkloadError("quality workload digest differs")
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination, 0o700)

    hidden_seed = _derived_integer(selection_seed, b"performance", 0, 2**31)
    if hidden_seed == public_plan.seed:
        hidden_seed = (hidden_seed + 1) % (2**31)
    resources = {
        "correctness_requests": _correctness_bytes(selection_seed),
        "performance_profile": _performance_bytes(public_plan, hidden_seed),
        "quality_requests": _quality_request_bytes(quality_requests),
        "quality_workload": quality_workload,
    }
    resource_digests = {
        name: digest_bytes(content) for name, content in resources.items()
    }
    if (
        resource_digests["correctness_requests"]
        == expectations.public_correctness_digest
        or resource_digests["performance_profile"]
        == expectations.public_performance_digest
    ):
        raise HiddenWorkloadError("hidden workload is not disjoint from public data")

    for name, filename in _RESOURCE_NAMES.items():
        _write_once_private(destination / filename, resources[name])
    digests = {
        **resource_digests,
        "quality_profile": expectations.quality_profile_digest,
        "quality_policy": expectations.quality_policy_digest,
    }
    manifest = {
        "schema_version": HIDDEN_WORKLOAD_SCHEMA,
        "scope": "hidden",
        "agent_visible": False,
        "campaign_manifest_digest": expectations.campaign_manifest_digest,
        "hidden_contract_digest": expectations.hidden_contract_digest,
        "selection_seed_commitment": digest_bytes(selection_seed),
        "required_gates": list(expectations.required_gates),
        "resources": {
            name: {"path": _RESOURCE_NAMES[name], "digest": resource_digests[name]}
            for name in sorted(_RESOURCE_NAMES)
        },
        "digests": {name: digests[name] for name in sorted(_DIGEST_NAMES)},
    }
    manifest_path = destination / "manifest.json"
    _write_once_private(manifest_path, canonical_json_bytes(manifest) + b"\n")
    return load_hidden_workload(manifest_path, expectations, public_plan)


def load_hidden_workload(
    manifest_path: Path,
    expectations: HiddenWorkloadExpectations,
    public_plan: BenchmarkPlan,
    *,
    registered_manifest_digest: str | None = None,
) -> HiddenWorkloadBundle:
    """Resolve every private resource and verify it against registered authority."""

    manifest_path = manifest_path.resolve(strict=True)
    root = manifest_path.parent
    observed_manifest_digest = digest_file(manifest_path)
    if registered_manifest_digest is not None:
        if not _DIGEST.fullmatch(registered_manifest_digest):
            raise HiddenWorkloadError("registered bundle digest is invalid")
        if observed_manifest_digest != registered_manifest_digest:
            raise HiddenWorkloadError("registered bundle manifest digest differs")
    _assert_private_mode(manifest_path)
    try:
        with manifest_path.open("r", encoding="utf-8") as source:
            manifest = load_json(source)
    except (json.JSONDecodeError, DuplicateKeyError) as error:
        raise HiddenWorkloadError("hidden workload manifest is ambiguous") from error
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version",
        "scope",
        "agent_visible",
        "campaign_manifest_digest",
        "hidden_contract_digest",
        "selection_seed_commitment",
        "required_gates",
        "resources",
        "digests",
    }:
        raise HiddenWorkloadError("hidden workload manifest fields differ")
    expected_identity = {
        "schema_version": HIDDEN_WORKLOAD_SCHEMA,
        "scope": "hidden",
        "agent_visible": False,
        "campaign_manifest_digest": expectations.campaign_manifest_digest,
        "hidden_contract_digest": expectations.hidden_contract_digest,
        "required_gates": list(expectations.required_gates),
    }
    if any(manifest.get(name) != value for name, value in expected_identity.items()):
        raise HiddenWorkloadError("hidden workload identity differs")
    if not _DIGEST.fullmatch(str(manifest.get("selection_seed_commitment", ""))):
        raise HiddenWorkloadError("hidden workload seed commitment is invalid")
    resources = manifest.get("resources")
    digests = manifest.get("digests")
    if not isinstance(resources, dict) or set(resources) != set(_RESOURCE_NAMES):
        raise HiddenWorkloadError("hidden workload resources differ")
    if not isinstance(digests, dict) or set(digests) != _DIGEST_NAMES:
        raise HiddenWorkloadError("hidden workload digests differ")
    expected_registered = {
        "quality_profile": expectations.quality_profile_digest,
        "quality_policy": expectations.quality_policy_digest,
        "quality_workload": expectations.quality_workload_digest,
    }
    if any(digests.get(name) != value for name, value in expected_registered.items()):
        raise HiddenWorkloadError("registered hidden workload digest differs")

    paths: dict[str, Path] = {}
    resource_digests: dict[str, str] = {}
    for name, filename in _RESOURCE_NAMES.items():
        record = resources[name]
        if not isinstance(record, dict) or set(record) != {"path", "digest"}:
            raise HiddenWorkloadError("hidden workload resource record differs")
        if record["path"] != filename or not _DIGEST.fullmatch(str(record["digest"])):
            raise HiddenWorkloadError("hidden workload resource identity is invalid")
        unresolved = root / filename
        if unresolved.is_symlink():
            raise HiddenWorkloadError("hidden workload resource escapes its bundle")
        path = unresolved.resolve(strict=True)
        if path.parent != root or not path.is_file():
            raise HiddenWorkloadError("hidden workload resource escapes its bundle")
        _assert_private_mode(path)
        observed = digest_file(path)
        if observed != record["digest"] or observed != digests[name]:
            raise HiddenWorkloadError("hidden workload resource digest differs")
        paths[name] = path
        resource_digests[name] = observed

    if (
        resource_digests["correctness_requests"]
        == expectations.public_correctness_digest
        or resource_digests["performance_profile"]
        == expectations.public_performance_digest
    ):
        raise HiddenWorkloadError("hidden workload duplicates public data")
    _validate_correctness(paths["correctness_requests"])
    _validate_performance(paths["performance_profile"], public_plan)
    _validate_quality_requests(paths["quality_requests"])
    return HiddenWorkloadBundle(
        manifest_path=manifest_path,
        manifest_digest=observed_manifest_digest,
        selection_seed_commitment=str(manifest["selection_seed_commitment"]),
        resource_paths=paths,
        resource_digests={**resource_digests, **expected_registered},
    )


def _correctness_bytes(seed: bytes) -> bytes:
    cases: list[dict[str, Any]] = []
    for index in range(4):
        token = hashlib.sha256(seed + b":echo:" + index.to_bytes(2, "big")).hexdigest()[
            :12
        ].upper()
        expected = f"PRIVATE_{token}"
        cases.append(
            {
                "id": f"hidden-echo-{index + 1:02d}",
                "messages": [
                    {
                        "role": "user",
                        "content": f"Reply with exactly {expected} and nothing else.",
                    }
                ],
                "max_tokens": 24,
                "check": {"kind": "exact", "value": expected},
            }
        )
    for index in range(4):
        left = 10 + _derived_integer(seed, b"left", index, 90)
        right = 10 + _derived_integer(seed, b"right", index, 90)
        expected = str(left + right)
        cases.append(
            {
                "id": f"hidden-arithmetic-{index + 1:02d}",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"What is {left} + {right}? Reply with only the integer."
                        ),
                    }
                ],
                "max_tokens": 16,
                "check": {"kind": "regex", "value": f"^{expected}$"},
            }
        )
    return b"".join(canonical_json_bytes(case) + b"\n" for case in cases)


def _performance_bytes(plan: BenchmarkPlan, seed: int) -> bytes:
    lines = [
        'schema_version = "serving-workload/v0alpha1"',
        f"seed = {seed}",
        "metric_percentiles = ["
        + ", ".join(str(value) for value in plan.metric_percentiles)
        + "]",
        "",
    ]
    for bucket in plan.buckets:
        lines.extend(
            [
                "[[buckets]]",
                f'id = "{bucket.bucket_id}"',
                f"input_tokens = {bucket.input_tokens}",
                f"output_tokens = {bucket.output_tokens}",
                "request_rates = ["
                + ", ".join(str(value) for value in bucket.request_rates)
                + "]",
                f"num_prompts = {bucket.num_prompts}",
                "",
            ]
        )
    return "\n".join(lines).encode("utf-8")


def _validate_correctness(path: Path) -> None:
    identifiers: set[str] = set()
    rows = path.read_text(encoding="utf-8").splitlines()
    if not rows:
        raise HiddenWorkloadError("hidden correctness workload is empty")
    for line in rows:
        try:
            value = parse_json(line)
        except (json.JSONDecodeError, DuplicateKeyError) as error:
            raise HiddenWorkloadError("hidden correctness request is invalid") from error
        if not isinstance(value, dict) or set(value) != {
            "id",
            "messages",
            "max_tokens",
            "check",
        }:
            raise HiddenWorkloadError("hidden correctness request fields differ")
        identifier = value["id"]
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise HiddenWorkloadError("hidden correctness IDs are invalid")
        identifiers.add(identifier)
        if type(value["max_tokens"]) is not int or value["max_tokens"] < 1:
            raise HiddenWorkloadError("hidden correctness token limit is invalid")
        messages = value["messages"]
        check = value["check"]
        if (
            not isinstance(messages, list)
            or len(messages) != 1
            or not isinstance(messages[0], dict)
            or set(messages[0]) != {"role", "content"}
            or messages[0]["role"] != "user"
            or not isinstance(messages[0]["content"], str)
            or not messages[0]["content"]
            or not isinstance(check, dict)
            or set(check) != {"kind", "value"}
            or check["kind"] not in {"exact", "regex", "casefold_exact"}
            or not isinstance(check["value"], str)
            or not check["value"]
        ):
            raise HiddenWorkloadError("hidden correctness request is invalid")


def _validate_performance(path: Path, public_plan: BenchmarkPlan) -> None:
    try:
        hidden = load_benchmark_plan(path)
    except ManifestValidationError as error:
        raise HiddenWorkloadError("hidden performance profile is invalid") from error
    if hidden.seed == public_plan.seed:
        raise HiddenWorkloadError("hidden performance seed matches public data")
    if (
        hidden.metric_percentiles != public_plan.metric_percentiles
        or hidden.buckets != public_plan.buckets
    ):
        raise HiddenWorkloadError("hidden performance shape differs from public plan")


def _validate_quality_requests(path: Path) -> None:
    try:
        with path.open("r", encoding="utf-8") as source:
            value = load_json(source)
    except (json.JSONDecodeError, DuplicateKeyError) as error:
        raise HiddenWorkloadError("hidden quality requests are ambiguous") from error
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "requests"}
        or value["schema_version"] != QUALITY_REQUESTS_SCHEMA
        or not isinstance(value["requests"], list)
        or not value["requests"]
        or any(not isinstance(request, dict) for request in value["requests"])
    ):
        raise HiddenWorkloadError("hidden quality requests are invalid")
    for request in value["requests"]:
        if set(request) != {"case_id", "body"}:
            raise HiddenWorkloadError("hidden quality request fields differ")
        case_id = request["case_id"]
        body = request["body"]
        if not isinstance(case_id, str) or not case_id or not isinstance(body, dict):
            raise HiddenWorkloadError("hidden quality request is invalid")
        if set(body) != {
            "model",
            "messages",
            "seed",
            "stream",
            "temperature_milli",
            "top_p_milli",
            "top_k",
            "min_p_milli",
            "max_tokens",
            "chat_template_kwargs",
        }:
            raise HiddenWorkloadError("hidden quality request body differs")
        if (
            not isinstance(body["model"], str)
            or not body["model"]
            or type(body["seed"]) is not int
            or body["stream"] is not False
            or any(
                type(body[name]) is not int or body[name] < 0
                for name in (
                    "temperature_milli",
                    "top_p_milli",
                    "top_k",
                    "min_p_milli",
                    "max_tokens",
                )
            )
            or not isinstance(body["messages"], list)
            or not body["messages"]
            or not isinstance(body["chat_template_kwargs"], dict)
        ):
            raise HiddenWorkloadError("hidden quality request body is invalid")


def _quality_request_bytes(
    requests: Sequence[Mapping[str, Any]],
) -> bytes:
    normalized: list[dict[str, Any]] = []
    for request in requests:
        if not isinstance(request, Mapping) or set(request) != {"case_id", "body"}:
            raise HiddenWorkloadError("quality request fields differ")
        body = request["body"]
        if not isinstance(body, Mapping):
            raise HiddenWorkloadError("quality request body is invalid")
        expected = {
            "model",
            "messages",
            "seed",
            "stream",
            "temperature",
            "top_p",
            "top_k",
            "min_p",
            "max_tokens",
            "chat_template_kwargs",
        }
        if set(body) != expected:
            raise HiddenWorkloadError("quality request body fields differ")
        normalized_body = {
            key: body[key]
            for key in (
                "model",
                "messages",
                "seed",
                "stream",
                "top_k",
                "max_tokens",
                "chat_template_kwargs",
            )
        }
        for source, destination in (
            ("temperature", "temperature_milli"),
            ("top_p", "top_p_milli"),
            ("min_p", "min_p_milli"),
        ):
            try:
                milli = Decimal(str(body[source])) * 1000
            except (InvalidOperation, ValueError) as error:
                raise HiddenWorkloadError(
                    "quality request sampling value is invalid"
                ) from error
            if milli != milli.to_integral_value() or not 0 <= milli <= 1000:
                raise HiddenWorkloadError("quality request sampling value is invalid")
            normalized_body[destination] = int(milli)
        normalized.append(
            {"case_id": request["case_id"], "body": normalized_body}
        )
    content = {
        "schema_version": QUALITY_REQUESTS_SCHEMA,
        "requests": normalized,
    }
    return canonical_json_bytes(content) + b"\n"


def _derived_integer(seed: bytes, label: bytes, index: int, modulus: int) -> int:
    digest = hashlib.sha256(seed + b":" + label + b":" + index.to_bytes(4, "big"))
    return int.from_bytes(digest.digest()[:8], "big") % modulus


def _write_once_private(path: Path, content: bytes) -> None:
    try:
        existing = path.read_bytes()
    except FileNotFoundError:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
        temporary = Path(name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != content:
                    raise HiddenWorkloadError(
                        "private workload already exists with different content"
                    )
            finally:
                temporary.unlink(missing_ok=True)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    else:
        if existing != content:
            raise HiddenWorkloadError(
                "private workload already exists with different content"
            )
        _assert_private_mode(path)


def _assert_private_mode(path: Path) -> None:
    if os.stat(path, follow_symlinks=False).st_mode & 0o077:
        raise HiddenWorkloadError("private workload permissions are too broad")
