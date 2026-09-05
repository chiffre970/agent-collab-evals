"""Session-bound candidate operations, independent of agent runtime and compute."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable, Mapping

from .canonical import canonical_json_bytes, digest_value
from .collaboration import SessionTransport
from .evaluation import CandidateReceipt
from .ports import StorageBackend, SubmissionRegistry
from .session_identity import SessionIdentityRegistry


class CandidateTools:
    """Expose only owner-private admission and public evaluation.

    The controller owns job initialization, result-release timing, selection,
    hidden evaluation, and closure. None is selectable by an agent tool call.
    """

    def __init__(
        self,
        sessions: SessionIdentityRegistry,
        storage: StorageBackend,
        submissions: SubmissionRegistry,
        validate_candidate: Callable[[Mapping[str, Any]], object],
        *,
        campaign_run_id: str,
        job_id: str,
        candidate_policy_digest: str,
        max_candidate_bytes: int = 32768,
    ) -> None:
        if not campaign_run_id or not job_id or max_candidate_bytes < 1:
            raise ValueError("candidate tool scope and bound are required")
        self._sessions = sessions
        self._storage = storage
        self._submissions = submissions
        self._validate = validate_candidate
        self._campaign_run_id = campaign_run_id
        self._job_id = job_id
        self._max_bytes = max_candidate_bytes
        self.profile_digest = digest_value({
            "service": "candidate-tools/v1", "candidate_policy": candidate_policy_digest,
            "max_candidate_bytes": max_candidate_bytes,
            "operations": ["submit", "evaluate", "result"],
            "identity": "server_bound", "result_release": "controller_owned",
        })

    def call(self, session: SessionTransport, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        context = self._sessions.resolve(session)
        if context.campaign_run_id != self._campaign_run_id:
            raise PermissionError("candidate tools are unavailable in this campaign")
        if operation == "submit":
            if set(arguments) != {"candidate", "idempotency_key"}:
                raise ValueError("candidate submission fields differ")
            key = arguments["idempotency_key"]
            if not isinstance(key, str) or not 1 <= len(key) <= 256:
                raise ValueError("candidate idempotency key is invalid")
            candidate = arguments["candidate"]
            self._validate(candidate)
            content = canonical_json_bytes(candidate)
            if len(content) > self._max_bytes:
                raise ValueError("candidate exceeds the admitted size")
            artifact = self._storage.put(
                session, content, "application/json",
                idempotency_key=digest_value({"job": self._job_id, "candidate_key": key}),
            )
            receipt = self._submissions.submit(session, self._job_id, artifact.ref, key)
            return {"receipt": receipt.value, "artifact_digest": artifact.digest}
        if operation in {"evaluate", "result"}:
            if set(arguments) != {"receipt"} or not isinstance(arguments["receipt"], str):
                raise ValueError("candidate receipt fields differ")
            receipt = CandidateReceipt(arguments["receipt"])
            # Ownership is checked before the controller-only execution method.
            result = self._submissions.visible_result(session, receipt)
            if operation == "evaluate":
                self._submissions.evaluate_visible(receipt)
                result = self._submissions.visible_result(session, receipt)
            return {
                "status": "released" if result is not None else "pending",
                "result": asdict(result) if result is not None else None,
            }
        raise PermissionError("candidate operation is not exposed")
