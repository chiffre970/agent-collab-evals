"""Trusted bridge: dispatch once or collect one already-dispatched matched pair."""

import argparse
import importlib.util
import os
from pathlib import Path

from agent_collab_evals.canonical import digest_file, digest_value, parse_json
from agent_collab_evals.compute_backend import ComputeExecutionRequest
from agent_collab_evals.pilot_evidence import retain_document
from agent_collab_evals.adapters.local_measurements import LocalMeasurementBundleStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("dispatch", "collect"), required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-digest", required=True)
    parser.add_argument("--request-digest", required=True)
    args = parser.parse_args()
    if digest_file(args.manifest) != args.manifest_digest:
        raise RuntimeError("paired manifest changed")
    manifest = parse_json(args.manifest.read_text())
    binding = manifest["binding"]
    root = args.manifest.parent
    from agent_collab_evals.adapters.modal_paired_performance import ModalPairedPerformanceEvidence, profiles
    resolver = ModalPairedPerformanceEvidence(root, binding)
    request = next(ComputeExecutionRequest.from_document(j["request"]) for j in binding["jobs"]
        if digest_value(j["request"]) == args.request_digest)
    job = resolver._job(request)
    script = Path(binding["modal_script"]["file"])
    spec = importlib.util.spec_from_file_location("modal_vllm_paired_bridge", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    evidence_root = job["spec"]["benchmark"]["evidence_root"]
    dispatch_path = root / "dispatch" / (request.request_digest[7:] + ".json")
    if args.mode == "dispatch":
        # The parent consumed authority; the child proves that durable state
        # before creating an App. A write-once intent prevents direct redispatch.
        from agent_collab_evals.compute_backend import FrozenComputeRunManifest
        from agent_collab_evals.adapters.sqlite_compute_spend import SqliteComputeSpendAuthorizationService
        authority = FrozenComputeRunManifest.load(root / "compute-manifest.json", expected_digest=manifest["compute_manifest_digest"])
        spend = SqliteComputeSpendAuthorizationService(root / "spend.sqlite3", authority)
        record = spend._authorization(request, profiles(binding)[0])
        if spend.status(record.authorization_id) != "consumed":
            raise PermissionError("paired bridge lacks consumed request authority")
        intent = root / "intent" / (request.request_digest[7:] + ".json")
        intent.parent.mkdir(parents=True, exist_ok=True)
        try:
            with intent.open("xb") as output:
                output.write(request.request_digest.encode())
                output.flush()
                os.fsync(output.fileno())
        except FileExistsError as error:
            raise PermissionError("paired dispatch intent already exists; inspect, never redispatch") from error
        descriptor = os.open(intent.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        reference = parse_json(Path(binding["reference"]["file"]).read_text())
        candidate = parse_json(Path(binding["candidate"]["file"]).read_text())
        with module.app.run(detach=True, environment_name="dev"):
            function = module._scored_function_with_staging(module.paired_serving_repetition, evidence_root)
            call = function.spawn(reference, candidate, job["spec"])
            retain_document(dispatch_path, {"schema_version": "modal-paired-dispatch/v1",
                "request_digest": request.request_digest, "function_call_id": call.object_id,
                "binding_digest": digest_value(binding), "evidence_root": evidence_root,
                "git_commit": binding["git_commit"]})
    else:
        call_id = parse_json(dispatch_path.read_text())["function_call_id"]
        resolver.resolve_dispatch(request, call_id)
        call = module.modal.FunctionCall.from_id(call_id)
        try:
            pointer = module._get_scored_call_result(call, evidence_root, 60, collect_only=True)
            if pointer is None:
                return
            pointer = module._ensure_durable_evidence(pointer, evidence_root=evidence_root)
            receipt, raw, durable = module._collect_remote_evidence(pointer, expected_root=evidence_root)
        except module.modal.exception.ConnectionError:
            return
        LocalMeasurementBundleStore(root / "measurements").save(request.request_digest[7:], 1,
            {"request_digest": request.request_digest, "function_call_id": call_id,
             "remote_receipt": receipt, "durable_evidence": durable}, raw)


if __name__ == "__main__":
    main()
