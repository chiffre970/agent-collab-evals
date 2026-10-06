"""No-authority composition and costing for matched exploratory peer runs."""

from pathlib import Path
from dataclasses import replace

from .adapters.modal_paired_serving import ModalPairedServingEvidence, ModalPairedServingTransport
from .adapters.paired_serving_evaluator import PairedServingEvaluator
from .adapters.split_scope_evaluator import EvaluationLaneProfile, RegisteredEvaluationProfile, SplitScopeServingEvaluator
from .adapters.sqlite_compute_routes import ComputeRouteAdapter, SqliteComputeRouteInventory
from .campaigns.serving_paired_performance import FUNCTION_SECONDS
from .campaigns.serving_paired_profile import PairedServingProfile
from .canonical import canonical_json_bytes, digest_file, digest_value, parse_json
from .domain import CoordinationCondition
from .artifacts import ArtifactRef
from .evaluation import EvaluationReservation, EvaluationReservationStatus, EvaluationScope
from .modal_pilot_cost import modal_paired_cost
from .pilot_evidence import retain_document
from .solo_live_configuration import LivePilotConfiguration
from .solo_pilot_stack import SoloPilotStack


def build_paired_peer_stack(root, configuration, run_id, hidden, policy):
    """Build the actual paired transports, evaluators, planners, and inventory.

    This factory neither grants compute nor enables the live command. The peer
    lifecycle uses the existing candidate services and runner unchanged.
    """
    root = Path(root)
    paired = PairedServingProfile.create(configuration.repository, configuration.campaign, hidden, policy)
    transport_digest = ModalPairedServingTransport.profile_digest_for(paired.digest, configuration.modal_cli)
    evidence_digest = ModalPairedServingEvidence.profile_digest_for(paired.digest)
    adapter = ComputeRouteAdapter(transport_digest, evidence_digest,
        lambda route, spend: ModalPairedServingTransport(paired, route, configuration.modal_cli, spend),
        lambda route: ModalPairedServingEvidence(paired, route, transport_digest))
    adapters = {"public": adapter, "hidden": adapter}
    inventory = SqliteComputeRouteInventory(root / "compute", run_id, adapters)
    public = PairedServingEvaluator(root / "public", paired, inventory.backend("public"), EvaluationScope.VISIBLE)
    hidden_evaluator = PairedServingEvaluator(root / "hidden", paired, inventory.backend("hidden"), EvaluationScope.HIDDEN)
    authority = digest_value({"configuration": configuration.document, "paired_profile": paired.digest})
    lanes = {scope: EvaluationLaneProfile(scope, paired.evaluator_digest(scope), adapter.backend_profile_digest,
        configuration.campaign.transitive_digests["public_profile"] if scope is EvaluationScope.VISIBLE else hidden.manifest_digest,
        "paired-" + scope.value, digest_value({"scope": scope.value, "authority": authority}), "paired-" + scope.value)
        for scope in EvaluationScope}
    split = RegisteredEvaluationProfile("exploratory-paired-peer", configuration.campaign.manifest_digest,
        authority, lanes[EvaluationScope.VISIBLE], lanes[EvaluationScope.HIDDEN])
    evaluator = SplitScopeServingEvaluator(root / "split.sqlite3", split, public, hidden_evaluator)
    document = {"execution_mode": "live_composition_only", "execution_authorized": False, "scoreable": False,
        "paired_profile_digest": paired.digest, "compute_backend_digest": adapter.backend_profile_digest,
        "visible_profile_digest": public.profile_digest, "hidden_profile_digest": hidden_evaluator.profile_digest,
        "evaluation_profile": split, "quality_policy": policy,
        "reserved_seconds_per_pair": FUNCTION_SECONDS, "hidden_pair_count": 7}
    retain_document(root / "profiles.json", document)
    stack = SoloPilotStack(inventory, public, hidden_evaluator, evaluator, hidden_evaluator.reserved_seconds,
        {public.profile_digest: "public", hidden_evaluator.profile_digest: "hidden"}, document)
    return stack, adapters


