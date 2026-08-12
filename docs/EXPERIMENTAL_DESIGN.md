# Experimental Design

## Research objective

The primary question is:

> At the same aggregate API-dollar budget, does peer communication improve verified solution quality over an otherwise identical isolated peer fleet, and does the peer-collaboration approach outperform the coding-agent runtime's native hierarchical handoffs?

The initial study asks whether the effect exists, not why. Collaboration behavior is observed descriptively; mechanism attribution and feature ablations follow only if an outcome effect warrants them.

## Experimental unit

One experimental unit is a durable organisation assigned to one condition for an entire campaign. Agent identities, sessions, private workspaces and condition-allowed shared state may persist across jobs within that campaign. Conditions and replicates never share those resources.

The organisation/campaign—not an individual agent or incoming job—is the unit of budget, outcome measurement and randomization. Jobs are repeated observations nested within that unit and must not be treated as independent samples.

The first serving experiment is one evolving mission and therefore one job sequence of length one. Supporting durable campaigns now prevents a later redesign without adding cross-job memory as a treatment in the first study.

## Initial conditions

Every run uses the same pinned OpenCode release, model profile, base tools, task materials and evaluator.

1. `solo`: one OpenCode primary agent; native subagents and peer communication are unavailable.
2. `native_multiagent`: one OpenCode primary agent; the runtime's standard general-purpose subagents are available through its native task mechanism.
3. `peer_isolated`: exactly `N` equivalent OpenCode primary agents; native subagents are unavailable and all peer-service state is actor-private, preventing peer communication.
4. `peer_collab`: exactly `N` equivalent OpenCode primary agents; native subagents are unavailable and the same peer-tool namespace is organisation-shared.

The native condition is deliberately not a hand-built supervisor/worker workflow. It uses the strongest stable default behavior supplied by the pinned runtime. The model decides whether and when to hand work to subagents.

The two peer conditions are identical in session topology, fleet size, activation schedule, base tools, peer-tool schema, private-workspace policy, candidate policy and aggregate resource envelope. V0 eagerly activates all `N` peers behind the same start barrier and applies the same concurrency, wake/resume and deadline rules. Only collaboration visibility and the minimal factual instructions required to describe it differ.

`peer_isolated` is an actor-level information boundary, not merely a private message board. Agents cannot discover one another or observe another actor's entries, artifacts, candidates, public feedback, compute or research jobs and results, queue metadata or caches. The neutral selector and post-run analysis may aggregate these records only after the agent-visible phase has closed.

The peer-collaboration condition is minimally elicited. Each agent receives the mission and factual instructions for using the collaboration tool, including that peers may see and respond to shared work. No roles, leader, task division, collaboration quota, recommended strategy or specialization is supplied.

The initial organisation size is selected in calibration, beginning with `N = 4`. The motivating scaling study later compares `N` collaborating agents with one agent receiving the same aggregate dollar budget.

## What is held constant

Within a study version, paired conditions have identical:

- OpenCode version and common runtime configuration;
- model provider, model version and inference settings;
- organisation-level API-dollar cap;
- wall-clock and cloud-GPU allowances;
- base workspace, mission and initial evidence;
- non-coordination tools and their permissions;
- candidate submission cap and public feedback;
- hidden evaluator and final selection rule.

For the peer-pair estimand, only communication visibility and its minimal instructions differ. Comparisons with `solo` and `native_multiagent` also change session topology by design. Every effective configuration and instruction difference is recorded verbatim.

## Outcome hierarchy

### 1. Validity and intention to treat

A submitted artifact must satisfy all campaign correctness, artifact, safety and reproducibility checks. An invalid artifact cannot outrank a valid one regardless of performance.

Every randomized campaign remains in the primary analysis. Each campaign definition declares its operational default or failure-floor outcome before execution. If the organisation produces no eligible candidate, that outcome is scored rather than silently dropping the run.

