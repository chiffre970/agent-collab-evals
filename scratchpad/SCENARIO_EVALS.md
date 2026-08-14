# Scenario and Evaluation Scratchpad

> Status: working notes, deliberately separate from the registered architecture and experimental design. None of the proposed weights, thresholds, targets or task variants are frozen.

## Objective

Build a small set of realistic tasks spanning different kinds of parallel work, then test where open peer communication helps, has no effect or causes harm.

The key distinction is between **parallelism** and **the value of collaboration**:

- A task can be highly parallel but require almost no communication. Independent agents should do well, and a shared board may only add overhead.
- A task can contain many parallel branches that must share evidence or converge into one result. This is where collaboration should have the most opportunity to help.
- A task can be difficult to parallelize at all. Extra agents and communication may fragment the work or induce groupthink.

The benchmark should therefore not assume a universal collaboration benefit. A credible result is a map of which task structures benefit, which do not and why.

## The four evaluations at a glance

Every evaluation scores the final verified outcome. The collaboration profile described below is reported beside that score and never contributes task points.

| Evaluation | Parallelism shape | Final outcome | Primary score | What collaboration could add |
| --- | --- | --- | --- | --- |
| Model-serving optimization | Broad portfolio search followed by difficult integration | Immutable optimized server that launches cleanly | Percentage goodput improvement over the reference across hidden workloads, conditional on quality, per-regime non-regression and reliability gates | Divide the search space, share failed experiments, combine compatible improvements and verify gains |
| Economics mini-paper | Serial synthesis in the compact task; modular research with expensive convergence in the empirical task | Reproducible model, estimates, graphics, provenance and accessible paper | Empirical task: anchored 0–100 score, 60 points executable and 40 points blind expert assessment; compact task: a separate anchored 0–100 artifact rubric | Specialize across modelling, evidence, validation, graphics and editing, then challenge assumptions before synthesis |
| Vulnerability discovery and remediation | Deep chains, broad surface search, shared root causes and independent verification | Reproducible findings and a regression-safe patch set for a private authorized target | Percentage of hidden risk value captured, split between confirmed root cause and remediation, minus false-positive penalties | Expand coverage, hand off chains, correlate symptoms, deduplicate causes and reproduce findings |
| Simulated customer operations | Wide independent and linked queues span simple fan-in through coupled work | Ordered action plan and customer responses replayed against a clean simulator | Percentage of verified business value completed minus policy, privacy and customer-harm penalties | Combine completed cases, detect shared causes, reuse resolutions, deconflict actions and prioritize the queue |

Do not combine these task-native scores into one leaderboard. The comparison of interest is `peer_collab - peer_isolated` within each registered study.

## Collaboration-use profile

The randomized outcome difference tells us **whether access to collaboration helped**. A small descriptive profile tells us **what kind of collaboration was actually observed**:

| Dimension | Minimal measure |
| --- | --- |
| Reach | Shared publishers / `N`; peer retrievers / `N`; realized directed actor links / `N(N-1)` |
| Exchange | Unique peer entries returned, replies to peer-authored entries and peer artifacts materialized |
| Integration | Selected-artifact owner, distinct peer source actors in its validated ancestry and cross-actor lineage edges |
| Functions | Outcome-blind human tags for division/help, unsolicited assistance, reuse, challenge/checking, deconfliction and specialization |
| Overhead | Collaboration tool calls and bytes; attributable context tokens and service latency where measurable |

This is a profile of the peer collaboration surface. Report it for both peer conditions: `peer_isolated` provides structural zeros, while `solo` and `native_multiagent` are `not_applicable` unless native handoffs later receive their own registered mapping. A shared publisher authors at least one organisation-shared entry; a peer retriever actually receives or materializes another actor's content. A directed link exists only when content from one actor is returned to another, not merely broadcast. Count each reader–entry and reader–artifact pair once, excluding retries and denied calls. “Returned” means exposed by the tool, not necessarily understood. Provenance is a lower-bound proxy for influence; if there is no eligible selected artifact, Integration is `not_applicable`, not zero.

