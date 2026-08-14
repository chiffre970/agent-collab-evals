# Campaigns and Task Families

## Purpose

Campaigns test whether the collaboration effect generalizes across different kinds of work. V0 implements one campaign well before adding breadth.

A campaign is the lifetime of one durable organisation under one experimental condition. Its agent identities, harness sessions, private workspaces and condition-allowed shared state persist while it receives an ordered sequence of jobs.

A campaign definition provides:

- one or more incoming jobs with frozen public materials;
- declarative compute, research and sandbox requirements;
- a frozen activation and scheduling policy for each organisation topology;
- public validation feedback;
- an independently bound hidden evaluator;
- a common resource envelope;
- fixed, non-transferable peer-actor allocations and a deterministic fixed-duration actor-slot schedule;
- per-job or campaign-level outcome aggregation;
- enough variants and repeated campaign runs for blocked comparison.

The architecture always supports persistence across jobs. The first serving experiment uses one evolving mission, so cross-job accumulation is not part of its initial treatment. Later campaign definitions can exercise the durable lifecycle without replacing the runtime or storage model.

## Selection requirements

A confirmatory campaign must:

- require several meaningful investigative or implementation choices;
- admit objectively hidden evaluation;
- resist task-specific hard-coding and evaluator leakage;
- permit a solo agent to make a valid, graded contribution at the chosen budget, without requiring that one agent can finish every item in a larger queue;
- leave plausible opportunities for decomposition, challenge or reuse;
- produce portable immutable submissions;
- run safely in an isolated environment;
- avoid obvious floor or ceiling effects in calibration.

A campaign does not need a forced mid-run change. Changes, interruptions and accumulated organisational memory are valuable later treatments, but requiring them now would confound the first test.

Both peer conditions use the same registered `N`, timing, topology, stopping rules, fixed per-actor allowances and deterministic actor-slot/result-release schedule. The initial pilot starts with `N = 4`; later registered studies may scale it, but `N` cannot change after condition outcomes are inspected. Exploratory compute and public evaluation both run in and charge the submitting actor's slots; unused time remains idle. Agents see only coarse state for their own compute jobs at the fixed release boundary. Only visibility of peer entries and explicitly published artifacts differs. This isolates information sharing; pooled budgets and transferable capacity are reserved for a required follow-up.

## Research modes

Campaigns that need external evidence declare one of two bounded modes and use the same mode in every condition:

- **Frozen research:** a versioned corpus or recorded search-and-fetch environment is replayed for reproducible confirmatory comparison.
- **Controlled live research:** agents receive read-only search and fetch through the research broker, with source policy, request and byte quotas, private/local/metadata-network blocking, download controls and complete recording. This better represents real work, but results must acknowledge that the web can change between experimental blocks.

Neither mode exposes raw network access, cloud credentials or unrelated host files. Recorded results, caches and request/byte allowances remain actor-scoped in both peer conditions so they cannot become an accidental peer channel. An agent may share a result in `peer_collab` only through an explicit collaboration publication; artifact access is authorized server-side.

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

Before condition comparison, sequential pilot runs choose a small serving **target model**, GPU and task envelope that satisfy:

- the reference server fits and runs reliably;
- a single agent can produce at least one valid submission;
- measurable headroom exists above the reference;
- evaluation is cheap enough for repeated runs;
- hidden quality measurement is stable;
- the task does not collapse to selecting one obvious configuration flag.

Calibration chooses aggregate API, GPU, research, retry and candidate limits that divide equally among the study's frozen `N` peers, with enough per-actor compute time for a valid attempt.

The agent model is separate from the serving target. After engineering and one common pass/fail task-feasibility check covering shell/edit, native handoffs and peer tools, the first registered four-condition study freezes the exact DeepSeek `deepseek-v4-flash` profile. A preregistered progression rule may fund a separate four-condition `deepseek-v4-pro` study if the Flash evidence is promising. Pro never replaces Flash within a study, the two model results are not pooled, and all attempted registered studies—including a decision not to progress—are reported. Each study also freezes its billing catalog, rate schedule and price-tier/block policy.