The default is task-specific. The serving-optimization campaign automatically registers its frozen reference server, so an organisation that finds no valid improvement retains reference performance. A generative or business-process campaign need not have a meaningful reference artifact; it instead declares a normalized hidden criterion scale and the score assigned to no eligible deliverable.

Predeclared infrastructure failures are reported separately from agent-caused invalid outcomes. Objective ledger rules fixed before execution determine whether the complete randomized block is retried, retained with a missing-outcome rule or excluded as infrastructure-invalid. The decision cannot depend on condition or observed score.

### 2. Primary outcome: solution quality

The confirmatory endpoint is the campaign's hidden score for its operationally selected outcome, including its predefined default when necessary. The first campaign uses held-out model-serving performance subject to strict quality, correctness and reliability gates and reports percentage improvement over the frozen reference. Campaigns without a meaningful reference artifact use a frozen normalized score against hidden test criteria.

One primary scalar or lexicographic rule is frozen before confirmatory runs. Component metrics are also reported; an opaque weighted index is not introduced after results are seen.

For the serving campaign, report every condition's hidden percentage improvement over the reference. Any value above zero is a task-level improvement. That comparison shows whether an organisation produced useful optimization; it does not by itself identify a collaboration benefit, which requires the primary `peer_collab - peer_isolated` contrast.

The comparison hierarchy is:

- **Primary:** `peer_collab - peer_isolated`, the causal effect of peer communication with fleet topology held fixed.
- **Secondary:** `peer_collab - native_multiagent`, whether the proposed peer approach beats conventional hierarchical delegation.

`peer_isolated - solo` estimates the benefit of multiple independent attempts, and `native_multiagent - solo` estimates the benefit of native hierarchy. These are important secondary contrasts rather than the product-thesis estimands.

### 3. Secondary outcomes

- Actual model API dollars spent.
- Cloud-GPU dollars and allocated seconds used.
- Wall-clock time to selected solution.
- Valid-run rate and failure category.
- Best visible score reached over time and spend.
- Number of candidates evaluated.

The priority order is solution quality, cost, time, then reliability and other diagnostics. A lower-cost run is not considered better if it materially reduces the primary outcome unless the result is explicitly reported as a frontier trade-off.

### 4. Descriptive emergence observations

Post-run human review records only whether the trace clearly shows:

- lateral delegation or help requests;
- unsolicited assistance;
- reuse of another agent's finding or artifact;
- challenge, critique or independent checking;
- explicit deconfliction of duplicated work;
- emergent specialization.

Labels are `observed`, `not_observed` or `unclear`, with a short evidence reference. Coordination labels primarily apply to `peer_collab`; comparable delegation and reuse observations may be recorded for `native_multiagent`. They do not enter the primary analysis. Detailed causal forensics, network measures, automated semantic coding and claims about *why* performance changed are deferred.

## Dollar-matched budgeting

The hard treatment budget is aggregate model API spend in US dollars. Token matching is inappropriate across models and can be distorted by caching, reasoning tokens and different input/output prices.

All model calls made for the organisation count, including primary agents, peers, subagents, retries, compaction and any other enabled runtime call. Confirmatory runs use metered API credentials behind the budget gateway. Chat or coding subscriptions are development conveniences only.

The gateway stops admitting calls before the cap can be exceeded using conservative reservations. Because a final in-flight response may settle below its reservation, actual spend can differ slightly between runs. Report both the cap and settled spend.

The manifest freezes the provider-cache policy. Confirmatory peer runs require effective actor and campaign isolation through provider namespaces, a frozen non-semantic isolation prefix or disabled caching; no condition is selectively prewarmed. Every response records requested and returned model identifiers, any revision or system fingerprint, provider request ID, cached-token accounting and the applicable price-catalog version. A fingerprint change within a randomized block normally invalidates and reruns the complete block under the predeclared rule; a persistent model change creates a new study version.

Cloud GPU usage is a separate, identically capped campaign resource. Report API and GPU dollars separately and combined; do not let cheap model calls buy extra GPU time or vice versa in the initial study.

## Calibration and freezing

