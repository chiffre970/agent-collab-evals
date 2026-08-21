# Agent Collaboration Evals

An experiment to test whether open peer collaboration improves the verified performance of agent systems.

## Research question

> With the agent model, coding-agent runtime, base tools and aggregate resource envelope held fixed, does making peer work visible improve verified solutions for otherwise matched fleets—and does it outperform the runtime's native hierarchical handoffs?

The comparison is about outcome quality, not how much agents communicate. API cost and completion time are secondary outcomes. Mechanism analysis comes later.

## First experiment

V0 uses a pinned release of [OpenCode](https://github.com/anomalyco/opencode) as the coding-agent runtime. It was selected as a mature, open-source Codex-like system with native subagents, a programmatic SDK and broad model-provider support—not because of any one provider integration. There is no separate multi-agent orchestrator.

Agent inference uses a dated DeepSeek model behind a provider-neutral `ModelProfile`. Model, transport and serving provider are registered study factors, not environment variables. Development currently uses `deepseek-v4-flash` through a pinned DeepInfra route on OpenRouter because that route passed the required identity, parameter, privacy and latency canary; this is a development choice rather than a permanent product dependency. Before a registered study, a frozen selection rule chooses the lowest projected-cost reputable route that passes the declared capability, privacy, reliability and latency thresholds. The selected route is then fixed across every condition and randomized block with fallbacks disabled. Changing the model, transport or provider creates a separate study version whose result is reported separately rather than pooled.

Development and feasibility work starts with an initial $20 total API allowance. The first registered four-condition study uses `deepseek-v4-flash`. A stronger model is not substituted after results are visible: if the Flash study reaches its preregistered promise trigger, a separately registered `deepseek-v4-pro` study may repeat the complete design. Every attempted registered study remains visible.

It compares four conditions:

1. `solo`: one OpenCode agent receives the full organisation-level budget.
2. `native_multiagent`: one OpenCode primary agent may automatically invoke general-purpose OpenCode subagents using the runtime's native task mechanism.
3. `peer_isolated`: a homogeneous fleet of `N` OpenCode primary agents works independently without peer visibility or native subagents.
4. `peer_collab`: the same `N`-agent peer fleet may communicate through the experimental collaboration service. Agents are told how to use the service, but receive no assigned roles or coordination plan.

`peer_collab - peer_isolated` is the primary causal comparison. The two peer conditions use the same number and profile of identities, session topology, peer-tool schema, candidate policy, fixed per-actor allowances and deterministic actor-slot schedule. Unused GPU slots remain idle, and actor-visible experiments and public evaluations both run within and charge the submitting actor's slots. Only whether peer entries and explicitly published artifacts are actor-private or organisation-visible changes. Fleet size `N` is configurable but frozen within each registered study; the initial pilot starts with four and later studies scale it upward.

`peer_collab - native_multiagent` is a useful but bundled comparison against conventional hierarchical delegation: topology, handoff mechanism and communication structure all differ, so it does not isolate one causal feature. V0 is deliberately a narrow test of information sharing under fixed allocations, not the complete organisational thesis. Dynamic pooling, resource reallocation and routing work to specialists are required follow-up treatments. Later studies also vary fleet size, including the motivating comparison between many collaborating agents and one agent with the same total spend.

The first campaign asks the DeepSeek-powered agents to improve the serving performance of a separate, small open-weight target model on a cheap cloud GPU while maintaining output quality, correctness and reliability. The target model is an artifact being optimized, not the model operating the agents. A hidden evaluator scores the final artifact on a held-out workload.

An organisation is durable for the life of a campaign: its agent sessions, workspaces and allowed shared state can persist across incoming jobs. The first serving experiment uses one evolving mission, while later campaigns may send a sequence of jobs to the same organisation.

## Experimental priorities

1. Verified solution quality.
2. API cost and cloud-compute cost.
3. Wall-clock time.
4. Reliability and valid-run rate.

All four conditions receive the same starting materials, public feedback, wall-time allowance, cloud-compute allowance and hard aggregate API-dollar cap. The peer arms divide relevant allowances equally among actors so resource exhaustion cannot become an incidental communication channel. Public evaluation reserves worst-case GPU time before accepting a submission and consumes the submitting actor's allocation; post-closure hidden evaluation uses a separate evaluator account and is reported as experimental overhead. Confirmatory experiments use metered API credentials; subscription-backed model access is acceptable for development only because it does not yield a comparable marginal cost.

Emergent coordination is observed, not optimized, in V0. Human reviewers record whether lateral delegation, unsolicited assistance, reuse, challenge, deconfliction or specialization occurred. These observations are descriptive and do not enter the primary score.

OpenCode telemetry supports measurement and reconciliation only. Hard limits are enforced outside the runtime by the budget gateway, sandboxes and scoped compute/research capability brokers.

## Scope

This repository is the minimum system needed to test the collaboration thesis. It is not yet the governed organisational platform described in [the product vision](docs/VISION.md). V0 excludes permission routing, procurement, approvals, organisational search, recommendation, long-term cross-campaign memory and autonomous real-world actions.

## Executable slice

The repository now contains the first scenario-shaped vertical slice:

- a dependency-free domain core with narrow harness, event and snapshot ports;
- a deterministic fake harness and atomic local campaign persistence;
- a pinned stock-OpenCode runtime adapter with per-actor state/workspace
  isolation, out-of-process events and durable session resume;
- a pinned session-bound MCP peer-tool path whose four-actor private and shared
  modes use identical OpenCode surfaces and differ only in backend visibility;
- durable SQLite collaboration, owner-private storage and server-authorized
  artifact publication adapters with complete local audit exports;
- a durable fixed-point model budget account and session-bound
  OpenAI-compatible gateway, proved end to end with stock OpenCode and a
  deterministic upstream;
- a dependency-free OpenRouter streaming adapter that retains both the raw
  stream and its correlated generation-metadata receipt for gateway-side
  attestation and billing;
- a fail-closed `model_serving_v0` campaign pack pinned to one Qwen revision;
- a pure nine-point public benchmark plan for vLLM 0.21.0;
- a pinned warm-steady-state measurement protocol, strict vLLM result
  normalizer and atomic evaluator-private raw-result store;
- a transitively pinned calibration scorer with bucket-specific TTFT/TPOT
  goodput, equal-weight cross-bucket normalization and a conservative
  three-repetition improvement bound;
- a frozen served-generation quality policy with paired aggregate and
  per-family case-cluster uncertainty gates;
- evaluator-owned durable raw and normalized evidence for the performance and
  quality calibration series; and
- a private Modal vLLM adapter that can run either an API canary or one
  isolated nine-point reference/candidate repetition on one L4.

The serving evaluator design also incorporates the documented lesson from
Hugging Face's Fast Gemma Challenge: teacher-forced perplexity alone is not
sufficient evidence of useful generated-answer quality. This informs an
architecture-neutral outcome score; it does not prohibit candidates from
changing model or serving internals. See [the evaluator research note](docs/GEMMA_CHALLENGE_EVALUATOR_LESSONS.md).

Run the local slice without model or GPU spend:

```bash
collab-evals validate-scenario
collab-evals fake-solo
python -m unittest discover -s tests -v
```

This is calibration infrastructure, not a completed experimental platform.
ADR 0001's stock-runtime, matched peer-tool and minimal collaboration,
publication and storage gates now pass, as does the budget gateway's local
enforcement and transport proof. Live route/billing qualification, sandbox and capability
brokers, submission selection, public and hidden evaluators, and registered
four-condition execution remain explicit later gates. See
[implementation status](docs/IMPLEMENTATION_STATUS.md).

## Design documents

- [Local setup](docs/SETUP.md)
- [Implementation status](docs/IMPLEMENTATION_STATUS.md)
- [Agent-inference provider calibration](docs/calibration/AGENT_INFERENCE_PROVIDER.md)
- [OpenCode runtime spike](docs/calibration/OPENCODE_RUNTIME_V0.md)
- [Model budget gateway proof](docs/calibration/MODEL_GATEWAY_V0.md)
- [Fast Gemma evaluator lessons](docs/GEMMA_CHALLENGE_EVALUATOR_LESSONS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Module contracts](docs/MODULE_CONTRACTS.md)
- [Experimental design](docs/EXPERIMENTAL_DESIGN.md)
- [Campaigns and task families](docs/CAMPAIGNS.md)
- [Long-term product vision](docs/VISION.md)
- [ADR 0001: runtime and collaboration substrate](docs/decisions/0001-agent-runtime-and-collaboration-substrate.md)
