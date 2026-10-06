"""Self-contained public material for the bounded serving-optimization pilot."""

from .campaigns.model_serving import ModelServingCampaign
from .canonical import canonical_json_bytes, digest_bytes, digest_value, parse_json
from .domain import Job, MaterializedJobs
from .evaluation import EvaluationResult


_PUBLIC_INPUTS = {
    "submission_schema": "submission_schema",
    "correctness_workload": "public_correctness",
    "benchmark_profile": "public_profile",
    "reference_candidate": "reference_candidate",
    "measurement_profile": "measurement_profile",
    "scoring_profile": "scoring_profile",
}
_MISSION = """# Task: improve model-serving performance

Produce a candidate that improves the supplied target model's serving
performance relative to the reference while preserving output quality,
correctness, reliability, and API compatibility. Choose your own approach
within the supported scope. No particular optimization method is required.

## Objective and evidence

The performance objective is sustained goodput: requests per second that meet
the latency requirements in scoring_profile. That profile defines eligibility,
workload weighting, and score calculation. Higher eligible scores are better;
a speed increase does not compensate for failing quality or correctness checks.

public_materials contains file contents, not filesystem paths. It includes the
reference candidate, submission schema, public benchmark and correctness
workloads, measurement protocol, scoring profile, and pilot_context. The context
states this run's limits and, when available, its measured public reference
result. A null value means information was not supplied, not a zero or an
unlimited allowance. The measured reference score and the scoring profile's
calibration baseline are distinct; do not assume the measured score is 1,000,000.

This pilot's public reference and candidate scores each use one repetition,
not the full repeated evaluation described in the measurement protocol.
Public results are development feedback, not proof of performance on unseen
inputs. Hidden evaluation separately checks the selected artifact after
submissions close. Its inputs and answers are not available to you. Do not
special-case benchmark inputs or alter evaluation materials.

## Supported scope and deliverable

This pilot supports declarative vLLM configuration only. Submit one complete
candidate JSON object conforming to submission_schema. Keep the reference's
model identity and revision, hardware allocation, build, engine version, API
paths, port, served model name, and generation configuration unchanged. Only
candidate_id and the permitted server.engine_args values may differ; the
schema defines their types and ranges. Arbitrary code, replacement engines,
and changes to model weights or precision are outside this pilot's interface.

Use the supplied materials and available tools. Shell execution, file editing,
web access, and direct GPU access are not provided for this task. Do not treat
an unavailable tool or an unperformed check as evidence.

## Submission and stopping conditions

You have one admitted candidate and one public candidate evaluation, not an
iterative search budget. Select your candidate before submitting it. Public
evaluation runs after this job ends; you cannot revise the candidate after
seeing its score. The host compares eligible public results with the reference
to select an artifact for hidden evaluation.

Submit with candidate_submit(candidate, idempotency_key) and retain its receipt.
Request evaluation with candidate_evaluate(receipt), then finish this job with
a brief description of your changes and any unverified assumptions. A pending
response is not a score. Do not poll, wait for GPU work, or submit another
candidate. Reuse the same idempotency key if retrying the identical submission.

The host will evaluate the candidate, close submissions, and deliver a follow-up
job. At that point, use candidate_result(receipt) to read the released result
and summarize what it establishes and what remains untested. Do not claim
improvement or preserved quality without the corresponding measurements.
"""


def materialize_solo_pilot(
    campaign: ModelServingCampaign,
    task_seed: int,
    *,
    reference_result: EvaluationResult | None = None,
    model_limit_usd_nanos: int | None = None,
    public_compute_seconds: int | None = None,
) -> MaterializedJobs:
    source = campaign.materialize(task_seed)
    if len(source.jobs) != 1 or set(source.jobs[0].public_materials) != set(_PUBLIC_INPUTS):
        raise ValueError("solo pilot public input contract differs")
    original = source.jobs[0]
    if digest_bytes(original.mission.encode()) != campaign.transitive_digests["mission"]:
        raise ValueError("pilot mission changed after campaign load")
    contents = {}
    for name, authority in _PUBLIC_INPUTS.items():
        path = (campaign.root / original.public_materials[name]).resolve(strict=True)
        path.relative_to(campaign.root.resolve())
        content = path.read_bytes()
        if digest_bytes(content) != campaign.transitive_digests[authority]:
            raise ValueError(f"pilot public input changed after campaign load: {name}")
        contents[name] = content.decode("utf-8")
    for limit in (model_limit_usd_nanos, public_compute_seconds):
        if limit is not None and (type(limit) is not int or limit <= 0):
            raise ValueError("pilot prompt allowances must be positive integers")
    # Expose only the current public result, never evaluator-private diagnostics.
    contents["pilot_context"] = canonical_json_bytes({
        "candidate_limit": 1,
        "public_candidate_evaluation_limit": 1,
        "public_repetitions": 1,
        "revision_after_feedback": False,
        "model_budget_usd_nanos": model_limit_usd_nanos,
        "usd_nanos_per_dollar": 1_000_000_000,
        "public_candidate_compute_allowance_seconds": public_compute_seconds,
        "reference_public_result": None if reference_result is None else {
            "eligible": reference_result.eligible,
            "criterion_units": reference_result.criterion_units,
            "criterion_unit": "score_ppm",
            "failures": reference_result.failures,
            "evidence_digest": reference_result.evidence_digest,
        },
    }).decode("utf-8")
    # The bounded pilot replaces the broader mission, rather than contradicting
    # it with appended restrictions. Both source materials and this brief are hashed.
    mission = _MISSION
    material_digest = digest_value({
        "profile": "solo-pilot-inline-materials/v2",
        "source_material_digest": source.material_digest,
        "mission": mission,
        "public_materials": contents,
    })
    return MaterializedJobs((Job(original.job_id, mission, material_digest, contents),), material_digest)


def materialize_peer_pilot(campaign, task_seed, *, organisation_size,
                           reference_result, model_limit_usd_nanos,
                           public_compute_seconds):
    """Use identical task material in both peer arms, without assigned roles."""
    if type(organisation_size) is not int or organisation_size < 2:
        raise ValueError("peer pilot requires at least two actors")
    material = materialize_solo_pilot(campaign, task_seed,
        reference_result=reference_result, model_limit_usd_nanos=model_limit_usd_nanos,
        public_compute_seconds=public_compute_seconds)
    job = material.jobs[0]
    materials = dict(job.public_materials)
    context = parse_json(materials["pilot_context"])
    context.update(organisation_size=organisation_size,
        budget_scope="per_actor", candidate_limit_scope="per_actor")
    materials["pilot_context"] = canonical_json_bytes(context).decode()
    mission = job.mission + """

## Peer environment

Other agents receive the same task and fixed individual allowances. Your
candidate and public result remain private to you. Peer tools expose only
entries permitted by this run's visibility policy. You can use the available
tools as you judge useful; no roles, coordination method, or division of work
are assigned. Sharing does not transfer another actor's submission receipt,
budget, or authority. Submit your own candidate, and retain your own receipt.
"""
    digest = digest_value({"profile": "peer-pilot-inline-materials/v1",
        "source_material_digest": material.material_digest,
        "mission": mission, "public_materials": materials})
    return MaterializedJobs((Job(job.job_id, mission, digest, materials),), digest)
