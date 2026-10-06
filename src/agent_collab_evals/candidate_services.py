"""Candidate lifecycle wiring with explicitly supplied evaluator authority."""

from dataclasses import dataclass
from pathlib import Path

from .adapters.local_artifact_storage import LocalArtifactStorage
from .adapters.sqlite_compute import SqliteComputeBroker
from .adapters.sqlite_submissions import SqliteSubmissionRegistry
from .artifacts import ArtifactStoragePolicy
from .candidate_tools import CandidateTools
from .campaigns.model_serving import ModelServingCampaign
from .domain import AgentIdentity, SessionHandle
from .evaluation import ComputePlan, EvaluationReceipt, SubmissionPolicy
from .ports import CandidateEvaluator
from .service_identity import ServiceIdentityRegistry
from .session_identity import SessionIdentityRegistry


@dataclass
class CandidateServices:
    sessions: SessionIdentityRegistry
    storage: LocalArtifactStorage
    compute: SqliteComputeBroker
    evaluator: CandidateEvaluator
    submissions: SqliteSubmissionRegistry
    tools: CandidateTools
    plan: ComputePlan


def create_solo_candidate_services(
    root: Path, campaign: ModelServingCampaign, *, evaluator: CandidateEvaluator,
    reference_receipt: EvaluationReceipt, plan: ComputePlan, policy: SubmissionPolicy,
    host_evaluation: bool = True,
) -> CandidateServices:
    if tuple(plan.actor_limits) != (AgentIdentity(plan.campaign_run_id, 0).actor_id,):
        raise ValueError("solo candidate services require exactly actor zero")
    return create_candidate_services(root, campaign, evaluator=evaluator,
        reference_receipt=reference_receipt, plan=plan, policy=policy,
        host_evaluation=host_evaluation)


def create_candidate_services(
    root: Path, campaign: ModelServingCampaign, *, evaluator: CandidateEvaluator,
    reference_receipt: EvaluationReceipt, plan: ComputePlan, policy: SubmissionPolicy,
    host_evaluation: bool = True,
) -> CandidateServices:
    """Wire services without dispatching evaluation or issuing spend authority.

    The caller obtains the reference receipt through an independently bounded
    evaluation path. Initialization resolves that receipt but never computes a
    new reference. External compute authorization and close-time reconciliation
    remain the caller's responsibilities.
    Host evaluation is the default; inline evaluation is an explicit option
    for existing synthetic rehearsals, not the long-running GPU pilot path.
    """
    actors = tuple(AgentIdentity(plan.campaign_run_id, index)
                   for index in range(len(plan.actor_allocations)))
    actor_ids = tuple(actor.actor_id for actor in actors)
    if tuple(plan.actor_limits) != actor_ids:
        raise ValueError("candidate services require an ordered contiguous actor roster")
    sessions = SessionIdentityRegistry()
    identities = ServiceIdentityRegistry()
    service = identities.bind("submission_registry")
    storage = LocalArtifactStorage(
        root / "artifacts", sessions, identities,
        ArtifactStoragePolicy(32768, 131072, 131072 * len(actors)),
        {"submission_registry": frozenset({"candidate_lifecycle", "hidden_evaluation"})},
    )
    storage.open_campaign(plan.campaign_run_id, actor_ids)
    compute = SqliteComputeBroker(
        root / "compute.sqlite3", sessions, identities, plan,
        hidden_evaluator_service="submission_registry",
    )
    submissions = SqliteSubmissionRegistry(
        root / "submissions.sqlite3", sessions, storage, compute, evaluator, service,
    )
    bootstrap = sessions.bind(actors[0], SessionHandle(f"{plan.campaign_run_id}-reference-bootstrap"))
    try:
        reference = campaign.reference_candidate_path.read_bytes()
        campaign.validate_reference_candidate()
        artifact = storage.put(bootstrap, reference, "application/json", idempotency_key="reference:optimize-serving")
        submissions.initialize(
            plan.campaign_run_id, "optimize-serving", actor_ids, policy,
            artifact.ref, reference_receipt,
        )
    finally:
        sessions.revoke(bootstrap)
    tools = CandidateTools(
        sessions, storage, submissions, campaign.validate_candidate_document,
        campaign_run_id=plan.campaign_run_id, job_id="optimize-serving",
        candidate_policy_digest=campaign.manifest_digest,
        host_evaluation=host_evaluation,
    )
    return CandidateServices(sessions, storage, compute, evaluator, submissions, tools, plan)