Do not collapse the vector into a weighted collaboration score. A compact report might be:

> Outcome: +12 points | Reach: 3/4 publishers, 4/4 retrievers, 7/12 links | Exchange: 14 entries, 3 artifacts | Integration: owner + 2 peer sources | Functions: reuse, deconfliction and verification observed | Overhead: 42 calls, 38 KB returned

Interpretation remains descriptive:

- improvement with broad integration but little adaptive coordination is primarily a **fan-in benefit**;
- improvement with reuse, deconfliction or verification is consistent with a richer coordination benefit;
- heavy exchange and overhead without integration or uplift suggests coordination tax or chatter;
- little use and no uplift means the treatment was not meaningfully adopted or elicited;
- rapid convergence, little checking and worse outcomes are consistent with herding.

Telemetry alone cannot prove which mechanism caused the outcome. Do not subset or adjust the primary outcome comparison using this post-treatment profile. A mechanism claim needs a registered ablation. Where outputs can be combined mechanically and safely, a later diagnostic may compare `isolated best-of-N`, `isolated + neutral aggregation` and `open collaboration`. Fan-in remains part of the primary product-level treatment.

## Informal taxonomy

Parallelisability is a profile, not a single score:

| Axis | Question |
| --- | --- |
| Work width | How many useful branches can proceed at once? |
| Coupling | How often does one branch need results from another? |
| Convergence cost | How difficult is it to turn partial work into one valid outcome? |
| Reuse potential | Can one discovery prevent repeated work elsewhere? |
| Shared-state risk | Can simultaneous actions conflict or invalidate one another? |
| Verification value | Is independent disagreement useful, or is it redundant? |
| Capability complementarity | Do different actors possess distinct tools, permissions or expertise? |
| Persistence | Can knowledge, routines or specialization compound across a sequence of jobs? |

Useful task shapes are:

1. **Serial synthesis:** narrow work width, tightly coupled reasoning and a high cost of merging different voices. Collaboration may be neutral or harmful.
2. **Modular synthesis:** research, analysis and production can be divided, but the final result needs a coherent integration pass.
3. **Portfolio search:** many plausible approaches can be tested independently, followed by selection, refinement or combination.
4. **Coordinated graph:** multiple branches are useful, but dependencies, shared root causes or scarce capabilities make information exchange important.
5. **Independent queue:** many self-contained units can be completed in parallel. Open collaboration can still help through mechanical fan-in; communication beyond aggregation should add little.

The fifth category is a control for coordination **beyond fan-in** only when the registered neutral-aggregation diagnostic is present. In the primary best-of-`N` comparison, a collaboration win on an independent queue may be a legitimate aggregation benefit. Similar effects across very different task shapes should motivate mechanism checks, not be dismissed automatically as artefacts.

## Proposed scenario map

| Scenario or variant | Work width | Coupling | Convergence cost | Dominant shape | Initial prediction for collaboration |
| --- | --- | --- | --- | --- | --- |
| Economics, compact argument | Low–medium | High | High | Serial synthesis | Small, zero or negative effect |
| Economics, empirical mini-paper | Medium | Medium–high | High | Modular synthesis | Possible benefit from specialist inputs and checking |
| Model-serving optimization | High | Medium | Medium–high | Portfolio search | Likely benefit from broader search and reuse; herding is a risk |
| Security, one deep exploit chain | Low–medium | High | Medium | Coordinated graph | Useful only if partial evidence is combined well |
| Security, broad attack surface | High | Medium | Medium | Portfolio search and verification | Likely benefit from coverage and independent reproduction |
| Customer operations, unrelated queue | Very high | Low | Low–medium | Independent queue | Fan-in benefit possible; little expected beyond aggregation |
| Customer operations, linked queue | Very high | High | Medium–high | Coordinated graph and information integration | Strong benefit beyond fan-in is plausible |

