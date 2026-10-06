"""Host-controlled, owner-private public evaluation for candidate pilots."""

from .canonical import digest_value
from .domain import AgentIdentity, Job
from .evaluation import EvaluationInProgress, EvaluationReservationStatus, VisibleEvaluationInput
from .ports import ComputeBroker, SubmissionRegistry


class CandidateEvaluationHandoff:
    """Reuse durable admission and evaluator idempotency, without a worker queue.

    The caller pauses agent delivery, prepares exact compute authority, and
    invokes evaluation outside HTTP/MCP. This object cannot grant spend. Use
    one host controller, one candidate per actor, and host-evaluation tools.
    All actor slots complete before any results are released. Agents receive
    no other actor's receipt or result through the common feedback job.
    """

    def __init__(self, submissions: SubmissionRegistry, compute: ComputeBroker,
                 campaign_run_id: str, actor_ids: tuple[str, ...],
                 job_id: str = "optimize-serving") -> None:
        if not actor_ids or len(set(actor_ids)) != len(actor_ids):
            raise ValueError("evaluation handoff requires a unique actor roster")
        self._submissions = submissions
        self._compute = compute
        self._run_id = campaign_run_id
        self._job_id = job_id
        self._actor_ids = actor_ids

    def prepare(self) -> tuple[VisibleEvaluationInput, ...]:
        inputs = self._submissions.prepare_visible_evaluations(self._run_id, self._job_id)
        by_actor = {item.reservation.actor_id: item for item in inputs}
        if len(inputs) != len(self._actor_ids) or set(by_actor) != set(self._actor_ids):
            raise RuntimeError("handoff requires exactly one admitted candidate per actor")
        return tuple(by_actor[actor] for actor in self._actor_ids)

    def evaluate(self) -> Job:
        """Collect authorized work and return stable feedback after completion.

        Retry after restart with the same durable services and compute manifest.
        Nonterminal evaluation never releases feedback. Submit the returned job
        through CampaignController's delivery outbox, not directly to a runtime.
        """
        prepared = CandidateEvaluationHandoff.prepare(self)
        for item in prepared:
            self._submissions.evaluate_visible(item.receipt)
        current = CandidateEvaluationHandoff.prepare(self)
        if any(item.reservation.status is EvaluationReservationStatus.RESERVED for item in current):
            raise EvaluationInProgress("public evaluation is still pending")
        if any(item.reservation.status is not EvaluationReservationStatus.COMPLETE for item in current):
            raise RuntimeError("public evaluation did not complete successfully")
        # Close validates terminal evaluator receipts and compute bindings before
        # the host releases the public result. Selection and hidden work are later.
        submissions = self._submissions.close(self._run_id, self._job_id)
        candidates = {item.receipt: item for item in submissions.candidates}
        if set(candidates) != {item.receipt for item in prepared}:
            raise RuntimeError("candidate set changed during evaluation")
        if any(item.visible_result is None for item in candidates.values()):
            raise RuntimeError("public feedback lacks a verified evaluator result")
        for actor in self._actor_ids:
            self._compute.release_visible_results(self._run_id, actor)
        materials = ({"candidate_receipt": prepared[0].receipt.value}
                     if len(prepared) == 1 else {})
        mission = "Read your own released public result with candidate_result(receipt), using the receipt you retained, then summarize it. Submissions are closed; hidden scoring has not run."
        digest = digest_value({
            "profile": "candidate-public-feedback/v2", "campaign_run_id": self._run_id,
            "job_id": self._job_id, "mission": mission, "materials": materials,
        })
        return Job("read-candidate-result", mission, digest, materials)


class SoloEvaluationHandoff(CandidateEvaluationHandoff):
    """Compatibility interface for the existing one-actor operator path."""

    def __init__(self, submissions, compute, campaign_run_id, job_id="optimize-serving"):
        super().__init__(submissions, compute, campaign_run_id,
                         (AgentIdentity(campaign_run_id, 0).actor_id,), job_id)

    def prepare(self) -> VisibleEvaluationInput:
        return super().prepare()[0]
