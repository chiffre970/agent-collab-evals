"""Deterministic provider-route qualification from frozen catalog evidence."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from .canonical import digest_bytes, digest_file, digest_value, load_json


ENDPOINT_CATALOG_URL = (
    "https://openrouter.ai/api/v1/models/"
    "deepseek/deepseek-v4-flash-0731/endpoints"
)
ZDR_CATALOG_URL = "https://openrouter.ai/api/v1/endpoints/zdr"


def extract_candidate_snapshot(
    endpoint_catalog: bytes,
    zdr_catalog: bytes,
    *,
    model_id: str,
    candidate_providers: tuple[str, ...],
) -> tuple[str, tuple[dict[str, object], ...]]:
    """Derive normalized candidate rows from exact OpenRouter response bytes."""

    endpoint_payload = _source_json(endpoint_catalog, "endpoint catalog")
    zdr_payload = _source_json(zdr_catalog, "ZDR catalog")
    endpoint_data = _source_mapping(endpoint_payload.get("data"), "endpoint data")
    if endpoint_data.get("id") != model_id:
        raise ValueError("endpoint catalog model differs from the frozen model")
    endpoint_rows = _source_list(endpoint_data.get("endpoints"), "endpoint rows")
    zdr_rows = _source_list(zdr_payload.get("data"), "ZDR rows")
    zdr_keys = {
        (
            _source_string(row.get("model_id"), "ZDR model ID"),
            _source_string(row.get("provider_name"), "ZDR provider"),
            _source_string(row.get("tag"), "ZDR tag"),
        )
        for item in zdr_rows
        for row in (_source_mapping(item, "ZDR row"),)
    }
    normalized: list[dict[str, object]] = []
    metadata_models: set[str] = set()
    for provider_name in candidate_providers:
        matching = [
            _source_mapping(item, "endpoint row")
            for item in endpoint_rows
            if isinstance(item, dict) and item.get("provider_name") == provider_name
        ]
        if len(matching) != 1:
            raise ValueError(
                f"endpoint catalog must contain one {provider_name} candidate"
            )
        row = matching[0]
        row_model = _source_string(row.get("model_id"), "endpoint model ID")
        tag = _source_string(row.get("tag"), "endpoint tag")
        name = _source_string(row.get("name"), "endpoint name")
        separator = name.find(" | ")
        if separator < 0:
            raise ValueError("endpoint name does not expose its metadata model")
        metadata_models.add(name[separator + 3 :])
        pricing = _source_mapping(row.get("pricing"), "endpoint pricing")
        supported = tuple(
            _source_string(value, "supported parameter")
            for value in _source_list(
                row.get("supported_parameters"), "supported parameters"
            )
        )
        if len(set(supported)) != len(supported):
            raise ValueError("endpoint supported parameters contain duplicates")
        zdr = (row_model, provider_name, tag) in zdr_keys
        normalized.append(
            {
                "provider_name": provider_name,
                "tag": tag,
                "quantization": _source_string(
                    row.get("quantization"), "endpoint quantization"
                ),
                "status": _source_integer(row.get("status"), "endpoint status"),
                "context_length": _source_integer(
                    row.get("context_length"), "endpoint context length"
                ),
                "max_completion_tokens": _source_integer(
                    row.get("max_completion_tokens"),
                    "endpoint maximum completion tokens",
                ),
                "supported_parameters": list(supported),
                "supports_implicit_caching": _source_boolean(
                    row.get("supports_implicit_caching"),
                    "endpoint implicit caching",
                ),
                "zdr": zdr,
                "uptime_last_1d_percent": _source_decimal_string(
                    row.get("uptime_last_1d"), "endpoint one-day uptime"
                ),
                "pricing": {
                    "prompt_usd_per_token": _source_decimal_string(
                        pricing.get("prompt"), "endpoint prompt price"
                    ),
                    "completion_usd_per_token": _source_decimal_string(
                        pricing.get("completion"), "endpoint completion price"
                    ),
                },
            }
        )
    if len(metadata_models) != 1:
        raise ValueError("candidate endpoints expose different metadata models")
    return next(iter(metadata_models)), tuple(normalized)


def _source_json(raw: bytes, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            parse_float=Decimal,
            object_pairs_hook=_unique_source_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid unique-key UTF-8 JSON") from error
    return _source_mapping(value, label)


def _unique_source_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"source JSON repeats key {key}")
        value[key] = item
    return value


def _source_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _source_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def _source_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _source_integer(value: object, label: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer")
    return value


def _source_boolean(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be a boolean")
    return value


def _source_decimal_string(value: object, label: str) -> str:
    if isinstance(value, str):
        candidate = value
    elif isinstance(value, (int, Decimal)) and not isinstance(value, bool):
        candidate = format(value, "f")
    else:
        raise ValueError(f"{label} must be an exact decimal")
    try:
        decimal = Decimal(candidate)
    except InvalidOperation as error:
        raise ValueError(f"{label} must be an exact decimal") from error
    if not decimal.is_finite() or decimal < 0:
        raise ValueError(f"{label} must be finite and nonnegative")
    return candidate


@dataclass(frozen=True, slots=True)
class ProviderRouteCandidate:
    provider_name: str
    tag: str
    quantization: str
    status: int
    context_length: int
    max_completion_tokens: int
    supported_parameters: tuple[str, ...]
    supports_implicit_caching: bool
    zdr: bool
    uptime_last_1d_percent: Decimal
    prompt_usd_per_token: Decimal
    completion_usd_per_token: Decimal


@dataclass(frozen=True, slots=True)
class ProviderRouteSelection:
    policy_id: str
    model_id: str
    selected_provider: str
    selected_tag: str
    selected_quantization: str
    projected_cost_usd_nanos: int
    eligible_providers: tuple[str, ...]
    rejected_providers: Mapping[str, tuple[str, ...]]
    candidate_snapshot_digest: str
    policy_digest: str
    resolved_digest: str

    def evidence(self) -> dict[str, object]:
        return {
            "policy_id": self.policy_id,
            "model_id": self.model_id,
            "selected_provider": self.selected_provider,
            "selected_tag": self.selected_tag,
            "selected_quantization": self.selected_quantization,
            "projected_cost_usd_nanos": self.projected_cost_usd_nanos,
            "eligible_providers": list(self.eligible_providers),
            "rejected_providers": {
                provider: list(reasons)
                for provider, reasons in self.rejected_providers.items()
            },
            "candidate_snapshot_digest": self.candidate_snapshot_digest,
            "policy_digest": self.policy_digest,
            "resolved_digest": self.resolved_digest,
        }


@dataclass(frozen=True, slots=True)
class ProviderQualificationPlan:
    policy_id: str
    status: str
    model_id: str
    expected_metadata_model: str
    candidates: tuple[ProviderRouteCandidate, ...]
    allowed_quantizations: frozenset[str]
    required_parameters: frozenset[str]
    require_zdr: bool
    require_no_implicit_caching: bool
    minimum_context_tokens: int
    minimum_completion_tokens: int
    minimum_uptime_last_1d_percent: Decimal
    representative_input_tokens: int
    representative_output_tokens: int
    candidate_snapshot_digest: str
    policy_digest: str
    resolved_digest: str

    @classmethod
    def load(
        cls, policy_path: Path, *, repository_root: Path
    ) -> "ProviderQualificationPlan":
        with policy_path.open("r", encoding="utf-8") as stream:
            policy = load_json(stream)
        cls._exact_keys(
            policy,
            {
                "schema_version",
                "policy_id",
                "status",
                "candidate_snapshot",
                "candidate_providers",
                "eligibility",
                "representative_mix",
                "tie_break",
            },
            "provider selection policy",
        )
        if policy["schema_version"] != "provider-selection-policy/v1":
            raise ValueError("unsupported provider selection policy schema")
        if policy["status"] not in {"development", "registered"}:
            raise ValueError("unsupported provider selection policy status")
        if policy["tie_break"] != [
            "projected_cost_usd_nanos",
            "provider_name",
        ]:
            raise ValueError("unsupported provider selection tie-break")

        snapshot_path = cls._config_member(
            repository_root,
            policy["candidate_snapshot"],
            "provider_qualification",
            "provider candidate snapshot",
        )
        if not snapshot_path.is_file():
            raise ValueError("provider selection policy snapshot is missing")
        with snapshot_path.open("r", encoding="utf-8") as stream:
            snapshot = load_json(stream)
        cls._exact_keys(
            snapshot,
            {
                "schema_version",
                "snapshot_id",
                "status",
                "observed_at",
                "source_manifest",
                "source_manifest_digest",
                "model_id",
                "expected_metadata_model",
                "candidate_scope",
                "candidate_providers",
                "candidates",
            },
            "provider candidate snapshot",
        )
        if snapshot["schema_version"] != "provider-candidate-snapshot/v1":
            raise ValueError("unsupported provider candidate snapshot schema")
        if snapshot["status"] != policy["status"]:
            raise ValueError("provider snapshot and policy statuses differ")
        if snapshot["candidate_scope"] != "predeclared_reputable_routes":
            raise ValueError("provider candidate scope is unsupported")
        candidate_providers = cls._strings(snapshot["candidate_providers"])
        if list(candidate_providers) != policy["candidate_providers"]:
            raise ValueError("snapshot candidate providers differ from policy")
        source_manifest_path = cls._config_member(
            repository_root,
            snapshot["source_manifest"],
            "provider_qualification",
            "provider source manifest",
        )
        (
            source_manifest_digest,
            source_status,
            source_observed_at,
            endpoint_raw,
            zdr_raw,
        ) = cls._load_source_manifest(source_manifest_path, repository_root=repository_root)
        if snapshot["source_manifest_digest"] != source_manifest_digest:
            raise ValueError("provider source manifest digest differs")
        if (
            snapshot["status"] != source_status
            or snapshot["observed_at"] != source_observed_at
        ):
            raise ValueError("provider snapshot source identity differs")
        expected_metadata_model, extracted_candidates = extract_candidate_snapshot(
            endpoint_raw,
            zdr_raw,
            model_id=cls._string(snapshot["model_id"], "snapshot model ID"),
            candidate_providers=candidate_providers,
        )
        if snapshot["expected_metadata_model"] != expected_metadata_model:
            raise ValueError("snapshot metadata model differs from raw sources")
        if snapshot["candidates"] != list(extracted_candidates):
            raise ValueError("provider candidates differ from deterministic extraction")

        eligibility = cls._mapping(policy["eligibility"], "eligibility")
        cls._exact_keys(
            eligibility,
            {
                "allowed_quantizations",
                "required_parameters",
                "require_zdr",
                "require_no_implicit_caching",
                "minimum_context_tokens",
                "minimum_completion_tokens",
                "minimum_uptime_last_1d_percent",
            },
            "provider eligibility",
        )
        mix = cls._mapping(policy["representative_mix"], "representative mix")
        cls._exact_keys(
            mix,
            {"uncached_input_tokens", "output_tokens"},
            "representative mix",
        )
        candidates = tuple(
            cls._candidate(value) for value in cls._list(snapshot["candidates"])
        )
        if not candidates:
            raise ValueError("provider candidate snapshot is empty")
        names = [candidate.provider_name for candidate in candidates]
        if len(set(names)) != len(names):
            raise ValueError("provider candidates must have unique names")
        allowed_quantizations = frozenset(
            cls._strings(eligibility["allowed_quantizations"])
        )
        required_parameters = frozenset(
            cls._strings(eligibility["required_parameters"])
        )
        if not allowed_quantizations or not required_parameters:
            raise ValueError("provider eligibility sets must be nonempty")
        minimum_context = cls._positive_integer(
            eligibility["minimum_context_tokens"], "minimum context tokens"
        )
        minimum_completion = cls._positive_integer(
            eligibility["minimum_completion_tokens"],
            "minimum completion tokens",
        )
        input_tokens = cls._positive_integer(
            mix["uncached_input_tokens"], "representative input tokens"
        )
        output_tokens = cls._positive_integer(
            mix["output_tokens"], "representative output tokens"
        )
        candidate_digest = digest_file(snapshot_path)
        policy_digest = digest_file(policy_path)
        resolved_digest = digest_value(
            {
                "policy": policy,
                "policy_digest": policy_digest,
                "candidate_snapshot": snapshot,
                "candidate_snapshot_digest": candidate_digest,
            }
        )
        return cls(
            policy_id=str(policy["policy_id"]),
            status=str(policy["status"]),
            model_id=str(snapshot["model_id"]),
            expected_metadata_model=str(snapshot["expected_metadata_model"]),
            candidates=candidates,
            allowed_quantizations=allowed_quantizations,
            required_parameters=required_parameters,
            require_zdr=cls._boolean(eligibility["require_zdr"], "require ZDR"),
            require_no_implicit_caching=cls._boolean(
                eligibility["require_no_implicit_caching"],
                "require no implicit caching",
            ),
            minimum_context_tokens=minimum_context,
            minimum_completion_tokens=minimum_completion,
            minimum_uptime_last_1d_percent=cls._decimal(
                eligibility["minimum_uptime_last_1d_percent"],
                "minimum uptime",
            ),
            representative_input_tokens=input_tokens,
            representative_output_tokens=output_tokens,
            candidate_snapshot_digest=candidate_digest,
            policy_digest=policy_digest,
            resolved_digest=resolved_digest,
        )

    @classmethod
    def _load_source_manifest(
        cls, source: Path, *, repository_root: Path
    ) -> tuple[str, str, str, bytes, bytes]:
        with source.open("r", encoding="utf-8") as stream:
            manifest = load_json(stream)
        cls._exact_keys(
            manifest,
            {
                "schema_version",
                "bundle_id",
                "status",
                "observed_at",
                "sources",
            },
            "provider source manifest",
        )
        if manifest["schema_version"] != "provider-source-bundle/v1":
            raise ValueError("unsupported provider source bundle schema")
        status = cls._string(manifest["status"], "provider source status")
        observed_at = cls._string(
            manifest["observed_at"], "provider source observation time"
        )
        sources = cls._mapping(manifest["sources"], "provider sources")
        cls._exact_keys(sources, {"endpoints", "zdr"}, "provider sources")
        raw_values: dict[str, bytes] = {}
        expected_urls = {
            "endpoints": ENDPOINT_CATALOG_URL,
            "zdr": ZDR_CATALOG_URL,
        }
        for source_id, expected_url in expected_urls.items():
            entry = cls._mapping(sources[source_id], f"{source_id} source")
            cls._exact_keys(
                entry,
                {
                    "url",
                    "file",
                    "content_encoding",
                    "raw_digest",
                    "file_digest",
                },
                f"{source_id} source",
            )
            if entry["url"] != expected_url or entry["content_encoding"] != "gzip":
                raise ValueError(f"{source_id} source location or encoding differs")
            evidence_path = cls._evidence_member(
                repository_root, entry["file"], f"{source_id} source file"
            )
            if evidence_path.stat().st_size > 2_000_000:
                raise ValueError(f"{source_id} compressed source is too large")
            if digest_file(evidence_path) != entry["file_digest"]:
                raise ValueError(f"{source_id} compressed source digest differs")
            try:
                raw = gzip.decompress(evidence_path.read_bytes())
            except (OSError, EOFError) as error:
                raise ValueError(f"{source_id} source is not valid gzip") from error
            if len(raw) > 10_000_000:
                raise ValueError(f"{source_id} raw source is too large")
            if digest_bytes(raw) != entry["raw_digest"]:
                raise ValueError(f"{source_id} raw source digest differs")
            raw_values[source_id] = raw
        return (
            digest_file(source),
            status,
            observed_at,
            raw_values["endpoints"],
            raw_values["zdr"],
        )

    def select(self) -> ProviderRouteSelection:
        eligible: list[tuple[int, ProviderRouteCandidate]] = []
        rejected: dict[str, tuple[str, ...]] = {}
        for candidate in self.candidates:
            reasons = self._rejection_reasons(candidate)
            if reasons:
                rejected[candidate.provider_name] = reasons
                continue
            eligible.append((self._projected_cost(candidate), candidate))
        if not eligible:
            raise ValueError("no provider route satisfies the frozen policy")
        eligible.sort(key=lambda item: (item[0], item[1].provider_name))
        cost, selected = eligible[0]
        evidence = {
            "plan_digest": self.resolved_digest,
            "selected_provider": selected.provider_name,
            "selected_tag": selected.tag,
            "projected_cost_usd_nanos": cost,
            "eligible_providers": [item[1].provider_name for item in eligible],
            "rejected_providers": rejected,
        }
        return ProviderRouteSelection(
            policy_id=self.policy_id,
            model_id=self.model_id,
            selected_provider=selected.provider_name,
            selected_tag=selected.tag,
            selected_quantization=selected.quantization,
            projected_cost_usd_nanos=cost,
            eligible_providers=tuple(item[1].provider_name for item in eligible),
            rejected_providers=rejected,
            candidate_snapshot_digest=self.candidate_snapshot_digest,
            policy_digest=self.policy_digest,
            resolved_digest=digest_value(evidence),
        )

    def _rejection_reasons(
        self, candidate: ProviderRouteCandidate
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if candidate.status != 0:
            reasons.append("endpoint_unavailable")
        if candidate.quantization not in self.allowed_quantizations:
            reasons.append("quantization_not_allowed")
        if not self.required_parameters.issubset(candidate.supported_parameters):
            reasons.append("required_parameters_missing")
        if self.require_zdr and not candidate.zdr:
            reasons.append("zdr_not_attested")
        if self.require_no_implicit_caching and candidate.supports_implicit_caching:
            reasons.append("implicit_caching_enabled")
        if candidate.context_length < self.minimum_context_tokens:
            reasons.append("context_too_small")
        if candidate.max_completion_tokens < self.minimum_completion_tokens:
            reasons.append("completion_limit_too_small")
        if candidate.uptime_last_1d_percent < self.minimum_uptime_last_1d_percent:
            reasons.append("uptime_below_threshold")
        return tuple(reasons)

    def _projected_cost(self, candidate: ProviderRouteCandidate) -> int:
        dollars = (
            candidate.prompt_usd_per_token * self.representative_input_tokens
            + candidate.completion_usd_per_token
            * self.representative_output_tokens
        )
        return int((dollars * Decimal(1_000_000_000)).to_integral_value(
            rounding=ROUND_CEILING
        ))

    @classmethod
    def _candidate(cls, value: object) -> ProviderRouteCandidate:
        candidate = cls._mapping(value, "provider candidate")
        cls._exact_keys(
            candidate,
            {
                "provider_name",
                "tag",
                "quantization",
                "status",
                "context_length",
                "max_completion_tokens",
                "supported_parameters",
                "supports_implicit_caching",
                "zdr",
                "uptime_last_1d_percent",
                "pricing",
            },
            "provider candidate",
        )
        pricing = cls._mapping(candidate["pricing"], "provider pricing")
        cls._exact_keys(
            pricing,
            {"prompt_usd_per_token", "completion_usd_per_token"},
            "provider pricing",
        )
        return ProviderRouteCandidate(
            provider_name=cls._string(candidate["provider_name"], "provider name"),
            tag=cls._string(candidate["tag"], "provider tag"),
            quantization=cls._string(candidate["quantization"], "quantization"),
            status=cls._integer(candidate["status"], "provider status"),
            context_length=cls._positive_integer(
                candidate["context_length"], "context length"
            ),
            max_completion_tokens=cls._positive_integer(
                candidate["max_completion_tokens"], "maximum completion tokens"
            ),
            supported_parameters=tuple(
                cls._strings(candidate["supported_parameters"])
            ),
            supports_implicit_caching=cls._boolean(
                candidate["supports_implicit_caching"],
                "implicit caching support",
            ),
            zdr=cls._boolean(candidate["zdr"], "ZDR attestation"),
            uptime_last_1d_percent=cls._decimal(
                candidate["uptime_last_1d_percent"], "one-day uptime"
            ),
            prompt_usd_per_token=cls._decimal(
                pricing["prompt_usd_per_token"], "prompt price"
            ),
            completion_usd_per_token=cls._decimal(
                pricing["completion_usd_per_token"], "completion price"
            ),
        )

    @staticmethod
    def _mapping(value: object, label: str) -> Mapping[str, Any]:
        if not isinstance(value, dict):
            raise ValueError(f"{label} must be an object")
        return value

    @staticmethod
    def _list(value: object) -> list[object]:
        if not isinstance(value, list):
            raise ValueError("provider candidates must be a list")
        return value

    @classmethod
    def _exact_keys(cls, value: object, expected: set[str], label: str) -> None:
        mapping = cls._mapping(value, label)
        if set(mapping) != expected:
            missing = sorted(expected - set(mapping))
            unknown = sorted(set(mapping) - expected)
            raise ValueError(
                f"{label} keys differ; missing={missing}, unknown={unknown}"
            )

    @staticmethod
    def _string(value: object, label: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{label} must be a nonempty string")
        return value

    @classmethod
    def _strings(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, list) or not value:
            raise ValueError("provider string list must be nonempty")
        result = tuple(cls._string(item, "provider list item") for item in value)
        if len(set(result)) != len(result):
            raise ValueError("provider string list must not contain duplicates")
        return result

    @staticmethod
    def _boolean(value: object, label: str) -> bool:
        if type(value) is not bool:
            raise ValueError(f"{label} must be a boolean")
        return value

    @staticmethod
    def _integer(value: object, label: str) -> int:
        if type(value) is not int:
            raise ValueError(f"{label} must be an integer")
        return value

    @classmethod
    def _positive_integer(cls, value: object, label: str) -> int:
        result = cls._integer(value, label)
        if result < 1:
            raise ValueError(f"{label} must be positive")
        return result

    @staticmethod
    def _decimal(value: object, label: str) -> Decimal:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{label} must be an exact decimal string")
        try:
            result = Decimal(value)
        except InvalidOperation as error:
            raise ValueError(f"{label} must be an exact decimal string") from error
        if not result.is_finite() or result < 0:
            raise ValueError(f"{label} must be finite and nonnegative")
        return result

    @staticmethod
    def _config_member(
        repository_root: Path,
        value: object,
        directory: str,
        label: str,
    ) -> Path:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{label} path must be nonempty")
        path = (repository_root / value).resolve()
        expected = (repository_root / "config" / directory).resolve()
        if path.parent != expected or not path.is_file():
            raise ValueError(f"{label} must be directly under {expected}")
        return path

    @staticmethod
    def _evidence_member(repository_root: Path, value: object, label: str) -> Path:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{label} path must be nonempty")
        path = (repository_root / value).resolve()
        expected = (
            repository_root / "evidence/provider_qualification/sources"
        ).resolve()
        if path.parent != expected or not path.is_file():
            raise ValueError(f"{label} must be directly under {expected}")
        return path


@dataclass(frozen=True, slots=True)
class QualifiedProviderRoute:
    record_id: str
    status: str
    selection: ProviderRouteSelection
    gateway_profile_id: str
    gateway_profile_digest: str
    record_digest: str
    resolved_digest: str

    @classmethod
    def load(
        cls, source: Path, *, repository_root: Path
    ) -> "QualifiedProviderRoute":
        with source.open("r", encoding="utf-8") as stream:
            record = load_json(stream)
        ProviderQualificationPlan._exact_keys(
            record,
            {
                "schema_version",
                "record_id",
                "status",
                "selection_policy",
                "gateway_profile",
                "selected_route",
                "qualification",
            },
            "provider selection record",
        )
        if record["schema_version"] != "provider-selection-record/v1":
            raise ValueError("unsupported provider selection record schema")
        policy_path = ProviderQualificationPlan._config_member(
            repository_root,
            record["selection_policy"],
            "provider_qualification",
            "provider selection policy",
        )
        plan = ProviderQualificationPlan.load(
            policy_path, repository_root=repository_root
        )
        selection = plan.select()
        gateway_path = ProviderQualificationPlan._config_member(
            repository_root,
            record["gateway_profile"],
            "gateway_profiles",
            "gateway profile",
        )
        from .model_gateway import ModelGatewayProfile

        gateway = ModelGatewayProfile.load(
            gateway_path, repository_root=repository_root
        )
        if record["status"] != plan.status or record["status"] != gateway.status:
            raise ValueError("provider selection component statuses differ")
        selected = ProviderQualificationPlan._mapping(
            record["selected_route"], "selected provider route"
        )
        ProviderQualificationPlan._exact_keys(
            selected,
            {
                "provider_name",
                "tag",
                "quantization",
                "selection_digest",
            },
            "selected provider route",
        )
        if (
            selected["provider_name"] != selection.selected_provider
            or selected["tag"] != selection.selected_tag
            or selected["quantization"] != selection.selected_quantization
            or selected["selection_digest"] != selection.resolved_digest
            or gateway.expected_provider != selection.selected_provider
            or gateway.requested_model != selection.model_id
            or gateway.cache_policy != "disabled"
        ):
            raise ValueError("provider selection record differs from resolved route")
        cls._validate_qualification(
            record["qualification"],
            repository_root=repository_root,
            selection=selection,
        )
        record_digest = digest_file(source)
        return cls(
            record_id=ProviderQualificationPlan._string(
                record["record_id"], "provider selection record ID"
            ),
            status=str(record["status"]),
            selection=selection,
            gateway_profile_id=gateway.profile_id,
            gateway_profile_digest=gateway.resolved_digest,
            record_digest=record_digest,
            resolved_digest=digest_value(
                {
                    "record": record,
                    "record_digest": record_digest,
                    "selection_digest": selection.resolved_digest,
                    "gateway_profile_digest": gateway.resolved_digest,
                }
            ),
        )

    @staticmethod
    def _validate_qualification(
        value: object,
        *,
        repository_root: Path,
        selection: ProviderRouteSelection,
    ) -> None:
        qualification = ProviderQualificationPlan._mapping(
            value, "provider route qualification"
        )
        ProviderQualificationPlan._exact_keys(
            qualification,
            {
                "started_at",
                "finished_at",
                "qualification_record_file",
                "qualification_record_digest",
                "probe_count",
                "text_probe_passed",
                "tool_call_probe_passed",
                "cache_isolation",
                "maximum_probe_elapsed_ms",
                "total_charged_usd_nanos",
                "budget_reconciliation_valid",
                "conformance_failures",
                "receipt_digests",
                "raw_receipts_storage",
            },
            "provider route qualification",
        )
        cache = ProviderQualificationPlan._mapping(
            qualification["cache_isolation"], "cache isolation evidence"
        )
        ProviderQualificationPlan._exact_keys(
            cache,
            {
                "gateway_cache_policy",
                "openrouter_response_cache_header",
                "provider_supports_implicit_caching",
                "repeated_request_digest",
                "reported_cached_input_tokens",
            },
            "cache isolation evidence",
        )
        cached = cache["reported_cached_input_tokens"]
        failures = qualification["conformance_failures"]
        receipts = qualification["receipt_digests"]
        if (
            type(qualification["probe_count"]) is not int
            or qualification["probe_count"] != 3
            or qualification["text_probe_passed"] is not True
            or qualification["tool_call_probe_passed"] is not True
            or qualification["budget_reconciliation_valid"] is not True
            or failures != []
            or type(qualification["maximum_probe_elapsed_ms"]) is not int
            or not 0 < qualification["maximum_probe_elapsed_ms"] <= 60_000
            or type(qualification["total_charged_usd_nanos"]) is not int
            or not 0 < qualification["total_charged_usd_nanos"] <= 50_000_000
            or qualification["raw_receipts_storage"]
            != "repository_retained_development_evidence"
            or cache["gateway_cache_policy"] != "disabled"
            or cache["openrouter_response_cache_header"] != "false"
            or cache["provider_supports_implicit_caching"] is not False
            or not isinstance(cache["repeated_request_digest"], str)
            or not cache["repeated_request_digest"].startswith("sha256:")
            or cached != [0, 0, 0]
            or not isinstance(receipts, list)
            or len(receipts) != 3
        ):
            raise ValueError("provider route qualification did not pass")
        for receipt in receipts:
            value = ProviderQualificationPlan._mapping(
                receipt, "provider qualification receipt"
            )
            ProviderQualificationPlan._exact_keys(
                value,
                {
                    "stream_digest",
                    "metadata_digest",
                    "stream_file",
                    "metadata_file",
                },
                "provider qualification receipt",
            )
            for evidence_type in ("stream", "metadata"):
                digest_key = f"{evidence_type}_digest"
                file_key = f"{evidence_type}_file"
                expected_digest = value[digest_key]
                if (
                    not isinstance(expected_digest, str)
                    or not expected_digest.startswith("sha256:")
                ):
                    raise ValueError(
                        "provider qualification receipt digest is invalid"
                    )
                evidence_path = QualifiedProviderRoute._receipt_evidence_member(
                    repository_root,
                    value[file_key],
                    f"provider qualification {evidence_type} receipt",
                )
                if evidence_path.stat().st_size < 1:
                    raise ValueError("provider qualification receipt is empty")
                if digest_file(evidence_path) != expected_digest:
                    raise ValueError(
                        "provider qualification receipt differs from retained evidence"
                    )
        record_path = QualifiedProviderRoute._qualification_record_member(
            repository_root,
            qualification["qualification_record_file"],
        )
        if digest_file(record_path) != qualification["qualification_record_digest"]:
            raise ValueError("retained qualification record digest differs")
        with record_path.open("r", encoding="utf-8") as stream:
            raw_record = load_json(stream)
        raw_selection = ProviderQualificationPlan._mapping(
            raw_record.get("selection"), "retained qualification selection"
        )
        raw_budget = ProviderQualificationPlan._mapping(
            raw_record.get("budget_reconciliation"),
            "retained qualification reconciliation",
        )
        raw_charges = raw_record.get("charges")
        raw_probes = raw_record.get("probes")
        if (
            raw_record.get("schema_version")
            != "provider-route-qualification-record/v1"
            or raw_record.get("qualified") is not True
            or raw_record.get("conformance_failures") != []
            or raw_record.get("started_at") != qualification["started_at"]
            or raw_record.get("finished_at") != qualification["finished_at"]
            or raw_record.get("total_charged_usd_nanos")
            != qualification["total_charged_usd_nanos"]
            or raw_budget.get("valid") is not True
            or raw_selection.get("resolved_digest") != selection.resolved_digest
            or not isinstance(raw_charges, list)
            or len(raw_charges) != 3
            or not isinstance(raw_probes, list)
            or len(raw_probes) != 3
        ):
            raise ValueError("retained qualification record differs from selection")
        if not all(isinstance(probe, dict) for probe in raw_probes):
            raise ValueError("retained qualification probes are invalid")
        maximum_elapsed = max(int(probe.get("elapsed_ms", -1)) for probe in raw_probes)
        repeated_digest = raw_probes[0].get("request_digest")
        if (
            qualification["probe_count"] != len(raw_probes)
            or qualification["maximum_probe_elapsed_ms"] != maximum_elapsed
            or repeated_digest != raw_probes[1].get("request_digest")
            or cache["repeated_request_digest"] != repeated_digest
            or raw_record.get("cache_policy") != cache["gateway_cache_policy"]
        ):
            raise ValueError("retained qualification probe summary differs")
        raw_cached_tokens = [
            charge.get("cached_input_tokens")
            for charge in raw_charges
            if isinstance(charge, dict)
        ]
        if raw_cached_tokens != cache["reported_cached_input_tokens"]:
            raise ValueError("retained qualification cache evidence differs")
        retained_digests = [
            {
                "stream_digest": charge.get("stream_digest"),
                "metadata_digest": charge.get("metadata_digest"),
            }
            for charge in raw_charges
            if isinstance(charge, dict)
        ]
        compact_digests = [
            {
                "stream_digest": receipt["stream_digest"],
                "metadata_digest": receipt["metadata_digest"],
            }
            for receipt in receipts
            if isinstance(receipt, dict)
        ]
        if retained_digests != compact_digests:
            raise ValueError("retained qualification receipt list differs")

    @staticmethod
    def _receipt_evidence_member(
        repository_root: Path, value: object, label: str
    ) -> Path:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{label} path must be nonempty")
        path = (repository_root / value).resolve()
        expected = (
            repository_root / "evidence/provider_qualification/receipts"
        ).resolve()
        if path.parent != expected or not path.is_file():
            raise ValueError(f"{label} must be directly under {expected}")
        return path

    @staticmethod
    def _qualification_record_member(
        repository_root: Path, value: object
    ) -> Path:
        if not isinstance(value, str) or not value:
            raise ValueError("qualification record path must be nonempty")
        path = (repository_root / value).resolve()
        expected = (
            repository_root / "evidence/provider_qualification/records"
        ).resolve()
        if path.parent != expected or not path.is_file():
            raise ValueError(
                f"qualification record must be directly under {expected}"
            )
        return path