These predictions should be recorded before trials, but they do not affect scoring.

For the taxonomy to remain honest, classify each variant from its frozen task specification before seeing outcomes. Prefer matched variants inside one domain that change the work structure while retaining the evaluator and approximate difficulty. Report the `condition × variant` interaction as exploratory evidence. With only four task families, the taxonomy is a set of grounded hypotheses—not enough data to claim that any one axis causally explains collaboration performance.

## Scenario 1: small open-model serving optimization

### Mission

Improve the serving performance of a fixed, small open-weight language model on a fixed cheap cloud GPU without materially reducing model quality, correctness or reliability.

The agent model doing the work is separate from the small model being optimized. The target model and GPU should be chosen during calibration so that:

- a solo agent can produce a valid attempt;
- meaningful headroom remains above the reference server;
- experiments are cheap enough to repeat;
- the task does not collapse to discovering one obvious flag;
- the target is small enough that iteration time does not dominate agent behavior.

### Why it is parallelisable

Agents can investigate profiling, serving engines, batching, compilation, attention kernels, quantization, cache policy, memory layout and load generation concurrently. This is primarily portfolio search, but successful changes may interact and need integration.

Collaboration could help agents share bottlenecks, negative results and artifacts, or ask peers to verify surprising speedups. It could hurt by causing premature convergence, duplicating fashionable experiments or combining individually good but incompatible changes.

### Submission

A self-contained immutable server artifact and launch manifest containing all deployable dependencies and transformed weights. The evaluator must be able to launch it from a clean image without network access.

### Validity and quality gates

A candidate must:

- start cleanly and implement the required API;
- pass deterministic schema, token-accounting and error-handling tests;
- remain stable under repeated and concurrent load;
- satisfy frozen memory and artifact-size limits;
- pass hidden checks for hard-coded prompts, replayed outputs and other benchmark-specific shortcuts;
- meet preregistered per-slice quality non-inferiority margins, with uncertainty bounds, across several actual capability evaluations;
- stay within declared tolerances on deterministic output or logit checks where applicable.

Quality should be measured on several held-out task categories rather than one aggregate benchmark. A frozen judge or blind human review may be used to detect unexpected qualitative degradation, but should be secondary to executable quality tests.

### Hidden performance workload

Use several workload regimes so the system cannot win by optimizing one narrow case:

1. offline or batch throughput;
2. latency-sensitive interactive serving;
3. concurrent mixed-length requests;
4. long-context or memory-pressure requests.

Randomize request order, prompt lengths and generation lengths. Include unseen prompts and harmless request perturbations so memorized response caches do not help.

Define **goodput** as valid output tokens completed per second while satisfying the frozen error, p99 time-to-first-token and time-per-output-token limits. Measure each regime repeatedly under an exclusive GPU lease, with fixed warm-up/reset rules and reference canaries before and after candidate measurements.

### Primary score

For a candidate passing every gate:

```text
performance_ratio = geometric_mean(
  candidate_goodput_regime_i / reference_goodput_regime_i
)

percentage_improvement = 100 * (performance_ratio - 1)
```

Every workload regime first applies a frozen non-regression floor. Among candidates passing every regime, the geometric mean rewards broad improvement without allowing an unacceptable regression through. The highest public-validation improvement selects the candidate; the disjoint hidden workload determines the final score. A hidden-gate failure returns the operational reference outcome and zero improvement.

A candidate counts as genuinely improving the task only when its preregistered repeated-measurement lower bound exceeds zero. There is no additional commercial threshold in the initial study.

### Anti-gaming controls

