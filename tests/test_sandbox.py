from __future__ import annotations

import http.server
import ipaddress
import os
import platform
import socket
import subprocess
import threading
import unittest
from pathlib import Path

from agent_collab_evals.adapters.darwin_sandbox import DarwinSandboxExec
from agent_collab_evals.sandbox import SandboxProfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = (
    REPOSITORY_ROOT
    / "config/sandbox_profiles/darwin-loopback-network-v0.json"
)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"SANDBOX_LOOPBACK_OK")

    def log_message(self, format: str, *args: object) -> None:
        return


class SandboxProfileTests(unittest.TestCase):
    def test_profile_is_pinned_and_direct_provider_endpoint_is_rejected(self) -> None:
        sandbox = DarwinSandboxExec(SandboxProfile.load(PROFILE_PATH))

        sandbox.validate_model_endpoint("http://127.0.0.1:9000/v1")
        with self.assertRaisesRegex(PermissionError, "loopback"):
            sandbox.validate_model_endpoint("https://openrouter.ai/api/v1")

        evidence = sandbox.evidence()
        self.assertEqual(evidence["network_mode"], "loopback_only")
        self.assertEqual(
            evidence["loopback_destinations"], "all_ports_and_services"
        )
        self.assertEqual(evidence["filesystem_enforcement"], "not_enforced")
        self.assertEqual(
            evidence["process_resource_enforcement"], "not_enforced"
        )
        self.assertEqual(evidence["credential_environment_allowlist"], [])
        self.assertTrue(
            str(evidence["sandbox_profile_digest"]).startswith("sha256:")
        )

    @unittest.skipUnless(
        platform.system() == "Darwin"
        and os.environ.get("RUN_SANDBOX_INTEGRATION") == "1",
        "set RUN_SANDBOX_INTEGRATION=1 on macOS to run sandbox-exec",
    )
    def test_kernel_policy_allows_all_loopback_and_denies_nonloopback(self) -> None:
        server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        sandbox = DarwinSandboxExec(SandboxProfile.load(PROFILE_PATH))
        try:
            loopback = subprocess.run(
                sandbox.wrap(
                    (
                        "/usr/bin/curl",
                        "--noproxy",
                        "*",
                        "-fsS",
                        "--max-time",
                        "5",
                        f"http://127.0.0.1:{server.server_port}",
                    )
                ),
                check=False,
                capture_output=True,
            )
            self.assertEqual(loopback.returncode, 0, loopback.stderr.decode())
            self.assertEqual(loopback.stdout, b"SANDBOX_LOOPBACK_OK")

            host = self._nonloopback_address()
            direct = subprocess.run(
                sandbox.wrap(
                    (
                        "/usr/bin/curl",
                        "--noproxy",
                        "*",
                        "-fsS",
                        "--max-time",
                        "2",
                        f"http://{host}:{server.server_port}",
                    )
                ),
                check=False,
                capture_output=True,
            )
            self.assertNotEqual(direct.returncode, 0)
            self.assertNotIn(b"SANDBOX_LOOPBACK_OK", direct.stdout)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    @staticmethod
    def _nonloopback_address() -> str:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 9))
            address = str(probe.getsockname()[0])
            if not ipaddress.ip_address(address).is_loopback:
                return address
        except OSError:
            pass
        finally:
            probe.close()
        raise unittest.SkipTest("host has no discoverable nonloopback IPv4 address")


if __name__ == "__main__":
    unittest.main()
