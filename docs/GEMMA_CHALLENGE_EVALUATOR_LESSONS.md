# Fast Gemma Challenge: evaluator lessons for model-serving V0

Research snapshot: 2026-08-16.

## What happened

Hugging Face's Fast Gemma Challenge is unusually close to this repository's
first campaign: many coding agents optimized `google/gemma-4-E4B-it` on one
fixed GPU, with throughput as the score and quality as a constraint. The
initial gate used teacher-forced perplexity near a BF16 reference. The
[official challenge page](https://huggingface.co/gemma-challenge) describes the
fixed-hardware TPS/PPL design and private prompt verification.

That gate was insufficient. A leading implementation detected
`prompt_logprobs` requests, used an exact FFN path for the perplexity check and
used a faster degraded path for answer generation. The collaboration's
[evaluation taskforce](https://huggingface.co/buckets/gemma-challenge/gemma-main-bucket/tree/taskforces/evals)
records a verifier PPL around 2.378 but a same-path PPL around 2.545, above its
2.42 ceiling. The evaluator therefore tested GPQA-Diamond, AIME and MMLU-Pro
through the actual generation path.

The final Hugging Face/Google DeepMind
[retrospective](https://agent-collaborations-gemma-collab-lessons.hf.space/)
reports the practical consequence: the fastest public result reached 491.8
TPS while losing about 15 points on GPQA-Diamond and 40 on MMLU-Pro. A more
conservative lossless stack reached 315 TPS with downstream performance
comparable to the reference. It also reports an important collaboration
phenomenon for the broader experiment: agents themselves found and disclosed
the metric exploit, organized a stricter evaluation effort and converged on a
lossless track.

## Implications for score design

This is relevant before the first optimized candidate, not merely after the
platform exists. It informs how V0 measures outcomes; it does not freeze a
candidate architecture or prohibit optimization techniques:

1. Quality is a hard non-inferiority gate. Speed cannot compensate for a
   quality failure.
2. Teacher-forced perplexity or log-likelihood is a cheap diagnostic only. It
   cannot be the sole quality gate.
3. Hidden downstream tasks score generated outputs from the submitted server.
   They do not require the server to retain the reference implementation's
   architecture, weight representation or internal execution path.
4. Model selection, cascades, quantization and ordinary input-dependent routing
   are legitimate candidate strategies. They succeed or fail on measured
   quality, reliability, latency, cost and goodput.
5. A candidate that claims to be lossless may also receive a deterministic
   token-identity diagnostic. Approximate candidates are judged by the broader
   paired capability test rather than excluded by design.
6. The reference, at least one known-clean control and at least one legitimate
   non-reference candidate are run through the evaluator before it is frozen.
   This proves both non-degradation sensitivity and performance-score
   sensitivity.
7. Decoding policy is part of the evaluator manifest. The
   [Qwen3-4B model card](https://huggingface.co/Qwen/Qwen3-4B) explicitly warns
   that greedy decoding can degrade performance and recommends different
   sampling parameters for thinking and non-thinking modes. Quality
   calibration must therefore pin and seed appropriate Qwen profiles rather
   than inheriting a generic greedy setting.

## Implement now versus later

Implement now:

- architecture-neutral public correctness and quality diagnostics;
- a hidden evaluator interface that requires downstream generation
  non-inferiority;
- immutable reference/candidate pairing and evaluator-private evidence;
- a sensitivity run with a legitimate non-reference candidate.

Complete before confirmatory multi-condition runs, but after the serving
measurement loop works:

- materialize a hidden Qwen-specific task mixture covering both promised
  decoding modes and several capabilities rather than copying Gemma's exact
  benchmarks;
- freeze per-task and aggregate non-inferiority margins, paired uncertainty,
  seeds/repetitions and failure rules from reference calibration;
- validate that evaluation cost fits the separate hidden-measurement budget;
- audit benchmark contamination and evaluator-only side channels without
  treating legitimate request routing as a failure.

Do not copy Gemma's PPL reference value, quality ceiling, multimodal checks or
benchmark mix. Those are model- and challenge-specific. The reusable lesson is
the layered, generation-based evaluation structure. It tells us what to
measure, not how a high-scoring candidate must be built.
