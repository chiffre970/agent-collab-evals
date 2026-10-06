"""Shared, exploratory-only environment checks for co-located comparisons."""

from ..canonical import digest_value


ALLOWED_DRIVERS = ("580.95.05", "610.57.04")
GPU_IDENTITY_KEYS = ("name", "memory_mib", "driver_version", "power_limit_watts", "pci_bus_id")


def validate_pair_policy(spec):
    """Reject undeclared drivers, altered specs, and unequal role order."""
    if (set(spec) != {"benchmark", "allowed_drivers", "expected_gpu", "order", "pair_digest"}
        or spec["allowed_drivers"] != list(ALLOWED_DRIVERS)
        or set(spec["expected_gpu"]) != {"name", "memory_mib", "power_limit_watts"}):
        raise RuntimeError("exploratory pair policy differs")
    repetition = spec["benchmark"].get("repetition")
    if type(repetition) is not int or not 1 <= repetition <= 3:
        raise RuntimeError("exploratory pair repetition differs")
    order = ["reference", "candidate"] if repetition % 2 else ["candidate", "reference"]
    if spec["order"] != order:
        raise RuntimeError("exploratory pair order differs")
    if spec["pair_digest"] != digest_value({k: v for k, v in spec.items() if k != "pair_digest"}):
        raise RuntimeError("exploratory pair specification digest differs")


def validate_pair_environment(spec, receipt, measurement):
    """Require one observed GPU environment throughout both fresh servers.

    Co-location comes from the single-use, one-GPU dispatch, not PCI metadata:
    Modal can return an unavailable PCI identifier. All observable identity
    values must nevertheless be present and unchanged. An allowlisted driver
    is not, by itself, qualification for an unpaired historical comparison.
    """
    validate_pair_policy(spec)
    expected_gpu = {"name": "NVIDIA L4", "memory_mib": str(measurement.gpu_memory_mib),
        "power_limit_watts": measurement.gpu_power_limit_watts}
    if spec["expected_gpu"] != expected_gpu:
        raise RuntimeError("paired GPU profile differs from the campaign")
    before = receipt.get("gpu_before", {})
    if before.get("driver_version") not in spec["allowed_drivers"]:
        raise RuntimeError("paired GPU driver is undeclared")
    if (any(before.get(k) != v for k, v in spec["expected_gpu"].items())
        or any(not isinstance(before.get(k), str) or not before[k] for k in GPU_IDENTITY_KEYS)):
        raise RuntimeError("paired GPU type, capacity or identity differs")
    roles = receipt.get("roles", {})
    if set(roles) != {"reference", "candidate"}:
        raise RuntimeError("paired role set differs")
    observations = [receipt.get("gpu_after", {})]
    observations.extend(r.get(k, {}) for r in roles.values() for k in ("gpu_before", "gpu_after"))
    if any(any(g.get(k) != before[k] for k in GPU_IDENTITY_KEYS) for g in observations):
        raise RuntimeError("paired GPU identity changed")
    environment = receipt.get("environment", {})
    expected = {"base_image_ref": "nvidia/cuda:12.9.0-devel-ubuntu22.04",
        "base_image_digest": measurement.base_image_digest,
        "package_set_digest": measurement.resolved_package_digest}
    if (any(environment.get(k) != v for k, v in expected.items())
        or any(r.get("environment") != environment for r in roles.values())):
        raise RuntimeError("paired software environment differs")
    return before["driver_version"]
