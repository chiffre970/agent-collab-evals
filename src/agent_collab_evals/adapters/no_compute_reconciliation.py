"""Explicit close gate for campaigns that create no external compute jobs."""

from __future__ import annotations

from pathlib import Path

from ..compute_backend import ComputeExecutionReceipt, FrozenComputeRunManifest


class NoComputeExecutionReconciler:
    """Reconcile a frozen run configuration that explicitly disables compute."""

    def __init__(self, authority: FrozenComputeRunManifest) -> None:
        authority.assert_no_compute(authority.campaign_run_id)
        self._authority = authority

    @classmethod
    def from_frozen_manifest(
        cls, path: Path, campaign_run_id: str
    ) -> "NoComputeExecutionReconciler":
        authority = FrozenComputeRunManifest.load_or_create(
            path,
            campaign_run_id=campaign_run_id,
            compute_enabled=False,
            transport_profile_digest=None,
            backend_profile_digest=None,
            requests=(),
        )
        return cls(authority)

    def reconcile(
        self, campaign_run_id: str
    ) -> tuple[ComputeExecutionReceipt, ...]:
        if not campaign_run_id:
            raise ValueError("campaign run ID is required")
        self._authority.assert_no_compute(campaign_run_id)
        return ()
