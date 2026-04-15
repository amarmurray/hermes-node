"""Natural-language response synthesis for the operator.

Small models tend to emit responses that *look* answer-shaped but feel
templated, robotic, or stuffed with rendering artifacts:

- "<think>...</think>" leakage from reasoning models
- preambles like "I will now use the X tool..." or "Sure, here you go:"
- repeated headings, bullet salads, "## Summary\n## Summary\n"
- raw JSON blobs echoed back from a tool result
- empty content with only ``response.tool_calls`` still in flight
- canned phrases like "As an AI..." or "I cannot help with that, however..."

This module is the natural-language seam every operator ingress (terminal,
Telegram, future Temporal-driven invocations) routes its final assistant
text through. It is intentionally:

- **pure-python** (no LLM call); cheap enough to run on every reply
- **non-destructive**: when in doubt it preserves grounding rather than
  rewriting it
- **mode-aware**: ``natural`` (default), ``brief``, ``full``, ``raw``
- **grounded**: tool result references survive; provenance lines are kept

The synthesizer never invents facts. It cleans, condenses, and humanizes
text the model already produced. If the model said nothing, the synthesizer
returns a short conversational fallback that *names what's missing* rather
than fabricating an answer.

The constitution still applies: artifacts > prose. This layer makes the
prose feel less like a status dump while keeping the underlying receipts,
workspaces, and ledger as the source of truth.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

SynthesisMode = str  # one of: "natural", "brief", "full", "raw"

VALID_MODES: tuple[str, ...] = ("natural", "brief", "full", "raw")

# Soft caps per mode. The synthesizer never truncates mid-sentence; it
# truncates at the nearest paragraph boundary above the cap.
MODE_SOFT_CAPS: dict[str, int] = {
    "natural": 1400,
    "brief": 480,
    "full": 6000,
    "raw": 0,  # unbounded
}


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Reasoning/thinking tag leakage we strip wholesale. Match across newlines.
_THINK_BLOCK = re.compile(
    r"<\s*(think|thinking|scratch|scratchpad|reflection)\s*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_BARE_TAGS = re.compile(
    r"<\s*/?\s*(think|thinking|scratch|scratchpad|reflection)\s*>",
    re.IGNORECASE,
)

# Preambles that small models love and humans hate.
_PREAMBLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(sure|certainly|of course|absolutely|alright|okay|ok)[,!.\s].*?\n", re.IGNORECASE),
    re.compile(r"^(let me|i (will|am going to|'ll)) (now |first |go ahead and )?(use|call|invoke|run|check|inspect)[^\n]*\n", re.IGNORECASE),
    re.compile(r"^here'?s? (the|a|your|what)[^\n]{0,80}:\s*\n", re.IGNORECASE),
    re.compile(r"^as an? (ai|assistant|language model|llm)[^\n]*\n", re.IGNORECASE),
    re.compile(r"^based on (the|my) (above|previous|tool|results?)[^\n]*\n", re.IGNORECASE),
    re.compile(r"^(thinking|reasoning|analysis):\s*\n", re.IGNORECASE),
)

# Canned status-dump openers we collapse rather than delete (we keep one).
_DUPLICATE_HEADINGS = re.compile(r"(?m)^(##+\s*[^\n]+)\n+\1\n", re.IGNORECASE)

# Bare JSON-only output from a small model that just echoed a tool result.
_LOOKS_LIKE_BARE_JSON = re.compile(r"^\s*[\{\[][\s\S]*[\}\]]\s*$")

# "Summary:" / "Result:" labels that small models sprinkle when they aren't
# producing actual structured output.
_LABEL_NOISE = re.compile(r"(?m)^(summary|result|response|answer|output)\s*:\s*", re.IGNORECASE)

# Repeated whitespace — collapsed last so we don't break code fences.
_MULTI_BLANK = re.compile(r"\n{3,}")

# Detect a code fence so we leave its contents alone.
_CODE_FENCE = re.compile(r"```[\s\S]*?```")


# ---------------------------------------------------------------------------
# Tool result gloss helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolResultRef:
    """A reference to a tool result the model used during this turn.

    The synthesizer can optionally inject a one-line gloss for these
    references when the model's text doesn't mention them. This keeps
    grounding visible without forcing a templated 'tool calls run:' block.
    """

    name: str
    payload_preview: str = ""
    success: bool = True


def _short_gloss(ref: ToolResultRef) -> str:
    """Return a single short human-readable sentence for a tool result."""
    base = ref.name.replace("_", " ")
    preview = ref.payload_preview.strip().splitlines()[0][:140] if ref.payload_preview else ""
    if not ref.success:
        if preview:
            return f"({base} failed: {preview})"
        return f"({base} failed; see receipts)"
    if not preview:
        return f"({base} ran clean)"
    return f"({base}: {preview})"


# ---------------------------------------------------------------------------
# Core synthesis
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SynthesisResult:
    """The result of synthesizing an assistant turn.

    ``text`` is what the user sees. ``raw`` is the unmodified model output
    (kept for grounding/forensics). ``notes`` records what the synthesizer
    decided to do (useful in tests and debugging)."""

    text: str
    raw: str
    notes: tuple[str, ...] = field(default_factory=tuple)
    mode: SynthesisMode = "natural"


def synthesize_assistant_text(
    content: str,
    *,
    mode: SynthesisMode = "natural",
    tool_refs: Iterable[ToolResultRef] | None = None,
    fallback: str = "",
) -> SynthesisResult:
    """Clean and humanize an assistant message.

    Parameters
    ----------
    content:
        Raw model output (may be empty, JSON, templated, etc.).
    mode:
        ``natural`` (default), ``brief``, ``full``, or ``raw``. ``raw``
        returns the input unchanged so forensic and test paths can opt out.
    tool_refs:
        Optional tool results from this turn. The synthesizer may inline
        a one-line gloss if the model's text doesn't already reference
        them. This preserves grounding without templated blocks.
    fallback:
        Optional fallback string used when the model produced nothing
        (or only noise). If empty, a short conversational default is used.
    """
    if mode not in VALID_MODES:
        raise ValueError(f"Unknown synthesis mode: {mode}")

    raw = content or ""
    notes: list[str] = []

    if mode == "raw":
        return SynthesisResult(text=raw, raw=raw, notes=("raw mode",), mode=mode)

    text = raw

    # 1. Strip <think>...</think> leakage from reasoning models. This is
    #    safe because it's never user-facing content.
    if _THINK_BLOCK.search(text):
        text = _THINK_BLOCK.sub("", text)
        notes.append("stripped think blocks")
    if _BARE_TAGS.search(text):
        text = _BARE_TAGS.sub("", text)
        notes.append("stripped bare think tags")

    # 2. If the entire payload is just JSON, the model likely echoed a tool
    #    result instead of synthesizing an answer. Convert it to a short
    #    natural sentence rather than dumping the JSON to the user.
    stripped = text.strip()
    if stripped and _LOOKS_LIKE_BARE_JSON.match(stripped):
        notes.append("collapsed bare JSON echo")
        text = _humanize_bare_json(stripped)

    # 3. Strip canned preambles. We only strip the *first* paragraph if it
    #    matches; we never touch later content because that's where the
    #    actual answer usually lives.
    text = _strip_preambles(text, notes)

    # 4. Collapse duplicate adjacent headings ("## Summary\n## Summary\n").
    if _DUPLICATE_HEADINGS.search(text):
        text = _DUPLICATE_HEADINGS.sub(r"\1\n", text)
        notes.append("collapsed duplicate headings")

    # 5. Drop label-noise lines like "Summary:" on their own.
    new_text, n_label = _LABEL_NOISE.subn("", text)
    if n_label:
        text = new_text
        notes.append("stripped label noise")

    # 6. Collapse 3+ blank lines.
    text = _MULTI_BLANK.sub("\n\n", text).strip()

    # 7. If the model said literally nothing useful, fall back gracefully.
    if not text:
        text = fallback or _empty_fallback(tool_refs)
        notes.append("used fallback for empty content")

    # 8. Optionally inject a one-line gloss for tool results the model
    #    didn't acknowledge in its text. This keeps grounding visible
    #    without rendering a templated "tool calls:" block.
    if tool_refs and mode in ("natural", "brief"):
        text = _maybe_append_tool_gloss(text, tool_refs, notes)

    # 9. Mode-specific soft cap.
    cap = MODE_SOFT_CAPS.get(mode, 0)
    if cap and len(text) > cap:
        text = _soft_truncate(text, cap)
        notes.append(f"soft-capped to {cap}")

    return SynthesisResult(text=text, raw=raw, notes=tuple(notes), mode=mode)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_preambles(text: str, notes: list[str]) -> str:
    head, _, _ = text.partition("\n\n")
    rest = text[len(head):]
    head_lower = head.lstrip().lower()
    for pat in _PREAMBLE_PATTERNS:
        if pat.match(head_lower + "\n"):
            notes.append("stripped preamble")
            return rest.lstrip("\n")
    # Also catch single-line preambles with no following paragraph.
    if "\n" not in text.strip():
        for pat in _PREAMBLE_PATTERNS:
            if pat.match(text.lower() + "\n"):
                notes.append("stripped single-line preamble")
                return ""
    return text


def _humanize_bare_json(payload: str) -> str:
    """Turn a JSON-only echo into a short natural sentence."""
    try:
        obj = json.loads(payload)
    except (ValueError, TypeError):
        return payload  # Not actually JSON — keep as-is.

    if isinstance(obj, dict):
        # Common shapes from this codebase. Order matters: more specific
        # shapes must be checked before the generic `error` fallback so a
        # `{"grounded": false, "error": ...}` payload is rendered as a
        # grounding refusal, not a flat error.
        if obj.get("grounded") is False:
            return f"No grounded result available: {obj.get('error', 'no evidence yet')}."
        if obj.get("written") is False or obj.get("submitted") is False:
            err = obj.get("error", "no reason given")
            return f"The operation was refused: {err}"
        if obj.get("submitted") is True and "task_id" in obj:
            return (
                f"Submitted task `{obj['task_id']}` "
                f"(state: {obj.get('state') or obj.get('task_state', 'unknown')})."
            )
        if "error" in obj:
            return f"That call returned an error: {str(obj['error'])[:200]}"
        if "summary" in obj and isinstance(obj["summary"], str):
            return obj["summary"].strip()
        if "counts" in obj and isinstance(obj["counts"], dict):
            counts = obj["counts"]
            return (
                f"{counts.get('present', 0)} present, "
                f"{counts.get('absent', 0)} absent, "
                f"{counts.get('future', 0)} future-slot."
            )
        # Fallback: list the top-level keys.
        keys = ", ".join(list(obj.keys())[:6])
        return f"Got a structured result with fields: {keys}."

    if isinstance(obj, list):
        return f"Got a list of {len(obj)} item(s)."

    return str(obj)[:200]


def _empty_fallback(tool_refs: Iterable[ToolResultRef] | None) -> str:
    refs = list(tool_refs or ())
    if refs:
        names = ", ".join(r.name for r in refs[:3])
        return (
            f"I ran {names} but didn't produce a written summary on top of it. "
            f"The tool results are in the session transcript if you want me to "
            f"walk through them."
        )
    return "I don't have anything to add on that yet."


def _maybe_append_tool_gloss(
    text: str,
    tool_refs: Iterable[ToolResultRef],
    notes: list[str],
) -> str:
    refs = list(tool_refs)
    if not refs:
        return text
    lower = text.lower()
    unmentioned = [r for r in refs if r.name.lower() not in lower]
    if not unmentioned:
        return text
    if len(unmentioned) > 3:
        unmentioned = unmentioned[:3]
    glosses = " ".join(_short_gloss(r) for r in unmentioned)
    notes.append(f"appended gloss for {len(unmentioned)} unmentioned tool(s)")
    return f"{text}\n\nGrounding: {glosses}"


def _soft_truncate(text: str, cap: int) -> str:
    """Truncate at the nearest paragraph boundary at or below ``cap``."""
    if len(text) <= cap:
        return text
    # Don't slice inside a code fence.
    fences = list(_CODE_FENCE.finditer(text))
    for f in fences:
        if f.start() < cap < f.end():
            cap = f.start()
            break
    head = text[:cap]
    cut = head.rfind("\n\n")
    if cut < cap // 2:
        cut = head.rfind(". ")
        if cut < cap // 2:
            cut = cap
        else:
            cut += 1
    return text[:cut].rstrip() + "\n\n…(reply trimmed; ask for the full version if needed)"


# ---------------------------------------------------------------------------
# Convenience for ingresses
# ---------------------------------------------------------------------------


def humanize_for_terminal(content: str, **kwargs: Any) -> str:
    """Convenience wrapper used by the REPL."""
    return synthesize_assistant_text(content, mode="natural", **kwargs).text


def humanize_for_telegram(content: str, **kwargs: Any) -> str:
    """Convenience wrapper used by the Telegram ingress."""
    return synthesize_assistant_text(content, mode="natural", **kwargs).text
