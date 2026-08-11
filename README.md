# Agent Collaboration Evals

## Thesis

Large agent fleets are unlikely to remain collections of isolated workers or neatly predefined delegation trees. They may discover shared problems, exchange information, divide work, form temporary teams and coordinate through whatever shared surfaces are available.

A recent OpenAI/Hugging Face agent incident provided a striking example: agents appeared to organise through unsanctioned channels such as metadata and comments, without that behaviour being explicitly elicited. If similar behaviour becomes common at larger scales, organisations will need more than conventional orchestration. They will need a sanctioned environment in which agents can self-organise while their consequential actions remain observable and controlled.

> The core idea is an agent-native work network where humans and agents can collaborate and self-organise, while actions remain attributable, permissioned and bounded.

## The proposed system

“Slack or Jira for agents” is useful shorthand, but the intended system is broader: a shared work graph designed around agent primitives rather than human project-management workflows.

It would allow agents and humans to:

- Discover relevant work, prior results, available resources and agents with useful experience, tools or permissions.
- Communicate, propose work, share hypotheses and artifacts, request help and form temporary teams.
- Delegate and combine work in graph-shaped patterns, including fan-out, fan-in, peer collaboration and bidirectional human-agent requests.
- Correlate independent needs into shared work, avoiding ten agents repeating the same research or initiating ten actions when one would solve the common problem.
- Preserve provenance: who proposed work, what evidence was used, which constraints applied, what action followed and how the result was verified.

The system should separate permissive coordination from controlled execution. Agents could discuss and organise freely, while privileged actions flow through constrained services with explicit tools, policies, budgets and approval rules. A procurement agent, for example, could receive requests from many other agents, combine duplicate needs, make an authorised purchase, escalate to a human or decline the request. Lower-permission agents could similarly request capabilities from specialised agents without every agent receiving “god mode” access.

This is therefore a productivity thesis as much as a security or governance thesis. Better discovery, specialisation, reuse and coordination could increase useful parallel work while reducing duplicated research, wasted tokens, unnecessary actions and human supervision. The resulting work graph could also make failures easier to diagnose by revealing common dependencies across otherwise separate agent activities.

The platform could be exposed to agents through an API, MCP server and skill instructions, with a dashboard for human participation, oversight and intervention. It should encourage use by being the most useful place to find work, knowledge and collaborators—not by forcing agents into one centrally prescribed organisational structure. Successful coordination patterns should be able to emerge, be evaluated and eventually be superseded by better ones.

## Purpose of this repository

This repository does **not** attempt to build the full platform yet. Its purpose is to test the premise on which that platform depends:

> Given the same agents, task and aggregate budget, does access to an open collaboration environment produce better verified outcomes than working alone or in isolation?

The benchmark will use real, multi-step problems with independently executable scoring rather than synthetic collaboration scenarios. Initial experiments will ask agent fleets to improve a small open-source model on an M4 Pro MacBook—for example, improving held-out task quality while satisfying constraints on inference speed, memory and artifact size.

Experiments will compare:

1. A single agent.
2. Multiple isolated agents.
3. The same agents with an open collaboration workspace.
4. Later, richer collaboration features such as search, matching, deduplication and structured task claims.

All conditions will receive the same aggregate agent-token budget, experiment-compute allowance, tools, starting information and evaluation rules. Performance will be measured as a budget-response curve, including verified outcome quality, tokens and compute used, duplicated work, reuse of discoveries, coordination overhead and time to improvement.

The immediate goal is evidence: determine whether open agent collaboration produces an accretive performance benefit, identify the tasks and conditions under which it helps or hurts, and learn which platform capabilities are actually worth building.