def paired_peer_cost(configuration, organisation_size):
    if type(organisation_size) is not int or not 2 <= organisation_size <= 8:
        raise ValueError("paired peer pilot requires two to eight actors")
    estimate = modal_paired_cost(configuration.public_compute.modal_script,
        configuration.repository / "config/compute/modal-pilot-cost-v1.json")
    # The wrapper must use precisely the already reviewed resource allocation.
    import ast
    wrapper = configuration.campaign.root / "reference/modal_paired.py"
    tree = ast.parse(wrapper.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "paired_serving")
    decorator, = function.decorator_list
    options = {k.arg: k.value for k in decorator.keywords if k.arg is not None}
    spreads = [k.value for k in decorator.keywords if k.arg is None]
    constants = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "PAIRED_FUNCTION_TIMEOUT_SECONDS"}
    if (constants != {"PAIRED_FUNCTION_TIMEOUT_SECONDS": FUNCTION_SECONDS}
        or len(spreads) != 1 or not isinstance(spreads[0], ast.Attribute)
        or ast.unparse(spreads[0]) != "base.GPU_RESOURCES"
        or any(key in options for key in ("secrets", "cpu", "memory", "startup_timeout", "region", "cloud", "nonpreemptible"))
        or ast.unparse(options["timeout"]) != "PAIRED_FUNCTION_TIMEOUT_SECONDS"
        or any(ast.literal_eval(options[key]) != value for key, value in {
            "gpu": "L4", "max_containers": 1, "min_containers": 0, "retries": 0,
            "block_network": True, "restrict_modal_access": True, "single_use_containers": True}.items())
        or "read_only=True" not in ast.unparse(options["volumes"])):
        raise ValueError("paired Modal resources/enforcement differ from the reviewed cost")
    executions = 1 + organisation_size + 7
    return {**{k: v for k, v in estimate.items() if k != "new_model_calls"}, "schema_version": "paired-peer-admission-proposal/v1",
        "paired_script_digest": digest_file(wrapper), "organisation_size": organisation_size,
        "new_gpu_calls": executions, "public_reference_calls": 1, "actor_public_calls": organisation_size,
        "hidden_calls": 7, "reserved_function_seconds": executions * FUNCTION_SECONDS,
        "per_arm_modal_allowance_usd_nanos": estimate["shared_overhead_allowance_usd_nanos"]
            + executions * estimate["per_execution_allowance_usd_nanos"],
        "execution_authorized": False, "provider_billing_cap_verified": False,
        "pricing_note": "Uses the pinned historical rate snapshot; reverify current rates before approval."}


def prepare_peer_offline_requests(stack, configuration, organisation_size):
    """Exercise the real planners with stock bytes, without dispatch or grants."""
    reference = configuration.campaign.reference_candidate_path.read_bytes()
    requests = [("public", (stack.public.prepare_visible_request(reference, None, "visible:reference"),))]
    artifact = ArtifactRef("artifact-" + digest_value({"stock": True})[7:39])
    for index in range(organisation_size):
        reservation = EvaluationReservation("evaluation-" + digest_value({"actor": index})[7:39],
            f"visible:offline-peer:{index}", "offline-peer-check", f"offline-peer-check:actor:{index}",
            artifact, EvaluationScope.VISIBLE, FUNCTION_SECONDS, EvaluationReservationStatus.RESERVED)
        requests.append(("public", (stack.public.prepare_visible_request(reference, reservation, reservation.reservation_key),)))
    reservation = EvaluationReservation("evaluation-" + digest_value({"hidden": True})[7:39],
        "hidden:offline-peer", "offline-peer-check", None, artifact, EvaluationScope.HIDDEN,
        stack.hidden_seconds, EvaluationReservationStatus.RESERVED)
    requests.append(("hidden", stack.hidden.prepare_hidden_requests(reference, reservation, reservation.reservation_key)))
    return tuple(requests)


def prepare_paired_stock_control(root, configuration, run_id, hidden, policy):
    """Freeze seven stock-versus-stock requests; issue no compute authority."""
    root = Path(root).resolve()
    stack, _ = build_paired_peer_stack(root / "evaluation", configuration, run_id, hidden, policy)
    reference = configuration.campaign.reference_candidate_path.read_bytes()
    reservation = EvaluationReservation("evaluation-" + digest_value({"stock_control": run_id,
        "profile": stack.hidden.profile_digest})[7:39], "hidden:stock-control", run_id, None,
        ArtifactRef("artifact-" + digest_value({"stock": digest_file(configuration.campaign.reference_candidate_path)})[7:39]),
        EvaluationScope.HIDDEN, stack.hidden_seconds, EvaluationReservationStatus.RESERVED)
    requests = stack.hidden.prepare_hidden_requests(reference, reservation, "hidden:stock-control")
    stack.inventory.register("hidden", requests)
    seal = stack.inventory.seal()
    # Construct/reconcile original authority stores now, still without grants.
    sources = stack.inventory.sources()
    from .adapters.sqlite_compute_spend import SqliteComputeSpendAuthorizationService
    for source in sources:
        spend = SqliteComputeSpendAuthorizationService(source.manifest.path.parent / "spend.sqlite3", source.manifest)
        if (any(spend.request_status(request, source.manifest.transport_profile_digest) is not None
            for request in source.manifest.requests())
            or any((source.manifest.path.parent / "dispatch").glob("*.json"))):
            raise PermissionError("stock preparation cannot reuse an authorized or dispatched control")
    estimate = modal_paired_cost(configuration.public_compute.modal_script,
        configuration.repository / "config/compute/modal-pilot-cost-v1.json")
    # Validate the wrapper's actual resources, not only the legacy base script.
    paired_peer_cost(configuration, 2)
    document = {"schema_version": "paired-stock-control-preparation/v1", "run_id": run_id,
        "execution_authorized": False, "scoreable": False, "new_model_calls": 0, "planned_gpu_calls": len(requests),
        "candidate_digest": digest_file(configuration.campaign.reference_candidate_path),
        "hidden_manifest_digest": hidden.manifest_digest, "evaluator_profile_digest": stack.hidden.profile_digest,
        "inventory_seal_digest": seal, "reservation": reservation,
        "compute_manifests": [{"file": str(s.manifest.path), "digest": s.manifest.manifest_digest} for s in sources],
        "requests": [r.document for r in requests],
        "modal_allowance_usd_nanos": estimate["shared_overhead_allowance_usd_nanos"]
            + len(requests) * estimate["per_execution_allowance_usd_nanos"],
        "cost_profile_digest": estimate["cost_profile_digest"], "configuration_digest": digest_value(configuration.document),
        "authorization_count": 0, "dispatch_count": 0,
        "remaining_gates": ["clean committed deployment and pinned remote wrapper conformance",
            "fresh expiring one-use stock-control authority tied to the cumulative currency journal",
            "provider gross-usage cap and current billing/rate checks"]}
    retain_document(root / "stock-control.json", document)
    return parse_json(canonical_json_bytes(document).decode())


