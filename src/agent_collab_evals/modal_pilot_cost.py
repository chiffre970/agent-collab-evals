"""Offline resource-derived admission estimates, never a provider billing cap."""

import ast
from pathlib import Path

from .canonical import digest_file, parse_json


def modal_pilot_cost(script: Path, cost_profile: Path) -> dict:
    policy = parse_json(cost_profile.read_text())
    if (not isinstance(policy, dict) or set(policy) != {"schema_version", "observed_on", "source_urls",
        "l4_usd_nanos_per_second", "cpu_core_usd_nanos_per_second", "memory_gib_usd_nanos_per_second",
        "cleanup_margin_seconds", "shared_overhead_allowance_usd_nanos", "provider_billing_cap_verified"}
        or policy["schema_version"] != "modal-pilot-cost/v1" or policy["provider_billing_cap_verified"] is not False):
        raise ValueError("Modal pilot cost policy differs")
    for key in ("l4_usd_nanos_per_second", "cpu_core_usd_nanos_per_second", "memory_gib_usd_nanos_per_second",
                "cleanup_margin_seconds", "shared_overhead_allowance_usd_nanos"):
        if type(policy[key]) is not int or policy[key] < 1:
            raise ValueError("Modal pilot cost values must be positive integers")
    tree = ast.parse(script.read_text())
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in {"GPU_RESOURCES", "EVIDENCE_RESOURCES", "FUNCTION_TIMEOUT_SECONDS"}:
                values[node.targets[0].id] = ast.literal_eval(node.value)
    expected = {"GPU_RESOURCES": {"cpu": (4.0, 4.0), "memory": (16384, 16384), "startup_timeout": 600},
        "EVIDENCE_RESOURCES": {"cpu": (1.0, 1.0), "memory": (1024, 1024), "startup_timeout": 60},
        "FUNCTION_TIMEOUT_SECONDS": 1800}
    if values != expected:
        raise ValueError("Modal resource profile changed; review the pilot admission estimate")
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    for name, resource in (("smoke_reference", "GPU_RESOURCES"), ("benchmark_serving_repetition", "GPU_RESOURCES"),
        ("quality_serving_repetition", "GPU_RESOURCES"), ("probe_staged_evidence", "EVIDENCE_RESOURCES"),
        ("persist_evaluator_evidence", "EVIDENCE_RESOURCES"), ("probe_evidence_volume", "EVIDENCE_RESOURCES")):
        decorators = [node for node in functions[name].decorator_list if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "app" and node.func.attr == "function"]
        if len(decorators) != 1:
            raise ValueError("Modal function decorator differs")
        keywords = decorators[0].keywords
        spreads = [node.value for node in keywords if node.arg is None]
        if len(spreads) != 1 or not isinstance(spreads[0], ast.Name) or spreads[0].id != resource:
            raise ValueError("Modal function does not apply its resource profile")
        options = {node.arg: node.value for node in keywords if node.arg is not None}
        if set(options) & {"cpu", "memory", "startup_timeout"}:
            raise ValueError("Modal resource settings must come only from the pinned profile")
        timeout = options["timeout"]
        if resource == "GPU_RESOURCES":
            if not isinstance(timeout, ast.Name) or timeout.id != "FUNCTION_TIMEOUT_SECONDS":
                raise ValueError("Modal GPU timeout differs")
            for key, value in {"gpu": "L4", "min_containers": 0, "max_containers": 1}.items():
                if ast.literal_eval(options[key]) != value:
                    raise ValueError("Modal GPU resources differ")
        elif ast.literal_eval(timeout) != 120:
            raise ValueError("Modal evidence timeout differs")
        if ast.literal_eval(options["retries"]) != 0 or ast.literal_eval(options["single_use_containers"]) is not True:
            raise ValueError("Modal execution reuse or retry policy differs")
    gpu_rate = (policy["l4_usd_nanos_per_second"] + 4 * policy["cpu_core_usd_nanos_per_second"]
        + 16 * policy["memory_gib_usd_nanos_per_second"])
    helper_rate = policy["cpu_core_usd_nanos_per_second"] + policy["memory_gib_usd_nanos_per_second"]
    return {"schema_version": "modal-pilot-admission-estimate/v1", "cost_profile_digest": digest_file(cost_profile),
        "modal_script_digest": digest_file(script), "function_timeout_seconds": 1800,
        "gpu_startup_timeout_seconds": 600,
        "per_execution_allowance_usd_nanos": (1800 + 600 + policy["cleanup_margin_seconds"]) * gpu_rate
            + (120 + 60 + policy["cleanup_margin_seconds"]) * helper_rate,
        "shared_overhead_allowance_usd_nanos": policy["shared_overhead_allowance_usd_nanos"],
        "assumed_evidence_writes_per_execution": 1, "provider_billing_cap_verified": False,
        "limitations": ["CPU limit is soft; timeout and cleanup timing are not exact.",
            "GPU preemption restarts and additional evidence helper dispatches require an outer provider usage cap.",
            "Shared overhead is reserved, not measured; setup, qualification, storage, and cleanup are not free."]}


def modal_paired_cost(script: Path, cost_profile: Path) -> dict:
    """Bound three same-GPU pairs without changing the original resource policy."""
    estimate = modal_pilot_cost(script, cost_profile)
    tree = ast.parse(script.read_text())
    constants = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "PAIRED_FUNCTION_TIMEOUT_SECONDS"}
    if constants != {"PAIRED_FUNCTION_TIMEOUT_SECONDS": 3000}:
        raise ValueError("paired function timeout differs from reviewed allowance")
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
        and node.name == "paired_serving_repetition")
    decorator, = function.decorator_list
    options = {k.arg: k.value for k in decorator.keywords if k.arg is not None}
    spreads = [k.value for k in decorator.keywords if k.arg is None]
    if (len(spreads) != 1 or not isinstance(spreads[0], ast.Name) or spreads[0].id != "GPU_RESOURCES"
        or any(k in options for k in ("cpu", "memory", "startup_timeout", "secrets", "region", "cloud", "nonpreemptible"))
        or not isinstance(options["timeout"], ast.Name) or options["timeout"].id != "PAIRED_FUNCTION_TIMEOUT_SECONDS"
        or any(ast.literal_eval(options[k]) != v for k, v in {"gpu": "L4", "min_containers": 0,
            "max_containers": 1, "retries": 0, "single_use_containers": True,
            "block_network": True, "restrict_modal_access": True}.items())):
        raise ValueError("paired GPU enforcement or resources differ")
    policy = parse_json(cost_profile.read_text())
    rate = policy["l4_usd_nanos_per_second"] + 4 * policy["cpu_core_usd_nanos_per_second"] + 16 * policy["memory_gib_usd_nanos_per_second"]
    return {**estimate, "schema_version": "modal-paired-admission-estimate/v1", "function_timeout_seconds": 3000,
        "per_execution_allowance_usd_nanos": estimate["per_execution_allowance_usd_nanos"] + 1200 * rate,
        "new_gpu_calls": 3, "new_model_calls": 0}