- Separate public and hidden prompts, workloads and quality cases.
- Count only verified output tokens from successful requests.
- Use the evaluator-owned fixed tokenizer and generation requirements so a candidate cannot inflate throughput by redefining tokens or ending responses early.
- Freeze the target architecture, tokenizer and permitted weight transformations. If model substitution is ever allowed, register it as a separate quality-constrained serving task rather than silently changing this one.
- Prohibit evaluator-specific branching, prompt fingerprinting and network calls.
- Run from clean state and inspect the complete process tree and artifact.
- Freeze hardware, image, driver, load generator and measurement protocol.
- Use multiple workload variants and randomized ordering.
- Cap candidate submissions and public feedback.
- Retain full component scores: goodput, TTFT, TPOT, error rate, memory, artifact size and each quality result.

## Scenario 2: natural versus neutral interest rates

### Mission

Produce a rigorous but accessible blog-style mini-paper answering:

> When should the natural and neutral rates of interest be treated as the same concept, when do they differ, how can they be estimated, and what can policymakers responsibly infer from those estimates?

The full empirical version should require:

- a dependency-locked model package accepting a standard data schema;
- estimates of the natural real rate, a clearly defined neutral nominal policy-rate range and uncertainty over time;
- at least two defensible specifications, or one primary model plus meaningful robustness alternatives;
- an estimate using a supplied historical data vintage;
- uncertainty and sensitivity analysis;
- a concise paper under a fixed word budget;
- two or three accurate, accessible graphics;
- a machine-readable claim and source table;
- a runnable analysis appendix and short model card covering definitions, equations, identification, assumptions and failure modes.

The frozen source room can include official conceptual material, papers, code and data for Laubach–Williams and Holston–Laubach–Williams-style estimates, alternative approaches and macroeconomic data vintages. Controlled-live research can be tested later, but frozen evidence is preferable for the first confirmatory comparison.

Economists sometimes use “natural” and “neutral” interchangeably. The submission must define and use its terms consistently, but the evaluator must not impose a false universal distinction.

### Parallelism variants

**Compact argument:** supply precomputed estimates and a small source pack. Most of the value is coherent reasoning and synthesis. This is the hard-to-parallelize control.

**Empirical mini-paper:** require literature comparison, executable modelling, data validation, sensitivity analysis, graphics, citation checking and editing. Inputs are modular, but the final thesis must be coherent.

Potential collaboration benefits are specialist research, independent calculation checks, counterargument and parallel graphic production. Likely harms are inconsistent definitions, incompatible modelling assumptions, citation laundering, fragmented prose and excessive breadth.

The compact and empirical versions are separate registered studies with separate rubrics. The compact task does not require an estimator. Its proposed anchored score is: conceptual correctness and distinction between terms (25), evidence and citation entailment (20), synthesis and argument structure (20), uncertainty and counterarguments (15), reader transfer (10), and numerical/visual communication (10). Missing the required paper or source table returns the frozen failure floor; fabricated or non-resolving citations cap the score under a preregistered rule. Blind raters use frozen behavioral anchors, while evaluator-owned checks verify citation resolution, quoted/numerical claims, word limits and graphic/table consistency. This contract must be frozen and calibrated before the compact task becomes a registered endpoint. Its 0–100 result must not be compared directly with the empirical task's 0–100 result.

### Why the score must be anchored

Use an absolute, frozen 0–100 rubric. The highest score wins, but the best observed paper is **not** automatically rescaled to 100. Otherwise a poor field can manufacture an apparent perfect result and scores cannot be compared across repeats.

There is also no directly observable true real-world value of `r*`. A candidate should not score highly merely because it is close to one official or consensus estimate. The evaluation should reward reproducibility, economic coherence, empirical usefulness, calibrated uncertainty and evidential honesty.

### Proposed 100-point score

