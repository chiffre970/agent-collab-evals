"""Fail-closed Docker-compatible OCI sandbox policy and command builder."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..canonical import digest_file, digest_value, load_json
from ..sandbox import SandboxLaunchContext, SandboxedProcess


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}")
_IMAGE_REFERENCE = re.compile(
    r"[a-z0-9]+(?:[._/-][a-z0-9]+)*(?::[A-Za-z0-9][A-Za-z0-9._-]*)?"
)
_CPU_LIMIT = re.compile(r"[1-9][0-9]*(?:\.[0-9]+)?|0\.[0-9]*[1-9][0-9]*")
_BYTE_LIMIT = re.compile(r"[1-9][0-9]*(?:m|g)")
_BRIDGE_RELATIVE = Path("opencode_bridge.mjs")


@dataclass(frozen=True, slots=True)
class OciSandboxProfile:
    profile_id: str
    status: str
    execution_authorized: bool
    engine: str
    image_reference: str | None
    image_digest: str | None
    node_executable: str
    bridge_executable: str
    bridge_digest: str
    launcher_executable: str
    launcher_digest: str
    container_model_endpoint: str
    container_peer_endpoint: str
    uid: int
    gid: int
    cpu_limit: str
    memory_limit: str
    pids_limit: int
    timeout_seconds: int
    tmpfs_size: str
    environment_allowlist: tuple[str, ...]
    required_conformance: tuple[str, ...]
    unresolved_gates: tuple[str, ...]
    source_digest: str
    resolved_digest: str

    @classmethod
    def load(
        cls, source: Path, *, repository_root: Path | None = None
    ) -> "OciSandboxProfile":
        with source.open("r", encoding="utf-8") as stream:
            value = load_json(stream)
        _keys(
            value,
            {
                "schema_version",
                "profile_id",
                "status",
                "execution_authorized",
                "engine",
                "image",
                "network",
                "filesystem",
                "process",
                "credentials",
                "required_conformance",
                "unresolved_gates",
            },
            "OCI sandbox profile",
        )
        if value["schema_version"] != "oci-process-sandbox-profile/v1":
            raise ValueError("unsupported OCI sandbox profile schema")
        profile_id = _string(value["profile_id"], "OCI sandbox profile ID")
        if not _SAFE_ID.fullmatch(profile_id):
            raise ValueError("OCI sandbox profile ID is invalid")
        status = value["status"]
        if status not in {"implementation_candidate", "registered"}:
            raise ValueError("unsupported OCI sandbox profile status")
        authorized = value["execution_authorized"]
        if type(authorized) is not bool:
            raise ValueError("OCI execution authorization must be a boolean")
        if value["engine"] != "docker-compatible-rootless-oci":
            raise ValueError("unsupported OCI sandbox engine")

        image = _mapping(value["image"], "OCI image")
        _keys(
            image,
            {
                "reference",
                "digest",
                "node_executable",
                "bridge_executable",
                "bridge_digest",
                "launcher_executable",
                "launcher_digest",
            },
            "OCI image",
        )
        network = _mapping(value["network"], "OCI network")
        _keys(
            network,
            {
                "mode",
                "model_broker_transport",
                "peer_broker_transport",
                "container_model_endpoint",
                "container_peer_endpoint",
                "provider_egress",
            },
            "OCI network",
        )
        if network != {
            "mode": "none",
            "model_broker_transport": "dedicated_session_unix_socket",
            "peer_broker_transport": "dedicated_session_unix_socket_when_enabled",
            "container_model_endpoint": "http://127.0.0.1:4317/v1",
            "container_peer_endpoint": "http://127.0.0.1:4318/v1/call",
            "provider_egress": "denied",
        }:
            raise ValueError("OCI network policy differs")
        filesystem = _mapping(value["filesystem"], "OCI filesystem")
        _keys(
            filesystem,
            {
                "root",
                "runtime_assets",
                "workspace",
                "runtime_state",
                "broker_socket_parents",
                "temporary",
                "other_host_paths",
            },
            "OCI filesystem",
        )
        if filesystem != {
            "root": "read_only",
            "runtime_assets": "read_only_image_layer",
            "workspace": "read_write_bind",
            "runtime_state": "read_write_bind",
            "broker_socket_parents": "read_only_bind",
            "temporary": "bounded_noexec_tmpfs",
            "other_host_paths": "not_mounted",
        }:
            raise ValueError("OCI filesystem policy differs")
        process = _mapping(value["process"], "OCI process")
        _keys(
            process,
            {
                "uid",
                "gid",
                "capabilities",
                "no_new_privileges",
                "cpu_limit",
                "memory_limit",
                "pids_limit",
                "timeout_seconds",
                "tmpfs_size",
            },
            "OCI process",
        )
        if (
            process["capabilities"] != "drop_all"
            or process["no_new_privileges"] is not True
        ):
            raise ValueError("OCI privilege policy differs")
        credentials = _mapping(value["credentials"], "OCI credentials")
        _keys(credentials, {"secrets", "environment_allowlist"}, "OCI credentials")
        if credentials["secrets"] != "none":
            raise ValueError("OCI sandbox must not receive secrets")
        environment = _strings(
            credentials["environment_allowlist"], "OCI environment allowlist"
        )
        if set(environment) != {
            "LANG",
            "HOME",
            "TMPDIR",
            "XDG_DATA_HOME",
            "XDG_CONFIG_HOME",
            "XDG_CACHE_HOME",
            "XDG_STATE_HOME",
            "AGENT_COLLAB_PROVIDER_ID",
            "AGENT_COLLAB_MODEL_ID",
        }:
            raise ValueError("OCI environment allowlist differs")
        conformance = _strings(value["required_conformance"], "OCI conformance")
        if conformance != (
            "gateway_only_network",
            "peer_gateway_only_network",
            "unrelated_loopback_denied",
            "provider_egress_denied",
            "evaluator_private_files_denied",
            "ambient_credentials_absent",
            "filesystem_write_scope_enforced",
            "process_resource_limits_enforced",
        ):
            raise ValueError("OCI conformance set differs")
        unresolved = _strings(
            value["unresolved_gates"], "OCI unresolved gates", allow_empty=True
        )
        reference = image["reference"]
        image_digest = image["digest"]
        bridge_digest = image["bridge_digest"]
        launcher_digest = image["launcher_digest"]
        if reference is not None and (
            not isinstance(reference, str) or not _IMAGE_REFERENCE.fullmatch(reference)
        ):
            raise ValueError("OCI image reference is invalid")
        for item, label in (
            (image_digest, "OCI image digest"),
            (bridge_digest, "OCI bridge digest"),
            (launcher_digest, "OCI launcher digest"),
        ):
            if item is not None and (not isinstance(item, str) or not _DIGEST.fullmatch(item)):
                raise ValueError(f"{label} is invalid")
        if bridge_digest is None or launcher_digest is None:
            raise ValueError("OCI runtime source digests are required")
        repository = (
            repository_root.resolve()
            if repository_root is not None
            else source.resolve().parents[2]
        )
        expected_sources = {
            bridge_digest: repository / "scripts/runtime/opencode_bridge.mjs",
            launcher_digest: repository / "scripts/runtime/session_launcher.py",
        }
        if any(
            not path.is_file() or digest_file(path) != digest
            for digest, path in expected_sources.items()
        ):
            raise ValueError("OCI runtime source digest differs")
        if authorized:
            if status != "registered" or unresolved:
                raise ValueError("authorized OCI profile must be fully registered")
            if not reference or image_digest is None or launcher_digest is None:
                raise ValueError("authorized OCI profile requires pinned artifacts")
        elif status == "registered":
            raise ValueError("registered OCI profile must authorize execution")
        elif not unresolved:
            raise ValueError("OCI implementation candidate must name unresolved gates")
        for name in ("uid", "gid", "pids_limit", "timeout_seconds"):
            if type(process[name]) is not int or process[name] < 1:
                raise ValueError(f"OCI process {name} must be a positive integer")
        if not _CPU_LIMIT.fullmatch(str(process["cpu_limit"])):
            raise ValueError("OCI CPU limit is invalid")
        for name in ("memory_limit", "tmpfs_size"):
            if not _BYTE_LIMIT.fullmatch(str(process[name])):
                raise ValueError(f"OCI process {name} is invalid")
        source_digest = digest_file(source)
        return cls(
            profile_id=profile_id,
            status=status,
            execution_authorized=authorized,
            engine=str(value["engine"]),
            image_reference=reference,
            image_digest=image_digest,
            node_executable=_absolute(image["node_executable"], "OCI node executable"),
            bridge_executable=_absolute(
                image["bridge_executable"], "OCI bridge executable"
            ),
            bridge_digest=bridge_digest,
            launcher_executable=_absolute(
                image["launcher_executable"], "OCI launcher executable"
            ),
            launcher_digest=launcher_digest,
            container_model_endpoint=str(network["container_model_endpoint"]),
            container_peer_endpoint=str(network["container_peer_endpoint"]),
            uid=int(process["uid"]),
            gid=int(process["gid"]),
            cpu_limit=_string(process["cpu_limit"], "OCI CPU limit"),
            memory_limit=_string(process["memory_limit"], "OCI memory limit"),
            pids_limit=int(process["pids_limit"]),
            timeout_seconds=int(process["timeout_seconds"]),
            tmpfs_size=_string(process["tmpfs_size"], "OCI tmpfs size"),
            environment_allowlist=environment,
            required_conformance=conformance,
            unresolved_gates=unresolved,
            source_digest=source_digest,
            resolved_digest=digest_value(
                {"profile": value, "profile_digest": source_digest}
            ),
        )


class OciSandboxExec:
    """Build one exact rootless OCI invocation for a registered profile."""

    def __init__(
        self,
        profile: OciSandboxProfile,
        engine_executable: Path,
        engine_identity_digest: str,
    ) -> None:
        if not profile.execution_authorized:
            raise PermissionError("OCI sandbox profile is not execution-authorized")
        if not engine_executable.is_absolute() or not engine_executable.is_file():
            raise ValueError("OCI engine executable is invalid")
        if not _DIGEST.fullmatch(engine_identity_digest):
            raise ValueError("OCI engine identity digest is invalid")
        self._profile = profile
        self._engine = engine_executable
        self._engine_identity_digest = engine_identity_digest

    @property
    def profile_id(self) -> str:
        return self._profile.profile_id

    @property
    def profile_digest(self) -> str:
        return digest_value(
            {
                "profile_digest": self._profile.resolved_digest,
                "engine_identity_digest": self._engine_identity_digest,
            }
        )

    def validate_model_endpoint(self, endpoint: str) -> None:
        if endpoint != self._profile.container_model_endpoint:
            raise PermissionError("OCI sandbox requires its registered model relay")

    def prepare(
        self,
        command: tuple[str, ...],
        context: SandboxLaunchContext,
        environment: Mapping[str, str],
    ) -> SandboxedProcess:
        self.validate_model_endpoint(context.model_endpoint)
        socket_path = context.broker_socket
        if socket_path is None:
            raise ValueError("OCI sandbox requires a dedicated broker socket")
        if context.peer_endpoint is not None and (
            context.peer_endpoint != self._profile.container_peer_endpoint
        ):
            raise PermissionError("OCI sandbox requires its registered peer relay")
        if (
            context.peer_endpoint is not None
            and context.peer_broker_socket is None
        ):
            raise ValueError("OCI peer tool requires a dedicated broker socket")
        mounted_paths = [
            context.workspace_root,
            context.runtime_state_root,
            socket_path.parent,
        ]
        if context.peer_broker_socket is not None:
            mounted_paths.append(context.peer_broker_socket.parent)
        if any("," in str(path) or "\n" in str(path) for path in mounted_paths):
            raise ValueError("OCI sandbox mount path contains an unsupported character")
        expected_bridge = (context.runtime_assets_root / _BRIDGE_RELATIVE).resolve()
        if len(command) != 2 or Path(command[1]).resolve() != expected_bridge:
            raise PermissionError("OCI sandbox accepts only the registered bridge command")
        if set(environment) - set(self._profile.environment_allowlist) - {"PATH"}:
            raise PermissionError("OCI sandbox environment contains an unregistered key")
        assert self._profile.image_reference is not None
        assert self._profile.image_digest is not None
        image = f"{self._profile.image_reference}@{self._profile.image_digest}"
        args = [
            str(self._engine),
            "run",
            "--interactive",
            "--rm",
            "--init",
            "--read-only",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            f"{self._profile.uid}:{self._profile.gid}",
            "--cpus",
            self._profile.cpu_limit,
            "--memory",
            self._profile.memory_limit,
            "--memory-swap",
            self._profile.memory_limit,
            "--pids-limit",
            str(self._profile.pids_limit),
            "--mount",
            _mount(context.workspace_root, read_only=False),
            "--mount",
            _mount(context.runtime_state_root, read_only=False),
            "--mount",
            _mount(socket_path.parent, read_only=True),
        ]
        if (
            context.peer_broker_socket is not None
            and context.peer_broker_socket.parent != socket_path.parent
        ):
            args.extend(
                (
                    "--mount",
                    _mount(context.peer_broker_socket.parent, read_only=True),
                )
            )
        args.extend(
            [
                "--tmpfs",
                f"/tmp:rw,noexec,nosuid,nodev,size={self._profile.tmpfs_size}",
                "--workdir",
                str(context.workspace_root),
                "--env",
                "PATH=/opt/agent-collab/runtime/node_modules/.bin:/usr/local/bin:/usr/bin:/bin",
            ]
        )
        for key in sorted(self._profile.environment_allowlist):
            if key in environment:
                args.extend(("--env", f"{key}={environment[key]}"))
        launcher = [
            image,
            self._profile.launcher_executable,
            "--timeout-seconds",
            str(self._profile.timeout_seconds),
            "--broker-socket",
            str(socket_path),
            "--model-endpoint",
            self._profile.container_model_endpoint,
        ]
        if context.peer_broker_socket is not None:
            launcher.extend(
                (
                    "--peer-broker-socket",
                    str(context.peer_broker_socket),
                    "--peer-endpoint",
                    self._profile.container_peer_endpoint,
                )
            )
        launcher.extend(
            (
                "--",
                self._profile.node_executable,
                self._profile.bridge_executable,
            )
        )
        args.extend(launcher)
        return SandboxedProcess(
            command=tuple(args),
            working_directory=context.runtime_assets_root,
            environment={"LANG": "C.UTF-8", "PATH": "/usr/bin:/bin"},
        )

    def evidence(self) -> dict[str, object]:
        return {
            "sandbox_profile_id": self.profile_id,
            "sandbox_profile_digest": self.profile_digest,
            "driver": self._profile.engine,
            "network_mode": "none_with_dedicated_session_unix_socket",
            "filesystem_enforcement": "read_only_root_and_explicit_mounts",
            "process_resource_enforcement": "cpu_memory_pids_and_timeout",
            "credential_environment_allowlist": [],
            "required_conformance": list(self._profile.required_conformance),
        }


def _mount(path: Path, *, read_only: bool) -> str:
    resolved = path.resolve()
    options = f"type=bind,src={resolved},dst={resolved}"
    return options + (",readonly" if read_only else "")


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _keys(value: object, expected: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{label} fields differ")


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _absolute(value: object, label: str) -> str:
    text = _string(value, label)
    if not Path(text).is_absolute():
        raise ValueError(f"{label} must be absolute")
    return text


def _strings(
    value: object, label: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"{label} must be a list")
    strings = tuple(_string(item, label) for item in value)
    if len(set(strings)) != len(strings):
        raise ValueError(f"{label} must not contain duplicates")
    return strings
