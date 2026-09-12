"""Close the solo pilot against selected evaluation and frozen compute receipts."""

from dataclasses import dataclass

from .compute_backend import ComputeExecutionReceipt, ComputeExecutionStatus, FrozenComputeRunManifest
from .evaluation import ComputePlan, EvaluationReservationStatus, EvaluationScope
from .domain import AgentIdentity
from .ports import ComputeBackend, ComputeBroker, SubmissionRegistry


@dataclass(frozen=True, slots=True)
class EvaluationComputeSource:
    manifest: FrozenComputeRunManifest
    backend: ComputeBackend


class SoloEvaluationClosure:
    """Mandatory compute gate, composed with the controller's model-budget gate.

    Sources must include the independently bounded public reference and every
    public/hidden execution. The deployment's frozen run inventory must supply
    that complete list; this development helper cannot discover omitted stores.
    It resolves evidence only and never dispatches or authorizes compute.
    """

    def __init__(self, submissions: SubmissionRegistry, compute: ComputeBroker,
                 plan: ComputePlan, sources: tuple[EvaluationComputeSource, ...],
                 *, job_id: str = "optimize-serving") -> None:
        if not sources:
            raise ValueError("evaluation closure requires frozen compute sources")
        if tuple(plan.actor_limits) != (AgentIdentity(plan.campaign_run_id, 0).actor_id,):
            raise ValueError("solo evaluation closure requires exactly actor zero")
        self._submissions = submissions
        self._compute = compute
        self._plan = plan
        self._sources = tuple(sources)
        self._job_id = job_id

    def reconcile(self, campaign_run_id: str) -> tuple[ComputeExecutionReceipt, ...]:
        if campaign_run_id != self._plan.campaign_run_id:
            raise ValueError("evaluation closure campaign differs")
        submissions = self._submissions.close(campaign_run_id, self._job_id)
        selection = self._submissions.select(submissions)
        if len(submissions.candidates) != 1:
            raise RuntimeError("solo evaluation closure requires one admitted candidate")
        # Ineligible is a measured outcome. Missing/failed evidence is not.
        self._submissions.resolve_hidden(selection.receipt)
        snapshot = self._compute.snapshot(campaign_run_id)
        if snapshot.organisation_limit_seconds != self._plan.organisation_limit_seconds:
            raise RuntimeError("compute snapshot limit differs from plan")
        if any(item.status is not EvaluationReservationStatus.COMPLETE for item in snapshot.reservations):
            raise RuntimeError("evaluation reservations are not all complete")
        if set(snapshot.actor_used_seconds) != set(self._plan.actor_limits):
            raise RuntimeError("evaluation compute actor roster differs")
        if any(snapshot.actor_used_seconds[actor] > limit for actor, limit in self._plan.actor_limits.items()):
            raise RuntimeError("actor compute exceeds its fixed allowance")
        if snapshot.hidden_used_seconds > self._plan.hidden_evaluator_limit_seconds:
            raise RuntimeError("hidden compute exceeds its fixed allowance")

        receipts = []
        seen = set()
        public_seconds = hidden_seconds = reference_count = 0
        for source in self._sources:
            manifest = source.manifest
            if not manifest.compute_enabled or manifest.backend_profile_digest != source.backend.profile_digest:
                raise RuntimeError("compute source differs from its frozen manifest")
            requests = {item.request_digest: item for item in manifest.requests()}
            resolved = source.backend.reconcile(manifest.campaign_run_id)
            if not requests or len(resolved) != len(requests) or {item.request_digest for item in resolved} != set(requests):
                raise RuntimeError("compute receipts differ from frozen requests")
            for receipt in resolved:
                request = requests[receipt.request_digest]
                if receipt.request_digest in seen:
                    raise RuntimeError("duplicate compute execution in closure")
                seen.add(receipt.request_digest)
                if receipt.status is not ComputeExecutionStatus.COMPLETE or receipt.used_seconds is None:
                    raise RuntimeError("evaluation execution did not complete")
                if receipt.used_seconds > request.maximum_seconds:
                    raise RuntimeError("evaluation execution exceeded its reservation")
                if request.campaign_run_id == campaign_run_id:
                    if request.scope is EvaluationScope.VISIBLE:
                        public_seconds += receipt.used_seconds
                    else:
                        hidden_seconds += receipt.used_seconds
                elif request.campaign_run_id == "registered-reference" and request.scope is EvaluationScope.VISIBLE:
                    reference_count += 1
                else:
                    raise RuntimeError("compute source is outside the solo evaluation scope")
                receipts.append(receipt)
        if reference_count != 1:
            raise RuntimeError("closure requires one public reference execution")
        if public_seconds != sum(snapshot.actor_used_seconds.values()) or hidden_seconds != snapshot.hidden_used_seconds:
            raise RuntimeError("execution usage differs from evaluation accounting")
        return tuple(receipts)
