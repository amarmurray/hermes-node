# hermes-node v0.3

`hermes-node` is a thin operator wrapper around the already-working upstream [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent) install on the VPS.

It does not reimplement Hermes internals. The wrapper owns:

1. local operator ingress
2. a SQLite run ledger
3. durable task workspaces
4. JSONL wrapper logs
5. queue execution and approval gates

Upstream Hermes remains the execution runtime.

## Separation

### 1. Upstream Hermes runtime

- pinned on the VPS at tag `v2026.4.13`
- pinned SHA `1af2e18d408a9dcc2c61d6fc1eef5c6667f8e254`
- repo: `~/apps/hermes-agent`
- home: `~/.hermes`
- credential surface: `~/.hermes/.env`
- execution backend: `~/.local/bin/hermes`

### 2. local hermes-node wrapper

- repo: `~/apps/hermes-node`
- CLI: `./bin/hermes-node`
- ledger: `state/hermes-node.db`
- workspaces: `state/workspaces/{task_id}/`
- wrapper logs: `logs/hermes-node.jsonl`

### 3. future Temporal ingress

Not implemented here.

The intended next step is wrapper CLI ingress first, then a local-only API ingress later if needed.

## Supported task kinds

`hermes-node` v0 supports only read-first task kinds:

- `summary`
- `research`
- `repo_read`

It does not support `repo_write` or shell mutation task classes by default.

Guardrails:

- `summary` uses non-destructive toolsets only
- `research` uses web-oriented toolsets only
- `repo_read` runs with the upstream `file` toolset and adds a strict read-only prompt preamble
- `repo_read` uses `--worktree` automatically when the target path looks like a git checkout
- no wrapper path enables `--yolo`

## Ledger model

The local SQLite ledger persists:

- `tasks`
- `runs`
- `events`
- `summaries`
- `approvals`

Each task row also persists `workspace_dir`.

The wrapper also writes structured JSONL records to `logs/hermes-node.jsonl` and durable per-task artifacts under `state/workspaces/`.

## File tree

```text
.
├── .env.example
├── README.md
├── bin/
│   └── hermes-node
├── docs/
│   ├── operator-ops.md
│   ├── routing-policy.md
│   └── upstream-notes.md
├── lib/
│   └── hermes_node_ops/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── codex.py
│       ├── jsonl.py
│       ├── ledger.py
│       ├── models.py
│       ├── routing.py
│       ├── workspaces.py
│       └── wrapper.py
├── logs/
│   └── .gitkeep
├── scripts/
│   └── bootstrap_ubuntu_2404.sh
├── state/
│   └── .gitkeep
├── systemd/
│   └── hermes-node.service
└── tests/
    ├── test_codex.py
    ├── test_config.py
    ├── test_ledger.py
    ├── test_routing.py
    ├── test_workspaces.py
    └── test_wrapper.py
```

## Configuration

Wrapper configuration lives in `~/apps/hermes-node/.env`.

Upstream Hermes secrets remain in `~/.hermes/.env`.

Important wrapper settings:

```dotenv
HERMES_NODE_UPSTREAM_HOME=~/.hermes
HERMES_NODE_UPSTREAM_REPO=~/apps/hermes-agent
HERMES_NODE_HERMES_BIN=~/.local/bin/hermes
HERMES_NODE_DB_PATH=state/hermes-node.db
HERMES_NODE_LOG_JSONL=logs/hermes-node.jsonl
HERMES_NODE_DEFAULT_PROVIDER=openrouter
HERMES_NODE_DEFAULT_MODEL=openai/gpt-oss-20b:free
HERMES_NODE_DEFAULT_MAX_TURNS=4
HERMES_NODE_POLL_INTERVAL_SECONDS=15
```

Codex auth options:

- `OPENAI_API_KEY` may live in `~/apps/hermes-node/.env`
- or a cached `codex login` can be used directly
- or `OPENROUTER_API_KEY` can stay in `~/.hermes/.env`; the wrapper will map Codex onto OpenRouter's OpenAI-compatible base URL for read-only repo tasks

## CLI

### submit

Queue a task in the local ledger.

