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

Put the OpenRouter API key after `OPENROUTER_API_KEY=` in `.env`. The defaults
pin the dated DeepSeek V4 Flash model to DeepInfra's compatible endpoint,
require zero data retention, deny provider data collection, disable fallbacks
and cap the preflight response at 512 completion tokens. Run the small
streaming request with:

```bash
npm run preflight:openrouter
```

The command streams the answer, verifies the response against a deterministic
canary, prints token/cost metadata and writes a key-free JSON receipt under
ignored `tmp/preflight/`. It fails rather than silently selecting another
provider or treating a reasoning-only response as usable. The preflight routing
settings are a development default; a registered campaign must copy the
finalized settings into its immutable study manifest instead of depending on
`.env`.

The local `OPENROUTER_MAX_COMPLETION_TOKENS` setting is sent as the legacy
`max_tokens` wire parameter because the pinned DeepSeek endpoint advertises
that parameter. `requireParameters` remains enabled so future incompatibility
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
`--output-path` to select a different local receipt path.

The reference smoke is not the serving baseline. The nine public benchmark
points are defined and validated locally, but baseline execution remains gated
on evaluator-owned result persistence and a fully resolved image/dependency
identity. Do not use calibration output as confirmatory evidence.

Verify the local installation with:

```bash
.venv/bin/modal --version
git status --ignored --short
```
