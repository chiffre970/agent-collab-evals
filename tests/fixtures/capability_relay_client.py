"""Local synthetic capability client run as a session-launcher child."""

import http.client
import json
import sys
from urllib.parse import urlsplit


def call(access, operation, arguments):
    endpoint = urlsplit(access["endpoint"])
    connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=5)
    try:
        connection.request(
            "POST", endpoint.path,
            json.dumps({"operation": operation, "arguments": arguments}),
            {"Authorization": "Bearer " + access["token"], "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"relay request failed: {response.status}")
        return json.loads(response.read())["result"]
    finally:
        connection.close()


def main():
    request = json.load(sys.stdin)
    if "receipt" in request:
        result = call(request["candidate_access"], "result", {"receipt": request["receipt"]})
    else:
        receipt = call(request["candidate_access"], "submit", {"candidate": request["candidate"], "idempotency_key": "relay-candidate"})
        evaluation = call(request["candidate_access"], "evaluate", {"receipt": receipt["receipt"]})
        permit = call(request["native_access"], "reserve", {"session_id": "primary", "call_id": "relay-task", "task_id": None, "subagent_type": "general"})
        completion = call(request["native_access"], "complete", {"permit": permit["permit"], "child_session_id": "child"})
        result = {"receipt": receipt["receipt"], "evaluation": evaluation, "native": completion}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
