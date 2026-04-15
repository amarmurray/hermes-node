"""File-backed repo registry for the operator.

The registry is the authoritative answer to "what repos can the operator
act on?". It is read-only from the operator's perspective: the file at
`config/repos.json` is human-edited and git-tracked. Absence of a path is
a first-class state — absent repos are still listed, but the operator
refuses to submit mutations against them. That is the fail-closed posture.

Future repos (e.g. TreasuryOS) live under `future_slots` until they have a
real path, then get promoted into `repos`. The shape of the file is
deliberately stable so new entries can be added without a migration.

All presence checks go through :func:`check_presence`, which also probes
the worktree state via the same helpers as `repo_awareness.py`. This gives
the operator a grounded, bounded view of "what is actually on disk right
now" without trusting free-form executor summaries.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_REGISTRY_PATH = Path("config") / "repos.json"


@dataclass(frozen=True)
class BranchPolicy:
    """Branch safety policy for a registered repo."""

    default_branch: str = "main"
    allow_direct_main_mutation: bool = False
    branch_prefix: str = ""
    require_clean_worktree: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "BranchPolicy":
        data = data or {}
        return cls(
            default_branch=str(data.get("default_branch", "main")),
            allow_direct_main_mutation=bool(data.get("allow_direct_main_mutation", False)),
            branch_prefix=str(data.get("branch_prefix", "")),
            require_clean_worktree=bool(data.get("require_clean_worktree", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "default_branch": self.default_branch,
            "allow_direct_main_mutation": self.allow_direct_main_mutation,
            "branch_prefix": self.branch_prefix,
            "require_clean_worktree": self.require_clean_worktree,
        }


@dataclass(frozen=True)
class RepoEntry:
    """A single registered repo."""

    name: str
    path: str
    description: str = ""
    aliases: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    status: str = "active"
    allowed_kinds: tuple[str, ...] = ()
    branch_policy: BranchPolicy = field(default_factory=BranchPolicy)
    specialist_roles: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RepoEntry":
        return cls(
            name=str(data["name"]),
            path=str(data["path"]),
            description=str(data.get("description", "")),
            aliases=tuple(str(a) for a in data.get("aliases", ()) or ()),
            tags=tuple(str(t) for t in data.get("tags", ()) or ()),
            status=str(data.get("status", "active")),
            allowed_kinds=tuple(str(k) for k in data.get("allowed_kinds", ()) or ()),
            branch_policy=BranchPolicy.from_dict(data.get("branch_policy")),
            specialist_roles=tuple(str(r) for r in data.get("specialist_roles", ()) or ()),
        )

    def resolved_path(self) -> Path:
        return Path(os.path.expandvars(os.path.expanduser(self.path))).resolve()

    def names_and_aliases(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)

    def allows_kind(self, kind: str) -> bool:
        return kind in self.allowed_kinds

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "resolved_path": str(self.resolved_path()),
            "description": self.description,
            "aliases": list(self.aliases),
            "tags": list(self.tags),
            "status": self.status,
            "allowed_kinds": list(self.allowed_kinds),
            "branch_policy": self.branch_policy.to_dict(),
            "specialist_roles": list(self.specialist_roles),
        }


@dataclass(frozen=True)
class FutureRepoSlot:
    """A planned-but-not-live repo slot."""

    name: str
    description: str = ""
    status: str = "planned"
    expected_tags: tuple[str, ...] = ()
    expected_specialist_roles: tuple[str, ...] = ()
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FutureRepoSlot":
        return cls(
            name=str(data["name"]),
            description=str(data.get("description", "")),
            status=str(data.get("status", "planned")),
            expected_tags=tuple(str(t) for t in data.get("expected_tags", ()) or ()),
            expected_specialist_roles=tuple(
                str(r) for r in data.get("expected_specialist_roles", ()) or ()
            ),
            notes=str(data.get("notes", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "expected_tags": list(self.expected_tags),
            "expected_specialist_roles": list(self.expected_specialist_roles),
            "notes": self.notes,
        }


@dataclass(frozen=True)
class RepoRegistry:
    """Loaded view of the repos registry file."""

    version: int
    repos: tuple[RepoEntry, ...]
    future_slots: tuple[FutureRepoSlot, ...]
    source_path: Path

    @classmethod
    def load(cls, repo_root: Path, *, path: Path | None = None) -> "RepoRegistry":
        source = (path or (repo_root / DEFAULT_REGISTRY_PATH)).resolve()
        if not source.exists():
            return cls(version=1, repos=(), future_slots=(), source_path=source)
        raw = json.loads(source.read_text(encoding="utf-8"))
        version = int(raw.get("version", 1))
        repos = tuple(RepoEntry.from_dict(r) for r in raw.get("repos", []) or [])
        slots = tuple(FutureRepoSlot.from_dict(s) for s in raw.get("future_slots", []) or [])
        _assert_unique(repos)
        return cls(version=version, repos=repos, future_slots=slots, source_path=source)

    def find(self, name_or_alias: str) -> RepoEntry | None:
        """Look up a registered repo by name or alias (case-insensitive)."""
        if not name_or_alias:
            return None
        needle = name_or_alias.strip().lower()
        for entry in self.repos:
            if entry.name.lower() == needle:
                return entry
            if any(a.lower() == needle for a in entry.aliases):
                return entry
        return None

    def require(self, name_or_alias: str) -> RepoEntry:
        entry = self.find(name_or_alias)
        if entry is None:
            raise KeyError(
                f"repo '{name_or_alias}' is not in the registry. "
                f"Known repos: {sorted(e.name for e in self.repos)}"
            )
        return entry

    def find_by_path(self, path: str | Path) -> RepoEntry | None:
        """Reverse-lookup a repo entry from a filesystem path.

        Walks upwards from the given path so that sub-paths of a registered
        repo (e.g. ``<repo>/lib/foo``) still resolve to the right entry.
        """
        target = Path(path).expanduser().resolve()
        for candidate in (target, *target.parents):
            for entry in self.repos:
                if entry.resolved_path() == candidate:
                    return entry
        return None


def _assert_unique(repos: tuple[RepoEntry, ...]) -> None:
    seen: set[str] = set()
    for entry in repos:
        for name in entry.names_and_aliases():
            lower = name.lower()
            if lower in seen:
                raise ValueError(f"duplicate repo name/alias in registry: {name}")
            seen.add(lower)


# ---------------------------------------------------------------------------
# Presence / readiness checks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepoPresence:
    """Result of a presence/readiness probe against a registered repo."""

    name: str
    resolved_path: str
    exists: bool
    is_dir: bool
    is_git: bool
    branch: str = ""
    head: str = ""
    dirty: bool = False
    change_count: int = 0
    active_worktrees: tuple[str, ...] = ()
    missing_reason: str = ""

    @property
    def ready_for_mutation(self) -> bool:
        return self.exists and self.is_dir and self.is_git and not self.missing_reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "resolved_path": self.resolved_path,
            "exists": self.exists,
            "is_dir": self.is_dir,
            "is_git": self.is_git,
            "branch": self.branch,
            "head": self.head,
            "dirty": self.dirty,
            "change_count": self.change_count,
            "active_worktrees": list(self.active_worktrees),
            "ready_for_mutation": self.ready_for_mutation,
            "missing_reason": self.missing_reason,
        }


def check_presence(entry: RepoEntry) -> RepoPresence:
    """Probe the filesystem + git state of a registered repo."""
    resolved = entry.resolved_path()
    if not resolved.exists():
        return RepoPresence(
            name=entry.name,
            resolved_path=str(resolved),
            exists=False,
            is_dir=False,
            is_git=False,
            missing_reason="path does not exist",
        )
    if not resolved.is_dir():
        return RepoPresence(
            name=entry.name,
            resolved_path=str(resolved),
            exists=True,
            is_dir=False,
            is_git=False,
            missing_reason="path is not a directory",
        )

    is_git = (resolved / ".git").exists()
    if not is_git:
        return RepoPresence(
            name=entry.name,
            resolved_path=str(resolved),
            exists=True,
            is_dir=True,
            is_git=False,
            missing_reason="not a git repository",
        )

    branch = _git_capture(resolved, "rev-parse", "--abbrev-ref", "HEAD")
    head = _git_capture(resolved, "rev-parse", "--short", "HEAD")
    status_out = _git_capture(resolved, "status", "--porcelain")
    change_lines = [l for l in status_out.splitlines() if l.strip()]
    worktrees = _active_worktree_paths(resolved)

    return RepoPresence(
        name=entry.name,
        resolved_path=str(resolved),
        exists=True,
        is_dir=True,
        is_git=True,
        branch=branch,
        head=head,
        dirty=len(change_lines) > 0,
        change_count=len(change_lines),
        active_worktrees=tuple(worktrees),
    )


def list_presence(registry: RepoRegistry) -> list[RepoPresence]:
    """Probe every registered repo; absent ones are included with missing_reason."""
    return [check_presence(entry) for entry in registry.repos]


# ---------------------------------------------------------------------------
# Worktree lifecycle helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorktreeRecord:
    """A single active git worktree attached to a registered repo."""

    repo_name: str
    primary_path: str
    worktree_path: str
    branch: str = ""
    head: str = ""
    detached: bool = False
    prunable: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_name": self.repo_name,
            "primary_path": self.primary_path,
            "worktree_path": self.worktree_path,
            "branch": self.branch,
            "head": self.head,
            "detached": self.detached,
            "prunable": self.prunable,
            "reason": self.reason,
        }


def list_worktrees_for_entry(entry: RepoEntry) -> list[WorktreeRecord]:
    """Parse `git worktree list --porcelain` for a registered repo."""
    resolved = entry.resolved_path()
    if not resolved.exists() or not (resolved / ".git").exists():
        return []
    raw = _git_capture(resolved, "worktree", "list", "--porcelain")
    records: list[WorktreeRecord] = []
    cur: dict[str, Any] = {}

    def flush() -> None:
        if not cur.get("worktree"):
            return
        records.append(
            WorktreeRecord(
                repo_name=entry.name,
                primary_path=str(resolved),
                worktree_path=str(cur.get("worktree", "")),
                branch=str(cur.get("branch", "")),
                head=str(cur.get("HEAD", ""))[:12],
                detached=bool(cur.get("detached", False)),
                prunable=bool(cur.get("prunable", False)),
                reason=str(cur.get("reason", "")),
            )
        )

    for line in raw.splitlines():
        if not line.strip():
            flush()
            cur = {}
            continue
        parts = line.split(maxsplit=1)
        key = parts[0]
        value = parts[1] if len(parts) > 1 else ""
        if key == "worktree":
            cur["worktree"] = value
        elif key == "HEAD":
            cur["HEAD"] = value
        elif key == "branch":
            cur["branch"] = value.removeprefix("refs/heads/")
        elif key == "detached":
            cur["detached"] = True
        elif key == "prunable":
            cur["prunable"] = True
            cur["reason"] = value
    flush()
    return records


def list_all_worktrees(registry: RepoRegistry) -> list[WorktreeRecord]:
    records: list[WorktreeRecord] = []
    for entry in registry.repos:
        records.extend(list_worktrees_for_entry(entry))
    return records


# ---------------------------------------------------------------------------
# Bounded rendering for operator tool payloads
# ---------------------------------------------------------------------------


def render_entry(entry: RepoEntry, presence: RepoPresence) -> dict[str, Any]:
    return {
        **entry.to_dict(),
        "presence": presence.to_dict(),
    }


def render_registry(registry: RepoRegistry) -> dict[str, Any]:
    presences = list_presence(registry)
    return {
        "version": registry.version,
        "source_path": str(registry.source_path),
        "repos": [
            render_entry(entry, presence)
            for entry, presence in zip(registry.repos, presences)
        ],
        "future_slots": [s.to_dict() for s in registry.future_slots],
        "counts": {
            "total": len(registry.repos),
            "present": sum(1 for p in presences if p.exists and p.is_git),
            "absent": sum(1 for p in presences if not p.exists),
            "future": len(registry.future_slots),
        },
    }


# ---------------------------------------------------------------------------
# git helpers
# ---------------------------------------------------------------------------


def _git_capture(cwd: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ("git", *args),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (result.stdout or "").strip()


def _active_worktree_paths(cwd: Path) -> list[str]:
    raw = _git_capture(cwd, "worktree", "list", "--porcelain")
    paths: list[str] = []
    for line in raw.splitlines():
        if line.startswith("worktree "):
            paths.append(line.split(maxsplit=1)[1])
    return paths
