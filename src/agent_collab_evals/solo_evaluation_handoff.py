"""Host-controlled public evaluation for the one-candidate exploratory pilot."""

from .canonical import digest_value
from .domain import AgentIdentity, Job
from .evaluation import EvaluationInProgress, EvaluationReservationStatus, VisibleEvaluationInput
from .ports import ComputeBroker, SubmissionRegistry


class SoloEvaluationHandoff:
    """Reuse durable admission and evaluator idempotency, without a worker queue.

    The caller pauses agent delivery, prepares exact compute authority, and
    invokes evaluation outside HTTP/MCP. This object cannot grant spend. Use
    one host controller, one candidate, and host-evaluation candidate tools.
    """

    def __init__(self, submissions: SubmissionRegistry, compute: ComputeBroker,
                 campaign_run_id: str, job_id: str = "optimize-serving") -> None:
        self._submissions = submissions
        self._compute = compute
        self._run_id = campaign_run_id
        self._job_id = job_id
        self._actor = AgentIdentity(campaign_run_id, 0)

    def prepare(self) -> VisibleEvaluationInput:
        inputs = self._submissions.prepare_visible_evaluations(self._run_id, self._job_id)
        if len(inputs) != 1 or inputs[0].reservation.actor_id != self._actor.actor_id:
            raise RuntimeError("solo handoff requires exactly one admitted actor-zero candidate")
        return inputs[0]

    def evaluate(self) -> Job:
        """Collect authorized work and return stable feedback after completion.

        Retry after restart with the same durable services and compute manifest.
        Nonterminal evaluation never releases feedback. Submit the returned job
        through CampaignController's delivery outbox, not directly to a runtime.
        """
        prepared = self.prepare()
        self._submissions.evaluate_visible(prepared.receipt)
        current = self.prepare()
        if current.reservation.status is EvaluationReservationStatus.RESERVED:
            raise EvaluationInProgress("public evaluation is still pending")
        if current.reservation.status is not EvaluationReservationStatus.COMPLETE:
            raise RuntimeError("public evaluation did not complete successfully")
        # Close validates terminal evaluator receipts and compute bindings before
        # the host releases the public result. Selection and hidden work are later.
        submissions = self._submissions.close(self._run_id, self._job_id)
        if len(submissions.candidates) != 1:
            raise RuntimeError("solo candidate set changed during evaluation")
        candidate = submissions.candidates[0]
        if candidate.receipt != prepared.receipt or candidate.visible_result is None:
            raise RuntimeError("public feedback lacks a verified evaluator result")
        self._compute.release_visible_results(self._run_id, self._actor.actor_id)
        materials = {"candidate_receipt": prepared.receipt.value}
        mission = "Read your released public result with candidate_result(receipt), then summarize it. Submissions are closed; hidden scoring has not run."
        digest = digest_value({
            "profile": "solo-public-feedback/v1", "campaign_run_id": self._run_id,
            "job_id": self._job_id, "mission": mission, "materials": materials,
            "evidence_digest": candidate.visible_result.evidence_digest,
        })
        return Job("read-candidate-result", mission, digest, materials)
