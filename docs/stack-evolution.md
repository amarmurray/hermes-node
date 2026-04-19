# Stack evolution

Canonical topology and roadmap for `hermes-node`. This is the map —
everything else (README, operator-architecture, phase docs) defers to
this file for the big picture.

## Where we are now

The architecture is structurally real. We are no longer in bootstrap mode.

Live today:

- upstream Hermes runtime substrate on the VPS
- Hermes-node constitutional membrane (ledger, routing, approvals, workspaces, receipts)
- resident cognitive operator on top of Hermes-node
- terminal REPL and Telegram as ingresses into the same operator
- Deep-Agents-style harness layered into the operator (plans, scratchpads, todos, callable specialists)
- Codex as an execution lane behind Hermes-node
- repo registry with live entries for `hermes-node` and `ixion`
- Temporal seam (invocation + outcome shapes) without Temporal itself
- trigger manifest scaffolding (all subscriptions disabled)

The current bottleneck is **operator usefulness / cognition**, not
architecture. Local operator quality is the constraint, not the
substrate's existence.

## Current topology

```
Human
  → ingresses (terminal REPL, Telegram)
  → operator (resident cognitive foreman)
  → Hermes-node (membrane: ledger / routing / approvals / workspaces / receipts)
  → executors (Codex lane, Hermes/OpenRouter, later Claude, later specialists)
  → artifacts (workspaces, receipts, session plans, notes, subagent reports)
  → operator reads back → reports to human
```

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

## Repo / domain status

| Repo | Role | Status |
|---|---|---|
| `hermes-node` | substrate + operator (this repo) | live |
| `hermes-agent` (upstream) | Hermes runtime | live, pinned on VPS |
| `ixion` / `robotics-vp-core` | robotics VP core | live on VPS |
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

## Completed phases (for context)

- v0.5 Telegram ingress
- v0.6 Deep-Agents-style operator harness
- v0.7 Runtime seam and response synthesis
- v0.8 Project memory and callable specialists
- v0.9 Specialist hardening, trigger scaffolding, runtime pressure relief,
  anti-narration guards, alias-resolving repo tools
