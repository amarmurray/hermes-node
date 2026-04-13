# Hermes Node Ops Wrapper

This repository is a thin operational wrapper around the upstream [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) runtime.

It does **not** ship a custom Hermes clone.

It separates three layers:

1. **Upstream Hermes runtime / CLI**  
   Installed from the upstream repo into the official layout under `~/.hermes/` and invoked through the official `hermes` CLI.
2. **Local wrapper and ops glue**  
   This repo. It handles bootstrap, env loading, wrapper-side JSONL logs, systemd/gateway management helpers, and local submission helpers.
3. **Future Temporal integration**  
   A later client of Hermes, preferably via the upstream local-only API server at `http://127.0.0.1:8642/v1`.

## Upstream Findings

Inspected on **April 13, 2026** against upstream commit `2773b18b`.

Deployed VPS runtime currently validated at exact upstream commit:

```text
1af2e18d408a9dcc2c61d6fc1eef5c6667f8e254
```

Deployed VPS runtime is now pinned to upstream release tag:

```text
v2026.4.13
```

GitHub's latest upstream release at the time of sync was **Hermes Agent v0.9.0**, published on **April 13, 2026** at **18:52:41 UTC**.

Canonical upstream sources:

- [Installation guide](https://hermes-agent.nousresearch.com/docs/getting-started/installation/)
- [Quickstart](https://hermes-agent.nousresearch.com/docs/getting-started/quickstart/)
- [CLI reference](https://hermes-agent.nousresearch.com/docs/reference/cli-commands/)
- [API server guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/)
- [Upstream repo](https://github.com/NousResearch/hermes-agent)

### Documented upstream install flow

The upstream manual install for Linux is:

```bash
git clone --recurse-submodules https://github.com/NousResearch/hermes-agent.git
cd hermes-agent
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv venv --python 3.11
export VIRTUAL_ENV="$(pwd)/venv"
uv pip install -e "."
mkdir -p ~/.hermes/{cron,sessions,logs,memories,skills,pairing,hooks,image_cache,audio_cache,whatsapp/session}
cp cli-config.yaml.example ~/.hermes/config.yaml
touch ~/.hermes/.env
mkdir -p ~/.local/bin
ln -sf "$(pwd)/venv/bin/hermes" ~/.local/bin/hermes
hermes version
hermes doctor
hermes status
hermes chat -q "Hello! What tools do you have available?"
```

The upstream quick installer is:

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

### What this wrapper preserves

- Upstream code stays in `~/.hermes/hermes-agent`
- Upstream config stays in `~/.hermes/config.yaml`
- Upstream secrets stay in `~/.hermes/.env`
- Upstream CLI stays `~/.local/bin/hermes`
- Upstream service management stays `hermes gateway ...`

This repo does not patch upstream internals.

## Host Layout

Target VPS:

- Ubuntu `24.04.x`
- user: `ubuntu`
- build path: `~/apps/hermes-node`
- private-first, Tailscale/SSH only

Effective layout after bootstrap:

```text
~/apps/hermes-node/              # this wrapper repo
~/apps/hermes-node/.env          # wrapper config
~/apps/hermes-node/logs/         # wrapper JSONL logs
~/.hermes/                       # upstream Hermes home
~/.hermes/hermes-agent/          # upstream repo checkout
~/.hermes/config.yaml            # upstream config
~/.hermes/.env                   # upstream keys and runtime env
~/.local/bin/hermes              # upstream CLI symlink
```

## Why the Bootstrap Uses `.[messaging]` by Default

The wrapper is intended to manage the upstream gateway service and leave a clean path for the upstream local API server later. The upstream API server relies on gateway-side HTTP dependencies. To stay inside upstream-supported install shapes, the bootstrap defaults to the official extra:

```bash
uv pip install -e ".[messaging]"
```

If you want the thinnest possible CLI-only runtime, set:

```bash
HERMES_NODE_UPSTREAM_INSTALL_SPEC=.
```

before running bootstrap.

## Bootstrap on Ubuntu 24.04

From `~/apps/hermes-node`:

```bash
cp .env.example .env
./scripts/bootstrap_ubuntu_2404.sh
```

What the bootstrap does:

- installs `uv` if needed
- clones or updates the upstream repo into `~/.hermes/hermes-agent`
- creates an upstream Python `3.11` venv with `uv`
- installs the upstream package with the configured official extras
- creates the standard `~/.hermes/` directory layout
- creates `~/.local/bin/hermes`
- copies the wrapper `.env.example` into `.env` if missing
- optionally enables the upstream local API server in `~/.hermes/.env`
- runs `hermes version`, `hermes status`, and `hermes doctor`

## Wrapper Commands

The wrapper entrypoint is:

```bash
./bin/hermes-node
```

### Pass through to upstream CLI

```bash
./bin/hermes-node upstream version
./bin/hermes-node upstream model
./bin/hermes-node upstream doctor
```

### Smoke-check the official CLI

```bash
./bin/hermes-node smoke
```

This runs:

- `hermes version`
- `hermes status`
- `hermes doctor`

### Manage the upstream gateway service

Default documented mode is the upstream **user service**:

```bash
./bin/hermes-node service install
./bin/hermes-node service start
./bin/hermes-node service status
./bin/hermes-node service restart
./bin/hermes-node service stop
```

For a boot-time **system service** running as `ubuntu`:

```bash
./bin/hermes-node service install --system
./bin/hermes-node service start --system
./bin/hermes-node service status --system
```

The wrapper delegates to upstream commands:

- user scope: `hermes gateway <action>`
- system scope: `sudo hermes gateway <action> --system [--run-as-user ubuntu]`

### One-shot local task submission

This uses the official upstream CLI one-shot mode:

```bash
./bin/hermes-node submit --query "Summarize the last 10 log lines"
./bin/hermes-node submit --query "Review the repo" --provider openrouter --model anthropic/claude-sonnet-4.6
./bin/hermes-node submit --query "Return only JSON" --quiet
```

The wrapper delegates to:

```bash
hermes chat -q "..." -Q
```

### Local API helper

If the upstream API server is enabled and the gateway is running:

```bash
./bin/hermes-node api-health
./bin/hermes-node api-submit --input "Check the latest gateway log errors"
./bin/hermes-node api-submit --input "Continue" --conversation ops-loop
```

This calls the upstream local-only OpenAI-compatible API:

- `GET /health`
- `POST /v1/responses`

## Environment Files

There are two separate env surfaces:

### Wrapper env: `~/apps/hermes-node/.env`

Used only by this repo for pathing, service defaults, and wrapper logging.

### Upstream env: `~/.hermes/.env`

Used by Hermes itself for provider keys and runtime settings such as:

- `OPENROUTER_API_KEY`
- `API_SERVER_ENABLED`
- `API_SERVER_KEY`
- `API_SERVER_HOST`
- `API_SERVER_PORT`

## Logs

Two log surfaces exist on purpose:

1. **Upstream logs** in `~/.hermes/logs/` and journald  
   These belong to Hermes itself.
2. **Wrapper logs** in `~/apps/hermes-node/logs/hermes-node.jsonl`  
   These record wrapper invocations, command dispatch, service actions, and API helper calls.

## Future Temporal Integration

The intended Temporal boundary is:

1. Temporal starts or depends on the upstream Hermes gateway service.
2. Hermes exposes only the upstream local API server on `127.0.0.1`.
3. Temporal submits work to `POST /v1/responses`.
4. Temporal watches:
   - `GET /v1/responses/{id}`
   - or `GET /v1/runs/{run_id}/events` when using the run/event path

This keeps Temporal out of Hermes internals. No private fork, no patched scheduler, no wrapper-owned run loop.

## Tests

Run:

```bash
python3 -m unittest discover -s tests -v
```

The tests only cover the wrapper layer:

- env loading
- service command construction
- submit command construction
- JSONL logging
- API request construction

## Notes

- The official CLI was validated locally during development with `hermes version`, `hermes status`, and `hermes doctor`.
- An actual model call was not executed in this repo because that depends on operator credentials in `~/.hermes/.env`.
