# Model-serving quality calibration

Status: corrected V2 calibration workload materialized; reference and
clean-control runs pending. This is not a frozen confirmatory quality policy.

## Purpose

Serving speed is eligible only when generated-answer quality is non-inferior to
the pinned reference. Teacher-forced likelihood remains diagnostic. The
authoritative path sends held-out requests to the submitted server and scores
the responses it actually returns.

The calibration profile follows the target model's own decoding guidance:

- Qwen non-thinking: temperature 0.7, top-p 0.8, top-k 20, min-p 0;
- Qwen thinking: temperature 0.6, top-p 0.95, top-k 20, min-p 0;
- no greedy decoding; every case has an evaluator-derived fixed seed.

The corrected V2 execution profile permits up to eight concurrent requests,
uses a 300-second per-request timeout under the unchanged 1,800-second whole
function cap, and allows up to 4,096 tokens for thinking responses. These are
versioned evaluator inputs, not candidate implementation requirements.

These parameters come from the pinned
[Qwen3-4B model card](https://huggingface.co/Qwen/Qwen3-4B). They define the
evaluation requests, not how a candidate must be implemented.

## Materialized mixture

The private calibration workload contains 64 cases, with 16 cases per family:

| Family | Mode | Objective scorer | Source |
| --- | --- | --- | --- |
| MMLU | non-thinking | choice | OpenAI simple-evals data snapshot |
| Structured transformation | non-thinking | exact | evaluator-generated from the private seed |
| GSM8K | thinking | numeric | official OpenAI repository |
| BBH reasoning | thinking | choice | date understanding and five-object logical deduction |

The committed source profile records immutable repository revisions where
available and a required SHA-256 digest for every downloaded byte sequence.
The MMLU blob URL is mutable, so its content digest—not its URL or the linked
simple-evals implementation commit—is the data identity.

The local materialization completed with:

- profile digest:
  `sha256:297f7e46f1d78b740ff22baf888a8ac3d7f2487573090b9755e00c197b14e36a`;
- selection-seed commitment:
  `sha256:8504c8f2e324f7e1b3b60f4089d959bf254dfc45449efd0ae45aa8b7b5d8d212`;
- workload digest:
  `sha256:9f129b49dda34d6af30d3e6a59f6ea945756b67fae32fbefca0ed9444492e8f4`.

The seed, selected prompts, answers and row indices remain under ignored
`tmp/evaluator-private/`. Agents receive neither the workload nor its outputs.

Reproduce or verify the private materialization with:

```bash
.venv/bin/python scripts/calibration/fetch_quality_sources.py
.venv/bin/python scripts/calibration/materialize_quality_workload.py
```

The source fetcher refuses content whose digest differs. The materializer
creates its 32-byte seed once with mode `0600`, refuses a changed workload at
the same destination and emits commitments rather than secret material.

## Remaining calibration gate

Run three served-generation repetitions for the pinned reference and the
known-clean stream-interval candidate using the same cases and per-case seeds.
Use their paired case transitions to freeze:

- aggregate and per-family non-inferiority margins;
- the paired uncertainty procedure;
- malformed/missing-response handling;
- the hidden quality GPU budget.

The margins must be frozen before optimization agents receive an evaluator.
The suite is deliberately broader than a perplexity check but is not claimed
to be a general model leaderboard. Its role is to detect degradation caused by
serving changes on the target model and decoding modes promised by the mission.

The executable runner is the `--quality` mode of
`campaigns/model_serving_v0/reference/modal_vllm.py`. It uses the same pinned
image, model revision, Modal L4 profile, durable dispatch records and
evaluator-owned evidence Volume as the performance runner. Reference and
clean-control series have distinct stable measurement IDs, and repetitions
must be dispatched and inspected sequentially. Exact commands live in
`docs/SETUP.md`.

## Superseded V1 diagnostic attempt

The first reference attempt used profile digest
`sha256:23276722f129e16096be5af5a950783fd2645fad92e21951b97760472b924c19`
and workload digest
`sha256:e3abb195aa18b3be0caaf21c333031de83229881aaf0a76fad8ea0381d7c7908`.
Modal app `ap-W2SwV1kzqlCXxbok8WMhED`, function call
`fc-01M06N08SX49KQXR4S05G721AN`, completed all 64 requests and durably
committed its receipt and raw responses. The function body took 1,731.628
seconds, including 233.317 seconds of startup and 1,494.102 seconds of served
generation. It generated 43,887 completion tokens; nine responses ended at
the 2,048-token ceiling.

The attempt is invalid and is not admitted as a quality score. Its roughly
15-KiB metadata return crossed Modal's inline-result boundary and attempted a
blob upload, which restricted Modal access correctly rejected with HTTP 401.
Diagnostic scoring also revealed that wording such as
`<answer>LETTER</answer>` was interpreted literally: all MMLU and BBH answers
failed the format parser even when the surrounding response named an option.
That measures a prompt/scorer defect, not serving quality.

V2 therefore:

- tells the model to replace an unambiguous `X` inside the answer tag;
- asks for concise reasoning and raises the thinking ceiling to 4,096;
- pins eight-way request concurrency and a 300-second request timeout; and
- returns only a small digest-bound evidence pointer while the trusted
  collector reconstructs the full receipt from the evaluator Volume.

The V1 failure bundle and remote bytes remain immutable. V2 uses new profile,
workload and measurement identities rather than retrying or relabeling V1.
