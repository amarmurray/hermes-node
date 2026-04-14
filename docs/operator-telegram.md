# Operator Telegram Ingress (v0.5)

A thin Telegram ingress wedge for the hermes-node operator. Maps Telegram
messages onto the existing operator service. Reuses backend, tools, sessions,
and constitution unchanged. **Not a parallel agent stack.**

## Architecture

```
Telegram user ──▶ Telegram Bot API
                      │
                      ▼ long-poll (urllib)
        lib/hermes_node_ops/operator/telegram/bot.py
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
    ToolExecutor  ChatBackend  session files
    (tools.py)    (backend.py) (session.py)
                      │
                      ▼
               hermes-node ledger / workspaces
```

One long-running process (`hermes-operator-telegram run`) does three things on
a single loop:

1. Long-polls `getUpdates` for new messages.
2. Periodically scans the ledger `events` table for notable events and pushes
   alerts to the allowlisted chats.
3. Routes each update through the same `ToolExecutor` / `ChatBackend` /
   session artifacts the terminal REPL uses.

No new agent stack, no new tool surface, no new DB tables. The constitution,
approval gates, and workspace discipline from v0.4 apply unchanged.

## Dependencies

Stdlib only — no `python-telegram-bot`. HTTPS via `urllib.request`. This keeps
the wedge deployable on the same minimal venv as the rest of hermes-node.

## Setup

1. **Create a bot** via [@BotFather](https://t.me/BotFather):
   - `/newbot`, pick a name and a `_bot`-suffixed username.
   - Save the token BotFather gives you.
2. **Put the token in `.env`** on the VPS:
   ```
   HERMES_OPERATOR_TELEGRAM_TOKEN=123456:ABC-your-token
   ```
3. **Find your chat_id**: message the bot once. It will refuse with
   `unauthorized. your chat_id is <N>`. Or check `logs/hermes-node.jsonl`
   for an `update_denied` entry.
4. **Allowlist the chat_id** in `.env`:
   ```
   HERMES_OPERATOR_TELEGRAM_ALLOWED_CHAT_IDS=123456789
   ```
   Multiple IDs are comma-separated. Leaving this empty makes the bot refuse
   all updates — intentionally fail-closed.
5. **Verify the token**:
   ```
   bin/hermes-operator-telegram check
   ```
6. **Run** (foreground for a smoke test):
   ```
   bin/hermes-operator-telegram run
   ```
7. **Install the service** (persistent):
   ```
   sudo cp systemd/hermes-operator-telegram.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now hermes-operator-telegram
   sudo journalctl -u hermes-operator-telegram -f
   ```

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `HERMES_OPERATOR_TELEGRAM_TOKEN` | *(empty)* | BotFather token. Required. |
| `HERMES_OPERATOR_TELEGRAM_ALLOWED_CHAT_IDS` | *(empty)* | Comma-separated chat IDs. **Empty = deny all.** |
| `HERMES_OPERATOR_TELEGRAM_POLL_TIMEOUT` | `30` | Long-poll timeout (seconds) passed to `getUpdates`. |
| `HERMES_OPERATOR_TELEGRAM_ALERT_POLL_SECONDS` | `60` | How often the alert watcher scans the ledger. |
| `HERMES_OPERATOR_BASE_URL` | `http://localhost:11434` | Shared with the terminal operator. |
| `HERMES_OPERATOR_MODEL` | `qwen3:1.7b` | Shared with the terminal operator. |
| `HERMES_OPERATOR_API_KEY` | *(empty)* | Shared with the terminal operator. |

## Slash commands

| Command | Description |
|---|---|
| `/start` | Show welcome + command list. |
| `/help` | One-line command summary. |
| `/status` | `get_queue_status` — counts, deferred, recent tasks/runs. |
| `/pending` | List tasks in `pending_approval` state. |
| `/approve <task_id>` | Approve a task. Writes an audit note. |
| `/task <task_id>` | Full task detail + latest run + latest summary. |
| `/runs` | Last 10 runs. |
| `/new` | Rotate to a fresh operator session for this chat. |
| `/session` | Print the current operator session id. |

Any non-slash message is routed through the LLM with the full tool surface.
The bot keeps a typing indicator on during tool rounds, caps tool rounds per
turn at `MAX_TOOL_ROUNDS = 8`, and chunks responses over 3,900 chars.

## Session mapping

Each Telegram chat gets its own persistent operator session:

- Marker file: `state/telegram_session_<chat_id>.txt` → contains a
  `session_…` id.
- Session dir: `state/operator_sessions/session_…/` with `thread.jsonl`,
  `notes.md`, `context.json`.

`/new` deletes the marker and creates a fresh session on the next message.
`/session` prints the current id so you can inspect it with the terminal
operator CLI:

```
bin/hermes-operator thread <session_id>
bin/hermes-operator thread <session_id> --notes
```

## Alerts

The alert watcher polls the `events` table every
`HERMES_OPERATOR_TELEGRAM_ALERT_POLL_SECONDS` and forwards these event types
to every allowlisted chat:

| Ledger event | Alert label |
|---|---|
| `run.failed` | `FAILED: <task_id>` |
| `task.deferred_rate_limit` | `DEFERRED (rate-limit): <task_id>` |
| `approval.requested` | `NEEDS APPROVAL: <task_id>` |

The cursor is stored in `state/telegram_alert_cursor.json`. On first boot the
cursor is initialized to the current `MAX(events.id)` so historical events
are not replayed as notifications.

## Security posture

- **Private-first.** Token lives in `.env`, never committed. `.env.example`
  only documents the variable name.
- **Fail-closed allowlist.** No chat IDs → no updates processed. Auth check
  happens *before* any LLM call or tool dispatch.
- **Reuses the constitution.** The same risk-class rules, approval gates, and
  workspace discipline apply. The bot cannot bypass the ledger because it
  dispatches through `ToolExecutor`, same as the terminal REPL.
- **No free-form shell.** The tool surface is the operator surface. Telegram
  has no extra escape hatch.
- **Audit trail.** Every inbound update, tool call, and alert is logged to
  `logs/hermes-node.jsonl` with `component=telegram_bot`. The LLM
  conversation is persisted in the same JSONL thread the terminal REPL uses.

## Local smoke test (no bot token)

Unit tests cover parsing, formatting, and routing with a mocked client. Run:

```
python3 -m pytest tests/test_operator_telegram_*.py -q
```

## Last-mile step

If you just deployed and the bot isn't responding:

1. Did you create the bot with BotFather and put the token in
   `/home/ubuntu/apps/hermes-node/.env`?
2. Did you put your chat_id in `HERMES_OPERATOR_TELEGRAM_ALLOWED_CHAT_IDS`?
3. Is the service running? `systemctl status hermes-operator-telegram`
4. Did `check` succeed?
   ```
   bin/hermes-operator-telegram check
   ```
5. Watch the log:
   ```
   tail -f logs/hermes-node.jsonl | grep telegram_bot
   ```
