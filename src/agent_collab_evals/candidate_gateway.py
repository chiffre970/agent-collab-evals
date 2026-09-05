"""Development HTTP and Unix transports for session-bound capability services."""

from __future__ import annotations

import hashlib
import secrets
import socketserver
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit

from .canonical import canonical_json_bytes, parse_json
from .collaboration import SessionTransport
from .domain import AgentIdentity, SessionHandle
from .session_identity import SessionIdentityRegistry


@dataclass(frozen=True, slots=True)
class CandidateToolAccess:
    token_id: str
    endpoint: str
    token: str = field(repr=False)
    broker_socket: Path | None = None


@dataclass
class _Access:
    token_id: str
    actor: AgentIdentity
    session: SessionTransport | None = None
    lock: Any = field(default_factory=threading.RLock)


class SessionTools(Protocol):
    @property
    def profile_digest(self) -> str: ...

    def call(self, session: SessionTransport, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]: ...


class _UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class SessionToolGateway:
    """Local-only transport; not a registered sandbox capability broker."""

    def __init__(
        self, tools: SessionTools, sessions: SessionIdentityRegistry, *,
        serve_http: bool = True, unix_socket_root: Path | None = None,
        advertised_endpoint: str | None = None,
    ) -> None:
        if (unix_socket_root is None) != (advertised_endpoint is None):
            raise ValueError("Unix capability transport requires a socket root and endpoint")
        if serve_http == (unix_socket_root is not None):
            raise ValueError("select either host HTTP or Unix capability transport")
        if advertised_endpoint is not None:
            parsed = urlsplit(advertised_endpoint)
            if (
                parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                or parsed.port is None or parsed.port < 1 or parsed.path != "/v1/call"
                or parsed.query or parsed.fragment or parsed.username or parsed.password
            ):
                raise ValueError("Unix capability endpoint must be fixed container loopback")
        self.tools = tools
        self._sessions = sessions
        self._access: dict[str, _Access] = {}
        self._lock = threading.RLock()
        self._closed = False
        self._advertised_endpoint = advertised_endpoint
        self._unix_socket_root = unix_socket_root.resolve() if unix_socket_root is not None else None
        self._unix_transports: dict[str, tuple[_UnixServer, threading.Thread, Path]] = {}
        if self._unix_socket_root is not None:
            self._unix_socket_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._unix_socket_root.chmod(0o700)
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                gateway._handle(self, getattr(self.server, "expected_token_id", None))

            def log_message(self, *_: object) -> None:
                return

        self._handler = Handler
        self._server = None
        self._thread = None
        if serve_http:
            self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            self._server.daemon_threads = True
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()

    @property
    def endpoint(self) -> str:
        if self._advertised_endpoint is not None:
            return self._advertised_endpoint
        return f"http://127.0.0.1:{self._server.server_port}/v1/call"

    @property
    def profile_digest(self) -> str:
        from .canonical import digest_value
        transport = "development-session-tools-unix/v1" if self._unix_socket_root is not None else "development-session-tools-http/v1"
        return digest_value({"transport": transport, "tools": self.tools.profile_digest})

    def issue(self, actor: AgentIdentity) -> CandidateToolAccess:
        token = secrets.token_urlsafe(32)
        token_id = "candidate-" + secrets.token_hex(16)
        with self._lock:
            if self._closed:
                raise RuntimeError("capability gateway is closed")
            broker_socket = self._start_unix_transport(token_id)
            self._access[self._digest(token)] = _Access(token_id, actor)
        return CandidateToolAccess(token_id, self.endpoint, token, broker_socket)

    def activate(self, access: CandidateToolAccess, session: SessionHandle) -> None:
        with self._lock:
            state = self._access[self._digest(access.token)]
            with state.lock:
                if state.session is not None:
                    raise RuntimeError("candidate access is already active")
                state.session = self._sessions.bind(state.actor, session)

    def revoke(self, access: CandidateToolAccess) -> None:
        with self._lock:
            state = self._access.pop(self._digest(access.token), None)
        if state is not None:
            try:
                with state.lock:
                    if state.session is not None:
                        self._sessions.revoke(state.session)
                        state.session = None
            finally:
                self._stop_unix_transport(state.token_id)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            states = tuple(self._access.values())
            self._access.clear()
        from contextlib import ExitStack
        with ExitStack() as cleanup:
            if self._server is not None:
                cleanup.callback(self._thread.join, timeout=5)
                cleanup.callback(self._server.server_close)
                cleanup.callback(self._server.shutdown)
            for state in states:
                cleanup.callback(self._stop_unix_transport, state.token_id)
                cleanup.callback(self._expire_session, state)

    def _expire_session(self, state: _Access) -> None:
        with state.lock:
            if state.session is not None:
                self._sessions.revoke(state.session)
                state.session = None

    def _start_unix_transport(self, token_id: str) -> Path | None:
        if self._unix_socket_root is None:
            return None
        directory = self._unix_socket_root / ("c-" + token_id[-12:])
        directory.mkdir(mode=0o755)
        path = directory / "capability.sock"
        server = None
        try:
            if len(str(path).encode()) >= 100:
                raise ValueError("Unix capability socket path exceeds the portable limit")
            server = _UnixServer(str(path), self._handler)
            server.expected_token_id = token_id
            # The private root is not mounted; the actor receives only its leaf.
            path.chmod(0o666)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
        except BaseException:
            if server is not None:
                server.server_close()
            path.unlink(missing_ok=True)
            directory.rmdir()
            raise
        self._unix_transports[token_id] = (server, thread, path)
        return path

    def _stop_unix_transport(self, token_id: str) -> None:
        with self._lock:
            transport = self._unix_transports.pop(token_id, None)
        if transport is None:
            return
        server, thread, path = transport
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        path.unlink(missing_ok=True)
        path.parent.rmdir()

    def _handle(self, handler: BaseHTTPRequestHandler, expected_token_id: str | None = None) -> None:
        handler.connection.settimeout(30)
        if handler.path != "/v1/call":
            self._respond(handler, 404, {"error": "unknown capability route"})
            return
        authorization = handler.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            self._respond(handler, 403, {"error": "candidate access denied"})
            return
        with self._lock:
            state = self._access.get(self._digest(authorization[7:]))
        if state is None or (expected_token_id is not None and state.token_id != expected_token_id):
            self._respond(handler, 403, {"error": "candidate access denied"})
            return
        try:
            values = handler.headers.get_all("Content-Length", [])
            if len(values) != 1 or handler.headers.get("Transfer-Encoding"):
                raise ValueError("invalid request framing")
            length = int(values[0])
            if not 1 <= length <= 65536:
                raise ValueError("request exceeds bound")
            body = handler.rfile.read(length)
            if len(body) != length:
                raise ValueError("incomplete request")
            value = parse_json(body.decode("utf-8"))
            if (
                not isinstance(value, dict) or set(value) != {"operation", "arguments"}
                or not isinstance(value["operation"], str)
                or not isinstance(value["arguments"], dict)
            ):
                raise ValueError("invalid capability request")
            with state.lock:
                if state.session is None:
                    raise PermissionError("session is not active")
                result = self.tools.call(state.session, value["operation"], value["arguments"])
            self._respond(handler, 200, {"result": result})
        except PermissionError:
            self._respond(handler, 403, {"error": "candidate access denied"})
        except (ValueError, KeyError):
            self._respond(handler, 400, {"error": "invalid candidate request"})
        except Exception:
            # Do not return evaluator paths or internal exception details to agents.
            self._respond(handler, 503, {"error": "candidate operation unavailable"})

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _respond(handler: BaseHTTPRequestHandler, status: int, value: dict[str, Any]) -> None:
        body = canonical_json_bytes(value)
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)


CandidateToolGateway = SessionToolGateway
