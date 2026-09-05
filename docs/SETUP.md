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
collab-evals fake-candidate-lifecycle
collab-evals rehearse-study
collab-evals rehearse-solo-adapters
python -m unittest discover -s tests -v
```

`fake-solo` writes its atomic snapshot and append-only event log below ignored
`tmp/fake-solo/`. It starts a campaign, delivers the serving mission, persists
the runtime snapshot, resumes it through a fresh harness instance and closes
it. The unit suite additionally delivers a second job after resume.

`fake-candidate-lifecycle` creates two actor-owned candidate artifacts, reserves
fixed non-transferable public-evaluation allowances, withholds results until
the release boundary, resolves scores from an evaluator-owned receipt ledger,
persists reference-aware neutral selection, evaluates the selected candidate or
reference artifact under a separate hidden allowance, and seals the artifact
store. Its scores are deterministic fixtures; it launches no candidate process
and uses no model API or GPU.

`rehearse-study` executes the complete randomized four-condition lifecycle
against local fakes. `rehearse-solo-adapters` instead exercises the real stock
OpenCode runtime, sandbox, session-scoped model gateway, SQLite budget account,
delivery outbox and close gates for the solo condition. Its model upstream is
deterministic and in-process, its compute manifest disables dispatch, and it
passes no credentials to OpenCode. It writes a canonical audit below
`tmp/adapter-rehearsals/` and replays the independent durable stores before it
reports success. Neither command contacts a provider, uses a GPU, or produces a
scoreable result.

Install the pinned OpenRouter and OpenCode packages:

```bash
npm install
```

Copy the example environment file if `.env` does not already exist:

```bash
cp .env.example .env
```

`.env` is ignored by Git. Do not put credentials in manifests, documentation,
agent workspaces or committed files.

## OpenCode runtime

OpenCode and its stable SDK are pinned exactly to 1.18.19 in `package-lock.json`.
The matched peer sidecar pins the MCP SDK to 1.30.0 and Zod to 4.4.3.
The runtime adapter reads model and runtime behavior from
`config/runtime_profiles/`. Its caller must provide a `GatewayTokenIssuer`
backed by the budget gateway; the issuer creates one opaque, revocable token
per top-level session. The OpenCode bridge receives a complete minimal
environment with an isolated home and temporary directory. It does not inherit
host provider, Modal, GitHub or Hugging Face credentials. The selected runtime
profile transitively digests its separate agent-inference provider profile.

Run the deterministic stock-runtime proof without API or GPU spend:

```bash
npm run spike:opencode
```

The command starts only loopback servers. It routes OpenCode through a local
OpenAI-compatible fake gateway, restarts the stock server, exercises its native
`task` handoff, checks actual solo tool removal and compares effective-surface
digests with and without out-of-process event observation. Its ignored detailed
report is written below `tmp/opencode-conformance/`.

The ordinary unit suite skips the slower real-runtime and MCP tests. Run them
explicitly, still without API spend, with:

```bash
RUN_OPENCODE_INTEGRATION=1 RUN_PEER_TOOL_INTEGRATION=1 \
  python -W error::ResourceWarning -m unittest discover -s tests -v
```

On macOS, include the kernel egress proof with:

```bash
RUN_SANDBOX_INTEGRATION=1 \
  python -W error::ResourceWarning -m unittest -v tests.test_sandbox
```

The pinned `sandbox-exec` development profile allows every loopback destination
and denies nonloopback network egress. It does not restrict filesystem access or
process resources. OpenCode snapshots retain the profile digest. Scored runs
must add gateway-specific local-service isolation plus filesystem and resource
enforcement; a deployment cannot silently fall back to this partial boundary or
an unenforced process.

Run OpenCode through the local budget gateway and deterministic upstream with:

```bash
RUN_MODEL_GATEWAY_INTEGRATION=1 \
  python -W error::ResourceWarning -m unittest -v \
  tests.test_model_gateway tests.test_model_gateway_integration
```

The committed gateway profile is conformance-only and uses a synthetic rate
card. This command does not read `.env`, contact OpenRouter or incur spend.

Run the complete real-adapter four-condition rehearsal with:

```bash
RUN_MODEL_GATEWAY_INTEGRATION=1 \
  python -W error::ResourceWarning -m unittest -v \
  tests.test_adapter_rehearsal
