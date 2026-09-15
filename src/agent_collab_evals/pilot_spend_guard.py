"""Bind the shared admission journal to the existing live pilot lifecycle."""

from .canonical import digest_value
from .modal_pilot_cost import modal_pilot_cost
from .pilot_spend import PilotSpendEnvelope


class PilotSpendGuard:
    def __init__(self, configuration, envelope: PilotSpendEnvelope, run_id: str):
        self.envelope = envelope
        self.run_id = run_id
        self.configuration_digest = digest_value(configuration.document)
        self.model_limit = configuration.document["model_limit_usd_nanos"]
        self.estimate = modal_pilot_cost(configuration.public_compute.modal_script,
            configuration.repository / "config/compute/modal-pilot-cost-v1.json")
        if any(value != self.estimate["function_timeout_seconds"] for value in configuration.document["phase_seconds"].values()):
            raise ValueError("pilot phase allowances must match the pinned function timeout")
        limits = envelope.snapshot()["provider_limits_usd_nanos"]
        if (type(self.model_limit) is not int or not 0 < self.model_limit <= limits["openrouter"]
            or configuration.document["modal_limit_usd_nanos"] != limits["modal"]):
            raise ValueError("pilot limits differ from the shared envelope")
        self._run_config_digest = None
        self._inventory_root = None
        self.operator_authorization = None

    def evidence(self):
        return {"spend_plan_digest": self.envelope.plan_digest, "configuration_digest": self.configuration_digest,
            "run_id": self.run_id, "estimate": self.estimate}

    def begin(self, run_id, run_config_digest):
        if self.operator_authorization is not None:
            self.operator_authorization.require_current()
        if run_id != self.run_id or self._run_config_digest not in (None, run_config_digest):
            raise ValueError("spend guard run binding differs")
        for provider, amount in (("openrouter", self.model_limit),
            ("modal", self.estimate["shared_overhead_allowance_usd_nanos"])):
            self.envelope.reserve(operation_key=f"pilot:single-attempt:{provider}:base", provider=provider,
                purpose="pilot" if provider == "openrouter" else "overhead",
                request_digest=digest_value({"run_config_digest": run_config_digest, "spend": self.evidence()}),
                maximum_usd_nanos=amount, allow_existing=False)
        self._run_config_digest = run_config_digest

    def authorize(self, stack, request, run_config_digest):
        if self.operator_authorization is not None:
            self.operator_authorization.require_current()
        if self._run_config_digest is None or run_config_digest != self._run_config_digest:
            raise PermissionError("pilot spend guard has no matching run admission")
        if request.maximum_seconds != self.estimate["function_timeout_seconds"]:
            raise ValueError("compute reservation differs from the pinned function timeout")
        if self._inventory_root not in (None, stack.inventory.root):
            raise ValueError("pilot spend guard cannot issue into a second compute inventory")
        self._inventory_root = stack.inventory.root
        receipt = self.envelope.reserve(
            operation_key=f"pilot:{self.run_id}:compute:{request.request_digest[7:]}",
            provider="modal", purpose="pilot", request_digest=digest_value({"request": request.document,
                "run_config_digest": run_config_digest, "estimate": self.estimate}),
            maximum_usd_nanos=self.estimate["per_execution_allowance_usd_nanos"])
        # If issuance fails after the debit, the retained allowance still counts.
        return stack.inventory.authorize(request, approval_reference="pilot-admission:" + digest_value(receipt))

    def snapshot(self):
        return self.envelope.snapshot()
