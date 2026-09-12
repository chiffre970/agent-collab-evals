from __future__ import annotations

import importlib.util
import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch


LAUNCHER_PATH = (
    Path(__file__).resolve().parents[1] / "scripts/runtime/session_launcher.py"
)
SPEC = importlib.util.spec_from_file_location("session_launcher", LAUNCHER_PATH)
assert SPEC is not None and SPEC.loader is not None
session_launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(session_launcher)


class SessionLauncherTests(unittest.TestCase):
    def test_named_capability_relays_are_validated_together(self) -> None:
        argv = ["--timeout-seconds", "10", "--broker-socket", "/tmp/model.sock", "--model-endpoint", "http://127.0.0.1:4317/v1"]
        for label, port in (("peer", 4318), ("candidate", 4319), ("native", 4320)):
            argv.extend([f"--{label}-broker-socket", f"/tmp/{label}.sock", f"--{label}-endpoint", f"http://127.0.0.1:{port}/v1/call"])
        arguments = session_launcher._arguments(argv + ["--", "/usr/bin/true"])
        with patch.object(session_launcher, "_broker_socket", side_effect=Path):
            specs = session_launcher._relay_specs(arguments)
            self.assertEqual([spec[0] for spec in specs], ["model", "peer", "candidate", "native"])
            self.assertEqual([spec[2] for spec in specs], [4317, 4318, 4319, 4320])
            arguments.native_endpoint = arguments.candidate_endpoint
            with self.assertRaisesRegex(ValueError, "different loopback ports"):
                session_launcher._relay_specs(arguments)

    def test_candidate_and_native_relay_options_require_both_fields(self) -> None:
        base = ["--timeout-seconds", "10", "--broker-socket", "/tmp/model.sock", "--model-endpoint", "http://127.0.0.1:4317/v1"]
        for label in ("candidate", "native"):
            with self.subTest(label=label), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    session_launcher._arguments(base + [f"--{label}-endpoint", "http://127.0.0.1:4319/v1/call", "--", "/usr/bin/true"])

    def test_accepts_only_fixed_loopback_model_endpoint(self) -> None:
        self.assertEqual(
            session_launcher._endpoint("http://127.0.0.1:4317/v1"),
            ("127.0.0.1", 4317),
        )
        for endpoint in (
            "https://127.0.0.1:4317/v1",
            "http://localhost:4317/v1",
            "http://127.0.0.1:4317/other",
            "http://openrouter.ai/v1",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    session_launcher._endpoint(endpoint)

    def test_requires_a_command_after_separator(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                session_launcher._arguments(
                    [
                        "--timeout-seconds",
                        "10",
                        "--broker-socket",
                        "/tmp/model.sock",
                        "--model-endpoint",
                        "http://127.0.0.1:4317/v1",
                    ]
                )

    def test_preserves_command_argv_after_separator(self) -> None:
        arguments = session_launcher._arguments(
            [
                "--timeout-seconds",
                "10",
                "--broker-socket",
                "/tmp/model.sock",
                "--model-endpoint",
                "http://127.0.0.1:4317/v1",
                "--",
                "/usr/bin/true",
                "literal argument",
            ]
        )

        self.assertEqual(arguments.command, ["/usr/bin/true", "literal argument"])

    def test_peer_relay_arguments_are_all_or_nothing(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                session_launcher._arguments(
                    [
                        "--timeout-seconds",
                        "10",
                        "--broker-socket",
                        "/tmp/model.sock",
                        "--model-endpoint",
                        "http://127.0.0.1:4317/v1",
                        "--peer-endpoint",
                        "http://127.0.0.1:4318/v1/call",
                        "--",
                        "/usr/bin/true",
                    ]
                )

        arguments = session_launcher._arguments(
            [
                "--timeout-seconds",
                "10",
                "--broker-socket",
                "/tmp/model.sock",
                "--model-endpoint",
                "http://127.0.0.1:4317/v1",
                "--peer-broker-socket",
                "/tmp/peer.sock",
                "--peer-endpoint",
                "http://127.0.0.1:4318/v1/call",
                "--",
                "/usr/bin/true",
            ]
        )
        self.assertEqual(arguments.peer_broker_socket, "/tmp/peer.sock")


if __name__ == "__main__":
    unittest.main()