| Component | Points | Evaluation |
| --- | ---: | --- |
| Empirical usefulness | 20 | Out-of-sample proper scores from frozen auxiliary inflation/activity models, compared with constant, trend and simple state-space baselines |
| Latent recovery and uncertainty | 15 | Accuracy, interval coverage and sharpness on hidden semi-synthetic economies where latent `r*` is known |
| Robustness and generalization | 10 | Real-time vintages, revisions, missing observations, masked cases and plausible measurement perturbations |
| Reproducibility and internal consistency | 10 | Clean execution, valid units/timing and consistency among estimates, claims, tables and charts |
| Mechanical provenance | 5 | Complete lineage, resolvable sources and exact numerical traceability |
| Economic and conceptual correctness | 12 | Blind anchored expert assessment of definitions, assumptions and policy interpretation |
| Identification, model choice and uncertainty | 10 | Blind assessment of identification limits, alternatives, robustness and calibrated restraint |
| Evidence and citation entailment | 8 | Blind assessment of source quality, coverage, attribution and support for claims |
| Synthesis and reader usefulness | 7 | Coherence, prioritization and value to a financially literate non-specialist |
| Visual communication | 3 | Graphic correctness, clarity and accessibility |

The first 60 points are machine-scored; the remaining 40 use blinded expert assessment. Each component is normalized against frozen floor and target values or anchored behavior, never against the competing submissions.

The quantitative and evidence sections should impose a frozen integrity floor so polished prose cannot compensate for a broken model, fabricated sources or material numerical errors. A candidate receives the declared failure-floor outcome if it cannot reproduce, uses post-cutoff information, fabricates a material source or result, branches on hidden cases, or omits a required artifact. Ordinary economic disagreement or a weak but honest model reduces the score rather than invalidating it.

### Hidden evaluation

- Re-run the notebook from a clean environment and regenerate every table and figure.
- Compare declared values with a structured claim table rather than extracting numbers only from prose.
- Evaluate the estimator on held-out historical vintages, masked country-period cases and several structurally distinct synthetic-economy families, including regime shifts, whose latent rate and shocks are known to the evaluator.
- Test whether the submitted `r*` series adds out-of-sample information in evaluator-defined auxiliary models without letting the candidate choose those comparisons.
- Use citation-resolution and entailment checks against the frozen source room.
- Give blinded expert raters anchored examples for each qualitative score band.
- Randomize paper order and hide condition, agent count and run identity.
- Use at least three economics raters plus a frozen adjudication rule for confirmatory work.
- Measure inter-rater agreement and retain the full component vector.

A frozen model judge can make internal pilots cheaper, but it should be calibrated against expert ratings and should not be the sole confirmatory evaluator—especially if it is from the same model family operating the agents.

### Optional downstream outcome test

Give blinded readers factual and policy-interpretation questions after reading the paper. Their accuracy and calibration provide a useful measure of whether the paper actually transfers understanding, rather than merely sounding expert. Treat this as secondary until reader sampling is reliable.

### Anti-gaming controls

- Fixed word, figure and citation budgets.
- Frozen data/source digests and a generic input schema used again with hidden vintages.
- Required provenance for every material numerical claim.
- Clean-room execution with no hidden-source access.
- Machine checks for broken, fabricated or non-entailing citations.
- No reward for length, citation count, model complexity or agreement with a preferred policy conclusion.
- Anchored rubric examples written before seeing condition outputs.
- Blind scoring, randomized order and adjudication.
- Public selection based on a disjoint data vintage and partial rubric; hidden results never select the winner.

## Scenario 3: contained vulnerability discovery and remediation

### Recommended framing

The confirmatory task should be **zero-day-like discovery in a private, authorized target**, not an uncontrolled hunt against a live open-source project.

A live hunt has no knowable denominator, is not repeatable, may be contaminated by public disclosures and creates legal and coordinated-disclosure obligations. Genuine unexpected findings can still be valuable, but they should be quarantined for human review and reported as a separate exploratory contribution.

### Mission

Audit an offline, OSS-like multi-service application; discover, reproduce, prioritize and remediate as much real security risk as possible within the fixed budget.

A suitable target is a private fork or purpose-built ML artifact and job-execution service with an API gateway, authorization, uploads/parsers, storage, scheduler, worker and shared libraries. It should contain privately seeded or semantically mutated weaknesses, realistic clean code and suspicious decoys.

### Parallelism variants

