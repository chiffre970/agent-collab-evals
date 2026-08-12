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
3. `peer_isolated`: up to `N` equivalent OpenCode primary agents; native subagents are unavailable and the peer-tool namespace is actor-private, preventing peer communication.
4. `peer_collab`: up to `N` equivalent OpenCode primary agents; native subagents are unavailable and the same peer-tool namespace is organisation-shared.

The native condition is deliberately not a hand-built supervisor/worker workflow. It uses the strongest stable default behavior supplied by the pinned runtime. The model decides whether and when to hand work to subagents.

The two peer conditions are identical in session topology, fleet cap, base tools, peer-tool schema, private-workspace policy, candidate policy and aggregate resource envelope. Only namespace visibility and the minimal factual instructions required to describe it differ. `peer_isolated` agents cannot discover one another or read one another's entries or artifacts.

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

### 1. Validity gates

A run must satisfy all campaign correctness, artifact, safety and reproducibility checks. An invalid artifact cannot outrank a valid one regardless of performance.

Predeclared infrastructure failures are reported separately from agent-caused invalid outcomes. The distinction is made from objective ledger evidence without regard to condition.

### 2. Primary outcome: solution quality

The confirmatory endpoint is the campaign's hidden score for the operationally selected valid artifact. The first campaign uses held-out model-serving performance subject to strict quality, correctness and reliability gates.

One primary scalar or lexicographic rule is frozen before confirmatory runs. Component metrics are also reported; an opaque weighted index is not introduced after results are seen.

The principal comparisons are:

- `peer_collab - peer_isolated`: the causal effect of peer communication with fleet topology held fixed;
- `peer_collab - native_multiagent`: whether the proposed peer approach beats conventional hierarchical delegation.

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

Cloud GPU usage is a separate, identically capped campaign resource. Report API and GPU dollars separately and combined; do not let cheap model calls buy extra GPU time or vice versa in the initial study.

## Calibration and freezing

Calibration occurs before confirmatory comparison and may determine:

- the first model and provider profile;
- `N`, the maximum live agents;
- the API-dollar, wall-time and cloud-GPU caps;
- OpenCode settings that make native subagents usable without assigned roles;
- public evaluator resolution and candidate cap;
- the hidden quality tolerance and primary score;
- task difficulty that avoids floor and ceiling effects.

Calibration runs are excluded from confirmatory estimates. After freezing, any change to the runtime, model profile, instructions, tool schema, prices, task materials, evaluator, selection rule or budget creates a new study version.

The initial DeepSeek model is a practical first profile, not a permanent architectural choice. Before confirmatory use, a small qualification set must show that the model can reliably use the OpenCode base tools, native subagent mechanism and collaboration tool. If it cannot, select another model before freezing rather than compensating with condition-specific prompts.

## Randomization and replication

Campaign variants are assigned in randomized complete blocks. A block contains the same variant or job sequence and resource envelope run once under all four conditions. Condition order is randomized to reduce provider, cloud and time-of-day effects.

Use repeated stochastic runs rather than claiming deterministic replay. Preserve the full manifest, artifacts and available traces for every replicate.

The number of blocks and statistical test are fixed after pilot variance is measured. Until then, pilot results are effect-size estimates with uncertainty, not hypothesis-confirming evidence.

Primary reporting includes:

- condition means or medians as appropriate;
- paired block-level differences;
- bootstrap or randomization-based confidence intervals;
- every invalid or infrastructure-failed run;
- raw component metrics and run-level data.

Do not discard an inconvenient valid run as an outlier without a predeclared mechanical rule.

## Candidate finalization

All conditions may submit the same maximum number of immutable candidates and receive the same public validation feedback. At the deadline, the runner selects the highest visible-score valid candidate with a frozen tie-break and evaluates it on hidden data.

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

Visible evaluation uses a disjoint public workload. Hidden inputs and scores are never placed in agent-visible storage or collaboration content.

## Later studies

Only after the V0 comparison is working should the research program vary:

- organisation size and dollar cap to estimate scaling curves;
- model/provider while holding the runtime fixed;
- heterogeneous models at equal aggregate cost;
- the number, cadence and dependency structure of jobs within durable campaigns;
- explicit versus emergent specialization;
- individual collaboration features such as search or matching;
- task families with increasingly subjective evaluation.

## Main threats to validity

- **Peer-treatment leakage:** `peer_isolated` and `peer_collab` must differ only in communication visibility and the instructions needed to expose it. Hidden shared caches, files or broker state would invalidate the communication estimand.
- **Runtime-treatment coupling:** native and peer conditions necessarily expose different tools and topology. Minimize and publish instruction differences.
- **Weak native baseline:** customized or broken subagent behavior would make collab look artificially good. Use pinned stock general-purpose behavior and qualification tests.
- **Provider-runtime mismatch:** some models may not use a runtime's tools well. Qualify before freezing and repeat later with other model profiles.
- **Budget leakage:** auxiliary calls or retries omitted from cost could favor a condition. Route all credentials through one account and reconcile traces.
- **Hidden evaluator leakage:** public and hidden workloads must be disjoint and hidden results unavailable until closure.
- **Infrastructure noise:** GPU and API variability can swamp effects. Block, randomize order and repeat.
- **Selection advantage:** different mergers or candidate counts can create the result. Use one submission and selection policy.
- **Researcher degrees of freedom:** freeze endpoints and rules after calibration, then publish all runs.
- **Overgeneralization:** one performance-engineering campaign cannot establish benefit for economics writing or organisational work. Treat those as separate studies.

## Decision rule

The communication thesis receives initial support only if `peer_collab` shows a practically meaningful, repeatable improvement over `peer_isolated` at the same dollar cap. The proposed product approach receives stronger support if `peer_collab` also outperforms `native_multiagent`, without unacceptable validity or reliability loss.

A null or negative result is informative. It may mean peer collaboration is not useful for this task, the minimal collaboration surface is insufficient, or coordination overhead consumes its benefits. The V0 study should distinguish those possibilities only through subsequent preregistered experiments, not post-hoc storytelling.