```bash
./bin/hermes-node submit --kind summary --query "Reply with exactly: ALIVE"
./bin/hermes-node submit --kind research --query "Summarize the latest OpenRouter quickstart flow"
./bin/hermes-node submit --kind repo_read --repo ~/apps/hermes-node --query "Describe the ledger schema"
./bin/hermes-node submit --kind research --query "Review this plan" --require-approval
```

Optional overrides:

- `--provider`
- `--model`
- `--max-turns`
- `--title`
- `--created-by`
- `--run-now`
- `--json`

### run

Run one queued task, or stay in a worker loop.

```bash
./bin/hermes-node run
./bin/hermes-node run --task-id task_...
./bin/hermes-node run --watch --poll-interval 15
./bin/hermes-node run --approve task_... --actor ubuntu
```

Approval note:

- `submit --require-approval` queues a task in `pending_approval`
- `run --approve task_...` records a granted approval and can immediately execute that task

### status

Inspect queue state, recent runs, approvals, and task details.

```bash
./bin/hermes-node status
./bin/hermes-node status --task-id task_...
./bin/hermes-node status --json
```

### summarize

Show the latest stored summary for a task or run.

```bash
./bin/hermes-node summarize
./bin/hermes-node summarize --task-id task_...
./bin/hermes-node summarize --run-id run_...
```

### workspace

Inspect the durable workspace for a task, or list known workspaces.

```bash
./bin/hermes-node workspace
./bin/hermes-node workspace --task-id task_...
./bin/hermes-node workspace --task-id task_... --json
```

Each task workspace contains:

```text
state/workspaces/{task_id}/
├── input.md
├── notes/
├── runs/
│   └── {run_id}/
│       ├── receipt.json
│       ├── stderr.txt
│       └── stdout.txt
└── worktree.json
```

## systemd unit

The worker unit lives in [systemd/hermes-node.service](/Users/amarmurray/Documents/autodev/ops/hermes-node/systemd/hermes-node.service).

It runs:

```text
/home/ubuntu/apps/hermes-node/bin/hermes-node run --watch --poll-interval 15
```

Install on the VPS:

```bash
cd ~/apps/hermes-node
sudo install -m 0644 systemd/hermes-node.service /etc/systemd/system/hermes-node.service
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-node.service
sudo systemctl status hermes-node.service
```

This service does not expose a public port.

## Migration / bootstrap on the VPS

This wrapper assumes upstream Hermes is already installed and working.

Current VPS facts:

- upstream Hermes repo: `~/apps/hermes-agent`
- upstream Hermes home: `~/.hermes`
- OpenRouter credential already configured in `~/.hermes/.env`

Bootstrap the wrapper:

```bash
cd ~/apps
git clone https://github.com/amarmurray/hermes-node.git
cd ~/apps/hermes-node
cp .env.example .env
./scripts/bootstrap_ubuntu_2404.sh
```

The bootstrap script does not reinstall upstream Hermes. It verifies the upstream binary, prepares wrapper state directories, and optionally installs the worker unit.

## Exact VPS verification commands

Run these on the VPS after pulling this repo:

```bash
cd ~/apps/hermes-node
python3 -m unittest discover -s tests -v
bash -n scripts/bootstrap_ubuntu_2404.sh
./bin/hermes-node status
./bin/hermes-node submit --kind summary --query "Reply with exactly: ALIVE"
./bin/hermes-node run
./bin/hermes-node summarize
./bin/hermes-node workspace --task-id task_...
```

Expected shape:

- `status` shows the local ledger counts
- `submit` creates a `task_...`
- `run` creates a `run_...` and calls upstream `hermes chat`
- `summarize` prints the stored summary from the completed run

## Local development tests

Run:

```bash
python3 -m unittest discover -s tests -v
```

The test suite covers:

- env/config loading
- ledger creation and approvals
- upstream command construction
- run execution bookkeeping
- summary extraction

## Notes

- Upstream proof-of-life remains recorded in [docs/upstream-notes.md](/Users/amarmurray/Documents/autodev/ops/hermes-node/docs/upstream-notes.md).
- `hermes-node` uses the upstream Hermes CLI as its only execution backend.
- Temporal is intentionally out of scope for this v0.
