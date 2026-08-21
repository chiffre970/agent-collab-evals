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
            self._bindings[id(identity)] = (identity, context)
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
            del self._bindings[id(transport._identity)]
