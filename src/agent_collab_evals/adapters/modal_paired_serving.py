"""Exact-authority Modal transport and raw resolver for paired serving jobs."""

import math
from pathlib import Path
import subprocess

from ..canonical import canonical_json_bytes, digest_bytes, digest_file, digest_value, parse_json
from ..compute_backend import ComputeEvidencePointer, ComputeExecutionRequest, ComputeExecutionStatus, ExternalDispatch, TransportPoll
from ..campaigns.serving_paired_result import score_paired_execution
from ..pilot_evidence import retain_bytes, retain_document
from .local_measurements import LocalMeasurementBundleStore
from .modal_vllm_compute import _minimal_modal_environment
from .sqlite_compute_spend import SqliteComputeSpendAuthorizationService


class ModalPairedServingEvidence:
    def __init__(self, profile, root, transport_digest):
        self.profile, self.root = profile, Path(root).resolve()
        self.transport_digest = transport_digest
        self.profile_digest = self.profile_digest_for(profile.digest)
        self.store = LocalMeasurementBundleStore(self.root / "measurements")

    @staticmethod
    def profile_digest_for(profile_digest):
        return digest_value({"adapter": "modal-paired-serving-evidence/v1", "profile": profile_digest,
            "source_digest": digest_file(Path(__file__))})

    def job_path(self, request):
        return self.root / "jobs" / (request.request_digest[7:] + ".json")

    def job(self, request):
        self.profile.check_inputs()
        job = parse_json(self.job_path(request).read_text())
        namespace = self.store.remote_namespace(request.request_digest[7:])
        expected = {"schema_version": "paired-serving-job/v1", "request": request.document,
            "profile_digest": self.profile.digest, "transport_digest": self.transport_digest,
            "namespace": namespace, "spec": self.profile.spec(request, namespace=namespace),
            "candidate_file": str(self.root / "candidates" / (request.request_digest[7:] + ".json"))}
        if job != expected or digest_file(Path(job["candidate_file"])) != request.candidate_digest:
            raise RuntimeError("paired serving frozen job differs")
        candidate = parse_json(Path(job["candidate_file"]).read_text())
        if self.profile.campaign.validate_candidate_document(candidate).manifest_digest != request.candidate_manifest_digest:
            raise RuntimeError("paired serving candidate manifest differs")
        return job

    def dispatch_path(self, request):
        return self.root / "dispatch" / (request.request_digest[7:] + ".json")

    def resolve_dispatch(self, request, external_call_id):
        job = self.job(request)
        document = parse_json(self.dispatch_path(request).read_text())
        expected = {"schema_version": "paired-serving-dispatch/v1", "request_digest": request.request_digest,
            "function_call_id": external_call_id, "job_digest": digest_value(job),
            "transport_digest": self.transport_digest, "evidence_root": job["spec"]["benchmark"]["evidence_root"]}
        if document != expected:
            raise RuntimeError("paired serving dispatch differs")
        return canonical_json_bytes(document)

    def _build(self, request, call):
        job = self.job(request)
        self.resolve_dispatch(request, call)
        bundle = self.store.load(request.request_digest[7:], 1)
        normalized = bundle.receipt["normalized"]
        remote, durable = normalized["remote_receipt"], normalized["durable_evidence"]
        if (normalized.get("request_digest") != request.request_digest or normalized.get("function_call_id") != call
            or durable.get("volume_name") != "agent-collab-evals-evaluator-evidence-v2"
            or durable.get("root") != job["spec"]["benchmark"]["evidence_root"]
            or durable.get("remote_receipt_digest") != digest_bytes(canonical_json_bytes(remote) + b"\n")
            or durable.get("raw_digests") != bundle.receipt["raw_digests"]):
            raise RuntimeError("paired serving durable evidence differs")
        milliseconds = remote.get("timing", {}).get("function_body_ms")
        if type(milliseconds) is not int or milliseconds < 0:
            raise RuntimeError("paired serving duration is missing")
        used = math.ceil(milliseconds / 1000)
        if remote.get("pair_digest") != job["spec"]["pair_digest"]:
            raise RuntimeError("paired serving receipt identity differs")
        if remote.get("ok") is not True:
            if not isinstance(remote.get("errors"), list) or not remote["errors"]:
                raise RuntimeError("paired serving rejection lacks failure evidence")
            status, result, failure = ComputeExecutionStatus.FAILED, {}, "paired_execution_rejected"
        else:
            result = {"paired_evaluation": score_paired_execution(self.profile, request, remote,
                bundle.raw_documents, parse_json(Path(job["candidate_file"]).read_text()), namespace=job["namespace"])}
            status, failure = ComputeExecutionStatus.COMPLETE, None
        document = {"schema_version": "compute-execution-evidence/v0alpha1", "request_digest": request.request_digest,
            "candidate_digest": request.candidate_digest, "candidate_manifest_digest": request.candidate_manifest_digest,
            "evaluator_profile_digest": request.evaluator_profile_digest, "transport_profile_digest": self.transport_digest,
            "evidence_profile_digest": self.profile_digest, "external_call_id": call,
            "status": status.value, "used_seconds": used, "failure": failure, "result": result}
        return canonical_json_bytes(document), status, used, failure

    def pointer(self, request, call):
        content, status, used, failure = self._build(request, call)
        return ComputeEvidencePointer(request.request_digest[7:] + ":" + call, digest_bytes(content)), status, used, failure

    def resolve(self, pointer):
        request_hash, call = pointer.locator.split(":", 1)
        job = parse_json((self.root / "jobs" / (request_hash + ".json")).read_text())
        request = ComputeExecutionRequest.from_document(job["request"])
        if request.request_digest[7:] != request_hash:
            raise RuntimeError("paired serving pointer identity differs")
        content, *_ = self._build(request, call)
        if digest_bytes(content) != pointer.digest:
            raise RuntimeError("paired serving resolved evidence digest differs")
        return content


