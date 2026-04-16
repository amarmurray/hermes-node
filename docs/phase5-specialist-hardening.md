# Phase 5: Specialist hardening, trigger scaffolding, runtime pressure relief

## Specialist registry

All four specialists are read-only and callable-only in Phase 5.

| Role                | Purpose                                          | Autonomy eligible | Trigger events                      |
|---------------------|--------------------------------------------------|-------------------|-------------------------------------|
| rate-limit-triage   | Diagnose rate-limit patterns from ledger history | Yes               | rate_limit_hit, schedule_tick       |
| repo-diagnostician  | Assess repo health, branch state, artifacts      | Yes               | repo_state_changed, schedule_tick   |
| patch-review        | Review patch proposals without mutating           | No                | task_completed                      |
| ixion-diagnostician | Diagnose Ixion registry presence and repo state  | Yes               | repo_state_changed, schedule_tick   |

New `SpecialistSpec` fields: `read_only`, `risk_class`, `autonomy_eligible`, `trigger_events`, `purpose`, `callable_only`.

`render_specialist_manifest()` returns a structured dict for schedulers and tools.

## Trigger manifest

`TriggerManifest` is a frozen dataclass holding `TriggerSubscription` tuples.
Each subscription pairs a specialist role with an event category and a cadence.

All subscriptions are `enabled=False` in Phase 5. The manifest is pure metadata
that a future Temporal workflow or scheduler can consume to wire up live triggers.

Event categories (declared vocabulary, not live sources):
- `repo_state_changed`, `approval_resolved`, `schedule_tick`, `task_completed`, `rate_limit_hit`

`build_trigger_manifest()` reads `SPECIALIST_SPECS` and generates one subscription
per (specialist, trigger_event) pair.

## Connection to future Temporal

- `SpecialistOutcome` gains `trigger_source` field (empty = manual invocation)
- `TriggerManifest.to_dict()` produces the shape a Temporal activity would consume
- No daemon, scheduler, or network code is added in Phase 5

## Context pressure relief

`_estimate_context_pressure()` in `runtime.py` uses a 4-chars-per-token heuristic
against a conservative 4096-token budget.

When pressure is "high" (ratio > 0.8):
- `max_tool_rounds` is capped at `min(max_tool_rounds, 4)`
- `response_mode` degrades from "natural" to "brief" (480-char soft cap)

`TurnEffects` gains `context_pressure` and `context_pressure_ratio` fields.

`BackendDescriptor` gains a `timeout_category` property: "fast" (<=30s), "normal" (<=90s), "slow" (>90s).

## TreasuryOS

Future-slot only. No implementation in Phase 5.
