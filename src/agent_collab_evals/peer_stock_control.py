"""One frozen stock-control series under explicit, cumulative dollar authority."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import fcntl
from pathlib import Path
import re
import subprocess
import time

from .canonical import digest_file, digest_value, parse_json
from .compute_backend import ComputeExecutionStatus
from .evaluation import EvaluationInProgress, EvaluationScope
from .peer_live_configuration import paired_peer_cost, stock_control_components
from .pilot_evidence import retain_document
from .pilot_spend import PilotSpendEnvelope
from .solo_live_configuration import LivePilotConfiguration


def pinned_document(reference):
    if (not isinstance(reference, dict) or set(reference) != {"file", "digest"}
        or not isinstance(reference["file"], str) or not Path(reference["file"]).is_absolute()
        or not isinstance(reference["digest"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", reference["digest"])
        or digest_file(Path(reference["file"])) != reference["digest"]):
        raise PermissionError("stock-control evidence pin differs")
    return parse_json(Path(reference["file"]).read_text())


def require_current(authorization):
    expires = datetime.fromisoformat(authorization["expires_at"])
    now = datetime.now(UTC)
    if expires.tzinfo is None or not now < expires <= now + timedelta(hours=24):
        raise PermissionError("stock-control authority must expire within 24 hours")


def require_clean_build(repository, commit):
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=repository, text=True).strip()
    top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=repository, text=True).strip()
    if dirty or actual != commit or Path(top).resolve() != repository:
        raise PermissionError("stock-control execution requires the approved clean source commit")


def stock_batch(preparation, prior_snapshot, provider_limits):
    """Build a proposal, not a grant; the operator must approve its exact bytes."""
    run_id = preparation["run_id"]
    return {"schema_version": "pilot-spend-batch/v1", "batch_id": run_id,
        "prior_snapshot_digest": digest_value(prior_snapshot), "provider_limits_usd_nanos": provider_limits,
        "total_limit_usd_nanos": sum(provider_limits.values()),
        "admissions": [{"operation_key": f"pilot:batch-{run_id}:modal:stock-control",
            "provider": "modal", "purpose": "pilot", "request_digest": digest_value(preparation),
            "maximum_usd_nanos": preparation["modal_allowance_usd_nanos"],
            "plan_digest": prior_snapshot["plan_digest"]}]}


@contextmanager
def controller_lock(root):
    with (root / ".stock-control.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise EvaluationInProgress("another controller owns this stock control") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def run_stock_control(root, authorization_path, authorization_digest):
    """Admit all seven jobs before issuing any, then reuse only their exact calls.

    This is bounded live conformance and current-workload calibration, not an
    agent run or a registered experiment. An in-flight deadline never releases
    dollars, cancels a valid call, or authorizes a replacement.
    """
    root = Path(root).resolve(strict=True)
    authorization = pinned_document({"file": str(Path(authorization_path).resolve(strict=True)), "digest": authorization_digest})
    fields = {"schema_version", "scope", "run_id", "expires_at", "preparation_digest",
        "configuration", "state_root", "repository", "git_commit", "journal", "batch_approval"}
    if (not isinstance(authorization, dict) or set(authorization) != fields
        or authorization["schema_version"] != "paired-stock-control-authorization/v1"
        or authorization["scope"] != "one_bounded_wrapper_conformance_and_stock_control"
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", authorization["run_id"])
        or not re.fullmatch(r"[0-9a-f]{40}", authorization["git_commit"])
        or any(not isinstance(authorization[k], str) or not Path(authorization[k]).is_absolute()
            for k in ("state_root", "repository"))
        or Path(authorization["state_root"]).resolve() != root):
        raise PermissionError("stock-control authority fields or state root differ")
    with controller_lock(root):
        return _run(root, authorization, authorization_digest)


def _run(root, authorization, authorization_digest):
    repository = Path(authorization["repository"]).resolve(strict=True)
    require_clean_build(repository, authorization["git_commit"])
    preparation_path = root / "stock-control.json"
    preparation = pinned_document({"file": str(preparation_path), "digest": authorization["preparation_digest"]})
    pinned_document(authorization["configuration"])
    configuration = LivePilotConfiguration.load(Path(authorization["configuration"]["file"]), repository)
    if (preparation.get("repository") != str(repository) or preparation.get("git_commit") != authorization["git_commit"]
        or preparation.get("run_id") != authorization["run_id"]
        or preparation.get("configuration_digest") != digest_value(configuration.document)
        or preparation.get("execution_authorized") is not False or preparation.get("scoreable") is not False):
        raise PermissionError("stock-control preparation differs from authority")
    stack, reservation, requests, seal = stock_control_components(root, configuration,
        authorization["run_id"], configuration.hidden_bundle(), configuration.campaign.quality_policy())
    source_pins = [{"file": str(s.manifest.path), "digest": s.manifest.manifest_digest} for s in stack.inventory.sources()]
    estimate = paired_peer_cost(configuration, 2)
    if (seal != preparation["inventory_seal_digest"] or [r.document for r in requests] != preparation["requests"]
        or source_pins != preparation["compute_manifests"] or len(requests) != 7
        or stack.hidden.profile_digest != preparation["evaluator_profile_digest"]
        or digest_value(reservation) != digest_value(preparation["reservation"])
        or preparation["modal_allowance_usd_nanos"] != estimate["shared_overhead_allowance_usd_nanos"]
            + 7 * estimate["per_execution_allowance_usd_nanos"]
        or preparation["cost_profile_digest"] != estimate["cost_profile_digest"]):
        raise PermissionError("stock-control inventory, evaluator, or costing differs")
    journal = authorization["journal"]
    if (not isinstance(journal, dict) or set(journal) != {"root", "plan_digest", "retry_amendment", "prior_batches"}
        or not isinstance(journal["root"], str) or not Path(journal["root"]).is_absolute()
        or not isinstance(journal["prior_batches"], list)):
        raise PermissionError("stock control requires the pinned existing journal")
    journal_root = Path(journal["root"]).resolve(strict=True)
    if root.is_relative_to(journal_root) or journal_root.is_relative_to(root):
        raise PermissionError("stock-control state and cumulative journal must be separate")
    plan = parse_json((repository / "config/pilots/solo-spend-envelope-v1.json").read_text())
    if digest_value(plan) != journal["plan_digest"] or digest_file(journal_root / "plan.json") != journal["plan_digest"]:
        raise PermissionError("stock control cannot create or replace its cumulative journal")
    retry = pinned_document(journal["retry_amendment"]) if journal["retry_amendment"] is not None else None
    previous = tuple(pinned_document(ref) for ref in journal["prior_batches"])
    batch = authorization["batch_approval"]
    # The currency inventory is a single atomic debit for exactly this control,
    # including overhead. No model call or another compute request is funded.
    expected_record = {"operation_key": f"pilot:batch-{authorization['run_id']}:modal:stock-control",
        "provider": "modal", "purpose": "pilot", "request_digest": digest_value(preparation),
        "maximum_usd_nanos": preparation["modal_allowance_usd_nanos"], "plan_digest": journal["plan_digest"]}
    if (not isinstance(batch, dict) or batch.get("batch_id") != authorization["run_id"]
        or batch.get("admissions") != [expected_record]):
        raise PermissionError("stock control cannot fund work outside its seven frozen requests")
    envelope = PilotSpendEnvelope(journal_root, plan, retry=retry, batch_approvals=(*previous, batch))
    has_claim = (root / "operator-authorization.json").exists()
    if not has_claim:
        require_current(authorization)
    retain_document(root / "operator-authorization.json", authorization)
    admission = envelope.admit_batch()
    reference = configuration.campaign.reference_candidate_path.read_bytes()
    backend = stack.inventory.backend("hidden")
    try:
        for request in requests:
            stack.hidden.profile.check_inputs()
            current = backend.inspect(request)
            if current is None or current.status is ComputeExecutionStatus.REGISTERED:
                require_current(authorization)
                require_clean_build(repository, authorization["git_commit"])
                stack.inventory.authorize(request, approval_reference="stock-admission:" + digest_value({
                    "admission": admission, "request_digest": request.request_digest, "authority_digest": authorization_digest}))
                current = backend.submit(request, reference)
            deadline = time.monotonic() + 4200
            while current.status is ComputeExecutionStatus.DISPATCHED:
                if time.monotonic() >= deadline:
                    raise EvaluationInProgress("stock control remains in flight; reconnect to the retained call")
                current = backend.collect(request, timeout_seconds=60)
            if current.status in {ComputeExecutionStatus.REGISTERED, ComputeExecutionStatus.DISPATCHING,
                ComputeExecutionStatus.AMBIGUOUS}:
                raise EvaluationInProgress("stock dispatch is uncertain; inspect it, never issue a replacement")
            if current.status is not ComputeExecutionStatus.COMPLETE:
                raise RuntimeError("stock control has a terminal failed execution")
            stack.inventory.require_authorized((request,), consumed=True)
            _, evidence = backend.resolve(request)
            retain_document(root / "completed" / (request.request_digest[7:] + ".json"), evidence)
        receipt = stack.hidden.hidden_evaluate(reference, reservation, "hidden:stock-control")
        result = stack.hidden.resolve(receipt, reference, reservation, EvaluationScope.HIDDEN)
        receipts = tuple(r for source in stack.inventory.sources() for r in source.backend.reconcile(authorization["run_id"]))
        if (len(receipts) != 7 or {r.request_digest for r in receipts} != {r.request_digest for r in requests}
            or any(r.status is not ComputeExecutionStatus.COMPLETE or r.used_seconds > 3000 for r in receipts)):
            raise RuntimeError("stock-control closure differs from its complete funded inventory")
        stack.inventory.require_authorized(requests, consumed=True)
        require_clean_build(repository, authorization["git_commit"])
        outcome = {"schema_version": "paired-stock-control-outcome/v1", "status": "complete",
            "run_id": authorization["run_id"], "scoreable": False, "model_calls": 0,
            "preparation_digest": authorization["preparation_digest"], "authorization_digest": authorization_digest,
            "evaluation_receipt": receipt, "result": result, "new_gpu_calls": 7,
            "compute_receipts": receipts, "used_seconds": stack.hidden.used_seconds(receipt),
            "spend_admission": envelope.snapshot(), "provider_billing_settlement": "pending"}
        retain_document(root / "outcome.json", outcome)
        return parse_json((root / "outcome.json").read_text())
    except Exception as error:
        observation = {"schema_version": "paired-stock-control-stop/v1", "scoreable": False,
            "status": "pending" if isinstance(error, EvaluationInProgress) else "stopped",
            "observed_at": datetime.now(UTC).isoformat(), "error_type": type(error).__name__,
            "preparation_digest": authorization["preparation_digest"], "spend_admission": envelope.snapshot(),
            "replacement_dispatch_authorized": False}
        # Leave known in-flight work available for exact-call collection. The
        # bounded function has retries=0 and a hard timeout; no reserve is freed.
        retain_document(root / "stops" / (digest_value(observation)[7:] + ".json"), observation)
        raise
