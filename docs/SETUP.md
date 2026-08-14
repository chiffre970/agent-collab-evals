# Local setup

This repository currently contains the collaboration-evaluation design and
contracts. Runtime adapters and campaign entry points will be added separately.

## Local environment

Create a project-local virtual environment and install tools into it:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install modal
```

Copy the example environment file when application code starts consuming
provider credentials:

```bash
cp .env.example .env
```

`.env` is ignored by Git. Do not put credentials in manifests, documentation,
agent workspaces or committed files.

## Modal

Authenticate the local CLI with:

```bash
modal setup
```

Modal stores the resulting token in `~/.modal.toml`, outside this repository.
For headless environments, use the token environment variables documented in
`.env.example` and inject them through the environment or a secret manager.

Verify the local installation with:

```bash
modal --version
git status --ignored --short
```
