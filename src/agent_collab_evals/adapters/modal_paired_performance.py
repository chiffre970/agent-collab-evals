"""Durable compute adapter for one exploratory same-GPU reference/candidate pair."""

import math
import subprocess
import sys
from pathlib import Path

from ..canonical import canonical_json_bytes, digest_bytes, digest_file, digest_value, parse_json
from ..compute_backend import ComputeExecutionStatus, ComputeEvidencePointer, ExternalDispatch, TransportPoll
from ..campaigns.model_serving import ModelServingCampaign, load_benchmark_plan
from ..campaigns.serving_paired_performance import evaluate_pair
from ..campaigns.serving_scoring import ScoringProfile
from .local_measurements import LocalMeasurementBundleStore
from .modal_vllm_compute import _minimal_modal_environment


def profiles(binding):
    transport = digest_value({"adapter": "modal-paired-performance-transport/v1", "binding": binding})
    evidence = digest_value({"adapter": "modal-paired-performance-evidence/v1", "binding": binding})
    return transport, evidence


class ModalPairedPerformanceEvidence:
    def __init__(self, root, binding):
        self.root, self.binding = Path(root), binding
        self.transport_digest, self.profile_digest = profiles(binding)
        self.campaign = ModelServingCampaign.load(Path(binding["campaign_manifest"]["file"]))
        self.plan = load_benchmark_plan(Path(binding["performance_profile"]["file"]))
        self.scoring = ScoringProfile.load(Path(binding["scoring_profile"]["file"]))

    def _job(self, request):
        self.check_inputs()
        job = next((j for j in self.binding["jobs"] if j["request"] == request.document), None)
        if job is None:
            raise PermissionError("paired request is outside frozen authority")
        return job

    def check_inputs(self):
        for key in ("campaign_manifest", "performance_profile", "scoring_profile", "modal_script", "bridge_script",
                    "candidate", "reference", "retained_inputs"):
            ref = self.binding[key]
            if digest_file(Path(ref["file"])) != ref["digest"]:
                raise RuntimeError("paired input changed: " + key)

    def resolve_dispatch(self, request, external_call_id):
        self._job(request)
        path = self.root / "dispatch" / (request.request_digest[7:] + ".json")
        doc = parse_json(path.read_text())
        if doc != {"schema_version": "modal-paired-dispatch/v1", "request_digest": request.request_digest,
            "function_call_id": external_call_id, "binding_digest": digest_value(self.binding),
            "evidence_root": self._job(request)["spec"]["benchmark"]["evidence_root"],
            "git_commit": self.binding["git_commit"]}:
            raise RuntimeError("paired dispatch identity differs")
        return canonical_json_bytes(doc)

    def _build(self, request, call):
        job = self._job(request)
        self.resolve_dispatch(request, call)
        bundle = LocalMeasurementBundleStore(self.root / "measurements").load(request.request_digest[7:], 1)
        normalized = bundle.receipt["normalized"]
        remote = normalized["remote_receipt"]
        durable = normalized["durable_evidence"]
        if (normalized.get("request_digest") != request.request_digest or normalized.get("function_call_id") != call
            or durable.get("volume_name") != "agent-collab-evals-evaluator-evidence-v2"
            or durable.get("root") != job["spec"]["benchmark"]["evidence_root"]
            or durable.get("remote_receipt_digest") != digest_bytes(canonical_json_bytes(remote) + b"\n")
            or durable.get("raw_digests") != {k: digest_bytes(v) for k, v in bundle.raw_documents.items()}):
            raise RuntimeError("paired durable evidence seal differs")
        milliseconds = remote.get("timing", {}).get("function_body_ms")
        if type(milliseconds) is not int or milliseconds < 0:
            raise RuntimeError("paired function timing is missing")
        reference = parse_json(Path(self.binding["reference"]["file"]).read_text())
        candidate = parse_json(Path(self.binding["candidate"]["file"]).read_text())
        # Terminal, sealed environment/execution rejection is a failed receipt,
        # not an accepted diagnostic score. A malformed score stays a hard error.
        if remote.get("ok") is not True:
            if remote.get("pair_digest") != job["spec"]["pair_digest"]:
                raise RuntimeError("rejected paired evidence identity differs")
            result, status, failure = {}, ComputeExecutionStatus.FAILED, str(remote.get("errors"))
        else:
            result = {"paired_performance": evaluate_pair(self.campaign, self.plan, self.scoring,
                job["spec"], reference, candidate, remote, bundle.raw_documents)}
            status, failure = ComputeExecutionStatus.COMPLETE, None
        used = math.ceil(milliseconds / 1000)
        document = {"schema_version": "compute-execution-evidence/v0alpha1",
            "request_digest": request.request_digest, "candidate_digest": request.candidate_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "evaluator_profile_digest": request.evaluator_profile_digest,
            "transport_profile_digest": self.transport_digest, "evidence_profile_digest": self.profile_digest,
            "external_call_id": call, "status": status.value, "used_seconds": used, "failure": failure,
            "result": result}
        return canonical_json_bytes(document), status, used, failure

    def pointer(self, request, external_call_id):
        content, status, used, failure = self._build(request, external_call_id)
        locator = request.request_digest[7:] + ":" + external_call_id
        return ComputeEvidencePointer(locator, digest_bytes(content)), status, used, failure

    def resolve(self, pointer):
        digest, call = pointer.locator.split(":", 1)
        from ..compute_backend import ComputeExecutionRequest
        job = next(j for j in self.binding["jobs"] if digest_value(j["request"])[7:] == digest)
        content, *_ = self._build(ComputeExecutionRequest.from_document(job["request"]), call)
        if digest_bytes(content) != pointer.digest:
            raise RuntimeError("paired result digest differs")
        return content


