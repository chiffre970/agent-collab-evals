"""Side-effect-free path checks shared by pilot preflight and socket creation."""

from pathlib import Path
import unittest

from agent_collab_evals.candidate_gateway import CandidateToolGateway
from agent_collab_evals.model_gateway import ModelBudgetGateway


class UnixSocketPathTests(unittest.TestCase):
    def test_portable_limit_counts_resolved_utf8_bytes(self):
        for gateway in (ModelBudgetGateway, CandidateToolGateway):
            with self.subTest(gateway=gateway):
                suffix_bytes = len(str(gateway.unix_socket_path(Path("/"))).encode())
                accepted = Path("/" + "x" * (98 - suffix_bytes))
                self.assertEqual(len(str(gateway.unix_socket_path(accepted)).encode()), 99)
                with self.assertRaisesRegex(ValueError, "shorter state root"):
                    gateway.unix_socket_path(Path(str(accepted) + "x"))
                with self.assertRaisesRegex(ValueError, "portable limit"):
                    gateway.unix_socket_path(Path("/" + "é" * 45))

    def test_real_failed_location_is_rejected_and_short_durable_location_fits(self):
        for gateway, label in ((ModelBudgetGateway, "model"), (CandidateToolGateway, "candidate")):
            with self.subTest(label=label):
                long_root = Path("/home/rmh.guest/agent-collab-evals/tmp/solo-pilots/first-solo/brokers") / label
                with self.assertRaisesRegex(ValueError, "portable limit"):
                    gateway.unix_socket_path(long_root)
                short_root = Path("/tmp/ace-runs/next-solo/brokers") / label
                planned = gateway.unix_socket_path(short_root)
                issued = gateway.unix_socket_path(short_root, "actual-token-123456789012")
                self.assertEqual(len(str(planned).encode()), len(str(issued).encode()))
                self.assertLess(len(str(issued).encode()), 100)
