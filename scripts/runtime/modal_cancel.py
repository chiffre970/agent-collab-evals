"""Cancel one retained Modal call; never deploy or spawn compute."""

import json
import re
import sys


def main():
    if len(sys.argv) != 2 or not re.fullmatch(r"fc-[A-Za-z0-9_-]+", sys.argv[1]):
        raise ValueError("expected one exact Modal function-call ID")
    import modal

    call_id = sys.argv[1]
    modal.FunctionCall.from_id(call_id).cancel(terminate_containers=True)
    print(json.dumps({"schema_version": "modal-call-cancellation/v1", "function_call_id": call_id,
        "status": "cancellation_requested"}))


if __name__ == "__main__":
    main()
