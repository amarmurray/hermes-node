"""File-backed harness state under an operator session."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ...config import Settings
from ...jsonl import utcnow
from ...models import new_id
from ..session import session_dir
from .specialists import SPECIALIST_ROLES, get_specialist_spec, run_specialist


TODO_LINE_RE = re.compile(r"^- \[(?P<done>[ xX])\] (?P<item_id>t\d+): (?P<text>.+)$")


def ensure_harness_layout(repo_root: Path, session_id: str) -> Path:
    """Ensure the harness files and directories exist for a session."""
    root = session_dir(repo_root, session_id)
    root.mkdir(parents=True, exist_ok=True)
    _ensure_file(root / "plan.md", "# Plan\n\n(no plan yet)\n")
    _ensure_file(root / "todo.md", "# Todo\n\n(no todo yet)\n")
    (root / "scratch").mkdir(parents=True, exist_ok=True)
    (root / "subagents").mkdir(parents=True, exist_ok=True)
    return root


def read_plan(repo_root: Path, session_id: str) -> str:
    root = ensure_harness_layout(repo_root, session_id)
    return (root / "plan.md").read_text(encoding="utf-8")


def write_plan(repo_root: Path, session_id: str, content: str) -> str:
    root = ensure_harness_layout(repo_root, session_id)
    plan = _normalize_markdown(content, default_heading="# Plan")
    (root / "plan.md").write_text(plan, encoding="utf-8")
    return plan


def update_plan(repo_root: Path, session_id: str, content: str) -> str:
    root = ensure_harness_layout(repo_root, session_id)
    plan_path = root / "plan.md"
    existing = plan_path.read_text(encoding="utf-8").rstrip()
    updated = (
        f"{existing}\n\n## Update {utcnow()}\n\n{content.strip()}\n"
        if existing
        else _normalize_markdown(content, default_heading="# Plan")
    )
    plan_path.write_text(updated, encoding="utf-8")
    return updated


def read_todo_items(repo_root: Path, session_id: str) -> list[dict[str, Any]]:
    markdown = read_todo_markdown(repo_root, session_id)
    items: list[dict[str, Any]] = []
    for line in markdown.splitlines():
        match = TODO_LINE_RE.match(line.strip())
        if not match:
            continue
        items.append(
            {
                "item_id": match.group("item_id"),
                "text": match.group("text").strip(),
                "done": match.group("done").lower() == "x",
            }
        )
    return items


def read_todo_markdown(repo_root: Path, session_id: str) -> str:
    root = ensure_harness_layout(repo_root, session_id)
    return (root / "todo.md").read_text(encoding="utf-8")


def write_todo_items(
    repo_root: Path,
    session_id: str,
    items: list[str],
    *,
    replace: bool = False,
) -> list[dict[str, Any]]:
    root = ensure_harness_layout(repo_root, session_id)
    existing = [] if replace else read_todo_items(repo_root, session_id)
    next_idx = _next_todo_index(existing)
    for text in items:
        existing.append(
            {"item_id": f"t{next_idx}", "text": text.strip(), "done": False}
        )
        next_idx += 1
    _write_todo_markdown(root / "todo.md", existing)
    return existing


def update_todo_item(
    repo_root: Path,
    session_id: str,
    item_id: str,
    text: str,
) -> dict[str, Any]:
    items = read_todo_items(repo_root, session_id)
    for item in items:
        if item["item_id"] == item_id:
            item["text"] = text.strip()
            _write_todo_markdown(ensure_harness_layout(repo_root, session_id) / "todo.md", items)
            return item
    raise ValueError(f"Todo item not found: {item_id}")


def complete_todo_item(
    repo_root: Path,
    session_id: str,
    item_id: str,
    *,
    note: str = "",
) -> dict[str, Any]:
    items = read_todo_items(repo_root, session_id)
    for item in items:
        if item["item_id"] == item_id:
            item["done"] = True
            if note.strip():
                item["text"] = f"{item['text']} ({note.strip()})"
            _write_todo_markdown(ensure_harness_layout(repo_root, session_id) / "todo.md", items)
            return item
    raise ValueError(f"Todo item not found: {item_id}")


def write_scratch(repo_root: Path, session_id: str, step_id: str, content: str) -> dict[str, Any]:
    root = ensure_harness_layout(repo_root, session_id)
    step_name = _safe_name(step_id)
    path = root / "scratch" / f"{step_name}.md"
    body = _normalize_markdown(content, default_heading=f"# Scratch {step_name}")
    path.write_text(body, encoding="utf-8")
    return {"step_id": step_name, "path": str(path), "content": body}


def read_scratch(repo_root: Path, session_id: str, step_id: str) -> dict[str, Any]:
    root = ensure_harness_layout(repo_root, session_id)
    step_name = _safe_name(step_id)
    path = root / "scratch" / f"{step_name}.md"
    if not path.exists():
        raise ValueError(f"Scratch file not found: {step_name}")
    return {"step_id": step_name, "path": str(path), "content": path.read_text(encoding="utf-8")}


def spawn_subagent(
    settings: Settings,
    repo_root: Path,
    session_id: str,
    *,
    role: str,
    goal: str,
    context: str = "",
    repo_path: str = "",
) -> dict[str, Any]:
    """Create and run a bounded child subagent under the parent session."""
    if role not in SPECIALIST_ROLES:
        raise ValueError(f"Unsupported subagent role: {role}")
    spec = get_specialist_spec(role)

    root = ensure_harness_layout(repo_root, session_id)
    subagent_id = new_id("subagent")
    subdir = root / "subagents" / subagent_id
    subdir.mkdir(parents=True, exist_ok=True)

    prompt = _build_subagent_prompt(
        role=role,
        goal=goal,
        context=context,
        repo_path=repo_path,
        constitution=spec.constitution,
        allowed_surfaces=spec.allowed_surfaces,
    )
    (subdir / "prompt.md").write_text(prompt, encoding="utf-8")
    _append_subagent_thread(subdir, role="user", content=prompt)

    metadata = {
        "subagent_id": subagent_id,
        "parent_session_id": session_id,
        "role": role,
        "goal": goal,
        "context": context,
        "repo_path": str(Path(repo_path).expanduser().resolve()) if repo_path else "",
        "status": "running",
        "created_at": utcnow(),
        "updated_at": utcnow(),
        "policy": {
            "approval": "forbidden",
            "mutation": "forbidden",
            "routing_bypass": "forbidden",
            "allowed_surfaces": list(spec.allowed_surfaces),
        },
        "constitution": spec.constitution,
    }
    _write_context(subdir, metadata)

    try:
        result = run_specialist(
            role,
            goal,
            context,
            repo_path,
            settings=settings,
        )
        (subdir / "result.md").write_text(result, encoding="utf-8")
        _append_subagent_thread(subdir, role="assistant", content=result)
        metadata["status"] = "completed"
        metadata["completed_at"] = utcnow()
        metadata["result_preview"] = _preview(result, 240)
    except Exception as exc:  # noqa: BLE001
        error = f"[subagent error] {exc}"
        (subdir / "result.md").write_text(error + "\n", encoding="utf-8")
        _append_subagent_thread(subdir, role="assistant", content=error)
        metadata["status"] = "failed"
        metadata["completed_at"] = utcnow()
        metadata["result_preview"] = error

    metadata["updated_at"] = utcnow()
    _write_context(subdir, metadata)
    return {
        "subagent_id": subagent_id,
        "role": role,
        "status": metadata["status"],
        "goal": goal,
        "result_preview": metadata["result_preview"],
    }


def list_subagents(repo_root: Path, session_id: str) -> list[dict[str, Any]]:
    root = ensure_harness_layout(repo_root, session_id) / "subagents"
    subagents: list[dict[str, Any]] = []
    for subdir in sorted(root.iterdir(), reverse=True):
        if not subdir.is_dir():
            continue
        ctx = _read_context(subdir)
        if ctx:
            subagents.append(ctx)
    return subagents


def read_subagent_result(repo_root: Path, session_id: str, subagent_id: str) -> dict[str, Any]:
    subdir = ensure_harness_layout(repo_root, session_id) / "subagents" / subagent_id
    if not subdir.exists():
        raise ValueError(f"Subagent not found: {subagent_id}")
    ctx = _read_context(subdir)
    result_path = subdir / "result.md"
    return {
        "subagent": ctx,
        "result": result_path.read_text(encoding="utf-8") if result_path.exists() else "",
        "thread": _read_thread(subdir),
    }


def _ensure_file(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _normalize_markdown(content: str, *, default_heading: str) -> str:
    text = content.strip()
    if not text:
        text = default_heading
    if not text.startswith("#"):
        text = f"{default_heading}\n\n{text}"
    if not text.endswith("\n"):
        text += "\n"
    return text


def _next_todo_index(items: list[dict[str, Any]]) -> int:
    indices = [
        int(item["item_id"][1:])
        for item in items
        if item.get("item_id", "").startswith("t") and item["item_id"][1:].isdigit()
    ]
    return max(indices, default=0) + 1


def _write_todo_markdown(path: Path, items: list[dict[str, Any]]) -> None:
    lines = ["# Todo", ""]
    if not items:
        lines.append("(no todo yet)")
    else:
        for item in items:
            marker = "x" if item.get("done") else " "
            lines.append(f"- [{marker}] {item['item_id']}: {item['text']}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("_")
    return cleaned or "step"


def _build_subagent_prompt(
    *,
    role: str,
    goal: str,
    context: str,
    repo_path: str,
    constitution: str,
    allowed_surfaces: tuple[str, ...],
) -> str:
    lines = [
        f"# Subagent {role}",
        "",
        f"Goal: {goal.strip()}",
        "Policy: read-only, no approvals, no routing bypass, no direct execution.",
        f"Constitution: {constitution}",
        "Allowed surfaces: " + ", ".join(allowed_surfaces),
    ]
    if repo_path.strip():
        lines.append(f"Repo Path: {Path(repo_path).expanduser().resolve()}")
    if context.strip():
        lines.extend(["", "Context:", context.strip()])
    return "\n".join(lines).strip() + "\n"


def _write_context(subdir: Path, payload: dict[str, Any]) -> None:
    (subdir / "context.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_context(subdir: Path) -> dict[str, Any]:
    path = subdir / "context.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _append_subagent_thread(subdir: Path, *, role: str, content: str) -> None:
    entry = {"timestamp": utcnow(), "role": role, "content": content}
    path = subdir / "thread.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def _read_thread(subdir: Path) -> list[dict[str, Any]]:
    path = subdir / "thread.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _preview(text: str, limit: int = 240) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
