"""Pinned peer-tool profile and session-bound collaboration gateway."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

from .canonical import digest_file, digest_value, load_json, parse_json
from .collaboration import (
    CollaborationEntry,
    CollaborationScope,
    CollaborationVisibility,
    Notification,
    Page,
    SessionTransport,
)
from .domain import AgentIdentity, SessionHandle
from .ports import CollaborationBackend
from .session_identity import SessionIdentityRegistry


@dataclass(frozen=True, slots=True)
class PeerToolIntegrationProfile:
    profile_id: str
    status: str
    mcp_sdk_version: str
    tool_names: tuple[str, ...]
    source_digest: str
    server_script_digest: str
    package_lock_digest: str
    resolved_digest: str

    @classmethod
    def load(
        cls,
        source: Path,
        *,
        repository_root: Path,
    ) -> "PeerToolIntegrationProfile":
        with source.open("r", encoding="utf-8") as stream:
            payload = load_json(stream)
        if not isinstance(payload, dict):
            raise ValueError("peer-tool profile must be a JSON object")
        expected = {
            "schema_version",
            "profile_id",
            "status",
            "mcp_sdk_version",
            "server_script",
            "tool_names",
        }
        if set(payload) != expected:
            raise ValueError("peer-tool profile keys differ")
        if payload["schema_version"] != "peer-tool-profile/v1":
            raise ValueError("unsupported peer-tool profile schema")
        tool_names = payload["tool_names"]
        if (
            not isinstance(tool_names, list)
            or not tool_names
            or any(not isinstance(name, str) or not name for name in tool_names)
            or len(set(tool_names)) != len(tool_names)
        ):
            raise ValueError("peer-tool names must be unique nonempty strings")
        server_script = repository_root / str(payload["server_script"])
        if not server_script.is_file():
            raise ValueError(f"peer-tool server script does not exist: {server_script}")
        package_path = (
            repository_root
            / "node_modules/@modelcontextprotocol/sdk/package.json"
        )
        with package_path.open("r", encoding="utf-8") as stream:
            installed_version = str(load_json(stream)["version"])
        expected_version = str(payload["mcp_sdk_version"])
        if installed_version != expected_version:
            raise ValueError(
                "MCP SDK version mismatch: "
                f"expected {expected_version}, got {installed_version}"
            )
        source_digest = digest_file(source)
        script_digest = digest_file(server_script)
        package_lock_digest = digest_file(repository_root / "package-lock.json")
        resolved_digest = digest_value(
            {
                "profile": payload,
                "profile_digest": source_digest,
                "server_script_digest": script_digest,
                "package_lock_digest": package_lock_digest,
            }
        )
        return cls(
            profile_id=str(payload["profile_id"]),
            status=str(payload["status"]),
            mcp_sdk_version=expected_version,
            tool_names=tuple(tool_names),
            source_digest=source_digest,
            server_script_digest=script_digest,
            package_lock_digest=package_lock_digest,
            resolved_digest=resolved_digest,
        )


@dataclass(frozen=True, slots=True)
class PeerToolAccess:
    token_id: str
    endpoint: str
    token: str = field(repr=False)


@dataclass(slots=True)
class _AccessState:
    actor: AgentIdentity
    scope: CollaborationScope
    transport: SessionTransport | None = None


class PeerToolGateway:
    """Maps an opaque sidecar credential to server-derived actor identity."""

    def __init__(
        self,
        backend: CollaborationBackend,
        identities: SessionIdentityRegistry,
        *,
        serve_http: bool = True,
    ) -> None:
        self._backend = backend
        self._identities = identities
        self._lock = threading.RLock()
        self._scopes: dict[str, CollaborationScope] = {}
        self._access: dict[str, _AccessState] = {}
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
                gateway._handle(self)

            def log_message(self, format: str, *args: object) -> None:
                return

        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        if serve_http:
            self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            self._server.daemon_threads = True
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name="peer-tool-gateway",
                daemon=True,
            )
            self._thread.start()

    @property
    def endpoint(self) -> str:
        if self._server is None:
            return "http://peer-tool.invalid/v1/call"
        host, port = self._server.server_address
        return f"http://{host}:{port}/v1/call"

    def provision(
        self, campaign_run_id: str, visibility: CollaborationVisibility
    ) -> CollaborationScope:
        scope = self._backend.provision(campaign_run_id, visibility)
        with self._lock:
            existing = self._scopes.get(campaign_run_id)
            if existing is not None and existing != scope:
                raise ValueError("peer-tool campaign scope changed")
            self._scopes[campaign_run_id] = scope
        return scope

    def issue(self, actor: AgentIdentity) -> PeerToolAccess:
        with self._lock:
            try:
                scope = self._scopes[actor.campaign_run_id]
            except KeyError as error:
                raise KeyError("peer-tool campaign is not provisioned") from error
            token_id = f"peer-token-{secrets.token_hex(12)}"
            token = secrets.token_urlsafe(32)
            token_digest = self._token_digest(token)
            self._access[token_digest] = _AccessState(actor, scope)
        return PeerToolAccess(token_id, self.endpoint, token)

    def activate(self, access: PeerToolAccess, session: SessionHandle) -> None:
        token_digest = self._token_digest(access.token)
        with self._lock:
            state = self._require_access(token_digest)
            if state.transport is not None:
                raise ValueError("peer-tool access is already active")
            state.transport = self._identities.bind(state.actor, session)

    def revoke(self, access: PeerToolAccess) -> None:
        token_digest = self._token_digest(access.token)
        with self._lock:
            state = self._access.pop(token_digest, None)
            if state is None:
                return
            if state.transport is not None:
                self._identities.revoke(state.transport)

    def invoke(
        self,
        access: PeerToolAccess,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Exercise the authenticated gateway without a network transport."""

        token_digest = self._token_digest(access.token)
        with self._lock:
            state = self._require_access(token_digest)
            if state.transport is None:
                raise PermissionError("peer-tool session is not active")
            return self._invoke(
                state.scope, state.transport, operation, arguments
            )

    def close(self) -> None:
        with self._lock:
            states = tuple(self._access.values())
            self._access.clear()
            for state in states:
                if state.transport is not None:
                    self._identities.revoke(state.transport)
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        if handler.path != "/v1/call":
            self._send(handler, 404, {"error": "unknown peer-tool endpoint"})
            return
        length_value = handler.headers.get("Content-Length")
        try:
            length = int(length_value or "")
        except ValueError:
            self._send(handler, 400, {"error": "invalid content length"})
            return
        if not 1 <= length <= 65_536:
            self._send(handler, 400, {"error": "peer-tool request is too large"})
            return
        authorization = handler.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            self._send(handler, 403, {"error": "peer-tool access denied"})
            return
        token_digest = self._token_digest(authorization[7:])
        try:
            payload = parse_json(handler.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict) or set(payload) != {
                "operation",
                "arguments",
            }:
                raise ValueError("peer-tool request shape is invalid")
            arguments = payload["arguments"]
            if not isinstance(arguments, dict):
                raise ValueError("peer-tool arguments must be an object")
            with self._lock:
                state = self._require_access(token_digest)
                if state.transport is None:
                    raise PermissionError("peer-tool session is not active")
                result = self._invoke(
                    state.scope,
                    state.transport,
                    str(payload["operation"]),
                    arguments,
                )
        except PermissionError as error:
            self._send(handler, 403, {"error": str(error)})
            return
        except (KeyError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            self._send(handler, 400, {"error": str(error)})
            return
        except Exception:
            self._send(handler, 500, {"error": "peer-tool operation failed"})
            return
        self._send(handler, 200, {"result": result})

    def _invoke(
        self,
        scope: CollaborationScope,
        transport: SessionTransport,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if operation == "publish":
            expected = {"idempotency_key", "body", "reply_to"}
            self._require_argument_keys(arguments, expected)
            entry = self._backend.publish(
                scope,
                transport,
                str(arguments["idempotency_key"]),
                str(arguments["body"]),
                self._optional_string(arguments["reply_to"]),
            )
            return {"entry": self._entry(entry)}
        if operation == "list_recent":
            self._require_argument_keys(arguments, {"cursor", "limit"})
            page = self._backend.list_recent(
                scope,
                transport,
                self._optional_string(arguments["cursor"]),
                self._limit(arguments["limit"]),
            )
            return self._entry_page(page)
        if operation == "get_thread":
            self._require_argument_keys(arguments, {"entry_id"})
            entries = self._backend.get_thread(
                scope, transport, str(arguments["entry_id"])
            )
            return {"entries": [self._entry(entry) for entry in entries]}
        if operation == "search":
            self._require_argument_keys(arguments, {"query", "cursor", "limit"})
            page = self._backend.search(
                scope,
                transport,
                str(arguments["query"]),
                self._optional_string(arguments["cursor"]),
                self._limit(arguments["limit"]),
            )
            return self._entry_page(page)
        if operation == "notifications":
            self._require_argument_keys(arguments, {"cursor", "limit"})
            page = self._backend.notifications(
                scope,
                transport,
                self._optional_string(arguments["cursor"]),
                self._limit(arguments["limit"]),
            )
            return {
                "items": [self._notification(item) for item in page.items],
                "next_cursor": page.next_cursor,
            }
        raise ValueError("unknown peer-tool operation")

    def _require_access(self, token_digest: str) -> _AccessState:
        try:
            return self._access[token_digest]
        except KeyError as error:
            raise PermissionError("peer-tool access denied") from error

    @staticmethod
    def _require_argument_keys(
        arguments: Mapping[str, Any], expected: set[str]
    ) -> None:
        if set(arguments) != expected:
            raise ValueError("peer-tool argument keys differ")

    @staticmethod
    def _optional_string(value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("optional peer-tool value must be a string or null")
        return value

    @staticmethod
    def _limit(value: object) -> int:
        if type(value) is not int:
            raise ValueError("peer-tool page limit must be an integer")
        return value

    @staticmethod
    def _entry(entry: CollaborationEntry) -> Mapping[str, Any]:
        return {
            "entry_id": entry.entry_id,
            "sequence": entry.sequence,
            "actor_id": entry.actor_id,
            "body": entry.body,
            "reply_to": entry.reply_to,
            "thread_root": entry.thread_root,
            "publication_ids": list(entry.publication_ids),
        }

    @classmethod
    def _entry_page(
        cls, page: Page[CollaborationEntry]
    ) -> Mapping[str, Any]:
        return {
            "items": [cls._entry(item) for item in page.items],
            "next_cursor": page.next_cursor,
        }

    @staticmethod
    def _notification(notification: Notification) -> Mapping[str, Any]:
        return {
            "sequence": notification.sequence,
            "entry_id": notification.entry_id,
            "actor_id": notification.actor_id,
            "kind": notification.kind,
        }

    @staticmethod
    def _token_digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _send(
        handler: BaseHTTPRequestHandler,
        status: int,
        payload: Mapping[str, Any],
    ) -> None:
        content = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(content)))
        handler.end_headers()
        handler.wfile.write(content)
