"""Pinned process-boundary profiles shared by harness runtime adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .canonical import digest_file, digest_value, load_json


@dataclass(frozen=True, slots=True)
class SandboxProfile:
    profile_id: str
    status: str
    driver: str
    executable: str
    network_mode: str
    loopback_destinations: str
    filesystem_enforcement: str
    process_resource_enforcement: str
    allowed_model_endpoint_hosts: tuple[str, ...]
    credential_environment_allowlist: tuple[str, ...]
    source_digest: str
    resolved_digest: str

    @classmethod
    def load(cls, source: Path) -> "SandboxProfile":
        with source.open("r", encoding="utf-8") as stream:
            payload = load_json(stream)
        cls._exact_keys(
            payload,
            {
                "schema_version",
                "profile_id",
                "status",
                "driver",
                "executable",
                "enforcement_scope",
                "network",
                "credential_environment_allowlist",
            },
            "sandbox profile",
        )
        if payload["schema_version"] != "process-sandbox-profile/v1":
            raise ValueError("unsupported process sandbox profile schema")
        if payload["status"] not in {"development", "registered"}:
            raise ValueError("unsupported process sandbox profile status")
        if payload["driver"] != "darwin-sandbox-exec":
            raise ValueError("unsupported process sandbox driver")
        scope = cls._mapping(payload["enforcement_scope"], "enforcement scope")
        cls._exact_keys(
            scope,
            {
                "network_outbound",
                "loopback_destinations",
                "filesystem",
                "process_resources",
            },
            "enforcement scope",
        )
        if scope != {
            "network_outbound": "loopback_only",
            "loopback_destinations": "all_ports_and_services",
            "filesystem": "not_enforced",
            "process_resources": "not_enforced",
        }:
            raise ValueError("unsupported development sandbox enforcement scope")
        network = cls._mapping(payload["network"], "sandbox network policy")
        cls._exact_keys(
            network,
            {"mode", "allowed_model_endpoint_hosts"},
            "sandbox network policy",
        )
        if network["mode"] != "loopback_only":
            raise ValueError("sandbox network mode must be loopback_only")
        hosts = cls._strings(
            network["allowed_model_endpoint_hosts"],
            "allowed model endpoint hosts",
        )
        if set(hosts) != {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("sandbox model endpoints must be loopback-only")
        credentials = cls._strings(
            payload["credential_environment_allowlist"],
            "credential environment allowlist",
            allow_empty=True,
        )
        if credentials:
            raise ValueError("V0 sandbox must not receive credential environment variables")
        source_digest = digest_file(source)
        return cls(
            profile_id=cls._string(payload["profile_id"], "sandbox profile ID"),
            status=str(payload["status"]),
            driver=str(payload["driver"]),
            executable=cls._string(payload["executable"], "sandbox executable"),
            network_mode=str(network["mode"]),
            loopback_destinations=str(scope["loopback_destinations"]),
            filesystem_enforcement=str(scope["filesystem"]),
            process_resource_enforcement=str(scope["process_resources"]),
            allowed_model_endpoint_hosts=hosts,
            credential_environment_allowlist=credentials,
            source_digest=source_digest,
            resolved_digest=digest_value(
                {"profile": payload, "profile_digest": source_digest}
            ),
        )

    @staticmethod
    def _mapping(value: object, label: str) -> Mapping[str, Any]:
        if not isinstance(value, dict):
            raise ValueError(f"{label} must be an object")
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
    def _strings(
        cls, value: object, label: str, *, allow_empty: bool = False
    ) -> tuple[str, ...]:
        if not isinstance(value, list) or (not value and not allow_empty):
            raise ValueError(f"{label} must be a list")
        result = tuple(cls._string(item, label) for item in value)
        if len(set(result)) != len(result):
            raise ValueError(f"{label} must not contain duplicates")
        return result
