# Stack evolution

Architecture and roadmap for `hermes-node`. This document distinguishes the
implemented components from the extensions planned around them. See the
[README status matrix](../README.md#implemented-simulated-and-planned) for current
validation status and the [offline demo](demo.md) for a reproducible check.

## Where we are now

Implemented components:

- an adapter around the separately installed upstream Hermes runtime
- Hermes-node constitutional membrane (ledger, routing, approvals, workspaces, receipts)
- resident cognitive operator on top of Hermes-node
- terminal REPL and Telegram as ingresses into the same operator
- Deep-Agents-style harness layered into the operator (plans, scratchpads, todos, callable specialists)
- Codex as an execution lane behind Hermes-node
- a repo registry for explicitly configured local checkouts
- Temporal seam (invocation + outcome shapes) without Temporal itself
- trigger manifest scaffolding (all subscriptions disabled)

April 2026 development records describe a VPS setup, minimal provider proof of
life, and experiments with a local operator model. These are historical checks.
The author has not yet completed a real repository task through this stack.

The current offline demonstration verifies approval enforcement, persisted task
state, a simulated rate-limit retry, and retained receipts using a deterministic
fake provider. The next validation milestone is a bounded real repository task:
inspection, proposed change, approval, execution, and independent review of the
diff and receipt. Repeated tasks should then establish which failures and human
interventions need further work.

## Current topology

Terminal or Telegram input reaches the shared operator. The operator inspects
state and submits work through Hermes-node's ledger, routing, and approval
interfaces. Executors produce workspace artifacts and receipts, which the
operator can inspect to report results to the user.

- Upstream Hermes: runtime substrate
- Hermes-node: constitutional membrane
- Operator: resident cognitive foreman and wedge into the substrate
- Terminal + Telegram: current ingresses into the same operator
- Codex: execution lane behind Hermes-node, never a parallel control plane
- Specialists: bounded, callable, read-only organs inside the operator harness
- Temporal: later durable orchestrator sitting above Hermes-node, not around it
- Mini App: later surface on top of operator + Hermes-node, not a new stack

## Next phases (practical ladder)

The work is a ladder, not a fan. Each phase unlocks the next.

1. **Next — operator runtime + specialist usefulness**
   - stronger resident-local operator quality (tool selection, grounding, anti-narration)
   - heavier remote escalation slot wired in for harder intents when the resident path is over its head
   - specialist suboperators become reliably callable with clean receipts
2. **Then — Codex lane hardening**
   - durable manifests for Codex invocations
   - receipts and artifact discipline on par with the Hermes lane
   - explicit sandbox envelope and failure surfaces
3. **Then — Temporal above Hermes-node**
   - durable orchestration and scheduling for retries, waits, approval loops, cadenced specialist wakeups
   - calls into Hermes-node through the existing invoke seam; does not bypass policy
4. **Later — selective autonomization of specialists**
   - flip individual `trigger_events` from manifest metadata to live wakeups
   - one specialist at a time, each gated by policy and receipts
5. **Later — richer surfaces (Mini App, etc.)**
   - new ingresses sit on top of operator + Hermes-node
   - never on raw upstream Hermes

## Intentionally deferred

- Temporal itself (seam is present; engine is not)
- specialist autonomy (manifest subscriptions all `enabled=False`)
- TreasuryOS repo (future-slot only until the repo actually exists)
- Mini App / web surfaces
- direct exposure of broader upstream Hermes capabilities before membrane wrapping

## Design rules

- operator thinks through Hermes-node, never around it
- operator is substrate-native, not surface-native (it is not a Telegram bot; Telegram is one ingress)
- governance propagates downward before autonomy does
- durable state lives in workspaces, receipts, session plans, todos, scratchpads, and subagent artifacts — not in live process memory
- upstream Hermes capabilities become autonomous behavior only after the membrane wraps them
- specialist suboperators become bounded organs, not rogue daemons
- every ingress shares the same operator turn runtime; surfaces never branch into their own control plane

## Development integration record

The target names below come from the April 2026 development configuration.
Registry entries identify intended checkouts; successful task execution is
tracked separately through run records.

| Repo | Role | Recorded scope |
|---|---|---|
| `hermes-node` | substrate + operator (this repo) | implemented code and reproducible offline demo |
| `hermes-agent` (upstream) | Hermes runtime | pinned VPS setup and minimal April proof-of-life check |
| `ixion` / `robotics-vp-core` | robotics VP core | configured repository target; real task completion unvalidated |
| `treasuryos` | future product | future-slot only, no path yet |

New repos are added to `future_slots` in the registry until they have a
real path, then promoted into `repos`.

## What belongs where

### Upstream Hermes

- model runtime substrate
- canonical Hermes CLI and provider integrations
- execution capabilities Hermes-node may call through

### Hermes-node

- policy membrane
- ledger, routing, approvals, workspaces, receipts
- bounded task submission and execution supervision
- invocation seam returning structured outcomes to future schedulers

### Operator

- resident cognitive foreman
- conversational ingress for humans through terminal and Telegram
- reads state, plans work, uses tools, submits bounded tasks, reports back
- hosts the Deep-Agents-style harness (plans, scratchpads, todos, subagents)
- callable specialists live here as bounded, read-only organs

### Codex lane

- one executor lane behind Hermes-node's routing policy
- hardening over time: manifests, receipts, durability, sandbox envelope
- not a separate agent stack

### Temporal (later)

- durable orchestration and scheduling above Hermes-node
- consumes the invoke seam, does not reimplement the membrane

## Implementation milestones (April 2026)

These labels describe code increments in the development history.

- v0.5 Telegram ingress
- v0.6 Deep-Agents-style operator harness
- v0.7 Runtime seam and response synthesis
- v0.8 Project memory and callable specialists
- v0.9 Specialist hardening, trigger scaffolding, runtime pressure relief,
  anti-narration guards, alias-resolving repo tools