def check_peer_live_configuration(path, repository, state_root):
    """Freeze a costed request composition without spending or reading keys."""
    document = parse_json(Path(path).read_text())
    fields = {"schema_version", "execution_authorized", "base_configuration", "condition",
        "organisation_size", "model_limit_usd_nanos", "measurement_policy"}
    if (not isinstance(document, dict) or set(document) != fields
        or document["schema_version"] != "peer-live-composition/v1" or document["execution_authorized"] is not False
        or document["measurement_policy"] != "co_located_reference_candidate_declared_drivers_v1"):
        raise ValueError("peer live composition differs; execution remains disabled")
    if CoordinationCondition(document["condition"]) not in {
        CoordinationCondition.PEER_ISOLATED, CoordinationCondition.PEER_COLLAB}:
        raise ValueError("peer live composition requires a peer condition")
    actors, model_limit = document["organisation_size"], document["model_limit_usd_nanos"]
    if type(actors) is not int or not 2 <= actors <= 8 or type(model_limit) is not int or model_limit < actors or model_limit % actors:
        raise ValueError("peer model budget must partition equally")
    repository = Path(repository).resolve(strict=True)
    base = (repository / document["base_configuration"]).resolve(strict=True)
    base.relative_to(repository)
    configuration = LivePilotConfiguration.load(base, repository)
    # Use the peer budgets in the composition itself, not just its report. A
    # legacy solo authorization/spend guard cannot admit this new schema or
    # its 3000-second pairs. No parent allowance is inherited or reset.
    configuration = replace(configuration, document={**configuration.document,
        "schema_version": "peer-live-configuration-candidate/v1", "condition": document["condition"],
        "organisation_size": actors, "model_limit_usd_nanos": model_limit,
        "phase_seconds": {key: FUNCTION_SECONDS for key in configuration.document["phase_seconds"]},
        "measurement_policy": document["measurement_policy"]})
    hidden = configuration.hidden_bundle()
    root = Path(state_root)
    stack, _ = build_paired_peer_stack(root, configuration, "offline-peer-check", hidden, configuration.campaign.quality_policy())
    requests = prepare_peer_offline_requests(stack, configuration, actors)
    cost = paired_peer_cost(configuration, actors)
    required_lifetime = (actors + 7) * (FUNCTION_SECONDS + 1200) + 2 * configuration.document["runtime_timeout_seconds"] + 600
    result = {"schema_version": "peer-live-composition-check/v1", "ok": True, "execution_authorized": False,
        "scoreable": False, "configuration_digest": digest_value(document), "base_configuration_digest": digest_file(base),
        "condition": document["condition"], "organisation_size": actors,
        "model": configuration.gateway.requested_model, "provider": configuration.gateway.expected_provider,
        "model_limit_usd_nanos": model_limit, "per_actor_model_limit_usd_nanos": model_limit // actors,
        "per_actor_public_seconds": FUNCTION_SECONDS, "hidden_seconds": stack.hidden_seconds,
        "planned_compute_executions": sum(len(group) for _, group in requests),
        "request_inventory_digest": digest_value([r.document for _, group in requests for r in group]),
        "evaluation_profile_digest": stack.evaluator.profile_digest, "hidden_manifest_digest": hidden.manifest_digest,
        "required_sandbox_lifetime_seconds": required_lifetime, "modal_proposal": cost,
        "remaining_gates": ["bounded live wrapper conformance and current stock control",
            "fresh request-bound peer operator authority and cumulative admission journal",
            "review current provider routing/prices and workspace gross-usage cap",
            "OCI lifetime profile covering the paired evaluation schedule"]}
    retain_document(root / "check.json", result)
    return result
