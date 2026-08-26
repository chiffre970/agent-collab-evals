"""Server-side binding from local transports to campaign session identity."""

from __future__ import annotations

import threading
from pathlib import Path

from .collaboration import SessionContext, SessionTransport
from .domain import AgentIdentity, SessionHandle


class SessionIdentityRegistry:
    """Keeps identity out of agent-controlled collaboration arguments."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bindings: dict[int, tuple[object, SessionContext]] = {}
        self._sessions: dict[str, tuple[str, int]] = {}
        self._workspaces: dict[int, Path] = {}

    def bind(
        self, actor: AgentIdentity, session: SessionHandle
    ) -> SessionTransport:
        identity = object()
        transport = SessionTransport(identity)
        context = SessionContext(
            campaign_run_id=actor.campaign_run_id,
            actor_id=actor.actor_id,
            session_id=session.value,
        )
        with self._lock:
            existing = self._sessions.get(session.value)
            if existing is not None:
                raise ValueError(
                    "session handle is already bound to an actor transport"
                )
            self._bindings[id(identity)] = (identity, context)
            self._sessions[session.value] = (context.actor_id, id(identity))
        return transport

    def resolve(self, transport: SessionTransport) -> SessionContext:
        with self._lock:
            binding = self._bindings.get(id(transport._identity))
        if binding is None or binding[0] is not transport._identity:
            raise PermissionError("unknown or expired session transport")
        return binding[1]

    def assign_workspace(
        self, transport: SessionTransport, workspace_root: Path
    ) -> None:
        """Bind one server-selected workspace root to an active session."""

        context = self.resolve(transport)
        root = workspace_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("session workspace root must be a directory")
        identity_key = id(transport._identity)
        with self._lock:
            binding = self._bindings.get(identity_key)
            if binding is None or binding[0] is not transport._identity:
                raise PermissionError("unknown or expired session transport")
            existing = self._workspaces.get(identity_key)
            if existing is not None and existing != root:
                raise ValueError(
                    f"session {context.session_id} workspace is already assigned"
                )
            self._workspaces[identity_key] = root

    def workspace(self, transport: SessionTransport) -> Path:
        """Resolve the server-selected workspace for an active session."""

        self.resolve(transport)
        with self._lock:
            root = self._workspaces.get(id(transport._identity))
        if root is None:
            raise PermissionError("session has no assigned workspace")
        return root

    def revoke(self, transport: SessionTransport) -> None:
        with self._lock:
            binding = self._bindings.get(id(transport._identity))
            if binding is None or binding[0] is not transport._identity:
                raise PermissionError("unknown or expired session transport")
            context = binding[1]
            session_binding = self._sessions.get(context.session_id)
            if session_binding != (context.actor_id, id(transport._identity)):
                raise RuntimeError("session identity index is inconsistent")
            del self._bindings[id(transport._identity)]
            del self._sessions[context.session_id]
            self._workspaces.pop(id(transport._identity), None)
