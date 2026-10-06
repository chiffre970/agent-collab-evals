"""Deterministic peer/candidate tool exercise; never external inference."""

import json
import re

from .adapters.deterministic_model import DeterministicToolModelUpstream


class PeerCandidateModel(DeterministicToolModelUpstream):
    """Exercise both MCP surfaces under the same scripted behavior in both arms.

    The publication barrier is test-only. Actual exploratory agents receive no
    script, synchronization instruction, or coordination method.
    """

    def __init__(self, *, candidate, **kwargs):
        super().__init__(**kwargs)
        self.candidate = candidate
        self._published_actors = set()

    def stream(self, request):
        value = json.loads(request)
        with self._condition:
            self._requests.append(value)
            self._raw_requests.append(request)
            ordinal = len(self._requests)
        messages = value["messages"]
        last_user = max(index for index, message in enumerate(messages) if message["role"] == "user")
        results = [message for message in messages[last_user + 1:] if message["role"] == "tool"]
        request_id = f"local-peer-candidate-{ordinal:04d}"
        read_phase = "read-candidate-result" in json.dumps(messages[last_user])
        count = len(results)
        if read_phase:
            if count == 0:
                return self._tool_stream(request_id, "peer_list_recent", {"cursor": None, "limit": 50})
            if count == 1:
                return self._tool_stream(request_id, "candidate_result", {"receipt": self._receipt(messages)})
            return self._text_stream(request_id, "PEER_CANDIDATE_RESULT_OK")
        if count == 0:
            return self._tool_stream(request_id, "peer_publish", {
                "idempotency_key": "finding", "body": "synthetic candidate finding", "reply_to": None})
        if count == 1:
            actors = re.findall(r'"actor_id"\s*:\s*"([^"]+)"', json.dumps(results[0]).replace('\\"', '"'))
            if not actors:
                raise RuntimeError("peer publication did not return an actor identity")
            with self._condition:
                self._published_actors.add(actors[-1])
                self._condition.notify_all()
                if not self._condition.wait_for(lambda: len(self._published_actors) == self._peer_actor_count, timeout=45):
                    raise RuntimeError("synthetic peer publication barrier did not complete")
            return self._tool_stream(request_id, "peer_list_recent", {"cursor": None, "limit": 50})
        if count == 2:
            return self._tool_stream(request_id, "candidate_submit", {
                "candidate": self.candidate, "idempotency_key": "candidate-1"})
        if count == 3:
            return self._tool_stream(request_id, "candidate_evaluate", {"receipt": self._receipt(messages)})
        return self._text_stream(request_id, "PEER_CANDIDATE_SUBMISSION_OK")

    @staticmethod
    def _receipt(messages):
        receipts = re.findall(r"candidate-[0-9a-f]{32}", json.dumps(messages))
        if not receipts:
            raise RuntimeError("candidate admission did not return a receipt")
        return receipts[-1]
