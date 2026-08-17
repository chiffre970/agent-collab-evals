# Local setup

This repository contains the collaboration-evaluation design, local core,
first executable campaign pack and credential/infrastructure preflights. The
local lifecycle remains deliberately runnable without provider or GPU spend.

## Local environment

Create a project-local virtual environment and install tools into it:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

This installs the repository itself in editable mode plus the pinned Modal
client. Verify the local core and scenario pack with:

```bash
collab-evals validate-scenario
collab-evals fake-solo
python -m unittest discover -s tests -v
```

`fake-solo` writes its atomic snapshot and append-only event log below ignored
`tmp/fake-solo/`. It starts a campaign, delivers the serving mission, persists
the runtime snapshot, resumes it through a fresh harness instance and closes
it. The unit suite additionally delivers a second job after resume.

Install the pinned OpenRouter SDK:

```bash
npm install
```

Copy the example environment file if `.env` does not already exist:

```bash
cp .env.example .env
```

`.env` is ignored by Git. Do not put credentials in manifests, documentation,
agent workspaces or committed files.

## OpenRouter

Put the OpenRouter API key after `OPENROUTER_API_KEY=` in `.env`. The dated
model, endpoint, expected returned identity, provider route, privacy policy and
preflight inference settings live in the versioned
`config/model_profiles/deepseek-v4-flash-openrouter-deepinfra-development.json`
profile.
They are deliberately not environment variables: changing a behavior-changing
input must create a reviewable profile change and digest. Validate that profile
without a credential or network call with:

```bash
npm run check:openrouter
```

Then run the small streaming request with:

```bash
npm run preflight:openrouter
```

An alternate committed development profile can be checked or exercised
explicitly without putting the model or provider in `.env`:

```bash
node scripts/preflight/openrouter.mjs --validate-profile \
  --profile config/model_profiles/example-development.json
npm run preflight:openrouter -- \
  --profile config/model_profiles/example-development.json
```

The selector accepts only a JSON file directly below `config/model_profiles/`.
A registered run does not use this CLI default: its immutable study manifest
names one exact profile and disables provider fallbacks.

The command streams the answer, verifies the response against a deterministic
canary, and waits up to 15.5 seconds for OpenRouter's eventually consistent
generation metadata to attest the returned provider and canonical model,
prints token/cost metadata and writes a key-free JSON receipt under ignored
`tmp/preflight/`. The receipt includes the exact profile-byte digest. It fails
rather than silently accepting another provider or model, or treating a
reasoning-only response as usable. This is a development profile; a registered
campaign must reference a separately frozen registered profile from its
immutable study manifest.

The profile's `preflight.max_completion_tokens` setting is sent as the legacy
`max_tokens` wire parameter because the pinned DeepSeek endpoint advertises
that parameter. `require_parameters` remains enabled so future incompatibility
fails visibly instead of being silently ignored.

## Modal

Authenticate the local CLI with:

```bash
modal setup
```

Modal stores the resulting token in `~/.modal.toml`, outside this repository.
For headless environments, use the token environment variables documented in
`.env.example` and inject them through the environment or a secret manager.

Use the `dev` Modal environment for calibration. Create a dashboard secret
named `huggingface-secret` containing the key `HF_TOKEN`. Do not also copy that
token into `.env`: Modal injects it only into functions that explicitly request
the secret. Modal secrets are environment-scoped; a secret created in `main`
must be created again in `dev` before the commands below will find it.

Verify Modal execution and authenticate to Hugging Face from a CPU container:

```bash
.venv/bin/modal run -e dev scripts/preflight/modal_access.py
```

After that succeeds, explicitly request a short, billable L4 allocation check:

```bash
.venv/bin/modal run -e dev scripts/preflight/modal_access.py --gpu
```

The GPU check only invokes `nvidia-smi`; it does not download a model.

Run the separate, billable stock-reference smoke check with:

```bash
.venv/bin/modal run -e dev campaigns/model_serving_v0/reference/modal_vllm.py
```

It builds the declared vLLM image, allocates at most one L4, downloads the
exact model revision into a named Modal cache volume, starts the private
OpenAI-compatible server and requires an exact chat canary. Retries are
disabled and the function is capped at 1,800 seconds. A cold first invocation
can take several minutes. The command writes a key-free receipt atomically to
ignored `tmp/calibration/model-serving-reference-smoke.json`; pass
`--output-path` to select a different local receipt path. The CUDA base image
is pinned to its linux/amd64 manifest digest rather than relying on the mutable
tag alone.

The reference smoke is not the serving baseline. After explicitly approving
the GPU spend, run exactly one baseline repetition with:

```bash
.venv/bin/modal run -e dev campaigns/model_serving_v0/reference/modal_vllm.py \
  --baseline --repetition 1 --attempt 1
```

The command uses a fresh single-use container and server process, but scores
only after health, a server canary and two fixed warmup requests per point. It
runs all nine points in canonical order and atomically writes the raw and
normalized evaluator-private bundle below ignored
`tmp/calibration/model-serving-reference/`. Startup, total client-observed time
and in-container function-body time are recorded separately from the serving
score. If an infrastructure-only failure qualifies for the one allowed
whole-repetition retry, rerun that repetition with `--attempt 2`; both attempts
remain durable.

Formal baseline commands require a clean Git worktree and record the commit and
Modal client version before allocating the GPU. The measurement profile also
requires the exact resolved package-set digest, GPU memory, driver and power
limit observed in the engineering pilot; drift fails the repetition rather
than silently changing the calibration environment.

