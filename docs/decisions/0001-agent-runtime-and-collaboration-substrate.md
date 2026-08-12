# ADR 0001: Agent runtime and collaboration substrate

- **Status:** Accepted for V0
- **Decision date:** 2026-08-12

## Context

The first study needs a capable coding-agent loop, native hierarchical handoffs, durable sessions, complete-enough instrumentation and a collaboration treatment whose visibility and audit semantics we can control exactly. Owning a general agent runtime would divert effort from task environments, evaluation and causal comparison. Adopting a feature-rich collaboration product as the experimental core could make the treatment harder to specify and reproduce.

## Decision

V0 will pin a stock OpenCode release and integrate it through the generic `HarnessRuntime` port. An external controller uses its SDK, a pinned plugin captures available session and tool events, and all model traffic passes through the experiment-owned budget gateway. OpenCode telemetry is observational; the gateway, capability brokers, authorization services and sandbox remain the enforcement boundaries.

We will not fork OpenCode initially. A fork is justified only if an instrumentation spike demonstrates that a required, predeclared event or control cannot be implemented through the SDK, plugin, provider gateway or external sandbox. Any fork must remain an adapter detail and must be pinned by commit.

V0 will implement a minimal collaboration backend directly behind the generic `CollaborationBackend` port. This gives the experiment exact actor-private versus organisation-shared authorization, immutable audit events, pagination and notifications without importing unrelated orchestration policy. A thin application service coordinates artifact publication: storage issues an unforgeable campaign- and audience-scoped grant only after verifying ownership, and collaboration carries that grant in the explicit publication. Neither adapter calls or discovers the other. The service must not assign roles, plan work, merge outputs or optimize coordination.

Hugging Face Agent Collabs is not the V0 runtime, collaboration dependency or evaluator. It remains a candidate future `CollaborationBackend` adapter and an external replication target after the minimal treatment is validated. Adding it must not change campaign, evaluator, storage or harness contracts.

## Consequences

- Engineering effort concentrates on the experimental boundary and outcome evaluation rather than another agent loop.
- OpenCode, the collaboration substrate, model provider and task family can be replaced independently.
- The initial collaboration treatment is deliberately smaller than the long-term product vision.
- We accept the maintenance cost of a thin service and conformance suite in exchange for precise isolation and audit behavior.
- We retain a clear path to compare the result with HF Agent Collabs without making it the source of truth for scoring.