```

This uses the pinned OpenCode runtime but a deterministic in-process model. It
forces the solo denial, native task, isolated-peer, and shared-peer surfaces,
then reconciles their durable evidence. It makes no external model or compute
call and is never scoreable.

Exercise the real solo candidate tools with synthetic evaluation:

```bash
.venv/bin/python -m agent_collab_evals rehearse-solo-candidates --run-id solo-candidate-001
```

This submits a predefined declarative candidate through real OpenCode, requests
synthetic public evaluation, releases results between two controller jobs, and
closes with budget and simulated-compute checks. It does not call an external
model or GPU. Use a new run ID for each invocation. This transport is
development-only and solo-only.

Add `--restart-runtime` to persist the campaign checkpoint, stop OpenCode and
the candidate gateway, reconstruct candidate services from disk, and resume
before reading the released result. The rehearsal checks session continuity,
capability rotation, and delivery replay without duplicate evaluation. The
synthetic model gateway remains running; this is a clean checkpoint recovery
test, not a whole-deployment crash qualification.

The shared candidate/native-admission gateway also supports per-capability
Unix sockets with host HTTP disabled. Local socket tests cover both services.
OCI relay wiring, matched peer-arm candidate tools, and registered denial
auditing remain pending; this transport support does not authorize OCI runs.

Inspect prerequisites without executing any workload:

```bash
.venv/bin/python -m agent_collab_evals readiness
```

This report does not authorize execution. Linux deployment, image and engine
pinning, native admission qualification, candidate capability transport, and
registered-study authority gates remain distinct requirements.

The registered-sandbox candidate additionally supports one dedicated Unix
socket per model-gateway token and peer-tool token. Exercise the direct socket
paths and in-container loopback relays locally with:

```bash
RUN_MODEL_GATEWAY_INTEGRATION=1 \
  python -W error::ResourceWarning -m unittest -v \
  tests.test_model_gateway_unix

RUN_PEER_TOOL_INTEGRATION=1 \
  python -W error::ResourceWarning -m unittest -v \
  tests.test_peer_tool_unix
```

This test also uses a deterministic local upstream and incurs no API spend. A
Docker-compatible engine is required only for the separate live OCI
conformance gate; do not treat this local relay test as container-isolation
evidence.

Run the dependency-free OpenRouter transport and generation-receipt tests with:

```bash
python -W error::ResourceWarning -m unittest -v \
  tests.test_openrouter_upstream tests.test_sqlite_budget
```

These tests use deterministic in-process HTTP doubles. They verify streaming,
bounded metadata retry, exact stream-to-generation correlation, provider/model
attestation and authoritative billed-cost persistence without reading
`OPENROUTER_API_KEY` or contacting OpenRouter.

Validate the committed development gateway and billing profiles without spend:

```bash
npm run check:model-gateway
```

Run one explicit live canary with a hard $0.01 gateway account and a 64-token
output limit:

```bash
npm run preflight:model-gateway
```

The current canary normally costs much less than one cent. It records a key-free
summary under ignored `tmp/preflight/`; only the gateway-side OpenRouter adapter
receives the API credential.

Do not point `OpenCodeHarnessRuntime` directly at OpenRouter for a study. Its
`OrganisationSpec.model_endpoint` must be the experiment-owned budget gateway;
that gateway will enforce the registered provider route, privacy settings and
dollar limit and issue the session token. The real adapter still rejects both
peer conditions unless the pinned peer profile and gateway are supplied
together, preventing an absent peer tool from silently collapsing treatment
and control.

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

Validate the frozen provider-selection policy and committed development record
without spend:

```bash
npm run check:provider-route
```

To refresh the mutable catalog evidence during calibration, first fetch and
retain exact endpoint and authenticated ZDR responses, then derive the candidate
snapshot:

```bash
npm run snapshot:provider-sources
```

Review and update the policy's snapshot path before qualifying a route. The
source command writes no credential and retains both compressed-file and raw
response digests.

To repeat the three-probe selected-route qualification under its hard `$0.05`
gateway cap, run:

```bash
npm run qualify:provider-route
```

The qualification sends two identical text probes and one forced tool-call
probe. It requires exact provider/model receipts, zero cached tokens and clean
budget reconciliation. It writes the key-free summary and owner-readable raw
receipts under ignored `tmp/provider-qualification/`. After review, retain one
passing record and its six receipts with:

```bash
npm run retain:provider-route -- \
  tmp/provider-qualification/provider-route-YYYY-MM-DDTHH-MM-SSZ.json