### Fixed inputs

- Serving target model and weights digest.
- GPU type, driver, runtime image and power/performance settings where controllable.
- Reference server and correctness suite.
- Allowed dependencies, compilation and network policy.
- Public and hidden request-set digests.
- Quality metric, tolerance and latency limits.
- API-dollar, GPU-time, wall-time and submission caps.
- Equal fixed peer-actor API, GPU, research and submission allocations plus the same deterministic actor-slot/result-release policy.
- Platform, application-service, adapter and enforcement-component versions, build/configuration digests and capability-manifest digests.

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

Every submitted candidate first receives public validation:

1. artifact and dependency validation;
2. cold-start and API-schema checks;
3. deterministic correctness checks;
4. output-quality tolerance on public prompts;
5. repeated and concurrent-load stability on the public workload;
6. prohibited-shortcut inspection.

The frozen reference server is automatically registered as a system-owned candidate. Every condition has the same total candidate allowance; in the peer arms it is divided equally into fixed actor allowances so peers cannot consume or signal through one another's capacity. Before candidate admission, the registry proves authenticated artifact ownership and reserves the public evaluator's worst-case duration from the submitting actor's GPU allocation and slots. At the deadline, the runner selects whichever has the highest public-validation score: the reference or the strongest valid agent candidate, using a frozen tie-break. In `peer_isolated`, this means choosing the largest independently produced public improvement; if no agent beats the reference publicly, the baseline wins. The selected candidate alone proceeds through corresponding correctness, quality, stability and shortcut gates on hidden prompts and workloads under a separate evaluator measurement account. If an agent candidate fails a hidden gate, the operational outcome reverts to the reference and records zero improvement rather than removing the campaign from analysis.

The primary metric for a candidate passing the hidden gates is sustained goodput on the held-out workload under the frozen latency service-level objective. Report alongside it:

- requests or tokens per second;
- latency percentiles and time to first token;
- quality delta from reference;
- peak GPU memory and utilization;
- cold-start time;
- error rate;
- API, GPU and total dollar cost;
- wall-clock time to selected candidate.

The exact scalar score is frozen after calibration. Prefer a gate-plus-performance rule over a weighted blend that lets speed compensate for quality failure.

An agent candidate counts as improving the task only when its preregistered lower measurement bound exceeds the reference under the frozen repetition, aggregation and resolution rules. No separate commercial uplift threshold applies.

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
5. Materialize and hash the Flash study's full randomized block schedule, then run its assigned execution positions across all four conditions.
6. Apply the preregistered progression rule; if it passes, register and run a separate complete Pro study, and otherwise record non-progression.
7. Run the required pooled-resource follow-up and select the required persistent non-coding campaign.
8. Replicate across additional variants or organisation sizes as justified by the result.

## Optional close-domain replication: software delivery

### Mission

Implement a multi-part change in a real repository under hidden functional, integration and performance tests.

### Why it is useful

Software delivery remains close to the coding runtime's intended domain, allowing an interpretable replication while adding merge, interface and integration pressure. It also supports exact hidden evaluation and can reuse most sandbox infrastructure.

It can begin as one multi-part job, then become the first campaign to send a sequence of related issues to the same durable organisation and measure whether accumulated context helps or hurts.

This is a close-domain replication, not evidence that the effect generalizes to organisational work. It may be run second because it is cheap to validate, but the research program should not make a broad productivity claim until it has also run an early non-coding campaign.

### Outcomes

- Hidden functional and integration tests passed.
- Regression, performance and security gates.
- Completeness against the task specification.
- Patch size and unnecessary churn as diagnostics.
- API cost, wall time and valid-run rate.

Use repository issues or authored variants that are contamination-reviewed and not already solved in the model's visible workspace.

## Required early non-coding generalization

After V0, choose and preregister at least one persistent, real-world-style business campaign. Software delivery may run before or after it, but is not a prerequisite. The first business campaign can be an economics publication desk or customer operations; both test changing queues, durable expertise and knowledge reuse that a single coding-shaped mission cannot.

