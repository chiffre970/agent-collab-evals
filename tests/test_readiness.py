from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from agent_collab_evals.readiness import readiness_report


class ReadinessTests(unittest.TestCase):
    def test_environment_inventory_cannot_authorize_a_study(self):
        repository = Path(__file__).resolve().parents[1]
        with patch("agent_collab_evals.readiness.shutil.which", return_value=None):
            report = readiness_report(repository, repository / "config/studies/model-serving-flash-v0.registration-candidate.json")
        self.assertFalse(report["execution_authorized"])
        self.assertFalse(report["engine_daemon_checked"])
        self.assertIn("container_engine_not_installed", report["deployment_gaps"])
        self.assertIn("registered_budget_plan", report["registration_gaps"])
        self.assertIn("candidate_oci_relay_and_matched_peer_wiring", report["runtime_qualification_gaps"])
        self.assertIn("registered_candidate_recovery_qualification", report["runtime_qualification_gaps"])


if __name__ == "__main__":
    unittest.main()
