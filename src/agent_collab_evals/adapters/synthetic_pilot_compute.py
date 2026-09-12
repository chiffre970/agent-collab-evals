"""No-network compute transport: generated evidence is always synthetic."""

from pathlib import Path

from ..canonical import canonical_json_bytes, digest_bytes, digest_value, parse_json
from ..compute_backend import ComputeEvidencePointer, ComputeExecutionStatus, ExternalDispatch, TransportPoll
from ..pilot_evidence import retain_document
from ..campaigns.serving_quality import QUALITY_RUN_SCHEMA


class SyntheticPilotEvidence:
    profile_digest = digest_value({"adapter": "synthetic-pilot-evidence/v1"})

    def __init__(self, root: Path):
        self.root = root

    def resolve(self, pointer):
        path = (self.root / pointer.locator).resolve()
        path.relative_to(self.root.resolve())
        content = path.read_bytes()
        if digest_bytes(content) != pointer.digest:
            raise RuntimeError("synthetic evidence digest differs")
        return content

    def resolve_dispatch(self, request, external_call_id):
        document = self.root / f"dispatch-{request.request_digest[7:]}.json"
        content = document.read_bytes()
        if parse_json(content.decode()) != {
            "request_digest": request.request_digest, "candidate_digest": request.candidate_digest,
            "external_call_id": external_call_id, "synthetic": True,
        }:
            raise RuntimeError("synthetic dispatch binding differs")
        return content


class SyntheticPilotTransport:
    """Exercise durable authorization and execution, without a provider client."""

    def __init__(self, root, spend, recipe, policy):
        self.evidence = SyntheticPilotEvidence(root)
        self.spend, self.recipe, self.policy = spend, recipe, policy
        self.profile_digest = self.profile_digest_for(recipe)

    @staticmethod
    def profile_digest_for(recipe):
        return digest_value({"adapter": "synthetic-pilot-transport/v1", "recipe": recipe})

    def dispatch(self, request, candidate):
        self.spend.consume(request, self.profile_digest)
        call_id = "synthetic-" + request.request_digest[7:39]
        digest = retain_document(self.evidence.root / f"dispatch-{request.request_digest[7:]}.json", {
            "request_digest": request.request_digest, "candidate_digest": digest_bytes(candidate),
            "external_call_id": call_id, "synthetic": True,
        })
        return ExternalDispatch(call_id, digest)

    def poll(self, request, external_call_id, timeout_seconds):
        self.evidence.resolve_dispatch(request, external_call_id)
        document = {
            "schema_version": "compute-execution-evidence/v0alpha1",
            "request_digest": request.request_digest, "candidate_digest": request.candidate_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "evaluator_profile_digest": request.evaluator_profile_digest,
            "transport_profile_digest": self.profile_digest,
            "evidence_profile_digest": self.evidence.profile_digest,
            "external_call_id": external_call_id, "status": "complete",
            "used_seconds": 7, "failure": None, "result": self._result(request),
        }
        locator = f"result-{request.request_digest[7:]}.json"
        digest = retain_document(self.evidence.root / locator, document)
        return TransportPoll(ComputeExecutionStatus.COMPLETE, ComputeEvidencePointer(locator, digest), 7, None)

    def _result(self, request):
        recipe = self.recipe
        phase = recipe["phase"]
        if phase == "public":
            return {"valid": True, "performance_score": {"eligible": True, "failures": [],
                "scalar_ppm": 1000000 if request.campaign_run_id == "registered-reference" else recipe["candidate_public_ppm"]},
                "candidate_id": "synthetic", "modal_function_call_id": None}
        identity = {
            "campaign_manifest_digest": recipe["campaign_manifest_digest"],
            "hidden_workload_manifest_digest": recipe["hidden_workload_manifest_digest"],
            "candidate_digest": request.candidate_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
        }
        if phase == "quality":
            role = request.execution_key.rsplit(":", 1)[1]
            repetition = int(request.execution_key.rsplit(":", 2)[1])
            return {"quality_evaluation": {
                **identity, "schema_version": "serving-quality-compute-evidence/v0alpha1",
                "quality_profile_digest": self.policy.quality_profile_digest,
                "quality_workload_digest": self.policy.quality_workload_digest,
                "role": role, "repetition": repetition, "run": self._quality_run(role, repetition),
            }}
        record = {**identity, "schema_version": "serving-candidate-compute-evidence/v0alpha1",
            "phase": phase, "workload_digest": recipe["workload_digest"], "eligible": True,
            "criterion_units": 1 if phase == "correctness" else 1001000,
            "failures": [], "diagnostics": {"synthetic": True}}
        record["result_evidence_digest"] = digest_value(record)
        return {"candidate_evaluation": record}

    def _quality_run(self, role, repetition):
        policy = self.policy
        count = policy.case_count // len(policy.families)
        cases = [{"case_id": f"{family}-{index:02d}", "family_id": family,
                  "passed": True, "extracted": "synthetic-answer",
                  "content_digest": digest_value({"synthetic": True, "role": role,
                      "repetition": repetition, "family": family, "case": index})}
                 for family in policy.families for index in range(count)]
        return {"schema_version": QUALITY_RUN_SCHEMA, "profile_digest": policy.quality_profile_digest,
            "workload_digest": policy.quality_workload_digest, "role": role, "repetition": repetition,
            "case_count": policy.case_count, "pass_count": policy.case_count, "score_ppm": 1000000,
            "family_scores": {family: {"case_count": count, "pass_count": count, "score_ppm": 1000000}
                              for family in policy.families}, "cases": cases}