### Candidate A: economics publication desk

#### Mission

Operate an economics desk across a sequence of related jobs: research notes, an accurate explanatory article, data checks, supporting graphics, revisions and follow-up questions. The campaign uses either frozen research or controlled live research supplied through `ResearchBackend` and its enforced broker.

#### Why exploratory

This tests research, argument, checking, writing, visual synthesis and reuse of accumulated subject knowledge rather than code performance. Evaluator uncertainty is higher, so conclusions should remain exploratory until the rubric is shown to be reliable.

#### Evaluation

Each job receives a normalized percentage score against frozen criteria, rubric anchors and test cases. The organisation may submit the same capped number of immutable candidates as every other condition; the candidate with the best public-validation score is selected under a frozen tie-break, then assessed against held-out criteria. Unlike serving optimization, there is no universal fallback artifact.

Use separable components:

- machine-checkable factual claims and calculations;
- citation entailment and evidence coverage;
- blind expert rubric scores for argument and economic reasoning;
- blind editorial scores for clarity and usefulness;
- graphic correctness and accessibility;
- factual error count, API cost and completion time.

Use multiple blinded raters, randomized presentation, adjudication rules and inter-rater reliability. Treat results as exploratory until the rubric is shown to be stable and sensitive on calibration examples.

### Candidate B: customer operations

Operate a simulated customer queue containing related support, renewal and account work against a hidden state simulator. The queue should contain more useful work than one agent is likely to complete within the service window, while allowing a solo organisation to earn a valid graded score by resolving a subset well. Recurring causes and evolving customer history create opportunities for durable specialization, reuse and deconfliction.

Score the percentage of available outcome value achieved against frozen policy, state-transition, response-quality and service-level criteria. Select the valid immutable submission with the best public-validation score under a frozen tie-break, then apply hidden cases and state checks. Do not substitute a canned fallback response for unfinished work; omissions and bad prioritization are part of the outcome.

## Later task families

- **Meeting to execution:** turn changing meeting artifacts into decisions, plans and completed simulated actions with dependency checks.
- **Research synthesis:** build a defensible answer from a frozen corpus with hidden factual and citation tests.
- **Incident response:** diagnose and remediate faults in a sandboxed service from partial, evolving evidence.
- **Edge deployment:** optimize a model for a base M4 target after the cloud-serving study is established.

These families should be added for a specific generalization question, not to make V0 look like a full benchmark suite.

## Follow-up treatments

Pooled or transferable peer budgets, submissions and compute scheduling are the required first follow-up because they test dynamic organization rather than information sharing alone. Other separate treatments include:

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

1. Run the two-day stock-OpenCode proof from ADR 0001: out-of-process observational events where possible, a separately pinned non-mutating instrumentation plugin only where necessary, and a distinct peer-tool integration path. Stop for an explicit runtime decision if it fails.
2. Define the minimal manifests and domain ports, then make fake adapters pass a durable two-job lifecycle.
3. Implement the minimal collaboration contract, local storage, durable publication registry and opaque server-authorized artifact publication. If the five-day fake-campaign exit check fails, stop and run the ADR's bounded substrate assessment; adopt or fork HF Agent Collabs only through an explicit recorded decision after it passes the treatment contract.
4. Add the provider-neutral model profile, budget gateway, sandbox, immutable compute staging and disabled/frozen research adapters behind their ports and brokers.
5. Implement the serving campaign and hidden evaluator end to end with `solo`.
6. Qualify stock native handoffs and the matched actor-private/organisation-shared peer modes.
7. Add trace export, human observation labels, frozen block/run manifests and randomized four-condition execution.
8. Complete calibration and freeze the first Flash confirmatory study, including its billing policy, analysis plan and Flash-to-Pro progression rule.
9. Run and publish the four-condition Flash experiment; apply the frozen progression rule and, if triggered, register and run Pro as a separate complete study.
10. Run the pooled-resource study and preregister the selected persistent non-coding campaign; add software replication only if useful.
