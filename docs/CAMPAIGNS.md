# Campaigns and Task Families

## Purpose

Campaigns test whether the collaboration effect generalizes across different kinds of work. V0 implements one campaign well before adding breadth.

A campaign is the lifetime of one durable organisation under one experimental condition. Its agent identities, harness sessions, private workspaces and condition-allowed shared state persist while it receives an ordered sequence of jobs.

A campaign definition provides:

- one or more incoming jobs with frozen public materials;
- declarative compute, research and sandbox requirements;
- public validation feedback;
- an independently bound hidden evaluator;
- a common resource envelope;
- per-job or campaign-level outcome aggregation;
- enough variants and repeated campaign runs for blocked comparison.

The architecture always supports persistence across jobs. The first serving experiment uses one evolving mission, so cross-job accumulation is not part of its initial treatment. Later campaign definitions can exercise the durable lifecycle without replacing the runtime or storage model.

## Selection requirements

A confirmatory campaign must:

- require several meaningful investigative or implementation choices;
- admit objectively hidden evaluation;
- resist task-specific hard-coding and evaluator leakage;
- be feasible for a solo agent at the chosen budget;
- leave plausible opportunities for decomposition, challenge or reuse;
- produce portable immutable submissions;
- run safely in an isolated environment;
- avoid obvious floor or ceiling effects in calibration.

A campaign does not need a forced mid-run change. Changes, interruptions and accumulated organisational memory are valuable later treatments, but requiring them now would confound the first test.

## Campaign 1: small-model cloud serving

### Mission

Improve the inference-serving performance of a specified small open-weight model on a fixed cloud GPU while preserving output quality, correctness and reliability.

The organisation receives one evolving job containing a reference implementation, model artifact or immutable model reference, public workload and submission rules. The campaign definition declares its environment requirements; independent compute and evaluator adapters provide the cloud GPU and scoring. The organisation submits a reproducible server artifact and launch manifest.

### Why this is first

- Solution quality is directly executable rather than judge-impression based.
- Performance work supports useful parallel investigation: profiling, serving engines, batching, kernels, quantization, compilation, memory layout and load testing.
- Local improvements interact, creating a real need for integration and challenge.
- The result has a clear performance frontier rather than a vague collaboration proxy.
- A cloud GPU matches the actual deployment target and avoids tying the experiment to the local base M4.
- The one-job form establishes the four-condition comparison before job sequences introduce memory and dependency effects.

### Calibration

Before condition comparison, sequential pilot runs choose a model, GPU and task envelope that satisfy:

- the reference server fits and runs reliably;
- a single agent can produce at least one valid submission;
- measurable headroom exists above the reference;
- evaluation is cheap enough for repeated runs;
- hidden quality measurement is stable;
- the task does not collapse to selecting one obvious configuration flag.

Calibration may use DeepSeek or another model profile. The chosen model must demonstrate reliable use of the `HarnessRuntime` adapter's shell/edit loop, native handoffs and peer tool on qualification tasks. The runtime, model, provider and prices are then frozen for the study.

### Fixed inputs

- Model and weights digest.
- GPU type, driver, runtime image and power/performance settings where controllable.
- Reference server and correctness suite.
- Allowed dependencies, compilation and network policy.
- Public and hidden request-set digests.
- Quality metric, tolerance and latency limits.
- API-dollar, GPU-time, wall-time and submission caps.
- Adapter versions and capability-manifest digests.

### Agent-controlled surface

Agents may change the serving implementation and legal build/runtime configuration, including:

- serving engine and scheduler;
- batching and concurrency;
- attention or kernel implementation;
- compilation and graph capture;
- cache and memory management;
- quantization or precision, subject to quality gates;
- request routing within the submitted server;
- load-test and profiling workflow.

They may not alter the hidden evaluator, hidden workload, required model semantics or hardware allocation. GPU access is mediated by the compute broker; agents never receive cloud credentials or direct backend access.

### Evaluation

All submissions first pass:

1. artifact and dependency validation;
2. cold-start and API-schema checks;
3. deterministic correctness checks;
4. output-quality tolerance on hidden prompts;
5. repeated and concurrent-load stability;
6. prohibited-shortcut inspection.

The primary metric for passing submissions is sustained goodput on the hidden workload under the frozen latency service-level objective. Report alongside it:

- requests or tokens per second;
- latency percentiles and time to first token;
- quality delta from reference;
- peak GPU memory and utilization;
- cold-start time;
- error rate;
- API, GPU and total dollar cost;
- wall-clock time to selected candidate.

