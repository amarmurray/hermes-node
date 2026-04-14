# hermes-node operator architecture

The operator is a resident cognitive layer that sits on top of hermes-node.
It talks to the human, reads hermes-node state, decides what kind of task
something is, invokes hermes-node tools to act, and reports back.

## Topology

```
Human
  -> terminal REPL (later: Telegram)
  -> operator LLM
  -> hermes-node tool surface (Python interfaces / CLI)
  -> routing / approvals / ledger / workspaces
  -> executors (Codex, OpenRouter/Hermes, later Claude)
  -> receipts / notes / artifacts
  -> operator reads them back
  -> reports to human
```

## Stack map

- Upstream Hermes: runtime substrate
- Hermes-node: constitutional membrane
- Operator: resident conversational foreman and wedge into the substrate
- Deep Agents: later harness for the operator, then bounded specialist suboperators
- Temporal: later durable orchestrator above hermes-node
- Executors under hermes-node: Codex, Hermes/OpenRouter, later Claude, later specialist lanes

Near-term sequence:

1. terminal-first operator on the VPS
2. local OSS model as the always-on foreman
3. tool-mediated interaction through hermes-node, never around it
4. Telegram later as another ingress wedge into the same operator
5. Deep Agents later as operator harness
6. specialist suboperators later, first callable then selectively autonomized
7. Temporal later above hermes-node
8. broader upstream Hermes capabilities only when they reinforce the membrane

Design rules:

- governance propagates downward before autonomy does
- durable state belongs in workspaces, receipts, sessions, and notes, not live process memory
- upstream Hermes capabilities should be wrapped by hermes-node policy before autonomous use
- specialist suboperators should become bounded, receipt-emitting, policy-constrained organs

## What the operator is

- A foreman, not a coder
- Routing-aware and approval-conscious
- A client of hermes-node's control plane
- Backed by a local/self-hosted small OSS model (24/7 VPS)

## What the operator is NOT

- A second orchestrator
- A direct executor of code
- A bypass of the ledger, routing, or approval discipline
- Dependent on external APIs for its core operation

## Integration boundary

The operator interacts with hermes-node exclusively through:

1. **Ledger queries** — task status, run history, queue snapshots
2. **Workspace reads** — input artifacts, receipts, notes
3. **Task submission** — `ledger.create_task()` via the tool surface
4. **Approval grants** — `ledger.record_approval()` via the tool surface
5. **Repo inspection** — read-only filesystem access (ls, read, grep, git status)

The operator never imports or calls `wrapper.py` execution functions.
Task execution happens through the existing queue worker (`hermes-node run --watch`).

## Tool surface

### Hermes-node tools
| Tool | Description |
|------|-------------|
| `get_queue_status` | Queue overview: counts, recent tasks/runs, deferred, cooldowns |
| `get_task_status` | Detailed status for a specific task |
| `submit_task` | Submit a new task to the queue |
| `approve_task` | Approve a pending task (after human confirms) |
| `list_recent_tasks` | Recent tasks list |
| `list_recent_runs` | Recent runs list |
| `inspect_workspace` | Task workspace manifest |
| `read_run_receipt` | Specific run receipt |
| `get_routing_policy` | Current routing table and executor availability |

### Repo inspection tools
| Tool | Description |
|------|-------------|
| `repo_ls` | List files in a directory |
| `repo_read_file` | Read file contents (bounded) |
| `repo_grep` | Search for patterns |
| `git_status` | Branch, dirty state, recent commits |

### Session tools
| Tool | Description |
|------|-------------|
| `save_note` | Persist a note to the current session |

## Session persistence

Sessions are stored at `state/operator_sessions/{session_id}/`:

- `thread.jsonl` — append-only conversation log (JSONL)
- `notes.md` — persistent scratch notes
- `context.json` — session metadata (model, timestamps)
- `.repl_history` — readline history

All artifacts are plain files, inspectable with `cat`/`jq`.

## Model backend

The operator uses an OpenAI-compatible chat completion backend.
Default: Ollama running locally on the VPS.

Supported backends:
- **Ollama** (`http://localhost:11434`) — recommended for VPS
- **llama.cpp server** (`http://localhost:8080`)
- **vLLM** (`http://localhost:8000`)
- **OpenRouter** or **OpenAI** (remote, requires API key)

Configuration via environment or `.env`:
```
HERMES_OPERATOR_BASE_URL=http://localhost:11434
HERMES_OPERATOR_MODEL=qwen3:1.7b
HERMES_OPERATOR_API_KEY=
```

## Running

```bash
# Start a chat session
./bin/hermes-operator chat

# With specific model
./bin/hermes-operator chat --model qwen3:1.7b --base-url http://localhost:11434

# Resume a previous session
./bin/hermes-operator chat --resume session_20260414_120000_abc123

# List sessions
./bin/hermes-operator sessions

# View a session thread
./bin/hermes-operator thread session_20260414_120000_abc123
```

## VPS setup

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull a small tool-capable model
ollama pull qwen3:1.7b

# Verify
curl http://localhost:11434/v1/models

# Start the operator
cd ~/apps/hermes-node
./bin/hermes-operator chat
```

## Design principles

1. **Operator is a client, not an orchestrator** — it submits tasks and reads results
2. **All state goes through hermes-node** — ledger, workspaces, receipts
3. **Approval requires human consent** — the operator asks, never self-approves
4. **Backend is swappable** — change the model without changing the tool layer
5. **Sessions persist as files** — crash-safe, inspectable, no extra database
6. **Read-only repo access** — the operator inspects, executors mutate
