"""Read-only repo awareness helpers for the operator.

These helpers shape filesystem, git, ledger, and workspace facts into
concise-first responses that are easy for the operator to consume and cite.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..ledger import Ledger
from ..workspaces import read_workspace_manifest

RENDER_MODES = ("brief", "full", "evidence")
_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
}


def summarize_repo_map(
    repo_path: str,
    *,
    max_depth: int = 2,
    mode: str = "brief",
) -> dict[str, Any]:
    target = _resolve_target(repo_path)
    tree = _collect_repo_map(target, max_depth=max_depth)
    top_dirs = tree["top_level_dirs"][:6]
    top_files = tree["top_level_files"][:6]
    summary = (
        f"{tree['name']} has {tree['top_level_count']} top-level entries, "
        f"{tree['directory_count']} directories, and {tree['file_count']} files "
        f"within depth {tree['max_depth']}."
    )
    evidence = []
    if top_dirs:
        evidence.append("Top directories: " + ", ".join(top_dirs))
    if top_files:
        evidence.append("Top files: " + ", ".join(top_files))
    if tree["tree_sample"]:
        evidence.append("Tree sample: " + ", ".join(tree["tree_sample"][:10]))
    return _render(
        mode=mode,
        summary=summary,
        evidence=evidence,
        provenance=[
            f"repo={tree['repo_path']}",
            f"git_root={tree['git_root'] or ''}",
            f"max_depth={tree['max_depth']}",
        ],
        details=tree,
    )


def summarize_repo_state(
    repo_path: str,
    *,
    mode: str = "brief",
) -> dict[str, Any]:
    target = _resolve_target(repo_path)
    state = _collect_repo_state(target)
    if not state["is_git_repo"]:
        return _render(
            mode=mode,
            summary=f"{state['repo_path']} is not a git checkout.",
            evidence=[],
            provenance=[f"repo={state['repo_path']}"],
            details=state,
        )

    dirty_text = "dirty" if state["dirty"] else "clean"
    summary = (
        f"{state['name']} is on {state['branch']} at {state['head_short']} "
        f"with a {dirty_text} worktree; {state['change_count']} pending paths."
    )
    evidence = [
        f"Branch: {state['branch']}",
        f"HEAD: {state['head']}",
    ]
    if state["recent_commits"]:
        evidence.append("Recent commits: " + "; ".join(state["recent_commits"][:3]))
    if state["status_lines"]:
        evidence.append("Status sample: " + "; ".join(state["status_lines"][:5]))
    return _render(
        mode=mode,
        summary=summary,
        evidence=evidence,
        provenance=[f"repo={state['repo_path']}", f"git_root={state['git_root']}"],
        details=state,
    )


def summarize_changed_files(
    repo_path: str,
    *,
    mode: str = "brief",
) -> dict[str, Any]:
    target = _resolve_target(repo_path)
    changes = _collect_changed_files(target)
    if not changes["is_git_repo"]:
        return _render(
            mode=mode,
            summary=f"{changes['repo_path']} is not a git checkout.",
            evidence=[],
            provenance=[f"repo={changes['repo_path']}"],
            details=changes,
        )

    if not changes["files"]:
        summary = f"{changes['name']} has no staged, unstaged, or untracked changes."
    else:
        summary = (
            f"{changes['name']} has {changes['count']} changed paths: "
            f"{changes['category_counts']}."
        )
    evidence = []
    if changes["files"]:
        evidence.append(
            "Changed files: " + ", ".join(item["path"] for item in changes["files"][:8])
        )
    return _render(
        mode=mode,
        summary=summary,
        evidence=evidence,
        provenance=[f"repo={changes['repo_path']}", f"git_root={changes['git_root']}"],
        details=changes,
    )


def find_repo_relevance(
    repo_path: str,
    query: str,
    *,
    limit: int = 8,
    mode: str = "brief",
) -> dict[str, Any]:
    target = _resolve_target(repo_path)
    relevance = _collect_relevance(target, query=query, limit=limit)
    if relevance["matches"]:
        top_names = ", ".join(match["path"] for match in relevance["matches"][:3])
        summary = f"Most relevant files for {query!r}: {top_names}."
    else:
        summary = f"No strong file matches found for {query!r} in {relevance['name']}."
    evidence = []
    for match in relevance["matches"][:5]:
        line = f"{match['path']} score={match['score']}"
        if match["snippets"]:
            line += f" snippet={match['snippets'][0]}"
        evidence.append(line)
    return _render(
        mode=mode,
        summary=summary,
        evidence=evidence,
        provenance=[
            f"repo={relevance['repo_path']}",
            f"git_root={relevance['git_root'] or ''}",
            f"query={query}",
        ],
        details=relevance,
    )


def summarize_repo_activity(
    settings_repo_root: Path,
    ledger: Ledger,
    repo_path: str,
    *,
    limit: int = 5,
    mode: str = "brief",
) -> dict[str, Any]:
    target = _resolve_target(repo_path)
    tasks = ledger.recent_tasks_for_repo(str(target), limit=limit)
    runs = ledger.recent_runs_for_repo(str(target), limit=limit)

    workspace_rows: list[dict[str, Any]] = []
    for task in tasks[: min(limit, 5)]:
        manifest = read_workspace_manifest(
            settings_repo_root,
            task["id"],
            workspace_dir=task.get("workspace_dir", ""),
        )
        latest_run = manifest["runs"][-1] if manifest.get("runs") else None
        workspace_rows.append(
            {
                "task_id": task["id"],
                "kind": task["kind"],
                "state": task["state"],
                "workspace_path": manifest.get("workspace_path", ""),
                "has_input": manifest.get("has_input", False),
                "run_count": len(manifest.get("runs", [])),
                "latest_receipt_path": (
                    latest_run.get("receipt_path", "") if latest_run else ""
                ),
            }
        )

    if not tasks and not runs:
        summary = f"No hermes-node run history is recorded yet for {target}."
    else:
        latest_run = runs[0] if runs else None
        latest_text = (
            f"latest run {latest_run['id']} [{latest_run['state']}]"
            if latest_run
            else "no runs yet"
        )
        summary = (
            f"{target.name} has {len(tasks)} recent tasks and {len(runs)} recent runs in the ledger; "
            f"{latest_text}."
        )

    evidence = []
    for row in workspace_rows[:5]:
        evidence.append(
            f"{row['task_id']} [{row['state']}] {row['kind']} workspace={row['workspace_path']}"
        )
    return _render(
        mode=mode,
        summary=summary,
        evidence=evidence,
        provenance=[f"repo={target}", f"ledger={ledger.db_path}"],
        details={
            "repo_path": str(target),
            "tasks": tasks,
            "runs": runs,
            "workspaces": workspace_rows,
        },
    )


def compare_repo_to_last_successful_run(
    settings_repo_root: Path,
    ledger: Ledger,
    repo_path: str,
    *,
    kind: str = "",
    mode: str = "brief",
) -> dict[str, Any]:
    target = _resolve_target(repo_path)
    baseline = ledger.latest_successful_run_for_repo(str(target), kind=kind)
    current_state = _collect_repo_state(target)
    current_changes = _collect_changed_files(target)

    if baseline is None:
        summary = f"No successful hermes-node run is recorded yet for {target.name}; current repo state only."
        evidence = []
        if current_changes["files"]:
            evidence.append(
                "Current changes: " + ", ".join(item["path"] for item in current_changes["files"][:8])
            )
        return _render(
            mode=mode,
            summary=summary,
            evidence=evidence,
            provenance=[f"repo={target}", f"ledger={ledger.db_path}"],
            details={
                "baseline": None,
                "current_state": current_state,
                "current_changes": current_changes,
            },
        )

    baseline_task = baseline["task"]
    baseline_run = baseline["run"]
    workspace = read_workspace_manifest(
        settings_repo_root,
        baseline_task["id"],
        workspace_dir=baseline_task.get("workspace_dir", ""),
    )
    since = baseline_run.get("finished_at") or baseline_run.get("started_at") or ""
    commits_since = _git_lines(
        current_state["git_root_path"],
        ["log", "--oneline", f"--since={since}"],
        timeout=10,
    ) if since and current_state["is_git_repo"] else []

    summary = (
        f"Since successful run {baseline_run['id']} on {since or 'unknown time'}, "
        f"{target.name} has {len(commits_since)} newer commits and {current_changes['count']} working-tree changes."
    )
    evidence = [
        f"Baseline task: {baseline_task['id']} [{baseline_task['kind']}]",
        f"Baseline run: {baseline_run['id']} [{baseline_run['state']}]",
    ]
    if baseline.get("summary", {}).get("content"):
        evidence.append("Last summary: " + _truncate(baseline["summary"]["content"], 160))
    if commits_since:
        evidence.append("Commits since baseline: " + "; ".join(commits_since[:5]))
    if current_changes["files"]:
        evidence.append(
            "Working-tree changes: " + ", ".join(item["path"] for item in current_changes["files"][:8])
        )

    return _render(
        mode=mode,
        summary=summary,
        evidence=evidence,
        provenance=[
            f"repo={target}",
            f"baseline_task={baseline_task['id']}",
            f"baseline_run={baseline_run['id']}",
            f"workspace={workspace.get('workspace_path', '')}",
        ],
        details={
            "baseline": baseline,
            "workspace": workspace,
            "commits_since": commits_since,
            "current_state": current_state,
            "current_changes": current_changes,
        },
    )


def diagnose_failed_task(
    settings_repo_root: Path,
    ledger: Ledger,
    task_id: str,
    *,
    mode: str = "brief",
) -> dict[str, Any]:
    task = ledger.get_task(task_id)
    run = ledger.latest_run_for_task(task_id)
    summary_row = ledger.latest_summary(task_id=task_id)
    events = ledger.events_for_task(task_id, limit=10)
    workspace = read_workspace_manifest(
        settings_repo_root,
        task_id,
        workspace_dir=task.get("workspace_dir", ""),
    )

    if run is None:
        summary = f"Task {task_id} has no recorded run yet."
        evidence: list[str] = []
        receipt_path = ""
    else:
        receipt_path = ""
        if workspace.get("runs"):
            for run_info in reversed(workspace["runs"]):
                if run_info.get("run_id") == run["id"]:
                    receipt_path = run_info.get("receipt_path", "")
                    break
        stderr_tail = _tail_text(run.get("stderr", ""), lines=8)
        signal = stderr_tail[0] if stderr_tail else ""
        summary = (
            f"Task {task_id} is {task['state']} after run {run['id']} "
            f"(executor={run.get('executor','')}, exit={run.get('exit_code')})."
        )
        if signal:
            summary += f" Likely signal: {signal}"
        evidence = []
        if stderr_tail:
            evidence.append("stderr tail: " + " | ".join(stderr_tail[:3]))
        if summary_row and summary_row.get("content"):
            evidence.append("stored summary: " + _truncate(summary_row["content"], 180))
        if events:
            evidence.append(
                "recent events: "
                + "; ".join(
                    f"{event.get('event_type')}={_truncate(event.get('message', ''), 60)}"
                    for event in events[:5]
                )
            )

    return _render(
        mode=mode,
        summary=summary,
        evidence=evidence,
        provenance=[
            f"task={task_id}",
            f"run={run['id'] if run else ''}",
            f"workspace={workspace.get('workspace_path', '')}",
            f"receipt={receipt_path}",
        ],
        details={
            "task": task,
            "run": run,
            "summary_row": summary_row,
            "events": events,
            "workspace": workspace,
        },
    )


def prepare_patch_proposal_plan(
    settings_repo_root: Path,
    ledger: Ledger,
    repo_path: str,
    goal: str,
    *,
    limit: int = 8,
    mode: str = "brief",
) -> dict[str, Any]:
    target = _resolve_target(repo_path)
    state = _collect_repo_state(target)
    changes = _collect_changed_files(target)
    relevance = _collect_relevance(target, query=goal, limit=limit)
    activity = summarize_repo_activity(
        settings_repo_root,
        ledger,
        str(target),
        limit=min(limit, 5),
        mode="full",
    )

    relevant_files = [match["path"] for match in relevance["matches"][:limit]]
    steps = [
        "Confirm the bounded change outcome and acceptance criteria.",
        "Inspect the most relevant files and existing run/workspace receipts first.",
        "Define a narrow file scope before submitting any repo_patch_branch task.",
        "Require explicit human approval before execution.",
    ]
    if state.get("branch") in {"main", "master"}:
        steps.insert(
            3,
            "Ensure the eventual mutation happens on a feature branch, never directly on main/master.",
        )
    if changes["files"]:
        steps.insert(
            2,
            "Account for the existing dirty worktree so new work does not trample in-flight changes.",
        )

    submission_prompt_lines = [
        goal.strip(),
        "",
        "Constraints:",
        "- keep the patch bounded to the scoped files",
        "- do not mutate main/master directly",
        "- preserve existing unrelated worktree changes",
    ]
    if relevant_files:
        submission_prompt_lines.extend(
            [
                "",
                "Likely files in scope:",
                *[f"- {path}" for path in relevant_files[:8]],
            ]
        )

    summary = (
        f"Prepared a bounded patch proposal for {target.name}; "
        f"{len(relevant_files)} relevant files identified and approval remains required."
    )
    evidence = []
    if relevant_files:
        evidence.append("Relevant files: " + ", ".join(relevant_files[:8]))
    if changes["files"]:
        evidence.append("Existing worktree changes: " + ", ".join(item["path"] for item in changes["files"][:6]))
    if activity.get("summary"):
        evidence.append("Recent repo activity: " + activity["summary"])

    return _render(
        mode=mode,
        summary=summary,
        evidence=evidence,
        provenance=[f"repo={target}", f"ledger={ledger.db_path}"],
        details={
            "repo_path": str(target),
            "goal": goal,
            "approval_required": True,
            "state": state,
            "current_changes": changes,
            "relevance": relevance,
            "recent_activity": activity.get("details", activity),
            "steps": steps,
            "suggested_submission": {
                "kind": "repo_patch_branch",
                "repo_path": str(target),
                "title": _truncate(goal.strip(), 72),
                "prompt": "\n".join(submission_prompt_lines).strip(),
            },
        },
    )


def _collect_repo_map(target: Path, *, max_depth: int) -> dict[str, Any]:
    if not target.exists():
        raise ValueError(f"Path does not exist: {target}")
    root = _git_root(target) or target
    max_depth = max(1, min(max_depth, 4))
    top_level_dirs: list[str] = []
    top_level_files: list[str] = []
    tree_sample: list[str] = []
    directory_count = 0
    file_count = 0

    for entry in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if entry.name in _IGNORED_DIRS:
            continue
        if entry.is_dir():
            top_level_dirs.append(entry.name)
        else:
            top_level_files.append(entry.name)

    root_depth = len(root.parts)
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in _IGNORED_DIRS)
        filenames = sorted(filenames)
        current = Path(current_root)
        rel_root = current.relative_to(root)
        depth = len(current.parts) - root_depth
        directory_count += len(dirnames)
        file_count += len(filenames)
        if depth >= max_depth:
            dirnames[:] = []
        if str(rel_root) != "." and len(tree_sample) < 80:
            tree_sample.append(str(rel_root) + "/")
        for name in filenames:
            if len(tree_sample) >= 80:
                break
            rel_path = current.joinpath(name).relative_to(root)
            if len(rel_path.parts) <= max_depth:
                tree_sample.append(str(rel_path))

    return {
        "name": root.name,
        "repo_path": str(target),
        "git_root": str(root) if root != target or _is_git_repo(root) else "",
        "max_depth": max_depth,
        "top_level_count": len(top_level_dirs) + len(top_level_files),
        "top_level_dirs": top_level_dirs,
        "top_level_files": top_level_files,
        "directory_count": directory_count,
        "file_count": file_count,
        "tree_sample": tree_sample[:40],
    }


def _collect_repo_state(target: Path) -> dict[str, Any]:
    root = _git_root(target)
    if root is None:
        return {
            "name": target.name,
            "repo_path": str(target),
            "git_root": "",
            "git_root_path": target,
            "is_git_repo": False,
            "branch": "",
            "head": "",
            "head_short": "",
            "dirty": False,
            "change_count": 0,
            "status_lines": [],
            "recent_commits": [],
        }

    branch = _git_line(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    head = _git_line(root, ["rev-parse", "HEAD"])
    status_lines = _git_lines(root, ["status", "--porcelain", "--branch"], timeout=10)
    recent_commits = _git_lines(root, ["log", "--oneline", "-5"], timeout=10)
    body = status_lines[1:] if status_lines[:1] and status_lines[0].startswith("##") else status_lines
    return {
        "name": root.name,
        "repo_path": str(target),
        "git_root": str(root),
        "git_root_path": root,
        "is_git_repo": True,
        "branch": branch or "?",
        "head": head,
        "head_short": head[:10] if head else "?",
        "dirty": bool(body),
        "change_count": len(body),
        "status_lines": body,
        "recent_commits": recent_commits,
    }


def _collect_changed_files(target: Path) -> dict[str, Any]:
    state = _collect_repo_state(target)
    if not state["is_git_repo"]:
        return {
            **state,
            "count": 0,
            "files": [],
            "category_counts": "none",
        }

    status_lines = _git_lines(state["git_root_path"], ["status", "--porcelain"], timeout=10)
    files: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for line in status_lines:
        parsed = _parse_status_line(line)
        if not parsed:
            continue
        files.append(parsed)
        counts[parsed["change_type"]] = counts.get(parsed["change_type"], 0) + 1

    category_counts = ", ".join(
        f"{name}={counts[name]}" for name in sorted(counts)
    ) if counts else "none"
    return {
        **state,
        "count": len(files),
        "files": files,
        "category_counts": category_counts,
    }


def _collect_relevance(target: Path, *, query: str, limit: int) -> dict[str, Any]:
    root = _git_root(target) or target
    files = _repo_files(root)
    tokens = _query_tokens(query)
    by_path: dict[str, dict[str, Any]] = {}

    for path in files:
        lowered = path.lower()
        score = sum(2 for token in tokens if token in lowered)
        if score:
            by_path[path] = {
                "path": path,
                "score": score,
                "path_matches": [token for token in tokens if token in lowered],
                "snippets": [],
            }

    pattern = "|".join(re.escape(token) for token in tokens[:8])
    if tokens:
        rg_args = ["rg", "-n", "-i", "-S", "--glob", "!.git", pattern, str(root)]
        if shutil.which("rg"):
            try:
                result = subprocess.run(
                    rg_args,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=15,
                )
                for line in result.stdout.splitlines()[: max(limit * 8, 24)]:
                    parsed = _parse_rg_line(root, line)
                    if not parsed:
                        continue
                    row = by_path.setdefault(
                        parsed["path"],
                        {
                            "path": parsed["path"],
                            "score": 0,
                            "path_matches": [],
                            "snippets": [],
                        },
                    )
                    row["score"] += 3
                    if len(row["snippets"]) < 3:
                        row["snippets"].append(parsed["snippet"])
            except (OSError, subprocess.TimeoutExpired):
                pass

    ranked = sorted(
        by_path.values(),
        key=lambda item: (-item["score"], item["path"]),
    )[: max(limit, 1)]
    return {
        "name": root.name,
        "repo_path": str(target),
        "git_root": str(root) if root != target or _is_git_repo(root) else "",
        "query": query,
        "tokens": tokens,
        "matches": ranked,
    }


def _repo_files(root: Path) -> list[str]:
    if shutil.which("rg"):
        try:
            result = subprocess.run(
                ["rg", "--files", str(root)],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            rows = [
                str(Path(line).resolve().relative_to(root))
                for line in result.stdout.splitlines()
                if line.strip()
            ]
            if rows:
                return rows[:2000]
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass

    rows: list[str] = []
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _IGNORED_DIRS]
        current = Path(current_root)
        for name in filenames:
            rows.append(str(current.joinpath(name).relative_to(root)))
            if len(rows) >= 2000:
                return rows
    return rows


def _query_tokens(query: str) -> list[str]:
    tokens = []
    for raw in query.lower().replace("/", " ").replace("-", " ").split():
        cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in {"_", "."})
        if len(cleaned) >= 2 and cleaned not in tokens:
            tokens.append(cleaned)
    if not tokens and query.strip():
        tokens.append(query.strip().lower())
    return tokens[:12]


def _parse_rg_line(root: Path, line: str) -> dict[str, str] | None:
    pieces = line.split(":", 2)
    if len(pieces) != 3:
        return None
    file_path, line_no, snippet = pieces
    try:
        rel = str(Path(file_path).resolve().relative_to(root))
    except ValueError:
        rel = file_path
    return {"path": rel, "snippet": f"{line_no}: {snippet.strip()}"}


def _parse_status_line(line: str) -> dict[str, Any] | None:
    if not line.strip():
        return None
    if line.startswith("?? "):
        path = line[3:].strip()
        return {
            "code": "??",
            "path": path,
            "change_type": "untracked",
            "staged": False,
            "unstaged": True,
        }

    code = line[:2]
    path = line[3:].strip()
    original_path = ""
    if " -> " in path:
        original_path, path = path.split(" -> ", 1)

    change_type = "modified"
    normalized = code.replace(" ", "")
    if "R" in normalized:
        change_type = "renamed"
    elif "A" in normalized:
        change_type = "added"
    elif "D" in normalized:
        change_type = "deleted"
    elif "M" in normalized:
        change_type = "modified"

    payload: dict[str, Any] = {
        "code": code,
        "path": path,
        "change_type": change_type,
        "staged": code[0] not in {" ", "?"},
        "unstaged": code[1] not in {" ", "?"},
    }
    if original_path:
        payload["original_path"] = original_path
    return payload


def _render(
    *,
    mode: str,
    summary: str,
    evidence: list[str],
    provenance: list[str],
    details: dict[str, Any],
) -> dict[str, Any]:
    render_mode = mode if mode in RENDER_MODES else "brief"
    payload: dict[str, Any] = {
        "render_mode": render_mode,
        "summary": summary.strip(),
        "evidence": evidence[:8],
        "provenance": provenance,
    }
    if render_mode in {"full", "evidence"}:
        payload["details"] = details
    return payload


def _resolve_target(repo_path: str) -> Path:
    raw = repo_path.strip()
    if not raw:
        raise ValueError("repo_path is required")
    return Path(raw).expanduser().resolve()


def _git_root(target: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(target if target.is_dir() else target.parent),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    stdout = result.stdout.strip()
    return Path(stdout).resolve() if stdout else None


def _is_git_repo(target: Path) -> bool:
    return _git_root(target) is not None


def _git_line(root: Path, args: list[str]) -> str:
    lines = _git_lines(root, args, timeout=5)
    return lines[0] if lines else ""


def _git_lines(root: Path, args: list[str], *, timeout: int) -> list[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def _tail_text(text: str, *, lines: int) -> list[str]:
    return [line.strip() for line in text.splitlines()[-lines:] if line.strip()]


def _truncate(text: str, limit: int) -> str:
    value = text.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."
