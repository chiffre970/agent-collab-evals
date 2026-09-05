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
- a durable SQLite delivery outbox that records complete fan-out before runtime
  calls, retains runtime-profile-bound acknowledgements and reconciles exact
  jobs, sessions and receipts before campaign closure;
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
- independently pinned synthetic and timestamped development billing catalogs,
  with one bounded live DeepInfra canary through the complete gateway;
- retained raw OpenRouter endpoint and ZDR catalogs, a deterministic candidate
  extractor and an evidence-complete three-probe qualification record whose
  stream and metadata receipts are resolved, digest-checked and independently
  replayed from the repository;
- condition-matched cache isolation through an endpoint with no implicit cache,
  an explicit OpenRouter response-cache denial and repeated-request receipts
  reporting zero cached tokens;
- a modular macOS development adapter that blocks nonloopback outbound traffic,
  exposes no provider credentials and binds its profile digest into runtime
  snapshots; it permits every loopback service and does not yet enforce
  filesystem or process-resource limits;
- a fail-closed OCI sandbox implementation candidate that accepts a
  server-derived per-session launch context and specifies no network, an exact
  Unix-socket broker path, read-only runtime mounts, actor-only writable mounts,
  no ambient secrets, and fixed CPU, memory, process, and lifetime limits. It
  cannot execute until its image and engine are pinned and its live
  conformance evidence is retained;
- per-token Unix-socket transports for the model and peer-tool gateways, plus a
  small session launcher. Each listener remains bound to its gateway's existing
  token and authority, rejects tokens issued for other sockets, and is removed
  on revocation; the launcher provides only the two fixed loopback endpoints
  inside a networkless container;
- a zero-spend real-adapter rehearsal that exercises all four condition
  surfaces through stock OpenCode: denied coordination in solo, native task
  handoff, actor-private peer publication/readback, and shared cross-actor
  publication/readback. It uses a deterministic local model and remains
  explicitly unscoreable;
- a mandatory close-time budget validity gate that prevents post-stream
  identity, usage, receipt, forfeiture or overrun defects from producing a
  scoreable campaign result, using a manifest-pinned immutable budget plan and
  an independent raw-provider-receipt verifier rather than trusting the
  mutable ledger as its own authority;
- a fail-closed `model_serving_v0` campaign pack pinned to one Qwen revision;
- a durable no-spend candidate lifecycle with session-derived ownership,
  per-actor submission and GPU-second allocations, delayed public-result
  release, evaluator-owned receipts, persisted reference-aware neutral
  selection, separate hidden evaluation for every winner, restart recovery and
  final artifact sealing;
- race-resistant single-file workspace snapshot and materialization paths that
  derive the root from the authenticated session and reject traversal, symlink
  escape and overwrite;
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
  isolated nine-point reference/candidate repetition on one L4;
- a declarative candidate contract whose typed, allowlisted vLLM settings are
  converted to argv by the evaluator. Candidates cannot provide an executable,
  model path, network destination, secret, or evidence path;
- a provider-neutral, durable compute-execution state machine that records
  dispatch intent before the external call, fails closed on ambiguous dispatch,
  resolves terminal evidence independently and reconciles every execution at
  campaign close. Its authority is reconstructed from a frozen, digest-bound
  run manifest after restart;
- a durable compute-spend authorization service that binds an approval to one
  run manifest, transport profile and exact request, then atomically consumes
  it before dispatch; and
- a pinned development transport that composes that state machine with the
  existing Modal/vLLM adapter and a visible-only candidate evaluator. Its model,
  campaign, runtime, environment and evidence Volume are profile inputs rather
  than environment variables. Scored GPU functions have no secret, block
  external networking, mount model data read-only and hand a bounded evidence
  bundle to a separate trusted persistence function; and
- a four-condition structural study runner that pins its source tree,
  composition candidate, randomized block plan and explicit no-model/no-compute
  authorities. It resolves every run, exercises campaign start, delivery,
  reconciliation and closure, retains canonical per-run evidence, and verifies
  a combined audit without authorizing spend, treatment claims or scoring.
- an executable solo real-adapter rehearsal that runs stock OpenCode through
  the actual macOS sandbox, session gateway, SQLite budget ledger, delivery
  outbox and close-time gates. Its upstream is deterministic and in-process,
  compute is frozen off, no credentials enter the child, and the retained
  audit is independently replayed; it cannot authorize scoring or external
  model/GPU spend.

The serving evaluator design also incorporates the documented lesson from
Hugging Face's Fast Gemma Challenge: teacher-forced perplexity alone is not
sufficient evidence of useful generated-answer quality. This informs an
architecture-neutral outcome score; it does not prohibit candidates from
changing model or serving internals. See [the evaluator research note](docs/GEMMA_CHALLENGE_EVALUATOR_LESSONS.md).

Run the local slice without model or GPU spend:

```bash
collab-evals validate-scenario
collab-evals fake-solo
collab-evals fake-candidate-lifecycle
collab-evals rehearse-study
collab-evals rehearse-solo-adapters
python -m unittest discover -s tests -v
```

This is calibration infrastructure, not a completed experimental platform.
ADR 0001's stock-runtime, matched peer-tool and minimal collaboration,
publication and storage gates now pass, as do the development provider-route,
cache-isolation, sandbox, budget-reconciliation and fake candidate-lifecycle
proofs. The hidden correctness, quality and performance adapters now pass both
their no-spend durable contracts and bounded live Modal conformance. The stock
reference exposed a hidden-performance SLO calibration issue. The pinned
three-measurement calibration is complete and its deterministic proposal is
retained, but not yet promoted to a registered profile. The source calibration
bundle is retired and a fresh hidden seed is required after policy freeze.
The no-spend structural four-condition rehearsal now passes, and the real-adapter
composition exercises all four treatment surfaces against a deterministic local
model. Only the latter exercises native and peer tools; it still uses the
partial development sandbox. Registered
enforcement, compute and evaluator promotion, research brokerage, the scored
composition root, native fleet admission, and the agent-facing candidate and
evaluation tools remain explicit later gates. See
[implementation status](docs/IMPLEMENTATION_STATUS.md).

## Design documents

- [Local setup](docs/SETUP.md)
- [Implementation status](docs/IMPLEMENTATION_STATUS.md)
- [Agent-inference provider calibration](docs/calibration/AGENT_INFERENCE_PROVIDER.md)
- [OpenCode runtime spike](docs/calibration/OPENCODE_RUNTIME_V0.md)
- [Model budget gateway proof](docs/calibration/MODEL_GATEWAY_V0.md)
- [Process sandbox proof](docs/calibration/SANDBOX_V0.md)
- [Fast Gemma evaluator lessons](docs/GEMMA_CHALLENGE_EVALUATOR_LESSONS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Module contracts](docs/MODULE_CONTRACTS.md)
- [Experimental design](docs/EXPERIMENTAL_DESIGN.md)
- [Campaigns and task families](docs/CAMPAIGNS.md)
- [Long-term product vision](docs/VISION.md)
- [ADR 0001: runtime and collaboration substrate](docs/decisions/0001-agent-runtime-and-collaboration-substrate.md)
- [ADR 0002: registered agent sandbox](docs/decisions/0002-registered-agent-sandbox.md)
