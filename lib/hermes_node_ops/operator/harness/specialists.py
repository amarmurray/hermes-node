"""Bounded specialist subagents for the operator harness.

These specialists are file-backed inspection organs under the operator.
They are read-only, policy-constrained, and route every conclusion back
through the parent operator.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...config import Settings
from ...ledger import Ledger
from ...wrapper import format_status_snapshot
from ..repo_awareness import (
    find_repo_relevance,
    summarize_changed_files,
    summarize_repo_activity,
    summarize_repo_map,
    summarize_repo_state,
)
from ..repo_registry import RepoRegistry, check_presence


@dataclass(frozen=True)
class SpecialistSpec:
    role: str
    constitution: str
    allowed_surfaces: tuple[str, ...]


SPECIALIST_SPECS: dict[str, SpecialistSpec] = {
    "rate-limit-triage": SpecialistSpec(
        role="rate-limit-triage",
        constitution=(
            "Inspect queue pressure, cooldowns, and deferrals. "
            "Do not replay tasks, change routing, or approve anything."
        ),
        allowed_surfaces=(
            "ledger.queue_snapshot",
            "ledger.cooldowns",
            "ledger.rate_limit_events",
        ),
    ),
    "repo-diagnostician": SpecialistSpec(
        role="repo-diagnostician",
        constitution=(
            "Inspect repository structure, git state, ledger history, and file relevance. "
            "Do not mutate the repo, execute patches, or approve risky work."
        ),
        allowed_surfaces=(
            "repo.state",
            "repo.map",
            "repo.changed_files",
            "repo.relevance",
            "ledger.repo_activity",
        ),
    ),
    "ixion-diagnostician": SpecialistSpec(
        role="ixion-diagnostician",
        constitution=(
            "Inspect the Ixion robotics-vp-core repo through the registry. "
            "Refuse to run if the repo is not registered or not present on disk. "
            "Do not mutate, patch, or approve anything."
        ),
        allowed_surfaces=(
            "registry.ixion",
            "repo.state",
            "repo.map",
            "repo.changed_files",
            "repo.relevance",
            "ledger.repo_activity",
        ),
    ),
}

SPECIALIST_ROLES = tuple(SPECIALIST_SPECS)


def get_specialist_spec(role: str) -> SpecialistSpec:
    try:
        return SPECIALIST_SPECS[role]
    except KeyError as exc:
        raise ValueError(f"Unsupported specialist role: {role}") from exc


def run_specialist(
    settings: Settings,
    *,
    role: str,
    goal: str,
    context: str = "",
    repo_path: str = "",
) -> str:
    """Execute a bounded specialist and return its markdown result."""
    spec = get_specialist_spec(role)
    if role == "rate-limit-triage":
        return _run_rate_limit_triage(settings, spec=spec, goal=goal, context=context)
    if role == "repo-diagnostician":
        return _run_repo_diagnostician(
            settings,
            spec=spec,
            goal=goal,
            context=context,
            repo_path=repo_path,
        )
    if role == "ixion-diagnostician":
        return _run_ixion_diagnostician(
            settings,
            spec=spec,
            goal=goal,
            context=context,
        )
    raise ValueError(f"Unsupported specialist role: {role}")


def _run_ixion_diagnostician(
    settings: Settings,
    *,
    spec: SpecialistSpec,
    goal: str,
    context: str,
) -> str:
    """Ixion-scoped diagnostician. Refuses to run if the repo is absent."""
    registry = RepoRegistry.load(Path(settings.repo_root))
    entry = registry.find("ixion")
    if entry is None:
        return _render_specialist_report(
            title="Ixion Diagnostician",
            spec=spec,
            goal=goal,
            summary="Refused: 'ixion' is not in the repo registry.",
            evidence=[
                f"Registry source: {registry.source_path}",
                f"Known repos: {', '.join(e.name for e in registry.repos) or '(none)'}",
            ],
            recommendations=[
                "Add an 'ixion' entry to config/repos.json before invoking this specialist.",
            ],
            provenance=[f"registry={registry.source_path}"],
            context=context,
        )

    presence = check_presence(entry)
    if not presence.ready_for_mutation and not presence.exists:
        return _render_specialist_report(
            title="Ixion Diagnostician",
            spec=spec,
            goal=goal,
            summary=(
                f"Refused: Ixion repo is not present on disk "
                f"({presence.missing_reason or 'absent'})."
            ),
            evidence=[
                f"Registry source: {registry.source_path}",
                f"Declared path: {entry.path}",
                f"Resolved path: {presence.resolved_path}",
                f"Missing reason: {presence.missing_reason or '(unknown)'}",
            ],
            recommendations=[
                "Place the robotics-vp-core checkout at the registered path, "
                "or update config/repos.json before invoking this specialist.",
            ],
            provenance=[
                f"registry={registry.source_path}",
                f"expected_path={entry.path}",
            ],
            context=context,
        )

    report = _run_repo_diagnostician(
        settings,
        spec=spec,
        goal=goal,
        context=context,
        repo_path=presence.resolved_path,
    )
    # Retitle so it reads as the Ixion specialist even though the body
    # is produced by the shared repo-diagnostician machinery.
    return report.replace("# Repo Diagnostician", "# Ixion Diagnostician", 1)


def _run_rate_limit_triage(
    settings: Settings,
    *,
    spec: SpecialistSpec,
    goal: str,
    context: str,
) -> str:
    with Ledger(settings.ledger_db) as ledger:
        snapshot = format_status_snapshot(ledger, limit=10)

    deferred = snapshot.get("deferred_tasks", []) or []
    cooldowns = snapshot.get("active_cooldowns", []) or []
    events = snapshot.get("recent_rate_limit_events", []) or []

    summary = (
        f"Rate-limit surface: {len(cooldowns)} active cooldowns, "
        f"{len(deferred)} deferred tasks, {len(events)} recent signals."
    )
    evidence = [
        f"Goal: {goal}",
        f"Allowed surfaces: {', '.join(spec.allowed_surfaces)}",
    ]
    if context.strip():
        evidence.append("Context: " + context.strip())
    for task in deferred[:5]:
        evidence.append(
            f"Deferred task {task.get('id', '?')} [{task.get('kind', '?')}] "
            f"next_attempt_at={task.get('next_attempt_at', '?')}"
        )
    for cooldown in cooldowns[:5]:
        evidence.append(
            f"Cooldown {cooldown.get('cooldown_key', '?')} until "
            f"{cooldown.get('cooldown_until', '?')}"
        )
    for event in events[:5]:
        evidence.append(
            f"Signal {event.get('created_at', '?')}: {event.get('message', '').strip()}"
        )

    recommendations = (
        [
            "Keep retries inside hermes-node backoff boundaries.",
            "Prefer waiting for cooldown expiry over ad hoc replays.",
            "If pressure persists, change routing deliberately rather than bypassing policy.",
        ]
        if (cooldowns or deferred or events)
        else ["No active rate-limit pressure is visible in the ledger right now."]
    )

    return _render_specialist_report(
        title="Rate-Limit Triage",
        spec=spec,
        goal=goal,
        summary=summary,
        evidence=evidence,
        recommendations=recommendations,
        provenance=[
            f"ledger={settings.ledger_db}",
            f"repo_root={settings.repo_root}",
        ],
    )


def _run_repo_diagnostician(
    settings: Settings,
    *,
    spec: SpecialistSpec,
    goal: str,
    context: str,
    repo_path: str,
) -> str:
    target = Path(repo_path or settings.repo_root).expanduser().resolve()
    if not target.exists():
        raise ValueError(f"Repository path does not exist: {repo_path or settings.repo_root}")
    if target.is_file():
        target = target.parent

    with Ledger(settings.ledger_db) as ledger:
        repo_state = summarize_repo_state(str(target), mode="full")
        changed = summarize_changed_files(str(target), mode="full")
        repo_map = summarize_repo_map(str(target), max_depth=2, mode="full")
        activity = summarize_repo_activity(
            settings.repo_root,
            ledger,
            str(target),
            limit=3,
            mode="full",
        )
        relevance = find_repo_relevance(
            str(target),
            query="\n".join(piece for piece in (goal.strip(), context.strip()) if piece).strip(),
            limit=5,
            mode="full",
        )

    summary_parts = [repo_state["summary"]]
    if changed["summary"]:
        summary_parts.append(changed["summary"])
    if relevance["summary"]:
        summary_parts.append(relevance["summary"])

    evidence = [
        f"Allowed surfaces: {', '.join(spec.allowed_surfaces)}",
        repo_map["summary"],
        activity["summary"],
    ]
    evidence.extend(repo_state.get("evidence", [])[:3])
    evidence.extend(changed.get("evidence", [])[:3])
    evidence.extend(relevance.get("evidence", [])[:3])

    recommendations = [
        "Use repo-aware tools to inspect the scoped files before proposing mutation.",
        "Keep any mutation request routed through hermes-node submission and approval.",
        "Cite workspace and receipt provenance when comparing current state to prior runs.",
    ]

    return _render_specialist_report(
        title="Repo Diagnostician",
        spec=spec,
        goal=goal,
        summary=" ".join(part for part in summary_parts if part).strip(),
        evidence=evidence,
        recommendations=recommendations,
        provenance=[
            f"repo={target}",
            f"ledger={settings.ledger_db}",
            *repo_state.get("provenance", [])[:2],
            *activity.get("provenance", [])[:2],
        ],
        context=context,
    )


def _render_specialist_report(
    *,
    title: str,
    spec: SpecialistSpec,
    goal: str,
    summary: str,
    evidence: list[str],
    recommendations: list[str],
    provenance: list[str],
    context: str = "",
) -> str:
    lines = [
        f"# {title}",
        "",
        f"Goal: {goal}",
        f"Constitution: {spec.constitution}",
    ]
    if context.strip():
        lines.extend(["", "Context:", context.strip()])
    lines.extend(
        [
            "",
            "## Summary",
            summary.strip() or "(no summary)",
            "",
            "## Evidence",
        ]
    )
    for item in evidence[:12]:
        lines.append(f"- {item}")
    lines.extend(["", "## Recommendation"])
    for item in recommendations:
        lines.append(f"- {item}")
    lines.extend(["", "## Provenance"])
    for item in provenance:
        if item:
            lines.append(f"- {item}")
    return "\n".join(lines).strip() + "\n"
