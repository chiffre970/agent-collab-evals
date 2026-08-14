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
3. `peer_isolated`: exactly `N` equivalent OpenCode primary agents; native subagents are unavailable and all peer-service state is actor-private, preventing the explicit peer communication under test.
4. `peer_collab`: exactly `N` equivalent OpenCode primary agents; native subagents are unavailable and the same peer-tool namespace is organisation-shared.

The native condition is deliberately not a hand-built supervisor/worker workflow. It uses the strongest stable default behavior supplied by the pinned runtime. The model decides whether and when to hand work to subagents.

The two peer conditions are identical in session topology, activation schedule, tools, workspaces, candidate policy and resources. Each registered study freezes `N` across every arm and block, activates all peers behind the same start barrier, and gives each the same fixed, non-transferable API, GPU, research and submission allowances and the same condition-blind serialized compute scheduler. Only collaboration visibility and the minimal instructions needed to describe it differ.

`peer_isolated` is an actor-level information boundary, not merely a private message board. Agents cannot discover one another or directly observe another actor's entries, artifacts, candidates, public feedback, compute or research jobs and results, queue metadata or caches. Fixed allowances prevent one actor from consuming another's capacity; the compute tool reveals only coarse state for the caller's own job. Because the GPU scheduler is shared, own-job completion time remains a low-bandwidth contention signal in both peer arms; it is recorded as a V0 limitation. The neutral selector and post-run analysis may aggregate records only after the agent-visible phase has closed.

The peer-collaboration condition is minimally elicited: agents learn that peers can see and respond to shared work, but receive no roles, leader, task division, quota, strategy or specialization. The initial pilot starts with `N = 4`; later preregistered studies may scale it. V0 estimates the value of peer information sharing under fixed allocations, not full dynamic organization such as reallocating shared money, compute or submission capacity. That is a required near-term follow-up.

## What is held constant

Within a study version, paired conditions have identical:

- OpenCode version and common runtime configuration;
- model provider, model version and inference settings;
- organisation-level API-dollar cap;
- fixed peer-actor API, GPU, research and candidate suballocations;
- wall-clock and cloud-GPU allowances;
- base workspace, mission and initial evidence;
- non-coordination tools and their permissions;
- candidate submission cap and public feedback;
- hidden evaluator and final selection rule.

For the peer-pair estimand, only communication visibility and its minimal instructions differ. Comparisons with `solo` and `native_multiagent` change topology and allocation semantics by design. In particular, peer versus native hierarchy is a bundled system comparison, not a clean causal estimate of topology alone. Every configuration and instruction difference is recorded verbatim.

## Outcome hierarchy

### 1. Validity and intention to treat

A submitted artifact must satisfy all campaign correctness, artifact, safety and reproducibility checks. An invalid artifact cannot outrank a valid one regardless of performance.

Every randomized campaign remains in the primary analysis. Each campaign definition declares its operational default or failure-floor outcome before execution. If the organisation produces no eligible candidate, that outcome is scored rather than silently dropping the run.

The default is task-specific. The serving-optimization campaign automatically registers its frozen reference server, so an organisation that finds no valid improvement retains reference performance. A generative or business-process campaign need not have a meaningful reference artifact; it instead declares a normalized hidden criterion scale and the score assigned to no eligible deliverable.

Predeclared infrastructure failures are reported separately from agent-caused invalid outcomes. Objective ledger rules fixed before execution determine whether the complete randomized block is retried, retained with a missing-outcome rule or excluded as infrastructure-invalid. The decision cannot depend on condition or observed score.

### 2. Primary outcome: solution quality

The confirmatory endpoint is the campaign's hidden score for its operationally selected outcome, including its predefined default when necessary. The first campaign uses held-out model-serving performance subject to strict quality, correctness and reliability gates and reports percentage improvement over the frozen reference. Campaigns without a meaningful reference artifact use a frozen normalized score against hidden test criteria.

One primary scalar or lexicographic rule is frozen before confirmatory runs. Component metrics are also reported; an opaque weighted index is not introduced after results are seen.

For the serving campaign, report every condition's hidden percentage improvement over the reference. A task-level improvement requires the preregistered lower measurement bound to exceed zero under the frozen repetition, aggregation and resolution rules; there is no additional commercial threshold. This shows whether an organisation optimized the target, not whether communication caused the gain.

The comparison hierarchy is:

- **Primary:** `peer_collab - peer_isolated`, the causal effect of peer communication with fleet topology held fixed.
- **Secondary:** `peer_collab - native_multiagent`, whether the complete peer system beats the complete native hierarchical system. Because topology and resource-allocation semantics differ, this is not a topology-only causal estimand.

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

The peer arms remain under the same organisation cap, but each top-level peer also has the same fixed, non-transferable API-dollar subcap. The peer subcaps partition the organisation cap exactly. A peer can observe only its own spend and rejection state, so another actor cannot exhaust its model allowance. The same rule applies to GPU time, research requests and bytes, candidate submissions and provider retries. `solo` and `native_multiagent` use the full organisation envelope because their session topology is intentionally part of those secondary comparisons.

Cloud GPU usage is a separate, identically capped campaign resource. Report API and GPU dollars separately and combined; do not let cheap model calls buy extra GPU time or vice versa in the initial study.

## Calibration and freezing