```

Update the development selection record to reference those retained paths and
digests. The retention command also appends one idempotent record to
`evidence/provider_qualification/development-attempts.jsonl`; failed or
diagnostic live attempts must be appended there as well, even when their bulky
raw files remain under ignored `tmp/`. `npm run check:provider-route` resolves
every source and receipt file, independently replays raw provider evidence to
reconstruct identity, usage and cost, validates the attempt-index digest and
rejects missing, altered or semantically inconsistent evidence.

For scored campaigns, generate a `budget-plan/v1` document only after actor
allocations and the billing rate card are frozen. Put its exact digest in the
resolved run manifest and pass both the loaded plan and the registered
provider-receipt verifier to the budget account. A `registered` plan cannot load
without that expected manifest digest. Never derive the close-time authority
from the SQLite ledger it is intended to check.

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

### Durable development compute path

The development composition wraps the same evaluator in a durable execution
state machine. Its committed profile fixes the campaign, evaluator script,
Modal client, `dev` environment, evidence Volume and one public repetition.
Candidate and provider choices are committed profile or command inputs, not
`.env` settings.

Candidate manifests contain typed vLLM settings, not executable commands. The
scored GPU function constructs the fixed vLLM command, receives no secret,
blocks external networking, mounts the populated Hugging Face model cache
read-only and cannot access the durable evaluator evidence Volume. It receives
only an evaluator-issued subpath of a staging Volume. After the candidate
process stops, evaluator code writes and syncs raw results there and returns a
small digest pointer. A trusted collector validates the staged bytes, and a
separate trusted function copies them into durable evaluator storage. Run the
authenticated smoke check first whenever the pinned model cache might not be
populated; a scored run fails closed rather than downloading missing data. The
scored command addresses the exact revision directory in the local cache. It
does not ask the offline Hugging Face resolver to enumerate optional repository
metadata, and the candidate cannot choose or alter either path.

Before a full GPU repetition, verify the large-result path without GPU spend:

```bash
.venv/bin/modal run -e dev \
  campaigns/model_serving_v0/reference/modal_vllm.py \
  --staging-probe \
  --output-path tmp/calibration/modal-staging-conformance.json
```

This sends a 4 MiB low-compressibility JSON artifact through isolated staging,
trusted readback and durable persistence. It fails unless every digest resolves.

After the cache is populated, run the bounded hardened-boundary conformance
instead of a full benchmark repetition:

```bash
.venv/bin/modal run -e dev \
  campaigns/model_serving_v0/reference/modal_vllm.py \
  --security-conformance --allow-gpu-spend \
  --output-path tmp/calibration/modal-security-conformance.json
```

This billable check starts the pinned server once and sends one short quality
request between the existing pre- and post-canaries. It exercises the scored
function's blocked external network, read-only model cache, absent secret,
absent durable evidence mount, isolated staging subpath and separate durable
evidence-persistence function. It does not run the nine-point throughput
benchmark and is not scoreable study evidence.

Dispatch is billable and requires an explicit spend flag:

```bash
collab-evals modal-compute-development \
  --dispatch --allow-gpu-spend \
  --run-id modal-development-reference-v1
```

The pinned compute profile supplies the public performance workload explicitly;
the runner records its digest throughout dispatch and result evidence. Do not
override that path for a durable development run. Hidden performance will use
a separate profile bound to the registered private-bundle digest.

The CLI first freezes the exact compute request and transport/backend profiles
in `compute-run-manifest.json`. A separate SQLite authorization service records
the approval digest and issues a request-, transport- and manifest-bound spend
authorization. The Modal transport atomically consumes that authorization
before invoking the CLI. Calling the transport without an issued authorization,
or trying to reuse one, fails before invoking Modal.

The underlying formal evaluator requires a clean Git worktree. After dispatch,
collect the same external call without authorizing another allocation:

```bash
collab-evals modal-compute-development \
  --collect --collect-timeout-seconds 30 \
  --run-id modal-development-reference-v1
