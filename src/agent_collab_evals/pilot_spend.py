"""Small, append-only dollar admission envelope for one exploratory pilot.

Qualification, setup, and the pilot share one host-private journal. Reservations
are never refunded automatically, even if dispatch or cleanup fails. This bounds
admitted allowances, not the provider invoice, and grants no execution authority.
"""

from contextlib import contextmanager
import fcntl
from pathlib import Path
import re

from .canonical import canonical_json_bytes, digest_bytes, digest_value, parse_json
from .pilot_evidence import retain_document


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}")


def admit_openrouter_qualification(repository: Path, root: Path, *, operation_key: str,
                                  request_digest: str, maximum_usd_nanos: int) -> dict:
    """Claim one bounded qualification attempt in the pilot's shared journal."""
    plan = parse_json((repository / "config/pilots/solo-spend-envelope-v1.json").read_text())
    return PilotSpendEnvelope(root, plan).reserve(operation_key=operation_key,
        provider="openrouter", purpose="qualification", request_digest=request_digest,
        maximum_usd_nanos=maximum_usd_nanos, allow_existing=False)


class PilotSpendEnvelope:
    """Serialize admission across processes with immutable, fsynced receipts.

    A file journal keeps this deliberately small: one qualification series and
    one pilot, not a fleet-scale accounting service. The host owns the directory.
    Do not create a new directory to recover from exhausted or corrupt evidence.
    """

    def __init__(self, root: Path, plan: dict, *, retry=None, batch_approvals=()):
        if (not isinstance(plan, dict) or set(plan) != {
            "schema_version", "plan_id", "total_limit_usd_nanos",
            "provider_limits_usd_nanos", "accounting"
        } or plan["schema_version"] != "pilot-spend-envelope/v1"
            or plan["accounting"] != "irrevocable_admission_allowances_not_actual_billing"):
            raise ValueError("pilot spending plan fields differ")
        limits = plan["provider_limits_usd_nanos"]
        if not isinstance(limits, dict) or set(limits) != {"modal", "openrouter"}:
            raise ValueError("pilot spending providers differ")
        if any(type(value) is not int or value < 1 for value in (*limits.values(), plan["total_limit_usd_nanos"])):
            raise ValueError("pilot spending limits must be positive integers")
        if not isinstance(plan["plan_id"], str) or not _KEY.fullmatch(plan["plan_id"]):
            raise ValueError("pilot spending plan ID is invalid")
        if sum(limits.values()) > plan["total_limit_usd_nanos"]:
            raise ValueError("provider limits exceed the total envelope")
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._plan_bytes = canonical_json_bytes(plan)
        self.plan_digest = digest_bytes(self._plan_bytes)
        self._limits = dict(limits)
        self._total = plan["total_limit_usd_nanos"]
        self._batch_approvals = tuple(batch_approvals)
        self.retry = retry
        self.retry_digest = digest_value(retry) if retry is not None else None
        self._prior_receipts = None
        self._model_release = 0
        self._releases = {"modal": 0, "openrouter": 0}
        self._previous_retry = None
        self._settlement_retry = None
        self._feedback_retry = None
        self._collector_retry = None
        self._series_retry = None
        self._connected_retry = None
        self._reference_retry = None
        self._staging_retry = None
        self._environment_retry = None
        self._evaluation_retry = None
        self._performance_retry = None
        self._performance_settlement_retry = None
        self._initial_retry = None
        if retry is not None:
            from .pilot_retry import validate_retry
            self._prior_receipts, self._releases = validate_retry(retry, self.plan_digest)
            self._model_release = self._releases["openrouter"]
            if retry["schema_version"] == "exploratory-solo-retry/v13":
                self._performance_settlement_retry = retry
                retry = parse_json(Path(retry["previous_amendment"]["file"]).read_text())
            if retry["schema_version"] == "exploratory-solo-retry/v12":
                self._performance_retry = retry
                retry = parse_json(Path(retry["previous_amendment"]["file"]).read_text())
            if retry["schema_version"] == "exploratory-solo-retry/v11":
                self._evaluation_retry = retry
                retry = parse_json(Path(retry["previous_amendment"]["file"]).read_text())
            if retry["schema_version"] == "exploratory-solo-retry/v2":
                self._previous_retry = parse_json(Path(retry["previous_amendment"]["file"]).read_text())
            elif retry["schema_version"] == "exploratory-solo-retry/v3":
                self._settlement_retry = parse_json(Path(retry["previous_amendment"]["file"]).read_text())
                self._previous_retry = parse_json(Path(self._settlement_retry["previous_amendment"]["file"]).read_text())
            elif retry["schema_version"] == "exploratory-solo-retry/v4":
                self._feedback_retry = parse_json(Path(retry["previous_amendment"]["file"]).read_text())
                self._settlement_retry = parse_json(Path(self._feedback_retry["previous_amendment"]["file"]).read_text())
                self._previous_retry = parse_json(Path(self._settlement_retry["previous_amendment"]["file"]).read_text())
            elif retry["schema_version"] == "exploratory-solo-retry/v5":
                self._collector_retry = parse_json(Path(retry["previous_amendment"]["file"]).read_text())
                self._feedback_retry = parse_json(Path(self._collector_retry["previous_amendment"]["file"]).read_text())
                self._settlement_retry = parse_json(Path(self._feedback_retry["previous_amendment"]["file"]).read_text())
                self._previous_retry = parse_json(Path(self._settlement_retry["previous_amendment"]["file"]).read_text())
            elif retry["schema_version"] in {"exploratory-solo-retry/v6",
                                             "exploratory-solo-retry/v7",
                                             "exploratory-solo-retry/v8",
                                             "exploratory-solo-retry/v9",
                                             "exploratory-solo-retry/v10"}:
                if retry["schema_version"] == "exploratory-solo-retry/v10":
                    self._environment_retry = retry
                    self._staging_retry = parse_json(Path(retry["previous_amendment"]["file"]).read_text())
                    self._reference_retry = parse_json(Path(self._staging_retry["previous_amendment"]["file"]).read_text())
                    self._connected_retry = parse_json(Path(self._reference_retry["previous_amendment"]["file"]).read_text())
                    self._series_retry = parse_json(Path(self._connected_retry["previous_amendment"]["file"]).read_text())
                elif retry["schema_version"] == "exploratory-solo-retry/v9":
                    self._staging_retry = retry
                    self._reference_retry = parse_json(Path(retry["previous_amendment"]["file"]).read_text())
                    self._connected_retry = parse_json(Path(self._reference_retry["previous_amendment"]["file"]).read_text())
                    self._series_retry = parse_json(Path(self._connected_retry["previous_amendment"]["file"]).read_text())
                elif retry["schema_version"] == "exploratory-solo-retry/v8":
                    self._reference_retry = retry
                    self._connected_retry = parse_json(Path(retry["previous_amendment"]["file"]).read_text())
                    self._series_retry = parse_json(Path(self._connected_retry["previous_amendment"]["file"]).read_text())
                elif retry["schema_version"] == "exploratory-solo-retry/v7":
                    self._connected_retry = retry
                    self._series_retry = parse_json(Path(retry["previous_amendment"]["file"]).read_text())
                else:
                    self._series_retry = retry
                self._collector_retry = parse_json(Path(self._series_retry["previous_amendment"]["file"]).read_text())
                self._feedback_retry = parse_json(Path(self._collector_retry["previous_amendment"]["file"]).read_text())
                self._settlement_retry = parse_json(Path(self._feedback_retry["previous_amendment"]["file"]).read_text())
                self._previous_retry = parse_json(Path(self._settlement_retry["previous_amendment"]["file"]).read_text())
                self._initial_retry = parse_json(Path(self._previous_retry["previous_amendment"]["file"]).read_text())
            self._limits = dict(retry["provider_limits_usd_nanos"])
            self._total = retry["total_limit_usd_nanos"]
        with self._locked():
            retain_document(self.root / "plan.json", plan)
            self._snapshot()
            if retry is not None:
                filename = ("performance-settlement-approval.json" if self._performance_settlement_retry is not None
                    else "performance-extension-approval.json" if self._performance_retry is not None
                    else "evaluation-settlement-approval.json" if self._evaluation_retry is not None
                    else "environment-settlement-approval.json" if self._environment_retry is not None
                    else "staging-settlement-approval.json" if self._staging_retry is not None
                    else "reference-probe-approval.json" if self._reference_retry is not None
                    else "connected-settlement-approval.json" if self._connected_retry is not None
                    else "series-settlement-approval.json" if self._series_retry is not None
                    else "collector-settlement-approval.json" if self._collector_retry is not None
                    else "feedback-settlement-approval.json" if self._feedback_retry is not None
                    else "final-settlement-approval.json" if self._settlement_retry is not None
                    else "settlement-approval.json" if self._previous_retry is not None
                    else "approval.json")
                retain_document(self.root / "retry" / filename, self.retry)

    @contextmanager
    def _locked(self):
        with (self.root / ".admission.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def reserve(self, *, operation_key: str, provider: str, purpose: str,
                request_digest: str, maximum_usd_nanos: int, allow_existing: bool = True) -> dict:
        """Durably debit an exact operation before any authority is issued."""
        if self._batch_approvals:
            raise PermissionError("batch journal accepts only its exact approved inventory")
        record = dict(operation_key=operation_key, provider=provider, purpose=purpose,
            request_digest=request_digest, maximum_usd_nanos=maximum_usd_nanos,
            plan_digest=self.plan_digest)
        self._validate_receipt(record)
        self._validate_evaluation_admission(record)
        if self.retry is not None and not operation_key.startswith((
            f"pilot:retry-{self.retry_digest[7:]}:", f"pilot:{self.retry['run_id']}:compute:")):
            raise PermissionError("retry journal only accepts the approved attempt")
        path = self.root / (digest_value(operation_key)[7:] + ".json")
        with self._locked():
            snapshot = self._snapshot()
            if path.exists():
                if path.read_bytes() != canonical_json_bytes(record):
                    raise RuntimeError("pilot admission retry differs")
                if not allow_existing:
                    raise RuntimeError("pilot operation was already admitted; automatic restart is disabled")
                return record
            if (snapshot["reserved_usd_nanos"][provider] + maximum_usd_nanos > self._limits[provider]
                or sum(snapshot["reserved_usd_nanos"].values()) + maximum_usd_nanos > self._total):
                raise PermissionError("pilot spending envelope exhausted")
            # A crash after this write burns the allowance. It cannot reopen
            # capacity after a possibly successful dispatch.
            retain_document(path, record)
        return record

    def snapshot(self) -> dict:
        with self._locked():
            return self._snapshot()

    def admit_batch(self) -> dict:
        """Reserve the entire latest approved inventory before any job is issued."""
        if not self._batch_approvals:
            raise PermissionError("batch admission requires independently pinned approval")
        from .pilot_spend_batch import retain_batch
        with self._locked():
            self._snapshot()  # Validate history, ceilings, and the whole inventory.
            return retain_batch(self.root, self._batch_approvals[-1])

    def _snapshot(self) -> dict:
        amendment = self.root / "retry/approval.json"
        expected_previous = (self._initial_retry if self._series_retry is not None
            else self._previous_retry if self._previous_retry is not None else self.retry)
        if amendment.exists() and (expected_previous is None
            or amendment.read_bytes() != canonical_json_bytes(expected_previous)):
            raise PermissionError("journal requires its pinned retry amendment")
        settlement = self.root / "retry/settlement-approval.json"
        expected_settlement = (self._previous_retry if self._series_retry is not None
            else self._settlement_retry if self._settlement_retry is not None else self.retry)
        if (self._previous_retry is not None and not amendment.exists()
            or settlement.exists() and (self._previous_retry is None
                or settlement.read_bytes() != canonical_json_bytes(expected_settlement))):
            raise PermissionError("journal requires its pinned settlement amendment")
        final_settlement = self.root / "retry/final-settlement-approval.json"
        expected_final = (self._settlement_retry if self._series_retry is not None
            else self._feedback_retry if self._feedback_retry is not None else self.retry)
        if (self._settlement_retry is not None and not settlement.exists()
            or final_settlement.exists() and (self._settlement_retry is None
                or final_settlement.read_bytes() != canonical_json_bytes(expected_final))):
            raise PermissionError("journal requires its pinned final settlement amendment")
        feedback_settlement = self.root / "retry/feedback-settlement-approval.json"
        expected_feedback = (self._feedback_retry if self._series_retry is not None
            else self._collector_retry if self._collector_retry is not None else self.retry)
        if (self._feedback_retry is not None and not final_settlement.exists()
            or feedback_settlement.exists() and (self._feedback_retry is None
                or feedback_settlement.read_bytes() != canonical_json_bytes(expected_feedback))):
            raise PermissionError("journal requires its pinned feedback settlement amendment")
        collector_settlement = self.root / "retry/collector-settlement-approval.json"
        expected_collector = self._collector_retry if self._series_retry is not None else self.retry
        if (self._collector_retry is not None and not feedback_settlement.exists()
            or collector_settlement.exists() and (self._collector_retry is None
                or collector_settlement.read_bytes() != canonical_json_bytes(expected_collector))):
            raise PermissionError("journal requires its pinned collector settlement amendment")
        series_settlement = self.root / "retry/series-settlement-approval.json"
        if (self._series_retry is not None and not collector_settlement.exists()
            or series_settlement.exists() and (self._series_retry is None
                or series_settlement.read_bytes() != canonical_json_bytes(self._series_retry))):
            raise PermissionError("journal requires its pinned series settlement amendment")
        connected_settlement = self.root / "retry/connected-settlement-approval.json"
        if (self._connected_retry is not None and not series_settlement.exists()
            or connected_settlement.exists() and (self._connected_retry is None
                or connected_settlement.read_bytes() != canonical_json_bytes(self._connected_retry))):
            raise PermissionError("journal requires its pinned connected settlement amendment")
        reference_settlement = self.root / "retry/reference-probe-approval.json"
        expected_reference = self._reference_retry if self._staging_retry is not None else self.retry
        if (self._reference_retry is not None and not connected_settlement.exists()
            or reference_settlement.exists() and (self._reference_retry is None
                or reference_settlement.read_bytes() != canonical_json_bytes(expected_reference))):
            raise PermissionError("journal requires its pinned reference-probe amendment")
        staging_settlement = self.root / "retry/staging-settlement-approval.json"
        if (self._staging_retry is not None and not reference_settlement.exists()
            or staging_settlement.exists() and (self._staging_retry is None
                or staging_settlement.read_bytes() != canonical_json_bytes(self._staging_retry))):
            raise PermissionError("journal requires its pinned staging settlement amendment")
        environment_settlement = self.root / "retry/environment-settlement-approval.json"
        if (self._environment_retry is not None and not staging_settlement.exists()
            or environment_settlement.exists() and (self._environment_retry is None
                or environment_settlement.read_bytes() != canonical_json_bytes(self._environment_retry))):
            raise PermissionError("journal requires its pinned environment settlement amendment")
        evaluation_settlement = self.root / "retry/evaluation-settlement-approval.json"
        if (self._evaluation_retry is not None and not environment_settlement.exists()
            or evaluation_settlement.exists() and (self._evaluation_retry is None
                or evaluation_settlement.read_bytes() != canonical_json_bytes(self._evaluation_retry))):
            raise PermissionError("journal requires its pinned evaluation-only settlement amendment")
        performance_extension = self.root / "retry/performance-extension-approval.json"
        if (self._performance_retry is not None and not evaluation_settlement.exists()
            or performance_extension.exists() and (self._performance_retry is None
                or performance_extension.read_bytes() != canonical_json_bytes(self._performance_retry))):
            raise PermissionError("journal requires its pinned performance-only extension")
        performance_settlement = self.root / "retry/performance-settlement-approval.json"
        if (self._performance_settlement_retry is not None and not performance_extension.exists()
            or performance_settlement.exists() and (self._performance_settlement_retry is None
                or performance_settlement.read_bytes() != canonical_json_bytes(self._performance_settlement_retry))):
            raise PermissionError("journal requires its pinned performance-only settlement")
        if (self.root / "plan.json").read_bytes() != self._plan_bytes:
            raise RuntimeError("pilot spending plan differs from pinned authority")
        totals = {provider: 0 for provider in self._limits}
        receipts = []
        for path in sorted(self.root.glob("*.json")):
            if path.name == "plan.json":
                continue
            raw = path.read_bytes()
            record = parse_json(raw.decode())
            self._validate_receipt(record)
            if (path.name != digest_value(record["operation_key"])[7:] + ".json"
                or raw != canonical_json_bytes(record)):
                raise RuntimeError("pilot admission receipt identity differs")
            totals[record["provider"]] += record["maximum_usd_nanos"]
            receipts.append(record)
        if self.retry is not None:
            prior_keys = {item["operation_key"] for item in self._prior_receipts}
            prior = [item for item in receipts if item["operation_key"] in prior_keys]
            if prior != self._prior_receipts:
                raise RuntimeError("retry prior admissions differ")
            allowed = (f"pilot:retry-{self.retry_digest[7:]}:", f"pilot:{self.retry['run_id']}:compute:")
            if any(item["operation_key"] not in prior_keys and not item["operation_key"].startswith(allowed)
                for item in receipts):
                raise RuntimeError("journal contains an unapproved retry")
            for item in receipts:
                if item["operation_key"] not in prior_keys:
                    self._validate_evaluation_admission(item)
            for provider, release in self._releases.items():
                totals[provider] -= release
        if any(totals[key] > self._limits[key] for key in totals) or sum(totals.values()) > self._total:
            raise RuntimeError("pilot admission journal exceeds its plan")
        snapshot = {"plan_digest": self.plan_digest, "reserved_usd_nanos": totals,
            "remaining_usd_nanos": {key: self._limits[key] - totals[key] for key in totals},
            "provider_limits_usd_nanos": dict(self._limits), "receipts": receipts,
            "actual_spend_usd_nanos": None, "provider_billing_cap_verified": False,
            **({"retry_amendment_digest": self.retry_digest,
                "released_unused_model_usd_nanos": self._model_release,
                "released_allowances_usd_nanos": self._releases} if self.retry is not None else {})}
        from .pilot_spend_batch import apply_batches
        return apply_batches(self.root, snapshot, self._batch_approvals, self._validate_receipt)

    def _validate_receipt(self, record):
        if (not isinstance(record, dict) or set(record) != {"operation_key", "provider", "purpose",
            "request_digest", "maximum_usd_nanos", "plan_digest"}
            or record["plan_digest"] != self.plan_digest):
            raise ValueError("pilot admission receipt fields differ")
        if (not isinstance(record["provider"], str) or record["provider"] not in self._limits
            or not isinstance(record["purpose"], str) or record["purpose"] not in {"qualification", "pilot", "overhead"}
            or not isinstance(record["operation_key"], str) or not _KEY.fullmatch(record["operation_key"])
            or not isinstance(record["request_digest"], str) or not _DIGEST.fullmatch(record["request_digest"])
            or type(record["maximum_usd_nanos"]) is not int or record["maximum_usd_nanos"] < 1):
            raise ValueError("pilot admission receipt is invalid")

    def _validate_evaluation_admission(self, record):
        if self._performance_settlement_retry is not None or self._performance_retry is not None:
            from .solo_performance_spend import performance_admissions
            allowed = performance_admissions(self._performance_settlement_retry or self._performance_retry)
        elif self._evaluation_retry is not None:
            from .solo_evaluation_spend import evaluation_admissions
            allowed = evaluation_admissions(self._evaluation_retry)
        else:
            return
        expected = allowed.get(record["operation_key"])
        if expected is None or (record["provider"], record["purpose"], record["maximum_usd_nanos"]) != expected:
            raise PermissionError("evaluation-only admission cannot fund agents, model calls, or unplanned compute")
        if self._performance_settlement_retry is not None or self._performance_retry is not None:
            amendment = self._performance_settlement_retry or self._performance_retry
            request_digest = (amendment["followup_manifest"]["digest"] if record["purpose"] == "overhead"
                else "sha256:" + record["operation_key"].rsplit(":", 1)[1])
            if record["request_digest"] != request_digest:
                raise PermissionError("performance admission request identity differs")