class ModalPairedServingTransport:
    def __init__(self, profile, root, modal_cli, spend):
        if not isinstance(spend, SqliteComputeSpendAuthorizationService):
            raise TypeError("paired serving requires durable compute authorization")
        self.profile, self.root, self.modal_cli, self.spend = profile, Path(root).resolve(), Path(modal_cli), spend
        self.profile_digest = self.profile_digest_for(profile.digest, self.modal_cli)
        self.resolver = ModalPairedServingEvidence(profile, self.root, self.profile_digest)

    @staticmethod
    def profile_digest_for(profile_digest, modal_cli):
        return digest_value({"adapter": "modal-paired-serving-transport/v1", "profile": profile_digest,
            "modal_cli": str(modal_cli), "source_digest": digest_file(Path(__file__)),
            "spend_service": SqliteComputeSpendAuthorizationService.profile_digest_for()})

    def retain_job(self, request, candidate):
        self.profile.slot(request)
        if digest_bytes(candidate) != request.candidate_digest:
            raise RuntimeError("paired serving candidate bytes differ")
        if self.profile.campaign.validate_candidate_document(parse_json(candidate.decode())).manifest_digest != request.candidate_manifest_digest:
            raise RuntimeError("paired serving candidate manifest differs")
        candidate_file = self.root / "candidates" / (request.request_digest[7:] + ".json")
        retain_bytes(candidate_file, candidate)
        namespace = self.resolver.store.remote_namespace(request.request_digest[7:])
        job = {"schema_version": "paired-serving-job/v1", "request": request.document,
            "profile_digest": self.profile.digest, "transport_digest": self.profile_digest,
            "namespace": namespace, "spec": self.profile.spec(request, namespace=namespace),
            "candidate_file": str(candidate_file)}
        retain_document(self.resolver.job_path(request), job)
        return job

    def _command(self, mode, request):
        return (str(self.modal_cli.parent / "python"), self.profile.pins["bridge"]["file"],
            "--mode", mode, "--root", str(self.root), "--job", str(self.resolver.job_path(request)),
            "--job-digest", digest_file(self.resolver.job_path(request)),
            "--authority-digest", self.spend._authority.manifest_digest,
            "--profile", str(self.root / "paired-profile.json"), "--profile-digest", digest_file(self.root / "paired-profile.json"))

    def dispatch(self, request, candidate):
        # Authority denial happens before subprocess creation or job retention.
        self.profile.check_inputs()
        self.profile.slot(request)
        self.spend.consume(request, self.profile_digest)
        self.retain_job(request, candidate)
        retain_document(self.root / "paired-profile.json", {"profile_digest": self.profile.digest, "pins": self.profile.pins})
        subprocess.run(self._command("dispatch", request), cwd=self.profile.repository,
            env=_minimal_modal_environment("dev"), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, timeout=900, check=False)
        if not self.resolver.dispatch_path(request).exists():
            raise RuntimeError("paired dispatch is uncertain; inspect before retry")
        dispatch = parse_json(self.resolver.dispatch_path(request).read_text())
        call = dispatch["function_call_id"]
        return ExternalDispatch(call, digest_bytes(self.resolver.resolve_dispatch(request, call)))

    def poll(self, request, external_call_id, timeout_seconds):
        self.resolver.resolve_dispatch(request, external_call_id)
        try:
            self.resolver.store.load(request.request_digest[7:], 1)
        except KeyError:
            try:
                completed = subprocess.run(self._command("collect", request), cwd=self.profile.repository,
                    env=_minimal_modal_environment("dev"), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, timeout=600, check=False)
            except subprocess.TimeoutExpired:
                return TransportPoll(ComputeExecutionStatus.DISPATCHED)
            try:
                self.resolver.store.load(request.request_digest[7:], 1)
            except KeyError:
                if completed.returncode:
                    raise RuntimeError("paired collection failed; retained call remains recoverable")
                return TransportPoll(ComputeExecutionStatus.DISPATCHED)
        pointer, status, used, failure = self.resolver.pointer(request, external_call_id)
        return TransportPoll(status, pointer, used, failure)

    def cleanup(self, request, canceller):
        from .modal_cleanup import cancel_retained_dispatch
        path = self.resolver.dispatch_path(request)
        dispatch = parse_json(path.read_text()) if path.exists() else None
        return cancel_retained_dispatch(request, dispatch, self.resolver, canceller, self.root)
