# Phase 3 Runtime And Temporal Seam

This phase does not add Temporal. It adds the seam Temporal will later call.

## What Landed

- shared operator turn runtime for terminal, Telegram, and future callers
- natural-language response synthesis on top of grounded tool results
- backend policy with resident-local, optional remote escalation, and fallback slots
- structured invoke seam returning approval/task/plan outcomes for future schedulers
- explicit inventory of repeated workflows that fit Temporal later

## Backend Policy

The operator stays always-on through a resident local backend first.

- `resident_local`: current default, local Ollama/qwen path
- `escalation_remote`: optional stronger remote slot for heavier repo cognition
- `fallback`: explicit emergency slot if both primary paths are unavailable

Selection is intent-driven, observable, and overridable at the CLI without
changing the policy model underneath.

## Natural-Language Layer

Terminal and Telegram now share one response synthesis pass.

- strips leaked thinking tags and canned preambles
- collapses raw tool/result echoes into conversational answers
- preserves grounding via tool/result glosses when needed
- keeps raw receipts and workspaces as the source of truth

The default answer is conversational and concise. Evidence still lives in the
receipts, workspaces, and tool surfaces.

## Temporal Seam

Future workflow engines should call the operator through:

- `OperatorInvocation`
- `invoke_operator_once(...)` or `invoke_operator_with_policy(...)`
- `OperatorOutcome`

The outcome answers the questions a scheduler needs without making Hermes-node
become the scheduler:

- what the operator said
- whether approval is needed
- whether tasks, plans, proposals, or subagents were created
- which tool surfaces were used

Temporal later sits above this seam, not under or around it.

## Workflow Candidates

- repeated repo diagnostics
- approval-wait / retry flows
- repo maintenance and triage loops
- specialist wakeups
- later GPU-backed provider bringups, loop runs, and training orchestration

These are good Temporal fits because they benefit from durable waiting,
resumption, and retries while still executing through Hermes-node policy.