Calibration occurs before confirmatory comparison and may determine:

- the separate small open-weight target model optimized by the first campaign;
- the `N` to freeze for a registered study, chosen without inspecting condition outcomes;
- the API-dollar, wall-time and cloud-GPU caps;
- peer-actor suballocations and the condition-blind serialized scheduler policy;
- OpenCode settings that make native subagents usable without assigned roles;
- public-feedback granularity and candidate cap;
- the hidden quality tolerance, primary score and evaluator resolution;
- task difficulty that avoids floor and ceiling effects.

Calibration runs are excluded from confirmatory estimates. After freezing, any change to the runtime, model profile, instructions, tool schema, prices, task materials, evaluator, selection rule, block assignment, actor allocation or budget creates a new study version.

DeepSeek's direct API is used behind `ModelProfile`. Flash is for engineering and smoke tests. Before preregistration, Pro must pass the same task-feasibility qualification across base tools, native handoffs and peer tools; that check is pass/fail and does not estimate condition effects. The confirmatory study then freezes the exact Pro endpoint, requested and returned model identifiers, inference settings and price catalog across every arm and block. Any post-freeze model change creates a new study version. The small open-weight model being optimized is a separate serving target, chosen for headroom, difficulty and GPU cost.

## Randomization and replication

Campaign variants are assigned in randomized complete blocks. A block contains the same materialized task, variant and resource envelope run once under all four conditions. Before execution, a versioned algorithm assigns condition labels to predeclared execution positions and stochastic seeds while preserving one shared task seed and material digest for the block. The complete schedule is hashed into the study manifest before any outcome is observed. Each campaign receives a mechanically derived resolved-run manifest. Condition order is randomized to reduce provider, cloud and time-of-day effects.

Use repeated stochastic runs rather than claiming deterministic replay. Preserve the full manifest, artifacts and available traces for every replicate.

The confirmatory hypothesis is directional: any reliably positive communication effect is useful initial evidence, with no commercial uplift threshold. After pilot variance and evaluator resolution are measured, preregistration freezes alpha, the power target, a planning effect of one reliably resolvable score unit and enough complete blocks for the exact paired test. Pilots remain effect-size estimates with uncertainty.

The primary analysis uses paired block-level `peer_collab - peer_isolated` differences, an exact paired sign-flip/randomization test and a one-sided randomization-based confidence bound at the preregistered alpha. The block count must be large enough for that exact test. The null is a non-positive communication effect. The bundled native-system contrast is secondary and cannot rescue a failed primary comparison. Preregistration also fixes the bound construction and handling of missing, defaulted and infrastructure-invalid runs.

Primary reporting includes:

- condition means or medians as appropriate;
- paired block-level differences;
- the preregistered randomization-based confidence bound and paired test;
- every invalid or infrastructure-failed run;
- raw component metrics and run-level data.

Do not discard an inconvenient valid run as an outlier without a predeclared mechanical rule.

## Candidate finalization

All conditions receive the same organisation-level maximum number of immutable candidates and the same public validation feedback. In each peer arm, that maximum is divided into equal, non-transferable per-actor allowances; one peer cannot consume another's slots or learn about its submissions through a quota rejection. At the deadline, the runner selects the eligible candidate with the greatest public-validation score under a frozen ordering and tie-break, then evaluates it on hidden data. For serving optimization, the reference server participates as a system-owned fallback and candidates are ordered by public percentage improvement over it. Thus the winner of `peer_isolated` is simply the strongest independently produced improvement. The identical neutral rule applies to `peer_collab`, `native_multiagent` and `solo`.

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

The first near-term follow-ups are required rather than optional: test pooled or transferable peer resources to measure dynamic organization, and run one persistent non-coding business campaign. Software delivery is a useful but optional close-domain replication.

## Main threats to validity

- **Peer-treatment leakage:** `peer_isolated` and `peer_collab` must differ only in communication visibility and the instructions needed to expose it. Hidden shared caches, files or broker state would invalidate the communication estimand.
- **Indirect peer channels:** candidate listings, public feedback, broker queues, timing, artifacts and research or provider caches can communicate even when the board is private. V0 uses actor-scoped caches and results, fixed non-transferable allowances, coarse compute status and server-authorized artifact publication. Shared-GPU completion time remains a residual low-bandwidth signal; measure it, inspect traces for exploitation and rerun with a stricter release policy if it carries task information. Conformance tests still attempt quota-exhaustion and guessed-reference channels before confirmatory runs.
- **Bundled native comparison:** native and peer conditions differ in tools, topology and allocation semantics. Treat their contrast as a system comparison, not a topology-only causal effect.
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

The communication thesis receives confirmatory initial support if the preregistered paired estimate for `peer_collab - peer_isolated` is positive, its one-sided test rejects the non-positive null at alpha and its one-sided `(1 - alpha)` lower confidence bound exceeds zero. There is no additional minimum percentage. A positive point estimate whose bound includes zero is suggestive, not confirmed. The proposed product approach receives stronger secondary support if `peer_collab` also outperforms `native_multiagent`, without unacceptable validity or reliability loss.

A null or negative result is informative. It may mean peer collaboration is not useful for this task, the minimal collaboration surface is insufficient, or coordination overhead consumes its benefits. The V0 study should distinguish those possibilities only through subsequent preregistered experiments, not post-hoc storytelling.
