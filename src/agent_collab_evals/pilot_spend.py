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

    def __init__(self, root: Path, plan: dict, *, retry=None):
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
        self.retry = retry
        self.retry_digest = digest_value(retry) if retry is not None else None
        self._prior_receipts = None
        self._model_release = 0
        if retry is not None:
            from .pilot_retry import validate_retry
            self._prior_receipts, self._model_release = validate_retry(retry, self.plan_digest)
            self._limits = dict(retry["provider_limits_usd_nanos"])
            self._total = retry["total_limit_usd_nanos"]
        with self._locked():
            retain_document(self.root / "plan.json", plan)
            self._snapshot()
            if retry is not None:
                retain_document(self.root / "retry/approval.json", retry)

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
        record = dict(operation_key=operation_key, provider=provider, purpose=purpose,
            request_digest=request_digest, maximum_usd_nanos=maximum_usd_nanos,
            plan_digest=self.plan_digest)
        self._validate_receipt(record)
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

    def _snapshot(self) -> dict:
        amendment = self.root / "retry/approval.json"
        if amendment.exists() and (self.retry is None
            or amendment.read_bytes() != canonical_json_bytes(self.retry)):
            raise PermissionError("journal requires its pinned retry amendment")
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
            totals["openrouter"] -= self._model_release
        if any(totals[key] > self._limits[key] for key in totals) or sum(totals.values()) > self._total:
            raise RuntimeError("pilot admission journal exceeds its plan")
        return {"plan_digest": self.plan_digest, "reserved_usd_nanos": totals,
            "remaining_usd_nanos": {key: self._limits[key] - totals[key] for key in totals},
            "provider_limits_usd_nanos": dict(self._limits), "receipts": receipts,
            "actual_spend_usd_nanos": None, "provider_billing_cap_verified": False,
            **({"retry_amendment_digest": self.retry_digest,
                "released_unused_model_usd_nanos": self._model_release} if self.retry is not None else {})}

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
