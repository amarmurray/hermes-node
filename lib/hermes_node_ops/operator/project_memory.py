"""Project memory loader and git-root-aware project detection.

Pure Python, no network, defensive: any IO error skips the layer
and never raises. Designed to be cheap to call per turn.

Layers are discovered from:
- AGENTS.md, .operator/constitution.md, .operator/memory.md at repo root
- AGENTS.md in any ancestor dir between cwd and repo root (deepest first)

Each file is capped at 8000 chars with a trailing "…(truncated)" marker.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

MAX_LAYER_CHARS = 8000
TRUNCATION_MARKER = "\n\n…(truncated)"


@dataclass(frozen=True)
class MemoryLayer:
    source: str  # absolute path
    scope: str   # "repo_root" | "ancestor" | "operator_dir"
    content: str


@dataclass(frozen=True)
class ProjectContext:
    cwd: str
    repo_root: str = ""
    branch: str = ""
    worktree: str = ""
    repo_name: str = ""
    memory_layers: tuple[MemoryLayer, ...] = field(default_factory=tuple)

    @property
    def in_git_repo(self) -> bool:
        return bool(self.repo_root)


def _git(cwd: str, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _read_capped(path: Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(raw) > MAX_LAYER_CHARS:
        return raw[:MAX_LAYER_CHARS] + TRUNCATION_MARKER
    return raw


def load_project_memory(repo_root: str | Path) -> tuple[MemoryLayer, ...]:
    """Load memory layers anchored at a given repo root.

    Does not walk ancestors — call `detect_project` for the full picture.
    """
    root = Path(repo_root)
    if not root.exists() or not root.is_dir():
        return ()

    layers: list[MemoryLayer] = []
    root_agents = root / "AGENTS.md"
    if root_agents.is_file():
        content = _read_capped(root_agents)
        if content:
            layers.append(
                MemoryLayer(source=str(root_agents), scope="repo_root", content=content)
            )

    for rel in (".operator/constitution.md", ".operator/memory.md"):
        p = root / rel
        if p.is_file():
            content = _read_capped(p)
            if content:
                layers.append(
                    MemoryLayer(source=str(p), scope="operator_dir", content=content)
                )
    return tuple(layers)


def _ancestor_agents(cwd: Path, repo_root: Path) -> list[MemoryLayer]:
    """Collect AGENTS.md in ancestor dirs between cwd (inclusive) and repo root (exclusive)."""
    layers: list[MemoryLayer] = []
    try:
        cwd_resolved = cwd.resolve()
        root_resolved = repo_root.resolve()
    except OSError:
        return layers
    if cwd_resolved == root_resolved:
        return layers
    try:
        rel = cwd_resolved.relative_to(root_resolved)
    except ValueError:
        return layers
    # Walk from cwd up to (but not including) repo root.
    current = cwd_resolved
    seen_dirs: list[Path] = []
    while current != root_resolved:
        seen_dirs.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    # Deepest first.
    for d in seen_dirs:
        agents = d / "AGENTS.md"
        if agents.is_file():
            content = _read_capped(agents)
            if content:
                layers.append(
                    MemoryLayer(source=str(agents), scope="ancestor", content=content)
                )
    _ = rel  # quiet unused
    return layers


def detect_project(cwd: str | Path | None = None) -> ProjectContext:
    """Detect the enclosing git project and load layered project memory.

    Returns a ProjectContext with empty fields when not in a git repo,
    never raises.
    """
    if cwd is None:
        try:
            cwd_path = Path.cwd()
        except OSError:
            cwd_path = Path("/")
    else:
        cwd_path = Path(cwd)

    cwd_str = str(cwd_path)
    repo_root = _git(cwd_str, "rev-parse", "--show-toplevel")
    if not repo_root:
        return ProjectContext(cwd=cwd_str)

    branch = _git(cwd_str, "rev-parse", "--abbrev-ref", "HEAD")
    worktree = _git(cwd_str, "rev-parse", "--show-superproject-working-tree")
    try:
        repo_name = Path(repo_root).name
    except Exception:  # noqa: BLE001
        repo_name = ""

    root_path = Path(repo_root)
    ancestor_layers = _ancestor_agents(cwd_path, root_path)
    root_layers = load_project_memory(root_path)
    # Deepest ancestors first, then root-level base layers.
    memory_layers = tuple(ancestor_layers) + tuple(root_layers)

    return ProjectContext(
        cwd=cwd_str,
        repo_root=repo_root,
        branch=branch,
        worktree=worktree,
        repo_name=repo_name,
        memory_layers=memory_layers,
    )


def render_memory_layers(layers: tuple[MemoryLayer, ...]) -> str:
    """Render memory layers as a markdown block.

    Empty tuple → empty string. Otherwise a `## Project memory` header
    followed by `### {scope}: {source}` subsections.
    """
    if not layers:
        return ""
    parts: list[str] = ["## Project memory", ""]
    for layer in layers:
        parts.append(f"### {layer.scope}: {layer.source}")
        parts.append("")
        parts.append(layer.content.rstrip())
        parts.append("")
    return "\n".join(parts).rstrip()