Do not start repetition 2 until repetition 1 is inspected. Later repetitions
fail closed if the resolved package set, base image, GPU model, memory, driver
or power limit changes. Baseline calibration output is not confirmatory
evidence.

After the calibration scorer is committed, the prepared architecture-neutral
sensitivity candidate can be measured with the same adapter:

```bash
.venv/bin/modal run --detach -e dev campaigns/model_serving_v0/reference/modal_vllm.py \
  --baseline \
  --candidate-path campaigns/model_serving_v0/candidates/vllm-stream-interval-10.json \
  --measurement-id candidate-stream-interval-10-sensitivity-v1 \
  --repetition 1 --attempt 1 \
  --dispatch-only
```

The dispatch command atomically records the Modal function-call identifier and
returns immediately. This decouples a long GPU measurement from the lifetime
of the local terminal. Poll and collect the same call without allocating a
second GPU:

```bash
.venv/bin/modal run --detach -e dev campaigns/model_serving_v0/reference/modal_vllm.py \
  --baseline \
  --candidate-path campaigns/model_serving_v0/candidates/vllm-stream-interval-10.json \
  --measurement-id candidate-stream-interval-10-sensitivity-v1 \
  --repetition 1 --attempt 1 \
  --collect-only --collect-timeout-seconds 30
```

The candidate keeps the same target model, weights, engine, image and hardware;
it changes only vLLM's supported stream interval from 1 to 10. The pinned
[vLLM engine-argument documentation](https://docs.vllm.ai/en/v0.21.0/configuration/engine_args/#schedulerconfig)
states that larger intervals reduce host streaming overhead and may increase
throughput. The evaluator still measures actual TTFT and TPOT, so delayed first
delivery is not hidden. Complete repetitions 2 and 3 only after inspecting the
preceding receipt. This is a calibration sensitivity run, not a confirmatory
comparison. Repeating either command for the same attempt resolves the durable
dispatch record; it never knowingly creates a second function call. A terminal
remote failure is committed as invalid evidence, while a collection timeout or
client connection interruption leaves the call pending and retrievable. The
evaluator assigns one stable `--measurement-id` to a calibration series and
uses it for every repetition and collection command; the ID is not supplied by
the candidate or an agent.

This split is intentional. Modal distinguishes queueing/container
initialization from function execution in its
[cold-start documentation](https://modal.com/docs/guide/cold-start), and its
[call-graph capture](https://modal.com/docs/sdk/py/latest/FunctionCall) is
documented as best-effort rather than a critical timing interface. The
evaluator therefore uses vLLM's in-container monotonic benchmark timer for
goodput and latency, while retaining client and function-body durations only
for operations and cost analysis.

Verify the local installation with:

```bash
.venv/bin/modal --version
git status --ignored --short
```

### Durable evaluator evidence

Formal calibration calls write raw benchmark files and their remote receipt to
the evaluator-owned Modal Volume
`agent-collab-evals-evaluator-evidence-v2` before returning a small result.
The GPU function has restricted Modal API access and commits through the v2
Volume mount. The trusted local collector downloads every file, verifies its
digest, publishes the normalized receipt once and keeps the existing ignored
local bundle only as a convenience mirror.

Verify this path without allocating a GPU:

```bash
.venv/bin/modal run -e dev campaigns/model_serving_v0/reference/modal_vllm.py \
  --evidence-probe
```

The probe must prove restricted write/sync and trusted readback before a
billable measurement. A large raw result is never returned through Modal's
function-result channel.

The evaluator-private quality workload is prepared separately:

```bash
.venv/bin/python scripts/calibration/fetch_quality_sources.py
.venv/bin/python scripts/calibration/materialize_quality_workload.py
```

See [the quality calibration ledger](calibration/MODEL_SERVING_QUALITY_V0.md)
for its source, profile and private workload commitments.

After materialization, dispatch one reference quality repetition without
keeping a terminal attached:

```bash
.venv/bin/modal run --detach -e dev campaigns/model_serving_v0/reference/modal_vllm.py \
  --quality \
  --candidate-path campaigns/model_serving_v0/reference/candidate.json \
  --measurement-id qwen-quality-reference-v1 \
  --quality-role reference \
  --repetition 1 --attempt 1 \
  --dispatch-only
```

Collect that exact call with:

```bash
.venv/bin/modal run --detach -e dev campaigns/model_serving_v0/reference/modal_vllm.py \
  --quality \
  --candidate-path campaigns/model_serving_v0/reference/candidate.json \
  --measurement-id qwen-quality-reference-v1 \
  --quality-role reference \
  --repetition 1 --attempt 1 \
  --collect-only --collect-timeout-seconds 30
```

The known-clean control uses the same workload and seeds but the stream-only
candidate artifact:

```bash
.venv/bin/modal run --detach -e dev campaigns/model_serving_v0/reference/modal_vllm.py \
  --quality \
  --candidate-path campaigns/model_serving_v0/candidates/vllm-stream-interval-10.json \
  --measurement-id qwen-quality-clean-control-v1 \
  --quality-role clean_control \
  --repetition 1 --attempt 1 \
  --dispatch-only
```

Use the same command with `--collect-only --collect-timeout-seconds 30` in
place of `--dispatch-only` to collect it. Complete repetitions 2 and 3
sequentially, changing only `--repetition`, after each preceding receipt is
valid. Raw prompts, answers and responses stay under the evaluator-owned
Volume and ignored `tmp/evaluator-private/` mirror; the normalized score keeps
only answer extractions and content digests. These runs calibrate the quality
gate and do not constitute a confirmatory experiment.
