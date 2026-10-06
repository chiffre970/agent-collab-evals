"""Staged candidate evaluation with externally approved compute requests."""

from dataclasses import dataclass
from typing import Callable

from .adapters.sqlite_compute_routes import SqliteComputeRouteInventory
from .candidate_services import CandidateServices
from .compute_backend import ComputeExecutionRequest
from .evaluation import HiddenEvaluationInput, VisibleEvaluationInput
from .solo_evaluation_closure import CandidateEvaluationClosure
from .solo_evaluation_handoff import CandidateEvaluationHandoff


@dataclass(frozen=True)
class PilotComputeRoute:
    adapter_id: str
    requests: tuple[ComputeExecutionRequest, ...]


class CandidatePilotRunner:
    """Stage and collect actor-private candidates without spending approval.

    The composition supplies runtime, evaluator factories, and pure exact-request
    planners. Agent delivery and feedback use CampaignController's durable outbox.
    Install this runner as that controller's compute-reconciliation gate. Public
    reference compute must already be retained in the same route inventory.
    """

    def __init__(self, services: CandidateServices, inventory: SqliteComputeRouteInventory,
                 public_plan: Callable[[VisibleEvaluationInput], tuple[PilotComputeRoute, ...]],
                 hidden_plan: Callable[[HiddenEvaluationInput], tuple[PilotComputeRoute, ...]],
                 *, hidden_seconds: int) -> None:
        if type(hidden_seconds) is not int or not 0 < hidden_seconds <= services.plan.hidden_evaluator_limit_seconds:
            raise ValueError("pilot hidden allowance exceeds the compute plan")
        self.services, self.inventory = services, inventory
        self._public_plan, self._hidden_plan = public_plan, hidden_plan
        self._hidden_seconds = hidden_seconds
        self._handoff = CandidateEvaluationHandoff(services.submissions, services.compute,
            services.plan.campaign_run_id, tuple(services.plan.actor_limits))

    def prepare_public(self) -> tuple[ComputeExecutionRequest, ...]:
        return self._retain(tuple(route for item in self._handoff.prepare()
                                  for route in self._public_plan(item)))

    def collect_public(self):
        self.inventory.require_authorized(self.prepare_public())
        return self._handoff.evaluate()

    def prepare_hidden(self) -> tuple[ComputeExecutionRequest, ...]:
        services = self.services
        selection = services.submissions.select(services.submissions.close(services.plan.campaign_run_id, "optimize-serving"))
        prepared = services.submissions.prepare_hidden_evaluation(selection.receipt, reserved_seconds=self._hidden_seconds)
        requests = self._retain(self._hidden_plan(prepared))
        self.inventory.seal()
        return requests

    def collect_hidden(self):
        self.inventory.require_authorized(self.prepare_hidden())
        services = self.services
        selection = services.submissions.select(services.submissions.close(services.plan.campaign_run_id, "optimize-serving"))
        return services.submissions.evaluate_hidden(selection.receipt, reserved_seconds=self._hidden_seconds)

    def reconcile(self, campaign_run_id):
        return CandidateEvaluationClosure(self.services.submissions, self.services.compute,
            self.services.plan, self.inventory.sources()).reconcile(campaign_run_id)

    def _retain(self, routes):
        if not routes or any(not route.requests for route in routes):
            raise ValueError("pilot phase planner returned no compute requests")
        requests = tuple(request for route in routes for request in route.requests)
        if len({request.request_digest for request in requests}) != len(requests):
            raise ValueError("pilot phase planner returned duplicate requests")
        for route in routes:
            self.inventory.register(route.adapter_id, route.requests)
        return requests


SoloPilotRunner = CandidatePilotRunner
