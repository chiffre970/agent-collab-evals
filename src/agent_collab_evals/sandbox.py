"""Pinned process-boundary profiles shared by harness runtime adapters."""

from __future__ import annotations

import stat
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping

from .canonical import digest_file, digest_value, load_json


@dataclass(frozen=True, slots=True)
class SandboxLaunchContext:
    """Server-derived paths and broker endpoint for one runtime process tree."""

    workspace_root: Path
    runtime_state_root: Path
    runtime_assets_root: Path
    model_endpoint: str
    broker_socket: Path | None = None
    peer_endpoint: str | None = None
    peer_broker_socket: Path | None = None

    def __post_init__(self) -> None:
        paths = (
            self.workspace_root,
            self.runtime_state_root,
            self.runtime_assets_root,
        )
        if any(not path.is_absolute() for path in paths):
            raise ValueError("sandbox launch paths must be absolute")
        if any(not path.is_dir() for path in paths):
            raise ValueError("sandbox launch paths must be existing directories")
        if not self.model_endpoint:
            raise ValueError("sandbox model endpoint is required")
        if self.peer_broker_socket is not None and self.peer_endpoint is None:
            raise ValueError(
                "sandbox peer broker socket requires its endpoint"
            )
        for label, socket_path in (
            ("model", self.broker_socket),
            ("peer", self.peer_broker_socket),
        ):
            if socket_path is None:
                continue
            if not socket_path.is_absolute():
                raise ValueError(f"sandbox {label} broker socket path must be absolute")
            try:
                mode = socket_path.stat().st_mode
            except FileNotFoundError as error:
                raise ValueError(
                    f"sandbox {label} broker socket does not exist"
                ) from error
            if not stat.S_ISSOCK(mode):
                raise ValueError(
                    f"sandbox {label} broker transport must be a Unix socket"
                )
        roots = [path.resolve() for path in paths]
        for left, right in combinations(roots, 2):
            if left == right or left in right.parents or right in left.parents:
                raise ValueError("sandbox workspace, state, and assets must be disjoint")
        for socket_path in (self.broker_socket, self.peer_broker_socket):
            if socket_path is None:
                continue
            parent = socket_path.parent.resolve()
            for root in roots:
                if parent == root or parent in root.parents or root in parent.parents:
                    raise ValueError("sandbox broker roots must be disjoint from runtime roots")


@dataclass(frozen=True, slots=True)
class SandboxedProcess:
    """Complete subprocess specification returned by a sandbox adapter."""

    command: tuple[str, ...]
    working_directory: Path
    environment: Mapping[str, str]

    def __post_init__(self) -> None:
        if not self.command or any(not value for value in self.command):
            raise ValueError("sandboxed process command must be nonempty")
        if (
            not self.working_directory.is_absolute()
            or not self.working_directory.is_dir()
        ):
            raise ValueError("sandboxed process working directory is invalid")
        if any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            for key, value in self.environment.items()
        ):
            raise ValueError("sandboxed process environment is invalid")
        object.__setattr__(self, "environment", dict(self.environment))


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
