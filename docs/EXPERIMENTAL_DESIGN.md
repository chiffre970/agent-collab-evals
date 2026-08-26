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

The two peer conditions are identical in session topology, activation schedule, tools, workspaces, candidate policy and resources. Each registered study freezes `N` across every arm and block, activates all peers behind the same start barrier, and gives each the same fixed, non-transferable API, GPU, research and submission allowances and the same deterministic fixed-duration actor slots. Exploratory GPU work and public evaluation both consume the submitting actor's slots and quota. Only collaboration visibility and the minimal instructions needed to describe it differ.

`peer_isolated` is an actor-level information boundary, not merely a private message board. Agents cannot discover one another or directly observe another actor's entries, artifacts, candidates, public feedback, compute or research jobs and results, scheduling metadata or caches. Fixed allowances and matched slots prevent one actor from consuming or delaying another's capacity. Unused slots idle, and the compute tool releases only the caller's coarse terminal state at a predeclared slot boundary. The neutral selector and post-run analysis may aggregate records only after the agent-visible phase has closed.

The peer-collaboration condition is minimally elicited: agents learn that peers can see and respond to shared work, but receive no roles, leader, task division, quota, strategy or specialization. The initial pilot starts with `N = 4`; later preregistered studies may scale it. V0 estimates the value of peer information sharing under fixed allocations, not full dynamic organization such as reallocating shared money, compute or submission capacity. That is a required near-term follow-up.

## What is held constant

Within a study version, paired conditions have identical:

- OpenCode version and common runtime configuration;
- platform build plus application-service, adapter and enforcement-component versions and configuration digests;
- model provider, model version and inference settings;
- provider billing schedule, price-tier rule and cache policy;
- organisation-level API-dollar cap;
- fixed peer-actor API, GPU, research and candidate suballocations;
- deterministic actor-slot and result-release schedule;
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

`peer_isolated - solo` compares `N` independent fixed-subcap attempts plus neutral selection with one agent using the full organisation envelope; it does not isolate the number of attempts from budget partitioning or selection. `native_multiagent - solo` is the intention-to-treat effect of enabling the pinned runtime's stock native handoffs under the same organisation envelope, whether or not the primary chooses to invoke them. These are important secondary system contrasts rather than the product-thesis estimands.

### 3. Secondary outcomes

- Actual model API dollars spent.
- Agent-visible cloud-GPU dollars and allocated seconds used, including public evaluation.
- Hidden-evaluation GPU dollars and seconds as separate study overhead.
- Wall-clock time to selected solution.
- Valid-run rate and failure category.
- Best visible score reached over time and spend.
- Number of candidates evaluated.

The priority order is solution quality, cost, time, then reliability and other diagnostics. A lower-cost run is not considered better if it materially reduces the primary outcome unless the result is explicitly reported as a frontier trade-off.

### 4. Descriptive collaboration-use profile

The two peer conditions report a five-part profile beside—but never inside—the task outcome. `peer_isolated` provides structural-zero controls; `solo` and `native_multiagent` are `not_applicable` unless a separate native-handoff mapping is registered.

- **Reach:** shared publishers / `N`, peer retrievers / `N` and realized directed actor links / `N(N-1)`.
- **Exchange:** unique peer entries returned, replies to peer-authored entries and peer artifacts materialized.
- **Integration:** selected-artifact owner, distinct peer source actors in its recursively validated ancestry and validated cross-actor lineage edges.
- **Functions:** outcome-blind human labels for division/help, unsolicited assistance, reuse, challenge/checking, deconfliction and specialization.
- **Overhead:** collaboration tool calls and bytes, attributable context tokens and service latency where reliably measurable.

A shared publisher authors organisation-shared content; a peer retriever actually receives or materializes another actor's content. A directed link requires content from one actor to be returned to another; merely making it available does not count. Retry and denial events are deduplicated or excluded under the frozen measurement profile. If no eligible artifact is selected, Integration is `not_applicable`, not zero. Human labels are `observed`, `not_observed` or `unclear`, each with event references, and reviewers receive a redacted trace containing no hidden evaluation events or scores.