```

Repeat collection as needed. A timeout remains pending. An ambiguous dispatch
fails closed and is never retried under the same execution key. The command is
visible-only, runs one development repetition and is not a registered or
confirmatory study. Use `--candidate` and a new `--run-id` for an explicitly
chosen candidate; never reuse a run ID for different bytes. Collection and
close-time reconciliation reconstruct authority from the frozen run manifest;
they do not require the dispatching process to replay the request first.
When collection becomes terminal, the CLI re-resolves the exact dispatch and
evidence and returns `reconciliation.valid: true`; absence of that field is not
a successful close.

Verify the local installation with:

```bash
.venv/bin/modal --version
git status --ignored --short
```

### Durable evaluator evidence

Formal calibration calls write raw benchmark files and their remote receipt to
an invocation-isolated subpath of
`agent-collab-evals-evaluator-staging-v2` after the candidate server exits. The
GPU function has restricted Modal API access and returns only a digest-bound
pointer. The trusted local collector downloads and verifies every staged file,
then a separate restricted function commits the complete bundle to
`agent-collab-evals-evaluator-evidence-v2`. The collector verifies the durable
copy, publishes the normalized receipt once and keeps the existing ignored
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

After the quality workload exists, materialize the complete hidden evaluator
bundle without network or GPU access:

```bash
PYTHONPATH=src .venv/bin/python \
  scripts/calibration/materialize_hidden_workload.py
```

The write-once bundle is stored under ignored `tmp/evaluator-private/` with
owner-only permissions. It binds disjoint correctness and synthetic
performance inputs, integer-unit quality request specifications, the frozen
quality workload and policy, and every resource digest. Registered consumers
must pin the resulting manifest digest; recomputing a digest after mutation is
not accepted. File modes are defense in depth only—the registered harness
sandbox must still prevent agents from reading evaluator-private paths.

For a study bundle, use a new seed and output directory after the applicable
scoring policy is committed. Never reuse the calibration bundle as held-out
study material. The current registration candidate pins only commitments and
digests; it deliberately contains no private path or seed.

Retain the current study bundle on its dedicated evaluator-private Modal
Volume and verify every byte with:

```bash
.venv/bin/python scripts/registration/retain_hidden_bundle.py
```

Use `--create-volume` only for the first retention attempt. The operation is
idempotent for identical bytes and rejects an existing path with different
content. It uploads the five bundle files but never uploads the selection seed.
The key-free receipt is written to
`evidence/hidden_workloads/model-serving-hidden-study-v1.json`.

After materialization, dispatch one reference quality repetition without
keeping a terminal attached:

```bash
.venv/bin/modal run --detach -e dev campaigns/model_serving_v0/reference/modal_vllm.py \
  --quality \
  --candidate-path campaigns/model_serving_v0/reference/candidate.json \
  --measurement-id qwen-quality-reference-v2 \
  --quality-role reference \
  --repetition 1 --attempt 1 \
  --dispatch-only
```

Collect that exact call with:

```bash
.venv/bin/modal run --detach -e dev campaigns/model_serving_v0/reference/modal_vllm.py \
  --quality \
  --candidate-path campaigns/model_serving_v0/reference/candidate.json \
  --measurement-id qwen-quality-reference-v2 \
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
  --measurement-id qwen-quality-clean-control-v2 \
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

After all three pairs are valid, reproduce the frozen decision with:

```bash
PYTHONPATH=src .venv/bin/python \
  scripts/calibration/evaluate_quality_series.py
```

The command verifies the six committed receipt digests, validates each score
from its per-case outcomes, and applies the frozen paired case-cluster
bootstrap. It exits with status 2 when a candidate is validly measured but
fails non-inferiority. For another candidate series, pass its evaluator-issued
measurement ID with `--candidate-measurement-id`; pair it with a corresponding
reference series through `--reference-measurement-id`.
