"""Intent inference for operator turns.

Maps a user's raw input (plus optional hints from the harness) to an
``OperatorIntent`` so the backend policy can route the turn deliberately
rather than hard-coding ``CHAT`` for everything.

Keep this lightweight and deterministic — no LLM call, no heuristics
that require live state. The policy is a seam; this is the classifier
that feeds it.

Design rules:

- inference never raises on malformed input — defaults to CHAT
- inference is purely pattern-based; no network, no LLM, no ledger reads
- inference is observable: each call returns (intent, matched_signal)
  so callers can log why escalation happened
- CHAT is the safe default; escalation to heavier slots requires a
  positive signal
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .backend_policy import OperatorIntent


# Multi-ask markers — "check X, Y, and Z" style prompts that will hit
# multiple tools in one turn. These benefit from the escalation slot
# because a small model often loses thread across many tool calls.
_MULTI_ASK: re.Pattern[str] = re.compile(
    r"\b("
    r"(check|inspect|look\s+at|tell\s+me\s+about)\s+[^?.!]*?\b(and|plus|as\s+well\s+as|,)\b"
    r"|a\s+few\s+things"
    r"|multiple\s+things"
    r"|both\s+(the\s+)?\w+(\s+\w+){0,2}\s+and"
    r")\b",
    re.IGNORECASE,
)

# Repo-heavy cognition markers.
_REPO_DIAGNOSTIC_MARKERS: re.Pattern[str] = re.compile(
    r"\b("
    r"diagnose|diagnosis|debug|root\s+cause|postmortem"
    r"|why\s+(did|does|is|was)\s+(\w+\s+){1,4}(fail|fails|failed|break|breaks|broke|crash|crashed|error|errored|stuck|hang|hanging)"
    r"|inspect\s+the\s+(repo|codebase|branch|worktree)"
    r"|compare\s+(to|with)\s+(last|previous)"
    r"|patch\s+(proposal|review|plan)"
    r"|propose\s+a?\s*patch"
    r"|assess\s+(repo|branch|codebase)"
    r")\b",
    re.IGNORECASE,
)

# Planning / harness markers.
_PLAN_MARKERS: re.Pattern[str] = re.compile(
    r"\b("
    r"plan\s+(this|that|the|a|out)"
    r"|write\s+a\s+plan"
    r"|update\s+the\s+plan"
    r"|think\s+through"
    r"|break\s+down"
    r"|decompose"
    r"|draft\s+a\s+(plan|roadmap|approach)"
    r")\b",
    re.IGNORECASE,
)

# Approval-brief markers — user wants a short verdict for approval.
_APPROVAL_BRIEF_MARKERS: re.Pattern[str] = re.compile(
    r"\b("
    r"approve|approval|sign\s*off"
    r"|should\s+i\s+approve"
    r"|(give\s+me\s+a|one-line|tldr|summary|brief)\s+(summary|verdict|recommendation)"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IntentInference:
    """The result of classifying a user turn."""

    intent: OperatorIntent
    signal: str
    """Short human-readable reason this intent was chosen. Goes into
    session notes / observability, not the LLM prompt."""

    def to_dict(self) -> dict[str, str]:
        return {"intent": self.intent.value, "signal": self.signal}


def infer_intent(user_input: str) -> IntentInference:
    """Classify a user turn into an ``OperatorIntent`` with a reason.

    Priority order (first match wins):
    1. REPO_DIAGNOSTIC — diagnose/debug/patch-review signals
    2. PLAN — planning/decomposition signals
    3. TOOL_DENSE — multi-ask signals ("check X, Y, and Z")
    4. APPROVAL_BRIEF — approval/signoff signals
    5. CHAT — default

    Not perfect — deliberately conservative. False-positive escalation
    is worse than a missed escalation because escalation costs money
    and latency on the remote slot.
    """
    text = (user_input or "").strip()
    if not text:
        return IntentInference(intent=OperatorIntent.CHAT, signal="empty input")

    m = _REPO_DIAGNOSTIC_MARKERS.search(text)
    if m:
        return IntentInference(
            intent=OperatorIntent.REPO_DIAGNOSTIC,
            signal=f"repo_diagnostic marker: {m.group(0)!r}",
        )

    m = _PLAN_MARKERS.search(text)
    if m:
        return IntentInference(
            intent=OperatorIntent.PLAN,
            signal=f"plan marker: {m.group(0)!r}",
        )

    m = _MULTI_ASK.search(text)
    if m:
        return IntentInference(
            intent=OperatorIntent.TOOL_DENSE,
            signal=f"multi-ask marker: {m.group(0)!r}",
        )

    m = _APPROVAL_BRIEF_MARKERS.search(text)
    if m:
        return IntentInference(
            intent=OperatorIntent.APPROVAL_BRIEF,
            signal=f"approval-brief marker: {m.group(0)!r}",
        )

    return IntentInference(intent=OperatorIntent.CHAT, signal="default (chat)")
