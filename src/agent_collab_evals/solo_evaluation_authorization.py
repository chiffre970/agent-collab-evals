"""Operator authority for one evaluation-only continuation, never a scored run."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
import re
import subprocess

from .canonical import digest_file, digest_value, parse_json
from .modal_pilot_cost import modal_pilot_cost
from .pilot_evidence import retain_document
from .pilot_spend import PilotSpendEnvelope


@dataclass(frozen=True)
class EvaluationContinuationAuthorization:
    document: dict
    digest: str

    @classmethod
    def load(cls, path, expected_digest):
        if not isinstance(expected_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_digest):
            raise ValueError("an independent evaluation-only authorization digest is required")
        if digest_file(path) != expected_digest:
            raise ValueError("evaluation-only authorization digest differs")
        document = parse_json(path.read_text())
        fields = {"schema_version", "scope", "run_id", "expires_at", "manifest_digest",
            "retry_amendment", "spend_journal", "state_root", "git_commit"}
        if (not isinstance(document, dict) or set(document) != fields
            or document["schema_version"] != "evaluation-continuation-authorization/v1"
            or document["scope"] != "one_exploratory_evaluation_continuation"
            or not re.fullmatch(r"[0-9a-f]{40}", document["git_commit"])
            or any(not isinstance(document[field], str) or not Path(document[field]).is_absolute()
                for field in ("spend_journal", "state_root"))):
            raise ValueError("evaluation-only authorization fields differ")
        authority = cls(document, expected_digest)
        authority.require_current()
        return authority

    def require_current(self):
        expires = datetime.fromisoformat(self.document["expires_at"])
        if expires.tzinfo is None or not datetime.now(UTC) < expires <= datetime.now(UTC) + timedelta(hours=24):
            raise PermissionError("evaluation-only authority must expire within 24 hours")

    def envelope(self, continuation, repository):
        """Validate deployment and approval before advancing the shared journal."""
        from .pilot_retry import _resolve
        self.require_current()
        document = self.document
        continuation._check_manifest(document["manifest_digest"])
        if (continuation.document["execution_mode"] != "live"
            or document["run_id"] != continuation.document["run_id"]
            or Path(document["state_root"]).resolve() != continuation.root):
            raise PermissionError("evaluation-only authorization run differs")
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository,
            check=True, capture_output=True, text=True).stdout.strip()
        checkout = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=repository,
            check=True, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repository,
            check=True, capture_output=True, text=True).stdout.strip()
        if dirty or commit != document["git_commit"] or Path(checkout).resolve() != repository.resolve():
            raise PermissionError("evaluation-only execution requires the approved clean source commit")
        binding = continuation.document["build_binding"]
        for name, digest in binding["source"].items():
            path = (repository / name).resolve(strict=True)
            path.relative_to(repository.resolve())
            if digest_file(path) != digest:
                raise PermissionError("evaluation-only source build differs")
        _, amendment = _resolve(document["retry_amendment"])
        if (amendment.get("schema_version") != "exploratory-solo-retry/v11"
            or amendment.get("run_id") != document["run_id"]
            or amendment.get("continuation_manifest") != {"file": str(continuation.root / "manifest.json"),
                "digest": continuation.digest}):
            raise PermissionError("evaluation-only settlement amendment differs")
        journal = Path(document["spend_journal"]).resolve(strict=True)
        if continuation.root.is_relative_to(journal) or journal.is_relative_to(continuation.root):
            raise PermissionError("continuation state and spend journal must be separate")
        plan = parse_json((repository / "config/pilots/solo-spend-envelope-v1.json").read_text())
        # The immutable claim permits restart of this exact inventory only; it
        # cannot create another continuation or change the approved manifest.
        retain_document(continuation.root / "operator-authorization.json", document)
        return PilotSpendEnvelope(journal, plan, retry=amendment)


class EvaluationContinuationSpendGuard:
    """Reserve dollars before issuing exact durable compute authority."""

    def __init__(self, continuation, envelope, estimate, *, authority):
        self.continuation, self.envelope, self.estimate = continuation, envelope, estimate
        self.authority = authority
        if (envelope.retry is None or envelope.retry["schema_version"] != "exploratory-solo-retry/v11"
            or envelope.retry["run_id"] != continuation.document["run_id"]
            or envelope.retry["continuation_manifest"]["digest"] != continuation.digest
            or estimate["per_execution_allowance_usd_nanos"] != 766_080_000
            or estimate["shared_overhead_allowance_usd_nanos"] != 1_000_000_000
            or any(r.maximum_seconds != estimate["function_timeout_seconds"] for r in continuation.requests.values())):
            raise PermissionError("evaluation-only spend guard differs from reviewed work")

    def begin(self):
        self.authority.require_current()
        overhead = self.envelope.reserve(operation_key=f"pilot:retry-{self.envelope.retry_digest[7:]}:modal:base",
            provider="modal", purpose="overhead", request_digest=self.continuation.digest,
            maximum_usd_nanos=self.estimate["shared_overhead_allowance_usd_nanos"])
        # Reserve the complete six-job ceiling before granting any request.
        # A crash burns the partial admission, but cannot start unfunded work.
        for request in self.continuation.requests.values():
            self._reserve(request)
        return overhead

    def _reserve(self, request):
        return self.envelope.reserve(operation_key=f"pilot:{self.continuation.document['run_id']}:compute:{request.request_digest[7:]}",
            provider="modal", purpose="pilot", request_digest=digest_value({"request": request.document,
                "manifest_digest": self.continuation.digest, "estimate": self.estimate}),
            maximum_usd_nanos=self.estimate["per_execution_allowance_usd_nanos"])

    def admit(self, continuation, request):
        self.authority.require_current()
        if continuation is not self.continuation or request not in continuation.requests.values():
            raise PermissionError("evaluation-only authority cannot admit another request or inventory")
        continuation._check_manifest(self.authority.document["manifest_digest"])
        record = self._reserve(request)
        return continuation.inventory.authorize(request, approval_reference="pilot-admission:" + digest_value(record))


def run_authorized_evaluation_continuation(continuation, configuration, authority):
    """Run the six frozen jobs under fresh one-use operator authority."""
    from .adapters.modal_cleanup import ModalCallCanceller
    envelope = authority.envelope(continuation, configuration.repository)
    estimate = modal_pilot_cost(configuration.public_compute.modal_script,
        configuration.repository / "config/compute/modal-pilot-cost-v1.json")
    guard = EvaluationContinuationSpendGuard(continuation, envelope, estimate, authority=authority)
    guard.begin()
    try:
        outcome = continuation.run(expected_digest=authority.document["manifest_digest"],
            admit=guard.admit, collection_seconds=configuration.public_compute.maximum_collection_seconds)
        retain_document(continuation.root / "spend-admission.json", envelope.snapshot())
        return outcome
    except Exception as error:
        cleanup = continuation.inventory.cleanup(ModalCallCanceller(configuration.repository, configuration.modal_cli))
        observation = {"schema_version": "evaluation-continuation-stop/v1", "status": "stopped",
            "manifest_digest": continuation.digest, "error_type": type(error).__name__,
            "observed_at": datetime.now(UTC).isoformat(), "remote_cleanup": cleanup, "spend_admission": envelope.snapshot(),
            "original_campaign_modified": False, "scoreable": False}
        retain_document(continuation.root / "stops" / (digest_value(observation)[7:] + ".json"), observation)
        raise
