# hermes-node routing policy

This document describes the execution constitution — which tasks are
allowed, what executor runs them, and what guardrails apply.

The source of truth is `lib/hermes_node_ops/routing.py`.

## Risk classes

| Class | Meaning |
|---|---|
| `read_only` | Cannot mutate repos, files, or host state |
| `branch_write` | May write to a named branch. Never main/master |
| `destructive` | Deletes data, force-pushes, or mutates host state. Blocked by default |

## Executors

| Executor | Backend | Notes |
|---|---|---|
| `hermes_chat` | Upstream Hermes CLI (`hermes chat`) via OpenRouter | Always available |
| `codex` | OpenAI Codex CLI (`codex`) | Requires install + OPENAI_API_KEY |

## Route table

| Kind | Risk | Default executor | Fallback | Approval | Description |
|---|---|---|---|---|---|
| `summary` | read_only | hermes_chat | — | no | Cheap summary via default OpenRouter model |
| `research` | read_only | hermes_chat | — | no | Web research, no host mutation |
| `repo_read` | read_only | codex | hermes_chat | no | Read-only repo inspection. Codex preferred |
| `repo_patch_branch` | branch_write | codex | — | **yes** | Code changes on a named branch. Codex only |

## Behavior on edge cases

| Scenario | Behavior |
|---|---|
| Default executor unavailable, fallback exists | Use fallback |
| Default executor unavailable, no fallback | Fail with legible error |
| 429 / rate limit | Defer with exponential backoff (same as v0.1) |
| Timeout | Treated as non-zero exit code failure |
| Missing API key for Codex | Codex reports unavailable, fallback or fail |
| Budget breach | Not yet implemented; placeholder for Temporal phase |
| Approval not granted | Task stays in `pending_approval` until approved or rejected |

## Destructive tasks

Destructive task kinds (`repo_write_main`, `shell_exec`, etc.) are not
in the route table and cannot be submitted. To add one:

1. Define the `TaskProfile` in `models.py`
2. Add a `Route` entry in `routing.py` with `risk_class=RISK_DESTRUCTIVE` and `approval_required=True`
3. Implement the executor integration
4. Add tests

This is intentionally friction-heavy.

## Codex lane prerequisites

To enable the Codex executor on the VPS:

```bash
# Install Codex CLI
npm install -g @openai/codex

# Set API key in wrapper .env
echo 'OPENAI_API_KEY=sk-...' >> ~/apps/hermes-node/.env

# Verify
hermes-node route --kind repo_read
# Should show: executor: codex (available)
```

## Inspecting routes

```bash
hermes-node route                        # show all routes + availability
hermes-node route --kind repo_read       # show specific route
hermes-node route --json                 # machine-readable
```
