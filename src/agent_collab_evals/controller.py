"""Durable campaign lifecycle orchestration, independent of runtime adapters."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
import threading

from .budget import BudgetReconciliation
from .canonical import digest_value
from .domain import (
    AgentIdentity,
    CampaignResult,
    CampaignSnapshot,
    CampaignStatus,
    HarnessOrganisation,
    HarnessSnapshot,
    Job,
    OrganisationSpec,
    SessionHandle,
    top_level_actor_count,
)
from .ports import (
    BudgetReconciliationGate,
    ComputeReconciliationGate,
    DeliveryOutbox,
    EventSink,
    HarnessRuntime,
)


@dataclass(slots=True)
class CampaignHandle:
    spec: OrganisationSpec
    organisation: HarnessOrganisation
    actors: tuple[AgentIdentity, ...]
    sessions: tuple[SessionHandle, ...]
    delivered_job_ids: list[str] = field(default_factory=list)
    status: CampaignStatus = CampaignStatus.ACTIVE


class CampaignCloseRejected(RuntimeError):
    """The stopped campaign failed a mandatory reconciliation gate."""

    def __init__(
        self,
        message: str,
        final_harness_snapshot: HarnessSnapshot,
        reconciliation: BudgetReconciliation | None,
    ) -> None:
        super().__init__(message)
        self.final_harness_snapshot = final_harness_snapshot
        self.reconciliation = reconciliation


class CampaignController:
    """Owns lifecycle only; it does not plan, delegate, merge, or score work."""

    def __init__(
        self,
        harness: HarnessRuntime,
        events: EventSink,
        budget_reconciliation: BudgetReconciliationGate | None = None,
        compute_reconciliation: ComputeReconciliationGate | None = None,
        delivery_outbox: DeliveryOutbox | None = None,
    ) -> None:
        self._harness = harness
        self._events = events
        self._budget_reconciliation = budget_reconciliation
        self._compute_reconciliation = compute_reconciliation
        self._delivery_outbox = delivery_outbox
        # V0 registers one campaign dispatcher. Serialize calls within that
        # dispatcher so two callers cannot both cross the runtime boundary
        # before either has durably acknowledged the same intent.
        self._delivery_lock = threading.RLock()

    def start(self, spec: OrganisationSpec) -> CampaignHandle:
        actor_count = top_level_actor_count(spec.condition, spec.organisation_size)
        organisation = self._harness.start_organisation(spec)
        if not organisation.value:
            raise RuntimeError("harness returned an empty organisation identifier")
        actors = tuple(
            AgentIdentity(spec.campaign_run_id, ordinal)
            for ordinal in range(actor_count)
        )
        try:
            sessions = tuple(
                self._harness.create_primary(organisation, actor) for actor in actors
            )
            if any(not session.value for session in sessions) or len(
                {session.value for session in sessions}
            ) != len(sessions):
                raise RuntimeError("harness returned invalid primary session identifiers")
            handle = CampaignHandle(spec, organisation, actors, sessions)
            self._events.append(
                spec.campaign_run_id,
                "campaign.started",
                {
                    "condition": spec.condition.value,
                    "organisation_size": spec.organisation_size,
                    "top_level_actor_count": actor_count,
                },
            )
            return handle
        except BaseException as error:
            try:
                self._harness.stop(organisation, "campaign startup failed")
            except BaseException as cleanup_error:
                raise BaseExceptionGroup(
                    "Campaign startup and cleanup failed", [error, cleanup_error]
                ) from None
            raise

    def deliver(self, handle: CampaignHandle, job: Job) -> None:
        with self._delivery_lock:
            self._deliver(handle, job)

    def _deliver(self, handle: CampaignHandle, job: Job) -> None:
        self._require_active(handle)
        outbox = self._require_delivery_outbox()
        intents = outbox.prepare(
            handle.spec.campaign_run_id, handle.sessions, job
        )

        def deliver_one(index: int) -> None:
            intent = intents[index]
            expected_runtime_profile = digest_value(self._harness.capabilities())
            existing = outbox.acknowledged(intent)
            if existing is not None:
                if existing.runtime_profile_digest != expected_runtime_profile:
                    raise RuntimeError("stored delivery receipt profile differs")
                return
            receipt = self._harness.deliver(handle.sessions[index], job)
            if receipt.runtime_profile_digest != expected_runtime_profile:
                raise RuntimeError("harness delivery receipt profile differs")
            outbox.acknowledge(intent, receipt)

        if len(handle.sessions) == 1:
            deliver_one(0)
        else:
            with ThreadPoolExecutor(max_workers=len(handle.sessions)) as executor:
                deliveries = [
                    executor.submit(deliver_one, index)
                    for index in range(len(handle.sessions))
                ]
                # Join in stable actor order so simultaneous failures surface
                # deterministically. Successful peers remain idempotent on retry.
                for delivery in deliveries:
                    delivery.result()
        receipts = outbox.complete(handle.spec.campaign_run_id, job.job_id)
        if job.job_id not in handle.delivered_job_ids:
            handle.delivered_job_ids.append(job.job_id)
        self._events.append(
            handle.spec.campaign_run_id,
            "job.delivered",
            {
                "job_id": job.job_id,
                "materials_digest": job.materials_digest,
                "recipient_count": len(handle.sessions),
                "delivery_receipt_ids": [
                    receipt.receipt_id for receipt in receipts
                ],
            },
        )

    def snapshot(self, handle: CampaignHandle) -> CampaignSnapshot:
        self._require_active(handle)
        snapshot = CampaignSnapshot(
            campaign_run_id=handle.spec.campaign_run_id,
            condition=handle.spec.condition,
            organisation_size=handle.spec.organisation_size,
            workspace_root=str(handle.spec.workspace_root),
            model_endpoint=handle.spec.model_endpoint,
            status=handle.status,
            organisation=handle.organisation,
            actors=handle.actors,
            sessions=handle.sessions,
            delivered_job_ids=tuple(handle.delivered_job_ids),
            harness=self._harness.snapshot(handle.organisation),
        )
        self._events.append(
            handle.spec.campaign_run_id,
            "campaign.snapshotted",
            {"delivered_job_count": len(handle.delivered_job_ids)},
        )
        return snapshot

    def resume(self, snapshot: CampaignSnapshot) -> CampaignHandle:
        if snapshot.status is not CampaignStatus.ACTIVE:
            raise ValueError("only active campaigns can be resumed")
        spec = OrganisationSpec(
            campaign_run_id=snapshot.campaign_run_id,
            condition=snapshot.condition,
            organisation_size=snapshot.organisation_size,
            workspace_root=Path(snapshot.workspace_root),
            model_endpoint=snapshot.model_endpoint,
        )
        organisation = self._harness.resume(snapshot.harness)
        if organisation != snapshot.organisation:
            raise RuntimeError("harness resumed a different organisation")
        completed_job_ids = self._require_delivery_outbox().completed_job_ids(
            snapshot.campaign_run_id
        )
        if not set(snapshot.delivered_job_ids).issubset(completed_job_ids):
            raise RuntimeError("campaign snapshot delivery state differs from outbox")
        handle = CampaignHandle(
            spec=spec,
            organisation=organisation,
            actors=snapshot.actors,
            sessions=snapshot.sessions,
            delivered_job_ids=list(completed_job_ids),
        )
        self._events.append(
            spec.campaign_run_id,
            "campaign.resumed",
            {"delivered_job_count": len(handle.delivered_job_ids)},
        )
        return handle

    def close(self, handle: CampaignHandle, reason: str) -> CampaignResult:
        self._require_active(handle)
        delivery_reconciliation = self._require_delivery_outbox().reconcile(
            handle.spec.campaign_run_id,
            handle.sessions,
            tuple(handle.delivered_job_ids),
        )
        expected_runtime_profile = digest_value(self._harness.capabilities())
        if any(
            receipt.runtime_profile_digest != expected_runtime_profile
            for receipt in delivery_reconciliation.receipts
        ):
            raise RuntimeError("delivery receipt runtime profile differs at close")
        if self._budget_reconciliation is None:
            raise RuntimeError(
                "campaign close requires a configured budget reconciliation gate"
            )
        if self._compute_reconciliation is None:
            raise RuntimeError(
                "campaign close requires a configured compute reconciliation gate"
            )
        self._events.append(
            handle.spec.campaign_run_id,
            "campaign.delivery_reconciled",
            {
                "job_count": len(delivery_reconciliation.job_ids),
                "receipt_count": len(delivery_reconciliation.receipts),
                "evidence_digest": delivery_reconciliation.evidence_digest,
            },
        )
        final_snapshot = self._harness.stop(handle.organisation, reason)
        try:
            reconciliation = self._budget_reconciliation.reconcile(
                handle.spec.campaign_run_id
            )
        except Exception as error:
            handle.status = CampaignStatus.INVALID
            self._events.append(
                handle.spec.campaign_run_id,
                "campaign.invalid",
                {
                    "reason": "budget_reconciliation_failed",
                    "error_type": type(error).__name__,
                    "delivered_job_count": len(handle.delivered_job_ids),
                },
            )
            raise CampaignCloseRejected(
                "campaign budget reconciliation failed",
                final_snapshot,
                None,
            ) from error
        campaign_matches = (
            reconciliation.campaign_run_id == handle.spec.campaign_run_id
        )
        if not reconciliation.valid or not campaign_matches:
            handle.status = CampaignStatus.INVALID
            self._events.append(
                handle.spec.campaign_run_id,
                "campaign.invalid",
                {
                    "reason": (
                        "budget_reconciliation_invalid"
                        if campaign_matches
                        else "budget_reconciliation_campaign_mismatch"
                    ),
                    "delivered_job_count": len(handle.delivered_job_ids),
                    "budget_reconciliation": reconciliation.evidence(),
                },
            )
            raise CampaignCloseRejected(
                "campaign budget reconciliation found invalid terminal state",
                final_snapshot,
                reconciliation,
            )
        self._events.append(
            handle.spec.campaign_run_id,
            "campaign.budget_reconciled",
            reconciliation.evidence(),
        )
        try:
            compute_receipts = self._compute_reconciliation.reconcile(
                handle.spec.campaign_run_id
            )
        except Exception as error:
            handle.status = CampaignStatus.INVALID
            self._events.append(
                handle.spec.campaign_run_id,
                "campaign.invalid",
                {
                    "reason": "compute_reconciliation_failed",
                    "error_type": type(error).__name__,
                    "delivered_job_count": len(handle.delivered_job_ids),
                },
            )
            raise CampaignCloseRejected(
                "campaign compute reconciliation failed",
                final_snapshot,
                reconciliation,
            ) from error
        self._events.append(
            handle.spec.campaign_run_id,
            "campaign.compute_reconciled",
            {
                "execution_count": len(compute_receipts),
                "execution_ids": [receipt.execution_id for receipt in compute_receipts],
            },
        )
        handle.status = CampaignStatus.CLOSED
        self._events.append(
            handle.spec.campaign_run_id,
            "campaign.closed",
            {"reason": reason, "delivered_job_count": len(handle.delivered_job_ids)},
        )
        return CampaignResult(
            campaign_run_id=handle.spec.campaign_run_id,
            delivered_job_ids=tuple(handle.delivered_job_ids),
            final_harness_snapshot=final_snapshot,
        )

    @staticmethod
    def _require_active(handle: CampaignHandle) -> None:
        if handle.status is not CampaignStatus.ACTIVE:
            raise RuntimeError("campaign is not active")

    def _require_delivery_outbox(self) -> DeliveryOutbox:
        if self._delivery_outbox is None:
            raise RuntimeError(
                "campaign delivery requires a configured durable outbox"
            )
        return self._delivery_outbox
