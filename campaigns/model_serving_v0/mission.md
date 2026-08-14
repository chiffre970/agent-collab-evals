# Mission: improve small-model serving

Improve the verified serving performance of the supplied `Qwen/Qwen3-4B`
model on exactly one NVIDIA L4 while preserving API compatibility, output
quality, correctness, and reliability.

Your deliverable is one self-contained candidate artifact with a
`candidate.json` launch manifest conforming to `submission.schema.json`.
Public checks and workloads may be used during development. Hidden evaluation
uses a disjoint workload after submissions close.

You may change the inference engine, batching, scheduling, kernels,
compilation, cache management, precision, and other legal serving settings.
Quantization is allowed only when it passes the quality gates. You may not
change the model identity or revision, GPU type or count, evaluator, workload,
required API paths, served model name, or hardware allocation.

Optimize sustained goodput under the eventual frozen latency SLO. Calibration
will determine that SLO from the stock reference; do not hard-code behavior for
the visible correctness prompts or benchmark inputs.
