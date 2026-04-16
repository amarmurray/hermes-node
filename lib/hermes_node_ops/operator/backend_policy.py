"""Operator backend policy — formal model strategy for the operator.

The operator is meant to be **substrate-native and always-on**. It must
not depend on a single ingress (Telegram), a single backend, or a single
provider being available. It must also be able to escalate to a stronger
remote backend for repo-heavy cognition without losing the resident
local fallback.

This module is the **policy layer** that sits above ``backend.create_backend``.
It does not replace the OpenAI-compatible HTTP backend. Instead it formalizes:

- which backend slots exist (resident local, escalation remote, fallback)
- how the operator picks one for a given intent
- how it escalates and falls back
- which env vars and Settings fields drive each slot

The policy is intentionally **inert metadata** plus a tiny pure-python
selection function. No network calls. No new backend implementations. This
keeps Phase 3 surgical: existing REPL/Telegram code keeps working with
their current backend, but future invocations (terminal commands, Temporal
workflows, bounded subagents) can call ``select_backend()`` and get a
descriptor with explicit reasons attached.

Design rules:

- the resident local backend must always be defined and reachable in
  principle (Ollama on the VPS today)
- the escalation slot is **optional**: if no remote backend is configured,
  the policy returns the resident slot with an explicit "no escalation
  configured" reason
- the operator never bypasses Hermes-node by talking to a backend directly
  for substrate operations; backends only generate operator *cognition*
- escalation is allowed for higher-cost intents (planning, repo
  diagnostics, multi-tool synthesis) but is never required
- the policy is observable: every selection comes with a ``reason`` that
  the operator can surface to the human or write into a session note

The policy doc lives at ``docs/phase3-runtime-and-temporal-seam.md``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..config import Settings


class BackendSlot(str, Enum):
    """Declared backend slots in the policy."""

    RESIDENT_LOCAL = "resident_local"
    ESCALATION_REMOTE = "escalation_remote"
    FALLBACK = "fallback"


class OperatorIntent(str, Enum):
    """Intent classes that drive backend selection.

    These are intentionally coarse. The policy is a *seam*, not a router —
    fine-grained routing is the upstream Hermes / hermes-node concern.
    """

    CHAT = "chat"  # resident conversational replies
    PLAN = "plan"  # multi-step planning / harness work
    REPO_DIAGNOSTIC = "repo_diagnostic"  # repo-heavy synthesis
    TOOL_DENSE = "tool_dense"  # many tool calls in a turn
    BACKGROUND = "background"  # idle / scheduled / autonomous
    APPROVAL_BRIEF = "approval_brief"  # short human-facing summaries


# ---------------------------------------------------------------------------
# Slot descriptors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BackendDescriptor:
    """A concrete backend the policy can select.

    All fields are optional so the policy works even when only the
    resident slot is configured. The operator never makes up credentials —
    if a slot has no ``base_url`` the policy treats it as unconfigured.
    """

    slot: BackendSlot
    provider: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout_seconds: int = 120
    notes: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.model)

    @property
    def timeout_category(self) -> str:
        """Classify timeout for degradation decisions."""
        if self.timeout_seconds <= 30:
            return "fast"
        elif self.timeout_seconds <= 90:
            return "normal"
        return "slow"

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot.value,
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "configured": self.is_configured,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BackendPolicy:
    """Resolved view of the backend slot configuration.

    Built by ``load_backend_policy(settings)``. The policy exposes the
    declared slots and a single ``select(intent)`` method that returns
    a chosen descriptor + explanation. The policy is read-only and
    deterministic.
    """

    resident_local: BackendDescriptor
    escalation_remote: BackendDescriptor
    fallback: BackendDescriptor

    # Ordered list of slots checked when the chosen slot is unavailable.
    failover_order: tuple[BackendSlot, ...] = (
        BackendSlot.ESCALATION_REMOTE,
        BackendSlot.RESIDENT_LOCAL,
        BackendSlot.FALLBACK,
    )

    def slots(self) -> tuple[BackendDescriptor, ...]:
        return (self.resident_local, self.escalation_remote, self.fallback)

    def get(self, slot: BackendSlot) -> BackendDescriptor:
        for d in self.slots():
            if d.slot == slot:
                return d
        raise KeyError(f"unknown slot: {slot}")

    def select(self, intent: OperatorIntent | str = OperatorIntent.CHAT) -> "BackendSelection":
        """Pick a backend for ``intent``, with an explicit reason."""
        intent_obj = OperatorIntent(intent) if isinstance(intent, str) else intent
        preferred = self._preferred_slot(intent_obj)

        # Walk the slot preference order, falling through if a slot isn't
        # configured. Resident local is the last conventional stop; the
        # fallback slot exists for emergencies (e.g. air-gapped runs).
        order: list[BackendSlot] = [preferred]
        for s in self.failover_order:
            if s != preferred and s not in order:
                order.append(s)

        tried: list[str] = []
        for slot in order:
            d = self.get(slot)
            if d.is_configured:
                reason = self._reason(intent_obj, slot, tried)
                return BackendSelection(
                    descriptor=d, intent=intent_obj, reason=reason, tried=tuple(tried)
                )
            tried.append(f"{slot.value}: not configured")

        # Nothing is configured. Return the resident slot anyway with an
        # explicit "no backend configured" reason so the caller can decide
        # what to do (usually: report up rather than silently fail).
        return BackendSelection(
            descriptor=self.resident_local,
            intent=intent_obj,
            reason="no backend slot is configured; returning resident_local placeholder",
            tried=tuple(tried),
        )

    @staticmethod
    def _preferred_slot(intent: OperatorIntent) -> BackendSlot:
        # Heavy cognition prefers the escalation slot if available.
        if intent in (
            OperatorIntent.PLAN,
            OperatorIntent.REPO_DIAGNOSTIC,
            OperatorIntent.TOOL_DENSE,
        ):
            return BackendSlot.ESCALATION_REMOTE
        # Chat, brief, background → resident local first to stay always-on
        # and cheap.
        return BackendSlot.RESIDENT_LOCAL

    @staticmethod
    def _reason(
        intent: OperatorIntent, chosen: BackendSlot, tried: list[str]
    ) -> str:
        if not tried:
            return f"intent={intent.value}: chose {chosen.value} as preferred slot"
        why = f"intent={intent.value}: fell through {len(tried)} slot(s) -> {chosen.value}"
        return why + " [" + "; ".join(tried) + "]"


@dataclass(frozen=True)
class BackendSelection:
    """The result of a single ``policy.select(intent)`` call."""

    descriptor: BackendDescriptor
    intent: OperatorIntent
    reason: str
    tried: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "chosen": self.descriptor.to_dict(),
            "reason": self.reason,
            "tried": list(self.tried),
        }


# ---------------------------------------------------------------------------
# Loading from Settings + env
# ---------------------------------------------------------------------------


def load_backend_policy(settings: Settings) -> BackendPolicy:
    """Build the runtime policy from Settings + environment.

    Env vars (read directly so the policy stays decoupled from Settings
    field churn):

    - ``HERMES_OPERATOR_BASE_URL``      → resident local base url
    - ``HERMES_OPERATOR_MODEL``         → resident local model
    - ``HERMES_OPERATOR_API_KEY``       → resident local api key (optional)
    - ``HERMES_OPERATOR_REMOTE_BASE_URL``  → escalation remote base url
    - ``HERMES_OPERATOR_REMOTE_MODEL``     → escalation remote model
    - ``HERMES_OPERATOR_REMOTE_API_KEY``   → escalation remote key
    - ``HERMES_OPERATOR_FALLBACK_BASE_URL``→ explicit emergency fallback
    - ``HERMES_OPERATOR_FALLBACK_MODEL``   → explicit emergency fallback
    """
    resident = BackendDescriptor(
        slot=BackendSlot.RESIDENT_LOCAL,
        provider="ollama",
        base_url=os.environ.get("HERMES_OPERATOR_BASE_URL", "http://localhost:11434"),
        api_key=os.environ.get("HERMES_OPERATOR_API_KEY", ""),
        model=os.environ.get("HERMES_OPERATOR_MODEL", "qwen3:1.7b"),
        notes="resident local operator backend; always-on, cheap, used for chat and brief intents",
    )

    escalation = BackendDescriptor(
        slot=BackendSlot.ESCALATION_REMOTE,
        provider=os.environ.get("HERMES_OPERATOR_REMOTE_PROVIDER", settings.default_provider),
        base_url=os.environ.get("HERMES_OPERATOR_REMOTE_BASE_URL", ""),
        api_key=os.environ.get("HERMES_OPERATOR_REMOTE_API_KEY", ""),
        model=os.environ.get("HERMES_OPERATOR_REMOTE_MODEL", ""),
        notes="optional stronger remote backend for repo-heavy and multi-tool cognition",
    )

    fallback = BackendDescriptor(
        slot=BackendSlot.FALLBACK,
        provider=os.environ.get("HERMES_OPERATOR_FALLBACK_PROVIDER", "ollama"),
        base_url=os.environ.get("HERMES_OPERATOR_FALLBACK_BASE_URL", ""),
        api_key=os.environ.get("HERMES_OPERATOR_FALLBACK_API_KEY", ""),
        model=os.environ.get("HERMES_OPERATOR_FALLBACK_MODEL", ""),
        notes="explicit emergency fallback; only used when both resident and escalation are unavailable",
    )

    return BackendPolicy(
        resident_local=resident,
        escalation_remote=escalation,
        fallback=fallback,
    )


def render_backend_policy(policy: BackendPolicy) -> dict[str, Any]:
    """Render the policy as a structured dict for tool/API responses."""
    return {
        "slots": [d.to_dict() for d in policy.slots()],
        "failover_order": [s.value for s in policy.failover_order],
    }


def resolve_backend(
    settings: Settings,
    *,
    intent: OperatorIntent | str = OperatorIntent.CHAT,
    model_override: str = "",
    base_url_override: str = "",
    api_key_override: str = "",
    timeout_seconds: int = 0,
) -> tuple[Any, BackendSelection]:
    """Resolve a concrete backend instance plus the policy selection used.

    Explicit overrides are allowed for operator CLI and tests, but they do
    not bypass the policy; they apply on top of the selected slot.
    """
    from .backend import create_backend

    policy = load_backend_policy(settings)
    selection = policy.select(intent)
    descriptor = selection.descriptor

    backend = create_backend(
        base_url=base_url_override or descriptor.base_url,
        api_key=api_key_override or descriptor.api_key,
        model=model_override or descriptor.model,
        timeout_seconds=timeout_seconds or descriptor.timeout_seconds,
    )
    if not any((model_override, base_url_override, api_key_override, timeout_seconds)):
        return backend, selection

    override_bits: list[str] = []
    if base_url_override:
        override_bits.append("base_url")
    if model_override:
        override_bits.append("model")
    if api_key_override:
        override_bits.append("api_key")
    if timeout_seconds:
        override_bits.append("timeout_seconds")

    effective = BackendDescriptor(
        slot=descriptor.slot,
        provider=descriptor.provider,
        base_url=base_url_override or descriptor.base_url,
        api_key=api_key_override or descriptor.api_key,
        model=model_override or descriptor.model,
        timeout_seconds=timeout_seconds or descriptor.timeout_seconds,
        notes=descriptor.notes,
    )
    return backend, BackendSelection(
        descriptor=effective,
        intent=selection.intent,
        reason=selection.reason + f"; applied explicit overrides: {', '.join(override_bits)}",
        tried=selection.tried,
    )