1. **Deep chain:** one cross-module issue whose later stages depend on earlier evidence. Hard to parallelize.
2. **Broad surface:** several independent components contain unrelated issues. Obviously parallel.
3. **Shared root cause:** many symptoms originate in one library or security primitive. Collaboration should help correlation and deduplication.
4. **Verification-heavy:** plausible findings include subtle false positives and require independent reproduction or challenge.

### Submission

A bounded set of structured findings plus one final patch set. Every finding must include the affected component, root cause, non-destructive reproducer, demonstrated security property violation, impact rationale and remediation link.

### Hidden ground truth

- A private manifest of vulnerabilities and frozen impact weights.
- Vulnerable and clean/fixed twins.
- Hidden security, functional, regression and performance tests.
- Plausible but non-vulnerable decoys.
- Semantic mutations such as renamed identifiers, altered control flow and moved components.
- Identical target digests across all conditions in a block.

### Primary score

For each unique hidden root cause with severity weight `w`:

- award `0.4w` when the evaluator independently reproduces the claimed violation and confirms the root cause;
- award the remaining `0.6w` when the submitted patch blocks all hidden attack cases without breaking required behavior.

```text
security_value_captured = clamp_0_100(
  100 * earned_hidden_weight / total_hidden_weight
  - false_positive_penalties
)
```

Severity weights come from the evaluator's demonstrated-impact policy, never the agent's claim. Broadly disabling the service, removing required functionality or creating a material regression fails the remediation component. Duplicate reports for one root cause receive no extra credit. Unsupported unique reports incur a frozen penalty, and a report cap prevents flooding.

An agent cannot classify its own report as unseeded. The evaluator first processes every submission under the confirmatory rules; only a finding independently validated as genuinely absent from the hidden manifest may move into a separate quarantined exploratory lane. That lane has its own small submission cap to prevent review flooding. Such findings are preserved for human validation and responsible disclosure, but neither escape confirmatory penalties retroactively nor change the confirmatory score denominator.

### Anti-gaming controls

- Reproduce agent-supplied tests on vulnerable and fixed/clean twins.
- Use evaluator-owned hidden attack oracles in addition to agent PoCs.
- Deduplicate by underlying root cause, not report wording or endpoint.
- Include clean negative controls and attractive false leads.
- Keep manifests, seeds and hidden tests outside agent-visible storage.
- Reset the target before every evaluation.
- Run functional and performance regressions against every patch.
- Cap findings and require executable evidence rather than confidence claims.

### Safety boundary

- Use only benchmark-owned or explicitly authorized code.
- Run inside an isolated VM or network namespace with no route to public systems.
- Use synthetic users, credentials, data and domains.
- Permit research only through the bounded read-only broker; prohibit code or target uploads.
- Restrict offensive tools to the declared target and enforce time and resource limits.
- Quarantine exploit artifacts and prohibit automatic external publication.
- Define a stop, preserve and human-review procedure for accidental real findings.

Historical-vulnerability suites can validate the harness, but private transformations or owned targets are preferable for confirmatory runs because public instances may be memorized.

## Scenario 4: simulated customer operations

### Why add this scenario

Customer operations provides realistic non-coding business work, deterministic outcome state and the clearest contrast between raw parallelism and collaboration.

### Mission

Operate a queue of support, renewal and account cases using a simulated CRM, order system, policy manual and bounded action API. The queue contains more available work than a solo agent can comfortably complete before the deadline, while still allowing a solo agent to earn a valid graded score.

For the first version, agents see the entire scored queue, inspect read-only case state and submit an immutable action plan plus customer responses. The evaluator hides the correct dispositions, downstream consequences, case values and state-transition oracle, then replays the selected plan from a clean simulator snapshot. Public tool qualification and feedback use separate demonstration cases rather than an unseen replacement queue. This avoids turning live shared CRM state into an unintended communication channel. A later study can explicitly test a shared action gateway and concurrent state changes.

