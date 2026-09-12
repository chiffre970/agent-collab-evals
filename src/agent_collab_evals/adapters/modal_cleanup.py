"""Bounded cancellation of an exact, locally verified Modal dispatch."""

import re
import subprocess
from pathlib import Path

from ..canonical import digest_bytes, digest_value, parse_json
from ..pilot_evidence import retain_document


class ModalCallCanceller:
    """Request cancellation only; acknowledgment is not terminal billing proof."""

    def __init__(self, repository: Path, modal_cli: Path):
        self.repository = repository
        self.python = modal_cli.parent / "python"

    def cancel(self, call_id: str) -> dict:
        from .modal_vllm_compute import _minimal_modal_environment

        if not re.fullmatch(r"fc-[A-Za-z0-9_-]+", call_id):
            raise ValueError("Modal cleanup call ID is invalid")
        result = subprocess.run(
            (str(self.python), str(self.repository / "scripts/runtime/modal_cancel.py"), call_id),
            cwd=self.repository, env=_minimal_modal_environment(), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=30, check=False)
        if result.returncode != 0:
            raise RuntimeError("Modal cancellation was not acknowledged")
        receipt = parse_json(result.stdout)
        expected = {"schema_version": "modal-call-cancellation/v1", "function_call_id": call_id,
            "status": "cancellation_requested"}
        if receipt != expected:
            raise RuntimeError("Modal cancellation acknowledgment differs")
        return receipt


def cancel_retained_dispatch(request, dispatch, evidence, canceller, root: Path) -> dict:
    """Verify request-to-call identity before making a targeted cancellation."""
    if dispatch is None:
        return {"request_digest": request.request_digest, "status": "unresolved_dispatch",
            "terminal_confirmed": False}
    call_id = dispatch.get("function_call_id")
    if not isinstance(call_id, str) or not re.fullmatch(r"fc-[A-Za-z0-9_-]+", call_id):
        raise ValueError("retained Modal call ID is invalid")
    verified = evidence.resolve_dispatch(request, call_id)
    if digest_bytes(verified) != digest_value(dispatch):
        raise RuntimeError("cleanup dispatch evidence differs")
    receipt = {"request_digest": request.request_digest, "dispatch_digest": digest_bytes(verified),
        "function_call_id": call_id, "status": "cancellation_requested", "terminal_confirmed": False}
    path = root / "cleanup" / f"{request.request_digest[7:]}.json"
    if path.exists():
        if parse_json(path.read_text()) != receipt:
            raise RuntimeError("retained cancellation differs")
        return receipt
    acknowledgment = canceller.cancel(call_id)
    if acknowledgment != {"schema_version": "modal-call-cancellation/v1", "function_call_id": call_id,
                           "status": "cancellation_requested"}:
        raise RuntimeError("Modal cancellation acknowledgment differs")
    retain_document(path, receipt)
    return receipt