Calibration occurs before confirmatory comparison and may determine:

- the DeepSeek agent-model profile used to operate the agents;
- the separate small open-weight target model optimized by the first campaign;
- `N`, the maximum live agents;
- the API-dollar, wall-time and cloud-GPU caps;
- OpenCode settings that make native subagents usable without assigned roles;
- public-feedback granularity and candidate cap;
- the hidden quality tolerance, primary score and evaluator resolution;
- task difficulty that avoids floor and ceiling effects.

Calibration runs are excluded from confirmatory estimates. After freezing, any change to the runtime, model profile, instructions, tool schema, prices, task materials, evaluator, selection rule or budget creates a new study version.

The DeepSeek direct API is the V0 default for agent inference. Flash and Pro use the same `ModelProfile` boundary; qualification selects one before freezing. Before confirmatory use, a small qualification set must show that the selected agent model can reliably use the OpenCode base tools, native subagent mechanism and collaboration tool. If neither DeepSeek profile qualifies, select another provider before freezing rather than compensating with condition-specific prompts. The open-weight target model is calibrated independently for optimization headroom, task difficulty and GPU cost.

## Randomization and replication

Campaign variants are assigned in randomized complete blocks. A block contains the same variant or job sequence and resource envelope run once under all four conditions. Condition order is randomized to reduce provider, cloud and time-of-day effects.

Use repeated stochastic runs rather than claiming deterministic replay. Preserve the full manifest, artifacts and available traces for every replicate.

The confirmatory hypothesis is directional: any positive verified communication effect is useful initial evidence, rather than requiring an arbitrary commercial uplift threshold. After pilot variance and evaluator resolution are measured, freeze the number of complete blocks needed to distinguish zero from a positive effect of at least one reliably measurable score unit. Until then, pilot results are effect-size estimates with uncertainty, not hypothesis-confirming evidence.

The primary analysis uses paired block-level `peer_collab - peer_isolated` differences, an exact paired sign-flip/permutation test where the block count permits it, and a randomization-based one-sided 95% lower confidence bound. The null is a non-positive communication effect. The native-hierarchy contrast is secondary and is not used to rescue a failed primary comparison. The study registration fixes the significance level, block count, bound construction and the mechanical handling of missing, defaulted and infrastructure-invalid runs.

Primary reporting includes:

- condition means or medians as appropriate;
- paired block-level differences;
- the preregistered randomization-based confidence bound and paired test;
- every invalid or infrastructure-failed run;
- raw component metrics and run-level data.

Do not discard an inconvenient valid run as an outlier without a predeclared mechanical rule.

## Candidate finalization

All conditions may submit the same maximum number of immutable candidates and receive the same public validation feedback. At the deadline, the runner selects the eligible candidate with the greatest public-validation score under a frozen ordering and tie-break, then evaluates it on hidden data. For serving optimization, the reference server participates as a system-owned fallback and candidates are ordered by public percentage improvement over it. Thus the winner of `peer_isolated` is simply the strongest independently produced improvement. The identical rule applies to `peer_collab`, `native_multiagent` and `solo`.

For campaigns without a meaningful reference artifact, the selector instead uses the campaign's frozen normalized public criterion. If no candidate is eligible, it returns the campaign-defined failure-floor outcome. Hidden scores never participate in selection.

If the selected serving candidate fails a hidden validity gate, the campaign receives reference performance and zero improvement. In a campaign without a reference artifact, hidden invalidity receives its frozen failure-floor score. Both remain intention-to-treat outcomes.

No condition receives a human editor, cross-session merger or extra finalizer. The native primary agent must combine delegated work through the runtime's normal behavior. Each isolated or collaborating peer must publish a candidate that stands on its own; the same neutral selector operates over both peer fleets.

Report the selected candidate as the primary operational result. An oracle-best hidden candidate may be reported separately to diagnose selection quality, clearly labeled as unavailable to the running organisation.

## First campaign evaluation

