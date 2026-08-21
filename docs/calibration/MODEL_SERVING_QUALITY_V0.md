# Model-serving quality calibration

Status: corrected V2 calibration workload materialized and the V0 quality
policy frozen from three valid reference/clean-control pairs. This remains
engineering calibration, not confirmatory study evidence.

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

## Completed calibration and frozen gate

Three served-generation repetitions completed for the pinned reference and
the known-clean stream-interval candidate using the same cases and per-case
seeds. Every run completed on attempt 1, passed environment and canary checks,
and was reconstructed from digest-verified evaluator-owned Volume evidence.

| Role | Repetition | Modal app / function call | Passes | Startup | Served generation | Function body | Local receipt SHA-256 |
| --- | ---: | --- | ---: | ---: | ---: | ---: | --- |
| Reference | 1 | `ap-DPz5vUIkKRGDLqBPUz8Cx8` / `fc-01M06QHWPK1F3VT5TKVF64J0HA` | 56/64 | 220.264 s | 279.501 s | 503.838 s | `70c7e9a06325c1cf2628269087001eaefffb29613784cf09bdb06a6cfa3a0f1d` |
| Clean control | 1 | `ap-JjpQEQ8QJuWOEODAlzCJYr` / `fc-01M06R83J977Y2DNJ9XMX8BY4M` | 55/64 | 254.323 s | 228.799 s | 487.648 s | `8625a38e25a19b3250a9fcb5efb1984115b68770c6a6180e0c889d95a707b178` |
| Reference | 2 | `ap-E88nHV9Q0gQRdlbd1IecJi` / `fc-01M06RY02EJQECTA3RWHC3ZYDC` | 56/64 | 217.265 s | 237.072 s | 458.432 s | `1a71aaa42b3858b68c633de05db61b53f7bceac07dd0541f0f2ccda3ce1ee0a6` |
| Clean control | 2 | `ap-lw8jBRq31tFKusVQPgbMHX` / `fc-01M06SJ0ZNCXAF6K05RNVAXHVP` | 55/64 | 249.334 s | 274.564 s | 528.286 s | `6fb787e515f7de484a60d6f1a44f4e8ba1506378336246526028f286f8d09a22` |
| Reference | 3 | `ap-FlYX2rG2F1SlQ133j0ZESa` / `fc-01M0GZQA6B4C4ZE87K7E6PS8KG` | 56/64 | 262.336 s | 241.081 s | 507.840 s | `3a43c23370e8e5ebdb5ff5217a7e8011a472b60b213a30493ea70456623adc97` |
| Clean control | 3 | `ap-UQ1dVZmfW9WKwTFi36wg3R` / `fc-01M0H0EQPQFYEXY11RGF0WJ0VR` | 56/64 | 239.313 s | 239.381 s | 482.810 s | `9249bafd1e26c280caa025231b6ab935a2ea2a19fffdb6a2fd76097c18060ad3` |

Across 192 paired observations, the reference passed 168 and the clean
control passed 166. The transitions were 166 pass/pass, two pass/fail, zero
fail/pass, and 24 fail/fail. The aggregate clean-control delta was -10,417
ppm. The losses occurred once in BBH reasoning and once in GSM8K; MMLU and
structured transformation had zero aggregate movement.

The frozen policy has digest
`sha256:eae72dc6000cdc7ecb0b09c012030bed0d66e668644040f8a8f1faae57a2bee4`
and uses:

- three paired repetitions of every case;
- a 31,250 ppm aggregate non-inferiority margin;
- a 62,500 ppm per-family margin;
- an inclusive one-sided 95% percentile bootstrap lower bound;
- case ID as the cluster, retaining all three paired outcomes together;
- stratification by family, 100,000 resamples, and the pinned SplitMix64
  resampling algorithm and seed; and
- a failed case for any missing or malformed response.

The clean control's aggregate lower bound was -20,833 ppm. Its worst family
lower bound was -62,500 ppm, which passes the inclusive family gate exactly.
The margins correspond to two of 64 aggregate cases and one of 16 family
cases. This keeps the gate interpretable at the suite's resolution rather
than fitting an arbitrary fractional threshold to the observed control.

The six function bodies used 2,968.854 L4-seconds. At the calibration rate of
$0.000222 per second, the GPU component is approximately $0.659, excluding
CPU, memory, volume, and other charges. Modal billing remains authoritative.

Reproduce the decision from the private local mirror with:

```bash
PYTHONPATH=src .venv/bin/python \
  scripts/calibration/evaluate_quality_series.py
```

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
