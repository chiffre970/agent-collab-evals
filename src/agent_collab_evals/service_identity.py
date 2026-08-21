"""Non-exportable identities for trusted internal services."""

from __future__ import annotations

import threading

from .artifacts import TrustedServiceTransport


class ServiceIdentityRegistry:
    """Resolves only object-identity transports created by this process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bindings: dict[int, tuple[object, str]] = {}
        self._services: dict[str, int] = {}

    def bind(self, service_name: str) -> TrustedServiceTransport:
        if not service_name:
            raise ValueError("service_name is required")
        identity = object()
        transport = TrustedServiceTransport(identity)
        with self._lock:
            if service_name in self._services:
                raise ValueError("service name already has an active transport")
            self._bindings[id(identity)] = (identity, service_name)
            self._services[service_name] = id(identity)
        return transport

    def resolve(self, transport: TrustedServiceTransport) -> str:
        with self._lock:
            binding = self._bindings.get(id(transport._identity))
        if binding is None or binding[0] is not transport._identity:
            raise PermissionError("unknown or expired trusted service transport")
        return binding[1]

    def revoke(self, transport: TrustedServiceTransport) -> None:
        with self._lock:
            binding = self._bindings.get(id(transport._identity))
            if binding is None or binding[0] is not transport._identity:
                raise PermissionError("unknown or expired trusted service transport")
            service_name = binding[1]
            if self._services.get(service_name) != id(transport._identity):
                raise RuntimeError("trusted service identity index is inconsistent")
            del self._bindings[id(transport._identity)]
            del self._services[service_name]
