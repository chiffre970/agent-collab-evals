# Implementation status

The current code is a scenario-shaped calibration slice. It establishes the
smallest useful seams before adding the runtime and collaboration machinery.

## Implemented

- Core campaign lifecycle types and narrow `HarnessRuntime`, `EventSink` and
  `CampaignSnapshotStore` ports have no dependency on OpenCode, Modal or the
  task family.
- The fake harness preserves actor, session and delivered-job identity across
  a serialized process-style resume. Local snapshots use atomic replacement;
  local events are append-only, fsynced and sequence checked.
- `model_serving_v0` transitively hashes its mission, schemas, workloads,
  hidden evaluator interface and reference candidate. Candidate validation
  rejects unknown fields and changes to the fixed model revision or hardware.
- The reference candidate pins Qwen/Qwen3-4B commit
  `1cfa9a7208912126459214e8b04321603b3df60c`, one L4, vLLM 0.21.0, the CUDA
  base image, API paths, served model name and generation-config policy.
- The public workload expands into nine deterministic vLLM benchmark argv
  arrays without invoking a shell. Optional goodput SLOs are explicit positive
  integer milliseconds and remain unset until calibration.
- The Modal reference adapter is private, requests one L4, mounts only the
  Hugging Face secret it needs, disables retries, limits runtime to 1,800
  seconds and records non-secret model, runtime, GPU, canary and resolved
  package-set metadata.

## Not implemented

The following remain gates, not implied capabilities:

1. The ADR 0001 stock-OpenCode feasibility spike and a real
   `HarnessRuntime` adapter.
2. Provider-gateway dollar enforcement and condition-matched OpenCode model
   routing.
3. Collaboration, publication authorization, artifact storage, submission,
   compute and research services.
4. Evaluator-owned candidate launch, public result release, baseline
   repetitions, SLO calibration, hidden gates and neutral selection.
5. Four-condition scheduling, registered manifests, audit export and the
   preregistered statistical analysis.

## Next implementation gate

Before running the nine-point stock baseline, freeze a complete evaluator
measurement profile and persist every raw result outside agent-visible
workspaces. The profile must include the resolved server/evaluator image and
dependency identities, driver/runtime state, cold/reset policy, warmups,
repetitions, point ordering, failure rules and reference-canary brackets.

The OpenCode conformance spike can proceed independently of evaluator
calibration. Both must pass before broader V0 implementation assumes OpenCode
can provide durable sessions, native handoffs, non-mutating observation and a
matched peer-tool surface around an enforceable serving campaign.
