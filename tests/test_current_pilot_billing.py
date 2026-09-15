"""Bind the exploratory pilot's rates to retained provider metadata."""

from dataclasses import replace
from decimal import Decimal
import gzip
import json
from pathlib import Path
import unittest

from agent_collab_evals.model_gateway import ModelGatewayProfile
from agent_collab_evals.provider_qualification import ProviderQualificationPlan, QualifiedProviderRoute
from scripts.preflight.provider_route_qualification import _validate_profile_binding


ROOT = Path(__file__).resolve().parents[1]


class CurrentPilotBillingTests(unittest.TestCase):
    def test_historical_and_current_receipts_survive_append_only_index_growth(self):
        old = QualifiedProviderRoute.load(ROOT / "config/provider_qualification/deepseek-v4-flash-deepinfra-development-selection.json", repository_root=ROOT)
        current = QualifiedProviderRoute.load(ROOT / "config/provider_qualification/deepseek-v4-flash-deepinfra-development-selection-20260914.json", repository_root=ROOT)
        self.assertEqual(old.selection.selected_provider, current.selection.selected_provider)
        self.assertNotEqual(old.gateway_profile_digest, current.gateway_profile_digest)

    def test_current_rates_match_retained_endpoint_and_selection(self):
        plan = ProviderQualificationPlan.load(ROOT / "config/provider_qualification/deepseek-v4-flash-development-policy-20260914.json", repository_root=ROOT)
        gateway = ModelGatewayProfile.load(ROOT / "config/gateway_profiles/openrouter-deepinfra-development-20260914.json", repository_root=ROOT)
        self.assertEqual(plan.select().selected_provider, "DeepInfra")
        _validate_profile_binding(plan, "DeepInfra", gateway)
        with gzip.open(ROOT / "evidence/provider_qualification/sources/openrouter-endpoints-20260914T083745Z.json.gz", "rt") as source:
            endpoint = next(item for item in json.load(source)["data"]["endpoints"] if item["tag"] == "deepinfra/fp8")
        for key, field in (("prompt", "uncached_input_usd_nanos_per_million"),
                           ("input_cache_read", "cached_input_usd_nanos_per_million"),
                           ("completion", "output_usd_nanos_per_million")):
            self.assertEqual(getattr(gateway.rate_card, field), Decimal(endpoint["pricing"][key]) * 10**15)
        old = ModelGatewayProfile.load(ROOT / "config/gateway_profiles/openrouter-deepinfra-development-v0.json", repository_root=ROOT)
        self.assertEqual(gateway.model_profile_digest, old.model_profile_digest)
        with self.assertRaisesRegex(ValueError, "rates differ"):
            _validate_profile_binding(plan, "DeepInfra", old)
        changed = replace(gateway, rate_card=replace(gateway.rate_card, output_usd_nanos_per_million=1))
        with self.assertRaisesRegex(ValueError, "rates differ"):
            _validate_profile_binding(plan, "DeepInfra", changed)


if __name__ == "__main__":
    unittest.main()
