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

For the peer comparison, `peer_isolated` and `peer_collab` must use the same `N`, activation timing, scheduling policy, session topology and stopping rules. The exact policy is selected during calibration and frozen in the study manifest. Visibility of peer entries and referenced artifacts is the treatment; an extra head start, always-on coordinator or different wake-up rule is not.

## Research modes

Campaigns that need external evidence declare one of two bounded modes and use the same mode in every condition:

- **Frozen research:** a versioned corpus or recorded search-and-fetch environment is replayed for reproducible confirmatory comparison.
- **Controlled live research:** agents receive read-only search and fetch through the research broker, with source policy, request and byte quotas, private/local/metadata-network blocking, download controls and complete recording. This better represents real work, but results must acknowledge that the web can change between experimental blocks.

Neither mode exposes raw network access, cloud credentials or unrelated host files. Recorded results and caches remain scoped according to the condition so they cannot become an accidental peer channel.

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

The agent model is qualified separately from the serving target. V0 calls the DeepSeek direct API; Flash and Pro are alternative configurations behind `ModelProfile`, and qualification selects one using the same shell/edit, native-handoff and peer-tool tasks. A non-DeepSeek provider is used only if both direct profiles fail that common qualification. The selected agent model, endpoint, runtime, inference settings and price catalog are then frozen for the study.

### Fixed inputs

- Serving target model and weights digest.
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

Every submitted candidate first receives public validation:

1. artifact and dependency validation;
2. cold-start and API-schema checks;
3. deterministic correctness checks;
4. output-quality tolerance on public prompts;
5. repeated and concurrent-load stability on the public workload;
6. prohibited-shortcut inspection.

The frozen reference server is automatically registered as a system-owned candidate. At the deadline, the runner selects whichever has the highest public-validation score: the reference or the strongest valid agent candidate, using a frozen tie-break. In `peer_isolated`, this means choosing the largest independently produced public improvement; if no agent beats the reference publicly, the baseline wins. The selected candidate alone proceeds through corresponding correctness, quality, stability and shortcut gates on hidden prompts and workloads. If an agent candidate fails a hidden gate, the operational outcome reverts to the reference and records zero improvement rather than removing the campaign from analysis.

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

## Close-domain replication: software delivery

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

After the serving study and any close-domain replication, the research program must run at least one persistent, real-world-style business campaign. The first can be an economics publication desk or customer operations. These tasks introduce changing queues, durable expertise, knowledge reuse and mixed qualitative and objective outcomes that coding-shaped benchmarks do not capture.

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
