# Hermes-node

An experimental Python control layer for persistent, inspectable agent work.
It sits around [Nous Research's Hermes Agent](https://github.com/NousResearch/hermes-agent)
and the Codex CLI, adding task tracking, approval records, retry handling,
execution artifacts, and a shared operator interface for terminal and Telegram.

Built with AI coding assistance. Hermes and Codex provide the underlying agent
execution; this repository contains the integration and operating layer.

**Personal prototype with a runnable offline demonstration.** Start below to
inspect the approval, persistence, and retry behavior in a couple of seconds.

## Try it without keys

Requires Python 3.10 or newer. No package installation, provider account,
Hermes/Codex installation, or `.env` file is needed for the demonstration.

```bash
python3 scripts/offline_demo.py
```

The demo uses the real SQLite ledger, approval gate, retry handling, queue
selection, and receipt writers with a **deterministic fake provider**. It:

1. Stores a synthetic task and verifies execution is blocked before approval.
2. Records a simulated reviewer approval.
3. Injects a fake rate-limit response and preserves the deferred task.
4. Closes and reopens the database to verify persistence.
5. Retries through the queue and records a scripted successful result.

It prints the path to a new temporary artifact directory. Inspect
`demo-summary.json`, the SQLite database, input, stdout/stderr, and both run
receipts there. Every result identifies the mock execution mode. No network
request, external process, or real repository action occurs.

[Demo walkthrough and limits](docs/demo.md)

## What is implemented

| Component | Behavior in this repository |
|---|---|
| Task ledger | SQLite records tasks, attempts, approvals, events, summaries, and provider cooldowns |
| Execution wrapper | Routes bounded work to Hermes or Codex and writes per-run output and receipts |
| Repository actions | Read-only inspection and approval-required changes on named feature branches |
| Recovery | Rate-limit deferral with exponential backoff, attempt limits, and queue polling |
| Operator | A tool-using conversational layer with persisted session history and project context |
| Planning tools | File-backed plans, todos, scratchpads, and callable read-only specialists |
| Interfaces | Terminal REPL and an allowlisted Telegram adapter share the operator's tool surface |
| Evaluation | A small prompt harness records tool choice, latency, errors, and output for manual inspection |

The normal flow is: an interface invokes the operator; the operator submits
bounded tasks through the ledger; the wrapper selects an executor; results are
stored as receipts and artifacts for inspection. A persistent queue worker
processes submitted tasks.

### Implemented, simulated, and planned

| Status | Scope |
|---|---|
| Implemented in code | Ledger, routing, sessions, provider adapters, repository tooling, Telegram ingress, and callable specialists |
| Exercised by the offline demo | Approval enforcement, task persistence across database reopen, a simulated rate-limit retry, and retained run receipts |
| Historical development checks | April 2026 Hermes/provider proof of life and experiments with operator tool selection; historical records only |
| Current validation limit | No real repository task has been completed yet. Current uptime, sustained autonomous operation, production reliability, and customer impact remain unvalidated |
| Planned | Temporal orchestration, event-triggered specialist autonomy, and broader unattended workflows; trigger subscriptions remain disabled |

The next milestone is one bounded real repository task completed end to end:
inspect the repository, propose a change, obtain approval, execute it, and review
the resulting diff and receipt. Then repeat across representative tasks, recording
failures, human interventions, and outcomes before expanding unattended operation.

## Reliability work

The development history contains concrete failures and corresponding changes:

- The operator described actions without calling tools. Prompt and runtime
  checks were added to encourage execution before reporting completion.
- Overlapping tool descriptions led to incorrect selections. Descriptions and
  accepted evaluation-tool matches were refined.
- Models supplied repository aliases or invented placeholder paths where tools
  expected actual paths. Registry-backed argument resolution was added.
- A seven-prompt evaluation run could appear to pass while the final answer
  still asserted unsupported repository state. That prompted closer output
  inspection and a resolver fix.

The [evaluation harness](scripts/operator_bakeoff.py) is deliberately small and
uses heuristic checks. A passing score is not a factuality guarantee. Unlike the
offline demo, running the bakeoff requires an explicitly configured model backend.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The suite uses mocks and local temporary files/repositories; no live provider
credentials are required. Git is needed for repository fixtures. If your local
Git installation has no author identity, set test-only `GIT_AUTHOR_NAME`,
`GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME`, and `GIT_COMMITTER_EMAIL` in the command's
environment. The CI workflow supplies these only for its test process.

Run only the demonstration checks:

```bash
python3 -m unittest discover -s tests -p 'test_offline_demo.py' -v
```

Unit tests exercise implementation behavior and failure cases with mocked
provider boundaries.

## Optional live setup

Live execution requires your own installed executor, provider configuration, and
test repository. Check compatibility with your installed CLI versions; historical
deployment notes describe the April 2026 development setup.

1. Install and configure upstream Hermes separately; this repository does not
   vendor or reinstall it.
2. Copy `.env.example` to `.env` and set local paths and provider/model values.
3. Configure the Codex CLI if you want its repository-execution route.
4. Inspect `config/repos.json` and register only repositories you intend to use.
5. Start with bounded read-only tasks and inspect their outputs and receipts.

Useful commands after that setup:

```bash
./bin/hermes-node status
./bin/hermes-node route --kind repo_read
./bin/hermes-node submit --kind summary --query "Reply with exactly: ALIVE"
./bin/hermes-node run
./bin/hermes-node summarize
./bin/hermes-node workspace
```

`run --watch` polls the persistent queue. The provided systemd units are optional
deployment templates; adapt their user, paths, and environment before use.

### Task routes

| Task kind | Default executor | Intended scope |
|---|---|---|
| `summary` | Hermes | Bounded summarization |
| `research` | Hermes | Web-oriented research |
| `repo_read` | Codex, with Hermes fallback | Repository inspection |
| `repo_patch_branch` | Codex | Approval-required changes on a named branch |

The [routing policy](lib/hermes_node_ops/routing.py) is the source of truth for
executor selection. The [Codex adapter](lib/hermes_node_ops/codex.py) refuses
writable execution on `main` and `master`. These are application controls, not a
complete security boundary; do not treat the prototype as a hardened sandbox
for untrusted users or repositories.

## Project map

| Path | Purpose |
|---|---|
| `scripts/offline_demo.py` | Standalone synthetic lifecycle demonstration |
| `lib/hermes_node_ops/ledger.py` | Durable task and approval state |
| `lib/hermes_node_ops/wrapper.py` | Execution, retry handling, and artifact bookkeeping |
| `lib/hermes_node_ops/codex.py` | Codex CLI integration |
| `lib/hermes_node_ops/operator/` | Sessions, tools, backend policy, plans, and interfaces |
| `scripts/operator_bakeoff.py` | Small live-backend evaluation harness |
| `tests/` | Offline unit and integration checks |
| `docs/` | Architecture, operating notes, and demo details |

Further reading: [stack roadmap](docs/stack-evolution.md),
[operator architecture](docs/operator-architecture.md),
[Telegram interface](docs/operator-telegram.md), and
[operating notes](docs/operator-ops.md).

## Attribution

This project builds on [Hermes Agent](https://github.com/NousResearch/hermes-agent)
and uses the [Codex CLI](https://github.com/openai/codex) as an optional execution
backend. It is an independent personal project and is not affiliated with those
projects. The commit history records AI-assisted implementation. Underlying
models, runtimes, and services remain separate projects with their own terms.