class ModalPairedPerformanceTransport:
    def __init__(self, root, binding, manifest_file, spend):
        self.root, self.binding, self.manifest_file, self.spend = Path(root), binding, manifest_file, spend
        self.profile_digest, _ = profiles(binding)
        self.resolver = ModalPairedPerformanceEvidence(root, binding)

    def _command(self, mode, request):
        return (sys.executable, self.binding["bridge_script"]["file"], "--mode", mode,
            "--manifest", str(self.manifest_file), "--manifest-digest", digest_file(self.manifest_file),
            "--request-digest", request.request_digest)

    def dispatch(self, request, candidate):
        self.resolver._job(request)
        if digest_bytes(candidate) != request.candidate_digest:
            raise RuntimeError("paired candidate bytes differ")
        self.spend.consume(request, self.profile_digest)
        completed = subprocess.run(self._command("dispatch", request), cwd=self.binding["repository"],
            env=_minimal_modal_environment("dev"), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=900, check=False)
        path = self.root / "dispatch" / (request.request_digest[7:] + ".json")
        if not path.exists():
            raise RuntimeError("paired dispatch is uncertain: " + completed.stdout[-2000:])
        call = parse_json(path.read_text())["function_call_id"]
        return ExternalDispatch(call, digest_bytes(self.resolver.resolve_dispatch(request, call)))

    def poll(self, request, external_call_id, timeout_seconds):
        self.resolver.resolve_dispatch(request, external_call_id)
        try:
            LocalMeasurementBundleStore(self.root / "measurements").load(request.request_digest[7:], 1)
        except KeyError:
            try:
                completed = subprocess.run(self._command("collect", request), cwd=self.binding["repository"],
                    env=_minimal_modal_environment("dev"), stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=600, check=False)
            except subprocess.TimeoutExpired:
                return TransportPoll(ComputeExecutionStatus.DISPATCHED)
            try:
                LocalMeasurementBundleStore(self.root / "measurements").load(request.request_digest[7:], 1)
            except KeyError:
                if completed.returncode:
                    raise RuntimeError("paired collection failed: " + completed.stdout[-2000:])
                return TransportPoll(ComputeExecutionStatus.DISPATCHED)
        pointer, status, used, failure = self.resolver.pointer(request, external_call_id)
        return TransportPoll(status, pointer, used, failure)
