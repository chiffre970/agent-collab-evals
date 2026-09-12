"""Self-contained public material for the bounded serving-optimization pilot."""

from .campaigns.model_serving import ModelServingCampaign
from .canonical import digest_bytes, digest_value
from .domain import Job, MaterializedJobs


_PUBLIC_INPUTS = {
    "submission_schema": "submission_schema",
    "correctness_workload": "public_correctness",
    "benchmark_profile": "public_profile",
    "reference_candidate": "reference_candidate",
    "measurement_profile": "measurement_profile",
    "scoring_profile": "scoring_profile",
}
_GUIDE = """Exploratory serving pilot: public_materials contains file contents,
not paths. Use the reference candidate and submission schema to construct a
complete candidate JSON object. This implementation exposes declarative vLLM
settings only; arbitrary commands, replacement code, and other engines are not
implemented, even though the broader research mission discusses them.
Use candidate_submit(candidate, idempotency_key) and preserve its receipt.
Request public evaluation with candidate_evaluate(receipt), then finish this
job; do not poll or wait for GPU work. A pending response is not a score. The
host evaluates the admitted candidate after this job finishes, closes
submissions, releases the result, and sends a follow-up job. Read that result
with candidate_result(receipt).
Do not infer speed or quality gains from a candidate's name. Hidden evaluation
is separate, after selection, and is not available through these tools.
"""


def materialize_solo_pilot(campaign: ModelServingCampaign, task_seed: int) -> MaterializedJobs:
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
    mission = original.mission + "\n\n" + _GUIDE
    material_digest = digest_value({
        "profile": "solo-pilot-inline-materials/v1",
        "source_material_digest": source.material_digest,
        "mission": mission,
        "public_materials": contents,
    })
    return MaterializedJobs((Job(original.job_id, mission, material_digest, contents),), material_digest)
