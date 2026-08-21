"""Server-side binding from local transports to campaign session identity."""

from __future__ import annotations

import threading

from .collaboration import SessionContext, SessionTransport
from .domain import AgentIdentity, SessionHandle


class SessionIdentityRegistry:
    """Keeps identity out of agent-controlled collaboration arguments."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bindings: dict[int, tuple[object, SessionContext]] = {}
        self._sessions: dict[str, tuple[str, int]] = {}

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