Report raw values and denominators rather than a weighted “collaboration index.” Positive outcomes with broad fan-in but little other coordination are described as fan-in benefits; heavier reuse, deconfliction or verification may motivate narrower ablations. The randomized outcome contrast establishes the effect of access to collaboration. The profile is descriptive, cannot establish which mechanism caused that effect, and must not be used to subset or adjust the primary estimate.

## Dollar-matched budgeting

The hard treatment budget is aggregate model API spend in US dollars. Token matching is inappropriate across models and can be distorted by caching, reasoning tokens and different input/output prices.

All model calls made for the organisation count, including primary agents, peers, subagents, retries, compaction and any other enabled runtime call. Confirmatory runs use metered API credentials behind the budget gateway. Chat or coding subscriptions are development conveniences only.

The gateway stops admitting calls before the cap can be exceeded using conservative reservations. The registered cap and actor allocations come from an immutable budget plan whose digest is pinned by the resolved run manifest, not from the mutable accounting database. Close-time reconciliation independently reconstructs usage and cost from raw provider receipts and rejects any ledger values that differ. Because a final in-flight response may settle below its reservation, actual spend can differ slightly between runs. Report both the cap and settled spend.

The manifest freezes the provider-cache and billing policies. Confirmatory peer runs require effective actor and campaign isolation through provider namespaces, a frozen non-semantic isolation prefix or disabled caching; no condition is selectively prewarmed. Billing freezes the catalog digest, rate-schedule version, allowed price tier/window, provider timestamp source and block rule. V0 completes all four positions in a block under one effective tier; an unplanned identity, catalog or tier transition invokes the preregistered whole-block rule. Every response records requested and returned model identifiers, any revision or system fingerprint, provider request ID and timestamp, cached-token accounting, effective tier and cache-hit/cache-miss/output unit rates. A persistent model or billing change creates a new study version.

The peer arms remain under the same organisation cap, but each top-level peer also has the same fixed, non-transferable API-dollar subcap. The peer subcaps partition the organisation cap exactly. A peer can observe only its own spend and rejection state, so another actor cannot exhaust its model allowance. The same rule applies to GPU time, research requests and bytes, candidate submissions and provider retries. `solo` and `native_multiagent` use the full organisation envelope because their session topology is intentionally part of those secondary comparisons.

Cloud GPU usage is a separate, identically capped campaign resource. Exploratory jobs and public evaluations both count against the owner actor and organisation treatment cap. Hidden post-closure evaluation uses a separate measurement account so the number of selected candidates cannot feed back into agent authority; report that evaluation overhead separately. Report API and treatment GPU dollars separately and combined; do not let cheap model calls buy extra GPU time or vice versa in the initial study.

## Calibration and freezing

Calibration occurs before confirmatory comparison and may determine:

- the separate small open-weight target model optimized by the first campaign;
- the `N` to freeze for a registered study, chosen without inspecting condition outcomes;
- the API-dollar, wall-time and cloud-GPU caps;
- peer-actor suballocations and deterministic actor-slot/result-release schedule;
- OpenCode settings that make native subagents usable without assigned roles;
- public-feedback granularity and candidate cap;
- the hidden quality tolerance, primary score and evaluator resolution;
- task difficulty that avoids floor and ceiling effects.

Calibration runs are excluded from confirmatory estimates. After freezing, any change to the platform or enforcement build, runtime, model/billing profile, instructions, tool schema, task materials, evaluator, selection rule, block assignment, actor allocation, slot schedule or budget creates a new study version.

The agent model is accessed behind `ModelProfile`. Before registration, a frozen provider-selection rule chooses the lowest projected-cost reputable route from a predeclared candidate snapshot after applying exact-identity, request/tool compatibility, privacy/cache isolation, billing-evidence, reliability and latency gates. The representative request mix, observation window, thresholds and deterministic tie-break are set before route measurements; treatment outcomes are never used. Dynamic routing and fallbacks are then disabled. The current development route is DeepInfra through OpenRouter, but it has no privileged architectural status.

