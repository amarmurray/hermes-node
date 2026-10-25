# hermes-node operator architecture

The operator is a resident cognitive layer that sits on top of hermes-node.
It talks to the human, reads hermes-node state, decides what kind of task
something is, invokes hermes-node tools to act, and reports back.

Canonical stack map: [stack-evolution.md](stack-evolution.md)
Runtime and future scheduler seam: [phase3-runtime-and-temporal-seam.md](phase3-runtime-and-temporal-seam.md)
Upstream membrane inventory: [hermes-capability-registry.md](hermes-capability-registry.md)

## Implementation and validation status

The shared operator runtime, terminal and Telegram ingresses, planning tools,
callable specialists, and future scheduler interface are implemented. April 2026
development experiments used a local 1.7B model and exposed tool-selection,
grounding, and narration-without-action failures that informed subsequent fixes.
Those records describe the development setup rather than current service uptime.

The [offline demo](demo.md) verifies the underlying approval, persistence, and
retry lifecycle with a fake provider. The author has not yet completed a real
repository task through this stack. The next milestone is a bounded task from
repository inspection through an approved change and reviewable diff/receipt,
followed by repeated runs that record failures and human interventions.

Implementation priorities supporting that milestone:

1. operator runtime strategy — resident local quality plus a heavier
   remote escalation slot for intents the resident model can't land
2. specialist hardening — make callable specialists reliably useful with
   clean receipts, before flipping any autonomy flags
3. Codex lane hardening — manifests, receipts, durability, sandbox envelope
4. only then Temporal above Hermes-node, then selective autonomization,
   then new surfaces (Mini App) on top of operator + Hermes-node

## Topology

Terminal and Telegram share the operator turn runtime and tool surface. The
operator reads state, creates plans, and submits bounded tasks. The queue worker
handles routing and execution through Hermes or Codex, writes results to
workspaces and the ledger, and makes those records available to the operator.

## Stack map

- Upstream Hermes: external runtime accessed through the wrapper
- Hermes-node: implemented ledger, routing, approval, and artifact layer
- Operator: implemented conversational and planning runtime
- Terminal + Telegram: implemented ingresses into the same operator
- Deep-Agents-style harness: layered inside the operator — plans, scratchpads,
  todos, callable specialists
- Codex lane: implemented adapter behind Hermes-node's routing policy; further
  hardening and real-task validation are planned
- Temporal: later durable orchestrator above Hermes-node, consumes the existing
  invoke seam
- Mini App: later surface on top of operator + Hermes-node, not raw Hermes

For the canonical ladder (next / then / later) see
[stack-evolution.md](stack-evolution.md).

Design rules:

- operator thinks through Hermes-node, never around it
- operator is substrate-native, not surface-native
- governance propagates downward before autonomy does
- durable state belongs in workspaces, receipts, sessions, plans, and notes —
  not in live process memory
- upstream Hermes capabilities become autonomous behavior only after the
  membrane wraps them
- specialist suboperators are bounded, receipt-emitting, policy-constrained
  organs — not rogue daemons

## What the operator is

- A foreman, not a coder
- Routing-aware and approval-conscious
- A client of hermes-node's control plane
- Configurable with a local/self-hosted model or a compatible remote backend

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
| `summarize_repo_map` | Concise repo tree and layout summary |
| `summarize_repo_state` | Branch, HEAD, cleanliness, recent commits |
| `summarize_changed_files` | Staged/unstaged/untracked summary |
| `find_repo_relevance` | File relevance helper for repo questions |
| `summarize_repo_activity` | Recent tasks, runs, workspaces, receipts per repo |
| `compare_repo_to_last_successful_run` | Current repo state vs last successful repo run |
| `diagnose_failed_task` | Failure diagnosis from runs, events, and workspace receipts |
| `prepare_patch_proposal_plan` | Bounded mutation plan without submitting or mutating |

## Repository workflow to validate

The implemented tools support the following inspection and planning sequence.
The next real-task validation should exercise it with independently checked
outputs:

1. answer a repo-structure question
2. summarize current branch/worktree state
3. compare current repo state to the last successful run
4. diagnose a failed task from its artifact trail
5. prepare a bounded patch proposal plan for later approval/routing

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
HERMES_OPERATOR_REMOTE_BASE_URL=
HERMES_OPERATOR_REMOTE_MODEL=
HERMES_OPERATOR_REMOTE_API_KEY=
```

The backend policy keeps the local resident path primary and lets heavier
intents escalate later without changing the ingress surface.

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