### Matched variants

**Independent queue:** cases concern unrelated customers and causes. They can be solved independently. Multiple agents should improve coverage, while collaboration can still add mechanical fan-in. Any uplift here should first be interpreted as aggregation benefit rather than sophisticated coordination.

**Linked queue:** cases contain recurring root causes, duplicate requests, related accounts, shared policy changes and plan dependencies. Collaboration can support correlation, reuse, deconfliction and prioritization. Because V1 replays one plan after closure, it tests information integration rather than concurrent shared-state coordination; live interacting actions belong to the later action-gateway study.

Keep case count, nominal difficulty, value, action budget and information volume matched between variants. Include some misleading similarities and apparently unrelated cases so crude deduplication is not always rewarded.

### Submission

A portable ordered plan containing idempotent simulator actions, case dispositions, evidence references and customer-facing responses. Each candidate must stand alone; collaborating agents may explicitly publish fragments and assemble them, while isolated peers submit separate complete candidates and the neutral selector chooses the strongest.

### Hidden evaluation

Each case has hidden initial state, permitted actions, resolution conditions, business value, policy rules, deadlines and harmful-action costs. Replay the candidate from a clean snapshot and evaluate final state rather than trusting its prose.

```text
operations_score = clamp_0_100(
  100 * verified_case_value_completed / total_available_case_value
  - normalized_harm_penalties
)
```

Case value is earned only when the required state transition is correct and the response includes required facts. Partial safe progress may receive a frozen fraction. Business priority and SLA importance belong in the case weights rather than in an after-the-fact judge score.

Report separately:

- cases correctly resolved;
- weighted business value completed;
- SLA and priority performance;
- duplicate, contradictory or invalid actions;
- unnecessary escalation or compensation;
- policy, privacy and customer-harm violations;
- response factual completeness and tone;
- API cost and wall time.

Severe privacy, authorization or financial-policy violations should invalidate the affected case or apply a predeclared harm penalty that outweighs its possible value. Free-form tone can use a blind rubric, but core success should come from simulator state and required/forbidden facts.

### Anti-gaming controls

- Randomized synthetic identities, wording and account histories.
- Hidden state-transition oracle and clean replay for every candidate.
- Idempotency keys and deterministic conflict rules.
- No credit for closing a case without reaching its required state.
- Action, escalation and compensation budgets.
- Decoy similarities and hidden true links.
- Frozen policy version, separate demonstration cases for public feedback and a scored queue with hidden consequences and oracles.
- Scripted or state-machine customers for confirmatory runs; stochastic model users only as a separate ecological test.

### Important fan-in diagnostic

On a wide independent queue, `peer_collab` may win mainly because agents can combine separate case work into one candidate, while `peer_isolated` can only offer its best standalone candidate. That is a real benefit of communication under the current product thesis, but it should be labelled **fan-in benefit**, not evidence of sophisticated coordination.

A later diagnostic can add an `isolated + neutral aggregation` arm using a deterministic, condition-blind and budget-preserving union of case plans. It uses frozen conflict and deduplication rules, sees no hidden state or outcome oracle, and cannot exceed the same organisation-wide action or submission budget. Comparing it with isolated best-of-N estimates mechanical fan-in; comparing open collaboration with neutral aggregation estimates coordination beyond fan-in. This is appropriate only where merging is genuinely mechanical, not for economics prose, interacting model optimizations or patch sets.

## Common evaluation contract

Every scenario should follow the same experimental discipline even though its outcome units differ:

