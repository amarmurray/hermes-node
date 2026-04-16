"""Trigger / autonomy scaffolding for specialist subagents.

Pure metadata. No daemon. No scheduler. No network.

This module declares the vocabulary of event categories that specialists
may respond to and builds a manifest of trigger subscriptions from the
specialist registry. All subscriptions are disabled in Phase 5 — no live
triggers exist yet. A future scheduler (Temporal or otherwise) will
consume this manifest to wire up actual event routing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Event categories that specialists may later respond to.
# These are declared vocabulary, not live event sources.
class EventCategory:
    REPO_STATE_CHANGED = "repo_state_changed"
    APPROVAL_RESOLVED = "approval_resolved"
    SCHEDULE_TICK = "schedule_tick"
    TASK_COMPLETED = "task_completed"
    RATE_LIMIT_HIT = "rate_limit_hit"


ALL_EVENT_CATEGORIES: tuple[str, ...] = (
    EventCategory.REPO_STATE_CHANGED,
    EventCategory.APPROVAL_RESOLVED,
    EventCategory.SCHEDULE_TICK,
    EventCategory.TASK_COMPLETED,
    EventCategory.RATE_LIMIT_HIT,
)


@dataclass(frozen=True)
class TriggerSubscription:
    """A specialist's declared interest in an event category.

    This is preparatory metadata. No subscription is live until
    a future scheduler (Temporal or otherwise) actually consumes it.
    """

    specialist_role: str
    event_category: str
    cadence: str = ""  # e.g. "on_event", "every_6h", "daily"
    enabled: bool = False  # always False in Phase 5 — no live triggers yet
    notes: str = ""


@dataclass(frozen=True)
class TriggerManifest:
    """The complete set of trigger subscriptions across all specialists."""

    subscriptions: tuple[TriggerSubscription, ...] = ()

    def for_specialist(self, role: str) -> tuple[TriggerSubscription, ...]:
        return tuple(s for s in self.subscriptions if s.specialist_role == role)

    def for_event(self, category: str) -> tuple[TriggerSubscription, ...]:
        return tuple(s for s in self.subscriptions if s.event_category == category)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subscriptions": [
                {
                    "specialist_role": s.specialist_role,
                    "event_category": s.event_category,
                    "cadence": s.cadence,
                    "enabled": s.enabled,
                    "notes": s.notes,
                }
                for s in self.subscriptions
            ],
            "total": len(self.subscriptions),
            "enabled_count": sum(1 for s in self.subscriptions if s.enabled),
        }


def build_trigger_manifest() -> TriggerManifest:
    """Build the trigger manifest from specialist specs.

    Reads SPECIALIST_SPECS and generates one subscription per
    (specialist, trigger_event) pair. All disabled in Phase 5.
    """
    from .harness.specialists import SPECIALIST_SPECS

    subs: list[TriggerSubscription] = []
    for spec in SPECIALIST_SPECS.values():
        for evt in spec.trigger_events:
            cadence = "every_6h" if evt == "schedule_tick" else "on_event"
            subs.append(
                TriggerSubscription(
                    specialist_role=spec.role,
                    event_category=evt,
                    cadence=cadence,
                    enabled=False,
                    notes=f"Phase 5 scaffold; {spec.purpose}"
                    if spec.purpose
                    else "Phase 5 scaffold",
                )
            )
    return TriggerManifest(subscriptions=tuple(subs))