The exact scalar score is frozen after calibration. Prefer a gate-plus-performance rule over a weighted blend that lets speed compensate for quality failure.

### Variants

Variants should change legitimate performance characteristics while retaining the same mission and evaluator shape, for example:

- different small model architectures;
- context-length and request-size distributions;
- latency-sensitive online versus throughput-oriented batch profiles;
- memory-constrained versus compute-constrained serving.

Do not reveal which variant dimension is stressed in the hidden workload beyond the public mission.

### Progression

1. Prove durable campaign lifecycle, provisioning, metering and hidden evaluation with `solo` only.
2. Qualify stock runtime-native handoffs and run `native_multiagent` pilots.
3. Qualify the identical peer-tool schema under actor-private `peer_isolated` and organisation-shared `peer_collab` scopes.
4. Verify that no files, artifacts, caches or broker state form an accidental channel in `peer_isolated`.
5. Freeze one study version and run randomized complete blocks across all four conditions.
6. Replicate across additional serving variants.
7. Only then vary organisation size, model profile or job-sequence length.

## Recommended second confirmatory family: software delivery

### Mission

Implement a multi-part change in a real repository under hidden functional, integration and performance tests.

### Why second

Software delivery remains close to the coding runtime's intended domain, allowing an interpretable replication while adding merge, interface and integration pressure. It also supports exact hidden evaluation and can reuse most sandbox infrastructure.

It can begin as one multi-part job, then become the first campaign to send a sequence of related issues to the same durable organisation and measure whether accumulated context helps or hurts.

### Outcomes

- Hidden functional and integration tests passed.
- Regression, performance and security gates.
- Completeness against the task specification.
- Patch size and unnecessary churn as diagnostics.
- API cost, wall time and valid-run rate.

Use repository issues or authored variants that are contamination-reviewed and not already solved in the model's visible workspace.

## Exploratory family: economics publication

### Mission

Produce an accurate, explanatory economics article and supporting graphics from a fixed evidence packet or recorded research environment supplied through `ResearchBackend` and its enforced broker.

### Why exploratory

This tests research, argument, checking, writing and visual synthesis rather than code performance. It is valuable for product generality, but evaluator uncertainty is much higher.

### Evaluation

Use separable measures:

- machine-checkable factual claims and calculations;
- citation entailment and evidence coverage;
- blind expert rubric scores for argument and economic reasoning;
- blind editorial scores for clarity and usefulness;
- graphic correctness and accessibility;
- factual error count, API cost and completion time.

Use multiple blinded raters, randomized presentation, adjudication rules and inter-rater reliability. Treat results as exploratory until the rubric is shown to be stable and sensitive on calibration examples.

## Later task families

- **Customer operations:** resolve a simulated queue of interdependent support, renewal and account tasks against a hidden customer-state simulator.
- **Meeting to execution:** turn changing meeting artifacts into decisions, plans and completed simulated actions with dependency checks.
- **Research synthesis:** build a defensible answer from a frozen corpus with hidden factual and citation tests.
- **Incident response:** diagnose and remediate faults in a sandboxed service from partial, evolving evidence.
- **Edge deployment:** optimize a model for a base M4 target after the cloud-serving study is established.

These families should be added for a specific generalization question, not to make V0 look like a full benchmark suite.

## Deferred treatments

The following are separate experiments because they change more than peer communication:

- single-job versus multi-job durable campaigns;
- changing priorities or mid-campaign incidents;
- explicitly assigned specialists versus emergent specialization;
- heterogeneous models or tools at equal dollar cost;
- semantic search, recommendation and automatic matching;
- deduplication and structured claims;
- human-agent mixed teams;
- privileged agents, approvals and consequential actions;
- alternative peer visibility or candidate-selection policies.

## Build order

1. Define the domain ports and make fake adapters pass the durable two-job campaign lifecycle.
2. Pin OpenCode as the first `HarnessRuntime` adapter and implement the provider-neutral model profile plus enforced dollar gateway.
3. Implement local storage, sandbox policy, cloud-GPU compute and disabled/frozen research adapters behind their ports and brokers.
4. Implement the serving campaign definition and evaluator adapter end to end with `solo`.
5. Enable stock runtime-native handoffs without custom orchestration.
6. Implement the collaboration adapter with identical actor-private and organisation-shared peer-tool modes.
7. Add trace export, human observation labels and randomized four-condition block execution.
8. Complete calibration and freeze the first confirmatory study.
9. Run and publish the four-condition serving experiment.
10. Decide from evidence whether to add a multi-job campaign, second task family or collaboration feature.