After engineering and a common pass/fail feasibility qualification across base tools, native handoffs and peer tools, the first registered four-condition study freezes the exact `deepseek-v4-flash` model, transport, serving provider, requested and expected returned identity, inference settings, cache policy and billing policy across every arm and block. Its preregistered `StudyProgressionRule` decides whether evidence is promising enough to fund a separate four-condition `deepseek-v4-pro` study. The Pro result is conditional model-profile replication evidence: it is not pooled with Flash, does not replace a Flash null, and all attempted registered studies are reported. The small open-weight model being optimized is a separate serving target, chosen for headroom, difficulty and GPU cost.

### Registered factors and replications

The four-condition assignment is the treatment factor in the initial causal study. Model, serving provider, gateway transport, runtime, organisation size, resource envelope, campaign variant, target hardware and evaluator version are additional experimental factors that may be varied in separately registered studies. They are fixed nuisance configuration within any one four-condition study. V0 does not create a large factorial experiment: it establishes the collaboration contrast under one cheap qualified configuration, then changes one important factor at a time where practical. A factor change always produces a new manifest and separately reported result; pooling requires a later analysis plan that explicitly models that factor.

## Randomization and replication

Campaign variants are assigned in randomized complete blocks. A block contains the same materialized task, variant and resource envelope run once under all four conditions. Before execution, a versioned algorithm assigns condition labels to predeclared execution positions and stochastic seeds while preserving one shared task seed and material digest for the block. The complete schedule is hashed into the study manifest before any outcome is observed. Each campaign receives a mechanically derived resolved-run manifest. Condition order is randomized to reduce provider, cloud and time-of-day effects.

Use repeated stochastic runs rather than claiming deterministic replay. Preserve the full manifest, artifacts and available traces for every replicate.

The confirmatory hypothesis is directional: any reliably positive average communication effect is useful initial evidence, with no commercial uplift threshold. After pilot variance and evaluator resolution are measured, preregistration freezes alpha, the power target, a planning effect of one reliably resolvable score unit and a minimum complete-block count adequate for the registered studentized weak-null method. Pilots remain effect-size estimates with uncertainty.

The primary estimand is the finite-population mean causal effect of collaboration visibility over the execution positions conditioned into the two peer arms across the registered blocks. If `D_b` is the observed `peer_collab - peer_isolated` difference in block `b`, the point estimator is `mean(D_b)` and the studentized statistic for candidate effect `tau_0` is `(mean(D_b) - tau_0) / (sd(D_b) / sqrt(B))`, with a preregistered zero-variance rule.

The primary one-sided test targets the weak null that this mean effect is non-positive. It conditions on each block's task/materials, four execution positions and the unordered pair of positions assigned to the peer conditions, then applies every permitted within-pair swap of `peer_collab` and `peer_isolated` labels from the actual registered assignment mechanism. V0 computes the complete conditional distribution by enumeration or an algebraically equivalent exact algorithm. For each candidate `tau_0`, it uses the compatible constant-additive sharp-null imputation and recomputes the studentized statistic. Inverting these tests over a preregistered numerical grid/root rule produces the one-sided lower confidence bound. Weak-null validity is described as asymptotic or conservative under the registered regularity conditions, not finite-sample exact.

A separate finite-sample exact Fisher conditional randomization/sign-flip test reports evidence against the sharp null that every conditioned peer execution position has identical potential outcomes under `peer_collab` and `peer_isolated`. It is supportive but cannot substitute for the average-effect test. The bundled native-system contrast is secondary and cannot rescue a failed primary comparison. Preregistration fixes both nulls, every inferential claim, and handling of missing, defaulted and infrastructure-invalid runs; calibration simulation verifies the implementation and operating characteristics before registration.

