#!/usr/bin/env python3
"""Operator backend bakeoff — compare model quality and latency on real tasks.

Usage:
    python3 scripts/operator_bakeoff.py [--model MODEL] [--base-url URL] [--out DIR]

Runs a fixed prompt set against the configured backend and saves results to
state/bakeoff/{timestamp}/. Pass --model and --base-url to compare different
backends. Results are structured JSON so you can diff runs.

Prompt set covers:
    1. Simple status query (queue)          — should need 1 tool + synthesis
    2. Repo registry lookup                 — should name registered repos
    3. Task history with grounding          — tests ledger + grounding
    4. Specialist recommendation            — tests constitution understanding
    5. Multi-step: repo state + branch      — tests chaining
    6. Narration-trap: vague "check things" — guard must prevent narration
    7. No-tool conversational               — must NOT call unnecessary tools
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.config import Settings
from hermes_node_ops.operator.backend import create_backend
from hermes_node_ops.operator.runtime import run_operator_turn
from hermes_node_ops.operator.session import create_session
from hermes_node_ops.operator.tools import ToolExecutor

# ---------------------------------------------------------------------------
# Prompt set
# ---------------------------------------------------------------------------

PROMPTS: list[dict] = [
    {
        "id": "queue_status",
        "prompt": "show me the current queue status",
        "expect_tools": ["get_queue_status"],
        "expect_no_narration": True,
        "category": "simple_tool",
    },
    {
        "id": "repo_registry",
        "prompt": "what repos are registered?",
        "expect_tools": ["list_registered_repos"],
        "expect_no_narration": True,
        "category": "simple_tool",
    },
    {
        "id": "ledger_tasks",
        "prompt": "what tasks are in the ledger right now?",
        "expect_tools": ["list_recent_tasks", "get_queue_status"],
        "expect_no_narration": True,
        "category": "simple_tool",
        "note": "Either tool surfaces recent tasks; list_recent_tasks is leaner.",
    },
    {
        "id": "specialist_rec",
        "prompt": "what specialist would be best to inspect recent failures?",
        "expect_tools": [],
        "expect_no_narration": True,
        "category": "constitution",
        "note": "Should answer from knowledge, not necessarily call a tool",
    },
    {
        "id": "repo_state_chained",
        "prompt": "what is the current branch and status of the ixion repo?",
        "expect_tools": [
            "summarize_repo_state",
            "grounded_repo_summary",
            "get_repo_registry_entry",
            "summarize_changed_files",
        ],
        "expect_no_narration": True,
        "category": "multi_tool",
        "note": "Any repo state/status tool counts — summarize_repo_state, grounded_repo_summary, etc. Not find_repo_relevance (that's for file search).",
    },
    {
        "id": "narration_trap",
        "prompt": "can you check a few things for me — queue, recent tasks, and any active cooldowns?",
        "expect_tools": ["get_queue_status"],
        "expect_no_narration": True,
        "category": "narration_guard",
        "note": "Must act, not describe what it would check",
    },
    {
        "id": "conversational",
        "prompt": "remind me what the operator's job is",
        "expect_tools": [],
        "expect_no_narration": False,
        "category": "conversational",
        "note": "No tools needed — should answer from constitution",
    },
]

# ---------------------------------------------------------------------------
# Narration detector (mirrors runtime._looks_like_narration_without_action)
# ---------------------------------------------------------------------------

import re

_NARRATION_MARKERS = re.compile(
    r"\b(i\s+(would|will|can|should|need\s+to|am\s+going\s+to|'ll)"
    r"|let\s+me|i'm\s+going\s+to)"
    r"\s+(call|check|use|invoke|run|look|query|inspect|fetch|retrieve|get)\b",
    re.IGNORECASE,
)


def _has_narration(text: str) -> bool:
    return bool(_NARRATION_MARKERS.search(text))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_bakeoff(
    model: str,
    base_url: str,
    timeout: int,
    out_dir: Path,
    max_tool_rounds: int = 5,
) -> list[dict]:
    settings = Settings.load(ROOT)
    backend = create_backend(model=model, base_url=base_url, timeout_seconds=timeout)

    if not backend.available():
        print(f"[error] backend not reachable at {base_url or '(default)'} with model {model}")
        sys.exit(1)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = out_dir / f"bakeoff_{run_id}_{model.replace(':', '-').replace('/', '-')}"
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"bakeoff: model={model}  timeout={timeout}s  out={run_dir}")
    print(f"{'#':<4} {'id':<22} {'cat':<16} {'ms':>6} {'rnd':>4} {'tools':<30} {'ok':>4}")
    print("-" * 95)

    results: list[dict] = []

    for p in PROMPTS:
        # Fresh session per prompt so history doesn't bleed
        sid = create_session(settings.repo_root, model=model)
        executor = ToolExecutor(settings, settings.repo_root, session_id=sid)

        t0 = time.time()
        try:
            r = run_operator_turn(
                settings=settings,
                backend=backend,
                executor=executor,
                repo_root=settings.repo_root,
                session_id=sid,
                user_input=p["prompt"],
                max_tool_rounds=max_tool_rounds,
            )
            elapsed_ms = int((time.time() - t0) * 1000)
            narration = _has_narration(r.text)
            tools_called = r.effects.tool_names

            # Pass/fail heuristics
            narration_fail = p["expect_no_narration"] and narration
            expected_tools = p["expect_tools"]
            tool_hit = (
                not expected_tools  # no tools expected → pass if none called
                or any(t in tools_called for t in expected_tools)
            )
            ok = not r.error and not narration_fail and tool_hit

            row = {
                "id": p["id"],
                "category": p["category"],
                "prompt": p["prompt"],
                "model": model,
                "elapsed_ms": elapsed_ms,
                "tool_rounds": r.tool_rounds,
                "tools_called": tools_called,
                "expected_tools": expected_tools,
                "tool_hit": tool_hit,
                "narration_in_output": narration,
                "narration_fail": narration_fail,
                "error": r.error,
                "ok": ok,
                "text": r.text,
                "raw_text": r.raw_text,
                "synthesis_notes": list(r.synthesis_notes),
                "context_pressure": r.effects.context_pressure,
                "tool_limit_reached": r.tool_limit_reached,
                "note": p.get("note", ""),
            }
            status = "✓" if ok else "✗"
            print(
                f"{status:<4} {p['id']:<22} {p['category']:<16} "
                f"{elapsed_ms:>6} {r.tool_rounds:>4} "
                f"{','.join(tools_called) or '-':<30} "
                f"{'OK' if ok else 'FAIL':>4}"
            )

        except Exception as exc:
            elapsed_ms = int((time.time() - t0) * 1000)
            row = {
                "id": p["id"],
                "category": p["category"],
                "prompt": p["prompt"],
                "model": model,
                "elapsed_ms": elapsed_ms,
                "error": str(exc),
                "ok": False,
            }
            print(f"{'✗':<4} {p['id']:<22} {p['category']:<16} {elapsed_ms:>6} {'ERR':>4}")

        results.append(row)

        # Save individual result immediately
        result_path = run_dir / f"{p['id']}.json"
        result_path.write_text(json.dumps(row, indent=2, default=str), encoding="utf-8")

    # Summary
    passed = sum(1 for r in results if r.get("ok"))
    total = len(results)
    avg_ms = sum(r.get("elapsed_ms", 0) for r in results) // total
    print("-" * 95)
    print(f"PASS {passed}/{total}  avg_ms={avg_ms}")

    summary = {
        "run_id": run_id,
        "model": model,
        "base_url": base_url,
        "timeout_seconds": timeout,
        "passed": passed,
        "total": total,
        "avg_ms": avg_ms,
        "results": results,
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    print(f"results saved to {run_dir}")
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Operator backend bakeoff — compare model quality and latency"
    )
    parser.add_argument("--model", default="qwen3:1.7b", help="Model name")
    parser.add_argument("--base-url", default="", help="Backend base URL")
    parser.add_argument("--timeout", type=int, default=300, help="Per-request timeout (s)")
    parser.add_argument(
        "--out",
        default=str(ROOT / "state" / "bakeoff"),
        help="Output directory",
    )
    args = parser.parse_args()
    run_bakeoff(
        model=args.model,
        base_url=args.base_url,
        timeout=args.timeout,
        out_dir=Path(args.out),
    )


if __name__ == "__main__":
    main()
