"""Development macOS adapter that blocks nonloopback outbound network access."""

from __future__ import annotations

import ipaddress
import platform
from pathlib import Path
from urllib.parse import urlsplit

from ..sandbox import SandboxProfile


class DarwinSandboxExec:
    """Wrap a process in a network-only, loopback-wide Seatbelt policy.

    This adapter does not restrict which loopback service or port is reachable.
    It also does not enforce filesystem or process-resource boundaries.
    """

    _POLICY = " ".join(
        (
            "(version 1)",
            "(allow default)",
            '(deny network-outbound (require-not (remote ip "localhost:*")))',
        )
    )

    def __init__(self, profile: SandboxProfile) -> None:
        self._profile = profile

    @property
    def profile_id(self) -> str:
        return self._profile.profile_id

    @property
    def profile_digest(self) -> str:
        return self._profile.resolved_digest

    def validate_model_endpoint(self, endpoint: str) -> None:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("sandbox model endpoint must be an absolute HTTP(S) URL")
        hostname = parsed.hostname
        try:
            loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            loopback = hostname == "localhost"
        if not loopback or hostname not in self._profile.allowed_model_endpoint_hosts:
            raise PermissionError(
                "development network sandbox accepts only a loopback model endpoint"
            )

    def wrap(self, command: tuple[str, ...]) -> tuple[str, ...]:
        if not command:
            raise ValueError("sandbox command must be nonempty")
        if platform.system() != "Darwin":
            raise RuntimeError("darwin-sandbox-exec is unavailable on this platform")
        executable = Path(self._profile.executable)
        if executable != Path("/usr/bin/sandbox-exec") or not executable.is_file():
            raise RuntimeError("pinned sandbox-exec executable is unavailable")
        return (str(executable), "-p", self._POLICY, *command)

    def evidence(self) -> dict[str, object]:
        return {
            "sandbox_profile_id": self.profile_id,
            "sandbox_profile_digest": self.profile_digest,
            "driver": self._profile.driver,
            "network_mode": self._profile.network_mode,
            "loopback_destinations": self._profile.loopback_destinations,
            "filesystem_enforcement": self._profile.filesystem_enforcement,
            "process_resource_enforcement": (
                self._profile.process_resource_enforcement
            ),
            "credential_environment_allowlist": list(
                self._profile.credential_environment_allowlist
            ),
        }
