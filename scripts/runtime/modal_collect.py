"""Collect an existing Modal call without creating a connected Modal App."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modal-script", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--baseline", action="store_true")
    mode.add_argument("--quality", action="store_true")
    mode.add_argument("--correctness", action="store_true")
    parser.add_argument("--candidate-path", required=True)
    parser.add_argument("--measurement-id", required=True)
    parser.add_argument("--repetition", type=int, required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--baseline-output-root")
    parser.add_argument("--performance-profile-path")
    parser.add_argument("--scoring-profile-path")
    parser.add_argument("--quality-output-root")
    parser.add_argument("--quality-profile-path")
    parser.add_argument("--quality-workload-path")
    parser.add_argument("--quality-role")
    parser.add_argument("--correctness-output-root")
    parser.add_argument("--correctness-workload-path")
    parser.add_argument("--correctness-profile-digest")
    parser.add_argument("--correctness-hidden-manifest-digest")
    parser.add_argument("--correctness-role")
    parser.add_argument("--collect-only", action="store_true", required=True)
    parser.add_argument("--collect-timeout-seconds", type=int, required=True)
    args = parser.parse_args()
    if not args.collect_only:
        parser.error("this process can only collect an existing call")
    if not 0 <= args.collect_timeout_seconds <= 300:
        parser.error("collection timeout is outside the registered range")
    if args.repetition < 1 or args.attempt < 1:
        parser.error("repetition and attempt must be positive")

    script_path = Path(args.modal_script).resolve(strict=True)
    spec = importlib.util.spec_from_file_location("modal_vllm_direct_collector", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("pinned Modal evaluator script could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    arguments = {
        key: value
        for key, value in vars(args).items()
        if key != "modal_script" and value is not None
    }
    try:
        module.main.info.raw_f(**arguments)
    except (module.modal.exception.ConnectionError, module.modal.exception.TimeoutError):
        print(json.dumps({"status": "collection_interrupted"}))
    except AttributeError as error:
        if str(error) != "'Connection' object has no attribute '_transport'":
            raise
        print(json.dumps({"status": "collection_interrupted"}))


if __name__ == "__main__":
    main()
