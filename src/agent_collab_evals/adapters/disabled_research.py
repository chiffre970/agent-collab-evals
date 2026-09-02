"""Registered deny-all research broker for studies without research access."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from ..registered_profiles import RegisteredProfile, load_research_profile


class DisabledResearchBroker:
    """Expose a stable disabled capability and reject every research request."""

    def __init__(self, profile: RegisteredProfile) -> None:
        if profile.schema_version != "registered-research-profile/v1":
            raise ValueError("disabled research profile schema differs")
        self._profile = profile

    @classmethod
    def from_profile(cls, path: Path) -> "DisabledResearchBroker":
        return cls(load_research_profile(path))

    @property
    def profile_digest(self) -> str:
        return self._profile.authority_digest

    def capabilities(self) -> Mapping[str, object]:
        return {
            "enabled": False,
            "network_access": False,
            "search": False,
            "fetch": False,
        }

    def search(self, session_transport: object, query: str) -> None:
        raise PermissionError("research is disabled by the registered profile")

    def fetch(self, session_transport: object, resource_id: str) -> None:
        raise PermissionError("research is disabled by the registered profile")
