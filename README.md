# Agent Collaboration Evals

An experiment to test whether open peer collaboration improves the verified performance of agent systems.

## Research question

> With the model, coding-agent runtime, tools, fleet size and aggregate inference-dollar budget held fixed, does peer communication improve verified solutions—and does it outperform the runtime's native hierarchical handoffs?

The comparison is about outcome quality, not how much agents communicate. API cost and completion time are secondary outcomes. Mechanism analysis comes later.

## First experiment

V0 uses a pinned release of [OpenCode](https://github.com/anomalyco/opencode) as the coding-agent runtime. It was selected as a mature, open-source Codex-like system with native subagents, a programmatic SDK and broad model-provider support—not because of any one provider integration. There is no separate multi-agent orchestrator. The initial study will use a pinned DeepSeek API model, but the model and provider are replaceable manifest fields. Every confirmatory run uses the same runtime, model, model settings and base tools.

It compares four conditions:

1. `solo`: one OpenCode agent receives the full organisation-level budget.
2. `native_multiagent`: one OpenCode primary agent may automatically invoke general-purpose OpenCode subagents using the runtime's native task mechanism.
3. `peer_isolated`: a homogeneous fleet of OpenCode primary agents works independently without peer visibility or native subagents.
4. `peer_collab`: the same peer fleet may communicate through the experimental collaboration service. Agents are told how to use the service, but receive no assigned roles or coordination plan.

`peer_collab - peer_isolated` identifies the effect of peer communication. `peer_collab - native_multiagent` tests the proposed approach against conventional hierarchical delegation. The fleet size is a manifest parameter. Initial calibration starts with four available identities; later studies vary this number, including the motivating comparison between many collaborating agents and one agent with the same total spend.

The first campaign asks agents to improve the serving performance of a small open-weight model on a cloud GPU while maintaining output quality, correctness and reliability. A hidden evaluator scores the final artifact on a held-out workload.

An organisation is durable for the life of a campaign: its agent sessions, workspaces and allowed shared state can persist across incoming jobs. The first serving experiment uses one evolving mission, while later campaigns may send a sequence of jobs to the same organisation.

## Experimental priorities

1. Verified solution quality.
2. API cost and cloud-compute cost.
3. Wall-clock time.
4. Reliability and valid-run rate.

All four conditions receive the same starting materials, public feedback, wall-time allowance, cloud-compute allowance and hard aggregate API-dollar cap. Confirmatory experiments use metered API credentials; subscription-backed model access is acceptable for development only because it does not yield a comparable marginal cost.

Emergent coordination is observed, not optimized, in V0. Human reviewers record whether lateral delegation, unsolicited assistance, reuse, challenge, deconfliction or specialization occurred. These observations are descriptive and do not enter the primary score.

OpenCode telemetry supports measurement and reconciliation only. Hard limits are enforced outside the runtime by the budget gateway, sandboxes and scoped compute/research capability brokers.

## Scope

This repository is the minimum system needed to test the collaboration thesis. It is not yet the governed organisational platform described in [the product vision](docs/VISION.md). V0 excludes permission routing, procurement, approvals, organisational search, recommendation, long-term cross-campaign memory and autonomous real-world actions.

## Design documents

- [Architecture](docs/ARCHITECTURE.md)
- [Module contracts](docs/MODULE_CONTRACTS.md)
- [Experimental design](docs/EXPERIMENTAL_DESIGN.md)
- [Campaigns and task families](docs/CAMPAIGNS.md)
- [Long-term product vision](docs/VISION.md)