The distinction between finite-sample exact sharp-null inference and studentized large-sample weak-null inference follows [Wu and Ding, *Randomization Tests for Weak Null Hypotheses in Randomized Experiments*](https://arxiv.org/abs/1809.07419). The registered analysis artifact must pin the actual implementation rather than treating that reference as an executable specification.

Primary reporting includes:

- condition means or medians as appropriate;
- paired block-level differences;
- the preregistered studentized weak-null test and lower confidence bound;
- the separate exact Fisher sharp-null test;
- every invalid or infrastructure-failed run;
- raw component metrics and run-level data.

Do not discard an inconvenient valid run as an outlier without a predeclared mechanical rule.

## Candidate finalization

All conditions receive the same organisation-level maximum number of immutable
candidates and the same public validation feedback. In each peer arm, that
maximum is divided into equal, non-transferable per-actor allowances; one peer
cannot consume another's slots or learn about its submissions through a quota
rejection. Candidate admission proves authenticated ownership and uses a
recoverable state machine to bind a provisional candidate to an idempotent
reservation for the public evaluator's worst-case duration. The reservation
comes from the submitter's GPU quota and deterministic slots. Public feedback
is released at the registered slot boundary. At the deadline, the runner
selects the eligible candidate with the greatest public-validation score under
a frozen ordering and tie-break, persists that selection and evaluates the
selected artifact on hidden data using the separate evaluator account. For
serving optimization, the reference server participates as a registered
system-owned fallback and candidates are ordered by public percentage
improvement over it. If the reference wins, its artifact still receives a
separate hidden evaluation. Thus the winner of `peer_isolated` is simply the
strongest independently produced improvement. The identical neutral rule
applies to `peer_collab`, `native_multiagent` and `solo`.

For campaigns without a meaningful reference artifact, the selector instead uses the campaign's frozen normalized public criterion. If no candidate is eligible, it returns the campaign-defined failure-floor outcome. Hidden scores never participate in selection.

If the selected serving candidate fails a hidden validity gate, the campaign receives reference performance and zero improvement. In a campaign without a reference artifact, hidden invalidity receives its frozen failure-floor score. Both remain intention-to-treat outcomes.

No condition receives a human editor, cross-session merger or extra finalizer. The native primary agent must combine delegated work through the runtime's normal behavior. Each isolated or collaborating peer must publish a candidate that stands on its own; the same neutral selector operates over both peer fleets.

Report the selected candidate as the primary operational result. An oracle-best hidden candidate may be reported separately to diagnose selection quality, clearly labeled as unavailable to the running organisation.

## First campaign evaluation

The small-model serving campaign evaluates a packaged inference server on a held-out request set. Before revealing performance, it verifies:

- artifact integrity and allowed dependency policy;
- successful cold start;
- response schema and deterministic correctness checks;
- absence of hidden-data access, forged measurements or privileged evaluator-signal use;
- teacher-forced likelihood or perplexity when used, as a diagnostic rather than a sufficient gate;
- reference-relative downstream generation quality on held-out requests sent to the submitted server;
- stability under repeated and concurrent load.

This is an architecture-neutral outcome evaluation. Candidates may alter model internals, derived weight representations, precision, serving paths and ordinary input-dependent routing within the declared target-model and campaign policy; the evaluator does not require an implementation to preserve the reference's code path. A deterministic token-identity check is an optional diagnostic for a candidate that claims exact losslessness, while approximate techniques such as quantization are judged directly by the broader non-inferiority suite. The target model's decoding profiles are frozen explicitly: a generic greedy setting is not silently substituted when the model author recommends sampling. Among candidates passing every gate, the primary score is sustained goodput on the held-out workload under fixed hardware and latency limits. Exact workload, decoding profiles, latency percentile, quality metrics, tolerances and paired uncertainty rule are campaign-version data frozen after calibration. See [the Fast Gemma evaluator lessons](GEMMA_CHALLENGE_EVALUATOR_LESSONS.md).

Hidden measurements run under an exclusive GPU lease. The study freezes the GPU SKU, image, driver and runtime, cold/reset and warm-up policy, load sequence, repetition count and aggregation rule. Candidate measurements are bracketed by a frozen reference canary; a canary excursion beyond tolerance triggers the same predeclared retry or invalidation rule in every condition.

Visible evaluation uses a disjoint public workload and the submitting actor's reserved GPU time. Hidden inputs and scores are never placed in agent-visible storage or collaboration content.

## Later studies

Only after the V0 comparison is working should the research program vary:

- organisation size and dollar cap to estimate scaling curves;
- preregistered API-dollar, GPU-dollar and wall-time envelopes, scoring frozen candidate snapshots after closure to estimate quality-cost-time Pareto frontiers;
- model, provider and transport as separate replication axes while holding the runtime and other factors fixed;
- heterogeneous models at equal aggregate cost;
- the number, cadence and dependency structure of jobs within durable campaigns;
- explicit versus emergent specialization;
- individual collaboration features such as search or matching;
- task families with increasingly subjective evaluation.

The first near-term follow-ups are required rather than optional: test pooled or transferable peer resources to measure dynamic organization, and run one persistent non-coding business campaign. Software delivery is a useful but optional close-domain replication.

## Main threats to validity

- **Peer-treatment leakage:** `peer_isolated` and `peer_collab` must differ only in communication visibility and the instructions needed to expose it. Hidden shared caches, files or broker state would invalidate the communication estimand.
- **Indirect peer channels:** candidate listings, public feedback, broker scheduling, timing, artifacts and research or provider caches can communicate even when the board is private. V0 uses actor-scoped caches and results, fixed non-transferable allowances, deterministic non-borrowable GPU slots, fixed result-release boundaries and server-authorized artifact publication. Conformance tests attempt quota-exhaustion, timing, cross-slot and guessed-reference channels before confirmatory runs.
- **Bundled native comparison:** native and peer conditions differ in tools, topology and allocation semantics. Treat their contrast as a system comparison, not a topology-only causal effect.
- **Weak native baseline:** customized or broken subagent behavior would make collab look artificially good. Use pinned stock general-purpose behavior and qualification tests.
- **Provider-runtime mismatch:** some models may not use a runtime's tools well. Qualify before freezing and repeat later with other model profiles.
- **Provider drift, caching and tariff windows:** mutable aliases, cross-run prefix caches or price-tier transitions can change effective capability or how much work a dollar buys. Isolate caches, freeze billing policy, retain provider identity and unit-rate receipts, keep a block within one effective tier and rerun complete affected blocks under the frozen rule.
- **Budget leakage or ledger tampering:** auxiliary calls, retries or rewritten accounting could favor a condition. Route all credentials through one account, pin the immutable budget plan and receipt-verifier profile outside the ledger, make credential revocation an in-flight request barrier and reject campaign closure on plan mismatches, raw-receipt mismatches, active reservations, forfeitures, overruns or incomplete receipts.
- **Hidden evaluator leakage:** public and hidden workloads must be disjoint and hidden results unavailable until closure.
- **Infrastructure noise:** GPU and API variability can swamp effects. Block, randomize order and repeat.
- **Selection advantage:** different mergers or candidate counts can create the result. Use one submission and selection policy.
- **Researcher degrees of freedom:** freeze endpoints and rules after calibration, then publish all runs.
- **Conditional model escalation:** running Pro only after a promising Flash result creates selected replication evidence. Freeze the trigger, publish non-progression and every attempted study, analyze Pro separately and do not use it to erase or pool away the Flash result.
- **Overgeneralization:** one performance-engineering campaign cannot establish benefit for economics writing or organisational work. Treat those as separate studies.

## Decision rule

The communication thesis receives confirmatory initial support if the preregistered mean paired estimate for `peer_collab - peer_isolated` is positive, the studentized one-sided weak-null test rejects a non-positive average effect at alpha and its one-sided `(1 - alpha)` lower confidence bound exceeds zero. There is no additional minimum percentage. The exact Fisher sharp-null test is reported separately and is not part of this average-effect decision rule. A positive point estimate whose bound includes zero is suggestive, not confirmed. The proposed product approach receives stronger secondary support if `peer_collab` also outperforms `native_multiagent`, without unacceptable validity or reliability loss.

A null or negative result is informative. It may mean peer collaboration is not useful for this task, the minimal collaboration surface is insufficient, or coordination overhead consumes its benefits. The V0 study should distinguish those possibilities only through subsequent preregistered experiments, not post-hoc storytelling.