1. **Evaluate the final outcome.** Messages, apparent teamwork and elegant plans do not earn task points.
2. **Use task-native units.** Model serving reports percentage improvement; economics uses an anchored 0–100 criterion score; security reports hidden security value captured; operations reports verified business value completed.
3. **Do not create a cross-domain leaderboard.** Compare `peer_collab - peer_isolated` within each scenario. Standardized effects across tasks are exploratory only.
4. **Freeze validity gates and failure floors.** Every randomized campaign remains in the analysis.
5. **Select publicly, score privately.** Candidate selection uses bounded public feedback; hidden tests evaluate only the selected artifact.
6. **Use several held-out variants.** One prompt, repository, workload or queue is not a task family.
7. **Make the task large enough to expose structure.** A four-agent pilot needs several meaningful branches or work units, not four cosmetic subtasks.
8. **Cap brute-force surfaces.** Limit candidates, findings, actions, research calls and compute experiments.
9. **Calibrate for headroom.** Solo must be able to produce a valid result, but neither floor nor ceiling effects should dominate.
10. **Report resource frontiers.** Equal API and compute budgets provide the first causal comparison; later report outcome against API dollars, GPU dollars and wall time so higher spend can be traded for faster or better results explicitly.
11. **Register task families separately.** Do not treat “at least one significant win” across four opportunities as confirmation. Report each family on its own and preregister any later multiplicity-controlled or hierarchical synthesis.

## Mechanism diagnostics

These measures help explain an outcome but never enter the primary score:

- unique useful branches explored;
- exact and near-duplicate work;
- negative-result reuse;
- cross-agent artifact derivation;
- time to first valid improvement or confirmed finding;
- integration failures and conflicting changes;
- independent verification and challenge;
- hypothesis or output diversity and convergence;
- coordination-token overhead;
- fan-out, fan-in and unresolved work at closure.

## Suggested sequence

1. **Model serving:** retain the current V0 because the endpoint is executable and directly tied to the existing architecture.
2. **Customer operations:** implement independent and linked queues next. This is comparatively cheap, mostly deterministic and gives the cleanest structural contrast.
3. **Economics:** run the compact and empirical variants once the blind-rater and artifact-evaluation pipeline is ready.
4. **Cybersecurity:** add after sandboxing, quarantined artifacts and private-target generation are mature.

Across all four, start with `N = 4`. Scaling to larger fleets is a separate registered study. Eventually test both fixed-total-resource scaling and roughly fixed-per-agent scaling; otherwise increasing `N` can mean either more organizational capacity or thinner budgets per agent.

## Open design decisions

- Which small target model, serving stack and cheap GPU produce enough headroom without long iteration cycles?
- Which public quality evaluations are fast enough to run repeatedly while resisting task-specific optimization?
- What word count, model complexity and rater composition make the economics task feasible but non-trivial?
- Should the security target be purpose-built, a private licensed fork or generated semantic mutations across several codebases?
- Can an existing customer-service simulator be adapted without importing its orchestration assumptions?
- How much public feedback should each task return before it becomes an optimization oracle?
- Which scenario variants are similar enough in difficulty to support a meaningful condition-by-task-structure analysis?

## Useful benchmark patterns

- [MLPerf Inference](https://mlcommons.org/benchmarks/inference-datacenter/) provides the useful pattern of throughput measurement under explicit quality and latency constraints; this design need not reproduce the full MLPerf suite.
- [MLPerf Client](https://mlcommons.org/benchmarks/client/) separates time to first token from subsequent tokens per second, which should also be reported here.
- The [New York Fed r-star project](https://www.newyorkfed.org/research/policy/rstar) publishes official LW/HLW descriptions, estimates, code and data suitable for a frozen economics source room.
- [CyberGym](https://www.cybergym.io/cybergym/) and [CyberGym-E2E](https://www.cybergym.io/cybergym-e2e/) demonstrate executable evaluation of historical vulnerability reproduction, discovery and remediation. They are design references, not evidence that a public instance is contamination-free.
- [NIST Juliet](https://www.nist.gov/publications/juliet-11-cc-and-java-test-suite) and the [OWASP Benchmark](https://owasp.org/www-project-benchmark/) are useful for security-harness calibration and negative controls.
- [tau-bench](https://github.com/sierra-research/tau2-bench) provides a useful policy, tools, tasks and hidden-state pattern for simulated customer operations.
