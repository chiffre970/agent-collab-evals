"""Dispatch once or reconnect to one frozen co-located serving execution."""

import argparse
import importlib.util
import importlib.metadata
import os
import tomllib
from pathlib import Path

from agent_collab_evals.adapters.local_measurements import LocalMeasurementBundleStore
from agent_collab_evals.adapters.sqlite_compute_spend import SqliteComputeSpendAuthorizationService
from agent_collab_evals.canonical import digest_file, digest_value, parse_json
from agent_collab_evals.compute_backend import ComputeExecutionRequest, FrozenComputeRunManifest
from agent_collab_evals.pilot_evidence import retain_document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("dispatch", "collect"), required=True)
    for name in ("root", "job", "profile"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("job-digest", "profile-digest", "authority-digest"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    if digest_file(args.job) != args.job_digest or digest_file(args.profile) != args.profile_digest:
        raise RuntimeError("paired bridge input changed")
    job, profile = parse_json(args.job.read_text()), parse_json(args.profile.read_text())
    if job["profile_digest"] != profile["profile_digest"]:
        raise RuntimeError("paired bridge profile differs")
    for ref in profile["pins"].values():
        if digest_file(Path(ref["file"])) != ref["digest"]:
            raise RuntimeError("paired bridge pinned input changed")
    with Path(profile["pins"]["measurement"]["file"]).open("rb") as source:
        measurement = tomllib.load(source)
    if importlib.metadata.version("modal") != measurement["environment"]["modal_client_version"]:
        raise RuntimeError("paired bridge Modal SDK differs from the frozen pin")
    request = ComputeExecutionRequest.from_document(job["request"])
    if digest_file(Path(job["candidate_file"])) != request.candidate_digest:
        raise RuntimeError("paired bridge candidate changed")
    # Resolve original authority from the route's frozen manifest and immutable
    # single-use service. A child invocation cannot grant itself a request.
    authority = FrozenComputeRunManifest.load(args.root / "manifest.json",
        expected_digest=args.authority_digest)
    authority.assert_authorized(request)
    authority.assert_backend_profiles(authority.backend_profile_digest, job["transport_digest"])
    spend = SqliteComputeSpendAuthorizationService(args.root / "spend.sqlite3", authority)
    if spend.request_status(request, job["transport_digest"]) != "consumed":
        raise PermissionError("paired bridge lacks consumed request authority")
    root = job["spec"]["benchmark"]["evidence_root"]
    dispatch_path = args.root / "dispatch" / (request.request_digest[7:] + ".json")
    if args.mode == "dispatch":
        intent = args.root / "intent" / (request.request_digest[7:] + ".json")
        intent.parent.mkdir(parents=True, exist_ok=True)
        with intent.open("xb") as target:
            target.write(request.request_digest.encode())
            target.flush()
            os.fsync(target.fileno())
        descriptor = os.open(intent.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    spec = importlib.util.spec_from_file_location("modal_paired_serving_bridge", profile["pins"]["paired_script"]["file"])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if args.mode == "dispatch":
        reference = parse_json(Path(profile["pins"]["reference"]["file"]).read_text())
        candidate = parse_json(Path(job["candidate_file"]).read_text())
        with module.app.run(detach=True, environment_name="dev"):
            function = module.base._scored_function_with_staging(module.paired_serving, root)
            call = function.spawn(reference, candidate, job["spec"])
            retain_document(dispatch_path, {"schema_version": "paired-serving-dispatch/v1",
                "request_digest": request.request_digest, "function_call_id": call.object_id,
                "job_digest": digest_value(job), "transport_digest": job["transport_digest"], "evidence_root": root})
    else:
        dispatch = parse_json(dispatch_path.read_text())
        if (dispatch.get("request_digest") != request.request_digest or dispatch.get("job_digest") != digest_value(job)
            or dispatch.get("evidence_root") != root or dispatch.get("transport_digest") != job["transport_digest"]):
            raise RuntimeError("paired bridge dispatch identity differs")
        call = module.modal.FunctionCall.from_id(dispatch["function_call_id"])
        try:
            pointer = module.base._get_scored_call_result(call, root, 60, collect_only=True)
            if pointer is None:
                return
            pointer = module.base._ensure_durable_evidence(pointer, evidence_root=root)
            receipt, raw, durable = module.base._collect_remote_evidence(pointer, expected_root=root)
        except module.modal.exception.ConnectionError:
            return
        LocalMeasurementBundleStore(args.root / "measurements").save(request.request_digest[7:], 1,
            {"request_digest": request.request_digest, "function_call_id": dispatch["function_call_id"],
                "remote_receipt": receipt, "durable_evidence": durable}, raw)


if __name__ == "__main__":
    main()
