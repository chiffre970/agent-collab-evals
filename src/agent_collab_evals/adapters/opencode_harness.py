"""Thin stock-OpenCode implementation of the harness runtime port."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import socket
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse

from ..canonical import canonical_json_bytes, digest_file, digest_value, load_json
from ..domain import (
    AgentIdentity,
    CoordinationCondition,
    HarnessOrganisation,
    HarnessSnapshot,
    Job,
    OrganisationSpec,
    SessionHandle,
    top_level_actor_count,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_BRIDGE_PATH = _REPOSITORY_ROOT / "scripts/runtime/opencode_bridge.mjs"
_NODE_BIN = _REPOSITORY_ROOT / "node_modules/.bin"


@dataclass(frozen=True, slots=True)
class OpenCodeRuntimeProfile:
    """Committed model/runtime variables with no credential material."""

    profile_id: str
    status: str
    opencode_version: str
    sdk_version: str
    provider_id: str
    provider_npm: str
    model_id: str
    model_name: str
    tool_call: bool
    context_tokens: int
    output_tokens: int
    agent_inference_profile: str
    instrumentation_mode: str
    source_digest: str
    agent_inference_digest: str
    package_lock_digest: str
    resolved_digest: str

    @classmethod
    def load(cls, source: Path, *, repository_root: Path = _REPOSITORY_ROOT) -> "OpenCodeRuntimeProfile":
        with source.open("r", encoding="utf-8") as stream:
            payload = load_json(stream)
        if not isinstance(payload, dict):
            raise ValueError("OpenCode runtime profile must be a JSON object")
        _require_keys(
            payload,
            {
                "schema_version",
                "profile_id",
                "status",
                "opencode_version",
                "sdk_version",
                "provider",
                "model",
                "agent_inference_profile",
                "instrumentation",
            },
            "runtime profile",
        )
        if payload["schema_version"] != "opencode-runtime-profile/v1":
            raise ValueError("unsupported OpenCode runtime profile schema")
        provider = _mapping(payload["provider"], "provider")
        model = _mapping(payload["model"], "model")
        instrumentation = _mapping(payload["instrumentation"], "instrumentation")
        _require_keys(provider, {"id", "npm"}, "provider")
        _require_keys(
            model,
            {"id", "name", "tool_call", "context_tokens", "output_tokens"},
            "model",
        )
        _require_keys(instrumentation, {"mode", "plugins"}, "instrumentation")
        if instrumentation["mode"] != "stock-sdk-out-of-process-sse":
            raise ValueError("V0 permits only stock out-of-process OpenCode observation")
        if instrumentation["plugins"] != []:
            raise ValueError("V0 runtime profile must not load instrumentation plugins")
        for field_name in ("context_tokens", "output_tokens"):
            if type(model[field_name]) is not int or model[field_name] < 1:
                raise ValueError(f"model.{field_name} must be a positive integer")
        if type(model["tool_call"]) is not bool:
            raise ValueError("model.tool_call must be a boolean")

        agent_profile_path = repository_root / str(payload["agent_inference_profile"])
        if not agent_profile_path.is_file():
            raise ValueError(f"agent inference profile does not exist: {agent_profile_path}")
        source_digest = digest_file(source)
        agent_digest = digest_file(agent_profile_path)
        package_lock_digest = digest_file(repository_root / "package-lock.json")
        resolved_digest = digest_value(
            {
                "runtime_profile": payload,
                "runtime_profile_digest": source_digest,
                "agent_inference_profile_digest": agent_digest,
                "package_lock_digest": package_lock_digest,
            }
        )
        return cls(
            profile_id=str(payload["profile_id"]),
            status=str(payload["status"]),
            opencode_version=str(payload["opencode_version"]),
            sdk_version=str(payload["sdk_version"]),
            provider_id=str(provider["id"]),
            provider_npm=str(provider["npm"]),
            model_id=str(model["id"]),
            model_name=str(model["name"]),
            tool_call=bool(model["tool_call"]),
            context_tokens=int(model["context_tokens"]),
            output_tokens=int(model["output_tokens"]),
            agent_inference_profile=str(payload["agent_inference_profile"]),
            instrumentation_mode=str(instrumentation["mode"]),
            source_digest=source_digest,
            agent_inference_digest=agent_digest,
            package_lock_digest=package_lock_digest,
            resolved_digest=resolved_digest,
        )


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _require_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(f"{label} keys differ; missing={missing}, unknown={unknown}")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@dataclass(frozen=True, slots=True)
class GatewayAccessToken:
    """Opaque, revocable credential scoped to one top-level agent session."""

    token_id: str
    value: str = field(repr=False)


class GatewayTokenIssuer(Protocol):
    def issue(
        self,
        *,
        campaign_run_id: str,
        actor_id: str,
        model_endpoint: str,
    ) -> GatewayAccessToken: ...

    def revoke(self, token_id: str, reason: str) -> None: ...


def _bridge_environment(
    state_root: Path, profile: OpenCodeRuntimeProfile
) -> dict[str, str]:
    """Build the complete child environment without inheriting host secrets."""

    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node is not installed or is absent from PATH")
    home = state_root / "home"
    temporary = state_root / "tmp"
    for directory in (home, temporary):
        directory.mkdir(parents=True, exist_ok=True)
    executable_paths = (
        str(_NODE_BIN),
        str(Path(node).resolve().parent),
        "/usr/bin",
        "/bin",
    )
    return {
        "PATH": os.pathsep.join(dict.fromkeys(executable_paths)),
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "LANG": "C.UTF-8",
        "XDG_DATA_HOME": str(state_root / "xdg/data"),
        "XDG_CONFIG_HOME": str(state_root / "xdg/config"),
        "XDG_CACHE_HOME": str(state_root / "xdg/cache"),
        "XDG_STATE_HOME": str(state_root / "xdg/state"),
        "AGENT_COLLAB_PROVIDER_ID": profile.provider_id,
        "AGENT_COLLAB_MODEL_ID": profile.model_id,
    }


class _Bridge:
    def __init__(
        self,
        *,
        state_root: Path,
        directory: Path,
        profile: OpenCodeRuntimeProfile,
        endpoint: str,
        gateway_token: str,
        native_handoffs: bool,
        timeout_seconds: int,
    ) -> None:
        state_root.mkdir(parents=True, exist_ok=True)
        directory.mkdir(parents=True, exist_ok=True)
        if not gateway_token:
            raise ValueError("gateway token issuer returned an empty credential")
        environment = _bridge_environment(state_root, profile)
        node = shutil.which("node", path=environment["PATH"])
        if node is None:
            raise RuntimeError("minimal bridge PATH cannot resolve node")
        self._timeout_seconds = timeout_seconds
        self._sequence = 0
        self._unusable = False
        self._responses: queue.Queue[object] = queue.Queue()
        self._stderr: list[str] = []
        self._lock = threading.Lock()
        self._process = subprocess.Popen(
            [node, str(_BRIDGE_PATH)],
            cwd=_REPOSITORY_ROOT,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()
        self.surface = self.request(
            "init",
            directory=str(directory),
            port=_free_port(),
            timeout_ms=timeout_seconds * 1000,
            config=_runtime_config(
                profile, endpoint, gateway_token, native_handoffs
            ),
        )["surface"]

    def _read_stdout(self) -> None:
        assert self._process.stdout is not None
        for line in self._process.stdout:
            try:
                self._responses.put(json.loads(line, parse_float=str))
            except json.JSONDecodeError as error:
                self._responses.put(RuntimeError(f"OpenCode bridge emitted invalid JSON: {error}"))
        self._responses.put(EOFError("OpenCode bridge closed its output"))

    def _read_stderr(self) -> None:
        assert self._process.stderr is not None
        for line in self._process.stderr:
            self._stderr.append(line.rstrip())
            if len(self._stderr) > 50:
                self._stderr.pop(0)

    def request(self, command: str, **payload: Any) -> Any:
        with self._lock:
            if self._unusable or self._process.poll() is not None:
                raise RuntimeError(self._failure("OpenCode bridge is not running"))
            self._sequence += 1
            request_id = self._sequence
            message = {"id": request_id, "command": command, **payload}
            assert self._process.stdin is not None
            self._process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            self._process.stdin.flush()
            try:
                response = self._responses.get(timeout=self._timeout_seconds)
            except queue.Empty as error:
                self._unusable = True
                self._terminate()
                raise TimeoutError(
                    self._failure(
                        f"OpenCode bridge timed out during {command}; bridge terminated"
                    )
                ) from error
            if isinstance(response, BaseException):
                raise RuntimeError(self._failure(str(response))) from response
            if response.get("id") != request_id:
                raise RuntimeError("OpenCode bridge response sequence mismatch")
            if not response.get("ok"):
                raise RuntimeError(self._failure(str(response.get("error"))))
            return response["result"]

    def close(self) -> None:
        if self._process.poll() is not None:
            self._close_streams()
            return
        if self._unusable:
            self._terminate()
            return
        try:
            self.request("shutdown")
        finally:
            self._terminate()

    def _terminate(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
        self._close_streams()

    def _close_streams(self) -> None:
        for stream in (
            self._process.stdin,
            self._process.stdout,
            self._process.stderr,
        ):
            if stream is not None and not stream.closed:
                stream.close()
        self._stdout_thread.join(timeout=1)
        self._stderr_thread.join(timeout=1)

    def _failure(self, message: str) -> str:
        details = "\n".join(self._stderr[-10:])
        return f"{message}\n{details}" if details else message


@dataclass(slots=True)
class _SessionState:
    handle: SessionHandle
    actor: AgentIdentity
    directory: Path
    state_root: Path
    bridge: _Bridge
    gateway_token_id: str
    delivered_jobs: dict[str, str] = field(default_factory=dict)
    events: list[Mapping[str, Any]] = field(default_factory=list)
    event_cursor: int = 0
    bridge_event_cursor: int = 0
    checkpoint: Mapping[str, Any] | None = None


@dataclass(slots=True)
class _OrganisationState:
    handle: HarnessOrganisation
    spec: OrganisationSpec
    sessions: dict[str, _SessionState] = field(default_factory=dict)
    stopped: bool = False


class OpenCodeHarnessRuntime:
    """One stock OpenCode server per top-level actor, behind HarnessRuntime."""

    def __init__(
        self,
        profile: OpenCodeRuntimeProfile,
        state_base: Path,
        gateway_tokens: GatewayTokenIssuer,
        *,
        timeout_seconds: int = 300,
    ) -> None:
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        self._profile = profile
        self._state_base = state_base
        self._gateway_tokens = gateway_tokens
        self._timeout_seconds = timeout_seconds
        self._organisations: dict[str, _OrganisationState] = {}
        self._session_to_organisation: dict[str, str] = {}
        self._verify_installation()

    def capabilities(self) -> Mapping[str, Any]:
        return {
            "capability_version": "opencode-harness/v1",
            "runtime_profile_id": self._profile.profile_id,
            "runtime_profile_digest": self._profile.resolved_digest,
            "durable_sessions": True,
            "native_handoffs": True,
            "observational_events": True,
            "peer_tool": False,
            "session_scoped_gateway_tokens": True,
        }

    def start_organisation(self, spec: OrganisationSpec) -> HarnessOrganisation:
        self._validate_spec(spec)
        organisation_id = f"opencode:{spec.campaign_run_id}"
        if organisation_id in self._organisations:
            raise ValueError(f"organisation already exists: {organisation_id}")
        handle = HarnessOrganisation(organisation_id)
        self._organisations[organisation_id] = _OrganisationState(handle, spec)
        return handle

    @staticmethod
    def _validate_spec(spec: OrganisationSpec) -> None:
        parsed = urlparse(spec.model_endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("OpenCode model endpoint must be an absolute HTTP(S) URL")
        if spec.condition in {
            CoordinationCondition.PEER_ISOLATED,
            CoordinationCondition.PEER_COLLAB,
        }:
            raise RuntimeError(
                "peer conditions require the not-yet-installed matched peer-tool profile"
            )

    def create_primary(
        self, organisation: HarnessOrganisation, actor: AgentIdentity
    ) -> SessionHandle:
        state = self._active_organisation(organisation)
        if actor.campaign_run_id != state.spec.campaign_run_id:
            raise ValueError("actor belongs to a different campaign")
        actor_key = f"actor-{actor.ordinal:04d}"
        directory = state.spec.workspace_root / actor_key
        state_root = self._organisation_state_root(state.spec.campaign_run_id) / actor_key
        credential = self._gateway_tokens.issue(
            campaign_run_id=state.spec.campaign_run_id,
            actor_id=actor.actor_id,
            model_endpoint=state.spec.model_endpoint,
        )
        if not credential.token_id or not credential.value:
            raise ValueError("gateway token issuer returned an invalid credential")
        try:
            bridge = self._start_bridge(
                state.spec, directory, state_root, credential.value
            )
        except Exception:
            self._gateway_tokens.revoke(
                credential.token_id, "runtime bridge creation failed"
            )
            raise
        try:
            created = bridge.request(
                "create_session",
                directory=str(directory),
                title=f"{state.spec.campaign_run_id} / actor {actor.ordinal}",
            )
            handle = SessionHandle(str(created["id"]))
            if handle.value in self._session_to_organisation:
                raise ValueError(f"session already exists: {handle.value}")
            session = _SessionState(
                handle,
                actor,
                directory,
                state_root,
                bridge,
                credential.token_id,
            )
            state.sessions[handle.value] = session
            self._session_to_organisation[handle.value] = organisation.value
            return handle
        except Exception:
            bridge.close()
            self._gateway_tokens.revoke(
                credential.token_id, "session creation failed"
            )
            raise

    def deliver(self, session: SessionHandle, job: Job) -> None:
        state = self._session(session)
        previous = state.delivered_jobs.get(job.job_id)
        if previous is not None:
            if previous != job.materials_digest:
                raise ValueError(f"job identifier reused with different materials: {job.job_id}")
            return
        prompt = canonical_json_bytes(
            {
                "job_id": job.job_id,
                "materials_digest": job.materials_digest,
                "mission": job.mission,
                "public_materials": job.public_materials,
            }
        ).decode("utf-8")
        state.bridge.request(
            "prompt",
            session_id=session.value,
            directory=str(state.directory),
            text=prompt,
        )
        state.delivered_jobs[job.job_id] = job.materials_digest

    def events(self, organisation: HarnessOrganisation) -> tuple[Mapping[str, Any], ...]:
        state = self._organisation(organisation)
        result: list[Mapping[str, Any]] = []
        for session in state.sessions.values():
            self._checkpoint(session)
            result.extend(session.events)
        return tuple(result)

    def snapshot(self, organisation: HarnessOrganisation) -> HarnessSnapshot:
        state = self._organisation(organisation)
        sessions = []
        for session in state.sessions.values():
            surface = session.bridge.request("surface")
            self._checkpoint(session)
            sessions.append(
                {
                    "session_id": session.handle.value,
                    "actor_ordinal": session.actor.ordinal,
                    "directory": str(session.directory),
                    "state_root": str(session.state_root),
                    "gateway_token_id": session.gateway_token_id,
                    "delivered_jobs": [
                        {"job_id": job_id, "materials_digest": materials_digest}
                        for job_id, materials_digest in session.delivered_jobs.items()
                    ],
                    "surface": surface,
                    "events": session.events,
                    "event_cursor": session.event_cursor,
                    "checkpoint": session.checkpoint,
                }
            )
        payload = {
            "schema": "opencode-harness-snapshot/v2",
            "runtime_profile_id": self._profile.profile_id,
            "runtime_profile_digest": self._profile.resolved_digest,
            "spec": {
                "campaign_run_id": state.spec.campaign_run_id,
                "condition": state.spec.condition.value,
                "organisation_size": state.spec.organisation_size,
                "workspace_root": str(state.spec.workspace_root),
                "model_endpoint": state.spec.model_endpoint,
            },
            "sessions": sessions,
            "stopped": state.stopped,
        }
        return HarnessSnapshot(organisation.value, payload)

    def resume(self, snapshot: HarnessSnapshot) -> HarnessOrganisation:
        payload = _mapping(snapshot.payload, "harness snapshot")
        if payload.get("schema") != "opencode-harness-snapshot/v2":
            raise ValueError("unsupported OpenCode harness snapshot schema")
        if payload.get("runtime_profile_digest") != self._profile.resolved_digest:
            raise ValueError("runtime profile changed across resume")
        if snapshot.organisation_id in self._organisations:
            raise ValueError(f"organisation already exists: {snapshot.organisation_id}")
        if payload.get("stopped"):
            raise ValueError("cannot resume a stopped harness organisation")
        spec_payload = _mapping(payload["spec"], "snapshot spec")
        spec = OrganisationSpec(
            campaign_run_id=str(spec_payload["campaign_run_id"]),
            condition=CoordinationCondition(str(spec_payload["condition"])),
            organisation_size=int(spec_payload["organisation_size"]),
            workspace_root=Path(str(spec_payload["workspace_root"])),
            model_endpoint=str(spec_payload["model_endpoint"]),
        )
        self._validate_spec(spec)
        handle = HarnessOrganisation(snapshot.organisation_id)
        session_items = payload.get("sessions")
        if not isinstance(session_items, list):
            raise ValueError("snapshot sessions must be a list")
        expected_actor_count = top_level_actor_count(
            spec.condition, spec.organisation_size
        )
        ordinal_values = [
            _mapping(item, "snapshot session")["actor_ordinal"]
            for item in session_items
        ]
        if any(type(ordinal) is not int for ordinal in ordinal_values):
            raise ValueError("snapshot actor ordinals must be integers")
        ordinals = set(ordinal_values)
        if len(session_items) != expected_actor_count or ordinals != set(
            range(expected_actor_count)
        ):
            raise ValueError("snapshot sessions do not match the condition topology")
        restored = _OrganisationState(handle, spec)
        try:
            for item_value in session_items:
                item = _mapping(item_value, "snapshot session")
                actor_ordinal = int(item["actor_ordinal"])
                actor_key = f"actor-{actor_ordinal:04d}"
                directory = spec.workspace_root / actor_key
                state_root = (
                    self._organisation_state_root(spec.campaign_run_id) / actor_key
                )
                if Path(str(item["directory"])) != directory:
                    raise ValueError("snapshot actor workspace is not canonical")
                if Path(str(item["state_root"])) != state_root:
                    raise ValueError("snapshot runtime state root is not canonical")
                actor = AgentIdentity(spec.campaign_run_id, actor_ordinal)
                credential = self._gateway_tokens.issue(
                    campaign_run_id=spec.campaign_run_id,
                    actor_id=actor.actor_id,
                    model_endpoint=spec.model_endpoint,
                )
                if not credential.token_id or not credential.value:
                    raise ValueError(
                        "gateway token issuer returned an invalid credential"
                    )
                try:
                    bridge = self._start_bridge(
                        spec, directory, state_root, credential.value
                    )
                except Exception:
                    self._gateway_tokens.revoke(
                        credential.token_id, "runtime bridge resume failed"
                    )
                    raise
                try:
                    session_id = str(item["session_id"])
                    if session_id in self._session_to_organisation:
                        raise ValueError(f"session already exists: {session_id}")
                    bridge.request(
                        "get_session", session_id=session_id, directory=str(directory)
                    )
                    surface = bridge.request("surface")
                    if surface != item["surface"]:
                        raise RuntimeError("effective OpenCode surface changed across resume")
                except Exception:
                    bridge.close()
                    self._gateway_tokens.revoke(
                        credential.token_id, "session resume failed"
                    )
                    raise
                session = _SessionState(
                    handle=SessionHandle(session_id),
                    actor=actor,
                    directory=directory,
                    state_root=state_root,
                    bridge=bridge,
                    gateway_token_id=credential.token_id,
                    delivered_jobs={
                        str(job["job_id"]): str(job["materials_digest"])
                        for job in item["delivered_jobs"]
                    },
                    events=list(item["events"]),
                    event_cursor=int(item["event_cursor"]),
                    checkpoint=_mapping(item["checkpoint"], "event checkpoint"),
                )
                restored.sessions[session_id] = session
                self._session_to_organisation[session_id] = handle.value
        except Exception:
            for session in restored.sessions.values():
                session.bridge.close()
                self._gateway_tokens.revoke(
                    session.gateway_token_id, "organization resume rolled back"
                )
                self._session_to_organisation.pop(
                    session.handle.value, None
                )
            raise
        self._organisations[handle.value] = restored
        return handle

    def suspend(self, organisation: HarnessOrganisation) -> HarnessSnapshot:
        """Release local transports after a durable snapshot without closing work."""

        state = self._active_organisation(organisation)
        snapshot = self.snapshot(organisation)
        for session in state.sessions.values():
            session.bridge.close()
            self._gateway_tokens.revoke(session.gateway_token_id, "runtime suspended")
            self._session_to_organisation.pop(session.handle.value, None)
        self._organisations.pop(organisation.value)
        return snapshot

    def stop(
        self, organisation: HarnessOrganisation, reason: str
    ) -> HarnessSnapshot:
        state = self._active_organisation(organisation)
        snapshot = self.snapshot(organisation)
        for session in state.sessions.values():
            session.bridge.close()
            self._gateway_tokens.revoke(session.gateway_token_id, reason)
        state.stopped = True
        payload = dict(snapshot.payload)
        payload.update({"stopped": True, "stop_reason": reason})
        return HarnessSnapshot(snapshot.organisation_id, payload)

    def delivered_jobs(self, session: SessionHandle) -> tuple[str, ...]:
        return tuple(self._session(session).delivered_jobs)

    def _checkpoint(self, session: _SessionState) -> None:
        checkpoint = _mapping(
            session.bridge.request(
                "checkpoint",
                session_ids=[session.handle.value],
                directory=str(session.directory),
            ),
            "event checkpoint",
        )
        if not checkpoint.get("complete"):
            raise RuntimeError(
                "OpenCode event checkpoint is incomplete: "
                f"loss={checkpoint.get('event_loss_reason')!r}, "
                f"stream={checkpoint.get('event_stream_error')!r}, "
                f"quiet={checkpoint.get('quiet')!r}, "
                f"terminal={checkpoint.get('terminal')!r}"
            )
        records = checkpoint.get("records")
        if not isinstance(records, list):
            raise RuntimeError("OpenCode event checkpoint records are invalid")
        for value in records:
            record = _mapping(value, "OpenCode event record")
            source_cursor = record.get("cursor")
            if (
                type(source_cursor) is not int
                or source_cursor != session.bridge_event_cursor + 1
            ):
                raise RuntimeError("OpenCode event source cursor has a gap")
            session.bridge_event_cursor = source_cursor
            session.event_cursor += 1
            session.events.append(
                {
                    "cursor": session.event_cursor,
                    "source_cursor": source_cursor,
                    "event": record.get("event"),
                }
            )
        if checkpoint.get("source_cursor") != session.bridge_event_cursor:
            raise RuntimeError("OpenCode checkpoint cursor differs from drained events")
        reconciliation = _mapping(
            checkpoint.get("reconciliation"), "session reconciliation"
        )
        reconciled_sessions = reconciliation.get("sessions")
        if not isinstance(reconciled_sessions, list):
            raise RuntimeError("OpenCode session reconciliation is invalid")
        observed = canonical_json_bytes(session.events)
        required_ids: list[str] = []
        for value in reconciled_sessions:
            reconciled = _mapping(value, "reconciled session")
            required_ids.append(str(reconciled["session_id"]))
            message_ids = reconciled.get("message_ids")
            if not isinstance(message_ids, list):
                raise RuntimeError("OpenCode message reconciliation is invalid")
            required_ids.extend(str(message_id) for message_id in message_ids)
        missing = [
            identifier
            for identifier in required_ids
            if identifier.encode("utf-8") not in observed
        ]
        if missing:
            raise RuntimeError(
                "OpenCode events do not reconcile with terminal state: "
                + ", ".join(missing)
            )
        session.checkpoint = {
            key: value for key, value in checkpoint.items() if key != "records"
        }

    def _start_bridge(
        self,
        spec: OrganisationSpec,
        directory: Path,
        state_root: Path,
        gateway_token: str,
    ) -> _Bridge:
        return _Bridge(
            state_root=state_root,
            directory=directory,
            profile=self._profile,
            endpoint=spec.model_endpoint,
            gateway_token=gateway_token,
            native_handoffs=spec.condition is CoordinationCondition.NATIVE_MULTIAGENT,
            timeout_seconds=self._timeout_seconds,
        )

    def _organisation_state_root(self, campaign_run_id: str) -> Path:
        identifier = hashlib.sha256(campaign_run_id.encode("utf-8")).hexdigest()[:24]
        return self._state_base / identifier

    def _verify_installation(self) -> None:
        versions = {
            "opencode": (
                _REPOSITORY_ROOT / "node_modules/opencode-ai/package.json",
                self._profile.opencode_version,
            ),
            "SDK": (
                _REPOSITORY_ROOT / "node_modules/@opencode-ai/sdk/package.json",
                self._profile.sdk_version,
            ),
        }
        for label, (package_path, expected) in versions.items():
            if not package_path.is_file():
                raise RuntimeError(f"{label} is not installed; run npm install")
            with package_path.open("r", encoding="utf-8") as stream:
                actual = str(load_json(stream)["version"])
            if actual != expected:
                raise RuntimeError(f"{label} version mismatch: expected {expected}, got {actual}")

    def _organisation(self, handle: HarnessOrganisation) -> _OrganisationState:
        try:
            return self._organisations[handle.value]
        except KeyError as error:
            raise KeyError(f"unknown organisation: {handle.value}") from error

    def _active_organisation(self, handle: HarnessOrganisation) -> _OrganisationState:
        state = self._organisation(handle)
        if state.stopped:
            raise RuntimeError("harness organisation is stopped")
        return state

    def _session(self, handle: SessionHandle) -> _SessionState:
        try:
            organisation_id = self._session_to_organisation[handle.value]
            return self._organisations[organisation_id].sessions[handle.value]
        except KeyError as error:
            raise KeyError(f"unknown session: {handle.value}") from error


def _runtime_config(
    profile: OpenCodeRuntimeProfile,
    endpoint: str,
    gateway_token: str,
    native_handoffs: bool,
) -> Mapping[str, Any]:
    denied_tools = {"bash": False, "edit": False, "webfetch": False, "write": False}
    permissions = {
        "edit": "deny",
        "bash": "deny",
        "webfetch": "deny",
        "doom_loop": "deny",
        "external_directory": "deny",
    }
    model = f"{profile.provider_id}/{profile.model_id}"
    return {
        "logLevel": "ERROR",
        "autoupdate": False,
        "share": "disabled",
        "snapshot": False,
        "plugin": [],
        "formatter": False,
        "lsp": False,
        "enabled_providers": [profile.provider_id],
        "model": model,
        "small_model": model,
        "provider": {
            profile.provider_id: {
                "name": "Experiment budget gateway",
                "npm": profile.provider_npm,
                "options": {
                    "apiKey": gateway_token,
                    "baseURL": endpoint,
                    "timeout": False,
                },
                "models": {
                    profile.model_id: {
                        "id": profile.model_id,
                        "name": profile.model_name,
                        "tool_call": profile.tool_call,
                        "temperature": False,
                        "reasoning": True,
                        "attachment": False,
                        "limit": {
                            "context": profile.context_tokens,
                            "output": profile.output_tokens,
                        },
                        "modalities": {"input": ["text"], "output": ["text"]},
                        "status": "active",
                    }
                },
            }
        },
        "permission": permissions,
        "tools": {**denied_tools, "task": native_handoffs},
        "agent": {
            "build": {
                "mode": "primary",
                "model": model,
                "tools": {**denied_tools, "task": native_handoffs},
                "permission": permissions,
            },
            "general": {
                "mode": "subagent",
                "model": model,
                "tools": {**denied_tools, "task": False},
                "permission": permissions,
            },
        },
    }
