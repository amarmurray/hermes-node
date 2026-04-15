"""Patch proposal artifacts — reviewable intermediates before repo_patch_branch.

A patch proposal is a file-backed artifact under a session's operator dir:

    state/operator_sessions/{sid}/patches/{patch_id}/
        proposal.md     # human-readable intent + evidence
        proposal.json   # machine-readable metadata

The operator writes a proposal *before* calling `submit_task`, and the
submission tool is wired to refuse mutation against a repo unless:

    1. the repo is registered
    2. the repo is present on disk and a valid git checkout
    3. the branch policy allows mutation via a bot-prefixed branch
    4. the proposal artifact exists and points at the right repo

This keeps mutation flow grounded in explicit, inspectable artifacts
rather than a single LLM tool call. Post-run synthesis then reads
receipts — never free-form executor summary text — to confirm what
actually landed.

No new DB tables. Everything is plain files under the session dir.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..jsonl import utcnow
from ..models import new_id
from .session import session_dir


PATCHES_SUBDIR = "patches"
PROPOSAL_STATES = ("draft", "submitted", "executing", "succeeded", "failed", "cancelled")


@dataclass
class PatchProposal:
    """Structured patch proposal metadata."""

    patch_id: str
    session_id: str
    repo_name: str
    repo_path: str
    goal: str
    branch: str
    baseline_head: str = ""
    files_in_scope: tuple[str, ...] = ()
    risk_notes: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    state: str = "draft"
    task_id: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "session_id": self.session_id,
            "repo_name": self.repo_name,
            "repo_path": self.repo_path,
            "goal": self.goal,
            "branch": self.branch,
            "baseline_head": self.baseline_head,
            "files_in_scope": list(self.files_in_scope),
            "risk_notes": list(self.risk_notes),
            "provenance": list(self.provenance),
            "state": self.state,
            "task_id": self.task_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PatchProposal":
        return cls(
            patch_id=str(data["patch_id"]),
            session_id=str(data["session_id"]),
            repo_name=str(data["repo_name"]),
            repo_path=str(data["repo_path"]),
            goal=str(data["goal"]),
            branch=str(data.get("branch", "")),
            baseline_head=str(data.get("baseline_head", "")),
            files_in_scope=tuple(str(f) for f in data.get("files_in_scope", []) or []),
            risk_notes=tuple(str(n) for n in data.get("risk_notes", []) or []),
            provenance=tuple(str(p) for p in data.get("provenance", []) or []),
            state=str(data.get("state", "draft")),
            task_id=str(data.get("task_id", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


# ---------------------------------------------------------------------------
# File layout
# ---------------------------------------------------------------------------


def _session_patches_dir(repo_root: Path, session_id: str) -> Path:
    return session_dir(repo_root, session_id) / PATCHES_SUBDIR


def _patch_dir(repo_root: Path, session_id: str, patch_id: str) -> Path:
    return _session_patches_dir(repo_root, session_id) / patch_id


def ensure_patches_layout(repo_root: Path, session_id: str) -> Path:
    path = _session_patches_dir(repo_root, session_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Creation / update
# ---------------------------------------------------------------------------


def _slug(text: str, *, limit: int = 28) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug or "patch")[:limit]


def _auto_branch_name(registry_branch_prefix: str, goal: str) -> str:
    slug = _slug(goal)
    prefix = registry_branch_prefix.rstrip("/")
    return f"{prefix}/{slug}" if prefix else f"auto/{slug}"


def create_proposal(
    repo_root: Path,
    session_id: str,
    *,
    repo_name: str,
    repo_path: str,
    goal: str,
    branch_prefix: str,
    baseline_head: str = "",
    files_in_scope: list[str] | None = None,
    risk_notes: list[str] | None = None,
    provenance: list[str] | None = None,
    branch: str = "",
) -> PatchProposal:
    """Create a new patch proposal in draft state and persist it to disk."""
    ensure_patches_layout(repo_root, session_id)
    patch_id = new_id("patch")
    now = utcnow()
    proposal = PatchProposal(
        patch_id=patch_id,
        session_id=session_id,
        repo_name=repo_name,
        repo_path=repo_path,
        goal=goal.strip(),
        branch=branch.strip() or _auto_branch_name(branch_prefix, goal),
        baseline_head=baseline_head,
        files_in_scope=tuple(files_in_scope or ()),
        risk_notes=tuple(risk_notes or ()),
        provenance=tuple(provenance or ()),
        state="draft",
        created_at=now,
        updated_at=now,
    )
    _write(repo_root, proposal)
    return proposal


def _write(repo_root: Path, proposal: PatchProposal) -> None:
    pdir = _patch_dir(repo_root, proposal.session_id, proposal.patch_id)
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "proposal.json").write_text(
        json.dumps(proposal.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (pdir / "proposal.md").write_text(_render_markdown(proposal), encoding="utf-8")


def _render_markdown(proposal: PatchProposal) -> str:
    lines = [
        f"# Patch proposal {proposal.patch_id}",
        "",
        f"- state: {proposal.state}",
        f"- repo: {proposal.repo_name}",
        f"- repo_path: {proposal.repo_path}",
        f"- branch: {proposal.branch}",
        f"- baseline_head: {proposal.baseline_head or '(unknown)'}",
        f"- task_id: {proposal.task_id or '(not submitted)'}",
        f"- created_at: {proposal.created_at}",
        f"- updated_at: {proposal.updated_at}",
        "",
        "## Goal",
        "",
        proposal.goal or "(no goal provided)",
        "",
        "## Files in scope",
        "",
    ]
    if proposal.files_in_scope:
        for f in proposal.files_in_scope:
            lines.append(f"- {f}")
    else:
        lines.append("(no explicit files in scope — patch proposal is exploratory)")
    lines.extend(["", "## Risk notes", ""])
    if proposal.risk_notes:
        for n in proposal.risk_notes:
            lines.append(f"- {n}")
    else:
        lines.append("(no explicit risk notes)")
    lines.extend(["", "## Provenance", ""])
    if proposal.provenance:
        for p in proposal.provenance:
            lines.append(f"- {p}")
    else:
        lines.append("(no provenance cited)")
    return "\n".join(lines).rstrip() + "\n"


def load_proposal(
    repo_root: Path, session_id: str, patch_id: str
) -> PatchProposal:
    pdir = _patch_dir(repo_root, session_id, patch_id)
    path = pdir / "proposal.json"
    if not path.exists():
        raise FileNotFoundError(f"No patch proposal at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return PatchProposal.from_dict(data)


def list_proposals(repo_root: Path, session_id: str) -> list[PatchProposal]:
    pdir = _session_patches_dir(repo_root, session_id)
    if not pdir.exists():
        return []
    out: list[PatchProposal] = []
    for child in sorted(pdir.iterdir()):
        if not child.is_dir():
            continue
        meta = child / "proposal.json"
        if not meta.exists():
            continue
        try:
            out.append(PatchProposal.from_dict(json.loads(meta.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    out.sort(key=lambda p: p.created_at, reverse=True)
    return out


def update_proposal_state(
    repo_root: Path,
    session_id: str,
    patch_id: str,
    *,
    state: str,
    task_id: str = "",
    extra_notes: list[str] | None = None,
) -> PatchProposal:
    if state not in PROPOSAL_STATES:
        raise ValueError(f"Unknown proposal state: {state}")
    proposal = load_proposal(repo_root, session_id, patch_id)
    new_notes = tuple(proposal.risk_notes) + tuple(extra_notes or ())
    updated = PatchProposal(
        patch_id=proposal.patch_id,
        session_id=proposal.session_id,
        repo_name=proposal.repo_name,
        repo_path=proposal.repo_path,
        goal=proposal.goal,
        branch=proposal.branch,
        baseline_head=proposal.baseline_head,
        files_in_scope=proposal.files_in_scope,
        risk_notes=new_notes,
        provenance=proposal.provenance,
        state=state,
        task_id=task_id or proposal.task_id,
        created_at=proposal.created_at,
        updated_at=utcnow(),
    )
    _write(repo_root, updated)
    return updated