The small-model serving campaign evaluates a packaged inference server on a held-out request set. Before revealing performance, it verifies:

- artifact integrity and allowed dependency policy;
- successful cold start;
- response schema and deterministic correctness checks;
- absence of prohibited test-specific shortcuts;
- minimum output-quality tolerance relative to the frozen reference;
- stability under repeated and concurrent load.

Among candidates passing every gate, the primary score is sustained goodput on the held-out workload under fixed hardware and latency limits. Exact workload, latency percentile, quality metric and tolerance are campaign-version data frozen after calibration.

Hidden measurements run under an exclusive GPU lease. The study freezes the GPU SKU, image, driver and runtime, cold/reset and warm-up policy, load sequence, repetition count and aggregation rule. Candidate measurements are bracketed by a frozen reference canary; a canary excursion beyond tolerance triggers the same predeclared retry or invalidation rule in every condition.

Visible evaluation uses a disjoint public workload. Hidden inputs and scores are never placed in agent-visible storage or collaboration content.

## Later studies

Only after the V0 comparison is working should the research program vary:

- organisation size and dollar cap to estimate scaling curves;
- preregistered API-dollar, GPU-dollar and wall-time envelopes, scoring frozen candidate snapshots after closure to estimate quality-cost-time Pareto frontiers;
- model/provider while holding the runtime fixed;
- heterogeneous models at equal aggregate cost;
- the number, cadence and dependency structure of jobs within durable campaigns;
- explicit versus emergent specialization;
- individual collaboration features such as search or matching;
- task families with increasingly subjective evaluation.

## Main threats to validity

- **Peer-treatment leakage:** `peer_isolated` and `peer_collab` must differ only in communication visibility and the instructions needed to expose it. Hidden shared caches, files or broker state would invalidate the communication estimand.
- **Indirect peer channels:** candidate listings, public feedback, broker queues, timing, artifacts and research or provider caches can communicate even when the board is private. They remain actor-scoped until closure unless explicitly published in `peer_collab`.
- **Runtime-treatment coupling:** native and peer conditions necessarily expose different tools and topology. Minimize and publish instruction differences.
- **Weak native baseline:** customized or broken subagent behavior would make collab look artificially good. Use pinned stock general-purpose behavior and qualification tests.
- **Provider-runtime mismatch:** some models may not use a runtime's tools well. Qualify before freezing and repeat later with other model profiles.
- **Provider drift and caching:** mutable model aliases or cross-run prefix caches can change effective capability or price. Isolate caches, retain provider identity receipts and rerun complete affected blocks under the frozen rule.
- **Budget leakage:** auxiliary calls or retries omitted from cost could favor a condition. Route all credentials through one account and reconcile traces.
- **Hidden evaluator leakage:** public and hidden workloads must be disjoint and hidden results unavailable until closure.
- **Infrastructure noise:** GPU and API variability can swamp effects. Block, randomize order and repeat.
- **Selection advantage:** different mergers or candidate counts can create the result. Use one submission and selection policy.
- **Researcher degrees of freedom:** freeze endpoints and rules after calibration, then publish all runs.
- **Overgeneralization:** one performance-engineering campaign cannot establish benefit for economics writing or organisational work. Treat those as separate studies.

## Decision rule

The communication thesis receives confirmatory initial support if the preregistered paired estimate for `peer_collab - peer_isolated` is positive, its one-sided test rejects the non-positive null at the frozen significance level and its one-sided 95% lower confidence bound is above zero. There is no additional minimum percentage: any reliably measured improvement is a useful baseline result. A positive point estimate whose bound still includes zero is reported as suggestive, not confirmed. The proposed product approach receives stronger secondary support if `peer_collab` also outperforms `native_multiagent`, without unacceptable validity or reliability loss.

A null or negative result is informative. It may mean peer collaboration is not useful for this task, the minimal collaboration surface is insufficient, or coordination overhead consumes its benefits. The V0 study should distinguish those possibilities only through subsequent preregistered experiments, not post-hoc storytelling.
