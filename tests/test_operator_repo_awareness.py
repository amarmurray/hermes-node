"""Tests for read-only repo-awareness helpers."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.config import Settings
from hermes_node_ops.ledger import Ledger
from hermes_node_ops.models import TaskSubmission
from hermes_node_ops.operator.repo_awareness import (
    compare_repo_to_last_successful_run,
    diagnose_failed_task,
    find_repo_relevance,
    prepare_patch_proposal_plan,
    summarize_changed_files,
    summarize_repo_activity,
    summarize_repo_map,
    summarize_repo_state,
)
from hermes_node_ops.workspaces import (
    write_input_artifact,
    write_run_output,
    write_run_receipt,
)


def _make_settings() -> Settings:
    tmpdir = tempfile.mkdtemp()
    root = Path(tmpdir)
    (root / ".env").write_text("", encoding="utf-8")
    settings = Settings.load(root)
    settings.ensure_wrapper_dirs()
    with Ledger(settings.ledger_db):
        pass
    return settings


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _init_repo(root: Path, name: str = "repo") -> Path:
    repo = root / name
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "ops@example.com")
    _git(repo, "config", "user.name", "Hermes Node Tests")
    (repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "repo_tools.py").write_text(
        "def summarize_repo_state():\n    return 'ok'\n\n"
        "def diagnose_queue_state():\n    return 'diagnosed'\n",
        encoding="utf-8",
    )
    (repo / "docs").mkdir()
    (repo / "docs" / "architecture.md").write_text(
        "# Architecture\n\noperator talks through hermes-node.\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


class RepoAwarenessTests(unittest.TestCase):
    def test_repo_map_state_changes_and_relevance(self) -> None:
        settings = _make_settings()
        repo = _init_repo(settings.repo_root, "treasuryos")
        (repo / "README.md").write_text("# Test Repo\n\nupdated\n", encoding="utf-8")
        (repo / "notes.txt").write_text("operator notes\n", encoding="utf-8")

        repo_map = summarize_repo_map(str(repo), max_depth=2, mode="full")
        repo_state = summarize_repo_state(str(repo), mode="full")
        changed = summarize_changed_files(str(repo), mode="full")
        relevance = find_repo_relevance(str(repo), "repo state diagnostics", limit=5, mode="full")

        self.assertIn("summary", repo_map)
        self.assertIn("README.md", "\n".join(repo_map["details"]["tree_sample"]))
        self.assertTrue(repo_state["details"]["is_git_repo"])
        self.assertTrue(changed["details"]["dirty"])
        paths = [item["path"] for item in changed["details"]["files"]]
        self.assertIn("README.md", paths)
        self.assertIn("notes.txt", paths)
        self.assertGreaterEqual(len(relevance["details"]["matches"]), 1)
        self.assertEqual(relevance["details"]["matches"][0]["path"], "src/repo_tools.py")

    def test_repo_activity_and_compare_to_last_successful_run(self) -> None:
        settings = _make_settings()
        repo = _init_repo(settings.repo_root, "ixion")

        with Ledger(settings.ledger_db) as ledger:
            task = ledger.create_task(
                TaskSubmission(
                    kind="repo_read",
                    prompt="Summarize the repo",
                    repo_path=str(repo),
                    provider="openrouter",
                    model="openai/gpt-oss-20b:free",
                )
            )
            write_input_artifact(
                settings.repo_root,
                task["id"],
                kind=task["kind"],
                prompt=task["prompt"],
                provider=task["provider"],
                model=task["model"],
                repo_path=task["repo_path"],
                created_at=task["created_at"],
                workspace_dir=task["workspace_dir"],
            )
            _, run = ledger.begin_run(
                task["id"],
                command=["hermes", "chat"],
                working_dir=str(repo),
                provider=task["provider"],
                model=task["model"],
                max_turns=2,
                executor="hermes_chat",
            )
            ledger.finish_run(
                run["id"],
                exit_code=0,
                stdout="repo summary",
                stderr="",
                duration_ms=20,
            )
            ledger.add_summary(
                task["id"],
                run_id=run["id"],
                content="Repo read succeeded.",
            )
            write_run_output(
                settings.repo_root,
                task["id"],
                run["id"],
                stdout="repo summary",
                stderr="",
                workspace_dir=task["workspace_dir"],
            )
            write_run_receipt(
                settings.repo_root,
                task["id"],
                run["id"],
                exit_code=0,
                duration_ms=20,
                executor="hermes_chat",
                provider=task["provider"],
                model=task["model"],
                state="done",
                created_at=run["created_at"],
                started_at=run["started_at"],
                finished_at=ledger.get_run(run["id"])["finished_at"],
                working_dir=str(repo),
                command=["hermes", "chat"],
                workspace_dir=task["workspace_dir"],
            )

            (repo / "README.md").write_text("# Test Repo\n\nchanged after baseline\n", encoding="utf-8")
            (repo / "scratch.txt").write_text("pending notes\n", encoding="utf-8")

            activity = summarize_repo_activity(settings.repo_root, ledger, str(repo), limit=5, mode="full")
            comparison = compare_repo_to_last_successful_run(
                settings.repo_root,
                ledger,
                str(repo),
                mode="full",
            )

        self.assertIn("recent tasks", activity["summary"])
        self.assertEqual(activity["details"]["tasks"][0]["id"], task["id"])
        self.assertEqual(comparison["details"]["baseline"]["task"]["id"], task["id"])
        self.assertIn(f"baseline_task={task['id']}", comparison["provenance"])
        changed_paths = [item["path"] for item in comparison["details"]["current_changes"]["files"]]
        self.assertIn("README.md", changed_paths)
        self.assertIn("scratch.txt", changed_paths)

    def test_failed_task_diagnosis_and_patch_plan(self) -> None:
        settings = _make_settings()
        repo = _init_repo(settings.repo_root, "diagnostics")
        (repo / "src" / "planner.py").write_text(
            "def prepare_patch_proposal_plan():\n    return []\n",
            encoding="utf-8",
        )
        _git(repo, "add", "src/planner.py")
        _git(repo, "commit", "-m", "add planner")

        with Ledger(settings.ledger_db) as ledger:
            task = ledger.create_task(
                TaskSubmission(
                    kind="repo_read",
                    prompt="Investigate repo failure",
                    repo_path=str(repo),
                    provider="openrouter",
                    model="openai/gpt-oss-20b:free",
                )
            )
            write_input_artifact(
                settings.repo_root,
                task["id"],
                kind=task["kind"],
                prompt=task["prompt"],
                provider=task["provider"],
                model=task["model"],
                repo_path=task["repo_path"],
                created_at=task["created_at"],
                workspace_dir=task["workspace_dir"],
            )
            _, run = ledger.begin_run(
                task["id"],
                command=["hermes", "chat"],
                working_dir=str(repo),
                provider=task["provider"],
                model=task["model"],
                max_turns=2,
                executor="hermes_chat",
            )
            ledger.finish_run(
                run["id"],
                exit_code=2,
                stdout="partial output",
                stderr="fatal: missing file\ntrace line\n",
                duration_ms=42,
            )
            ledger.add_summary(
                task["id"],
                run_id=run["id"],
                content="Repo read failed due to a missing file.",
            )
            finished_run = ledger.get_run(run["id"])
            write_run_output(
                settings.repo_root,
                task["id"],
                run["id"],
                stdout="partial output",
                stderr="fatal: missing file\ntrace line\n",
                workspace_dir=task["workspace_dir"],
            )
            write_run_receipt(
                settings.repo_root,
                task["id"],
                run["id"],
                exit_code=2,
                duration_ms=42,
                executor="hermes_chat",
                provider=task["provider"],
                model=task["model"],
                state="failed",
                created_at=run["created_at"],
                started_at=run["started_at"],
                finished_at=finished_run["finished_at"],
                working_dir=str(repo),
                command=["hermes", "chat"],
                workspace_dir=task["workspace_dir"],
            )

            diagnosis = diagnose_failed_task(
                settings.repo_root,
                ledger,
                task["id"],
                mode="full",
            )
            plan = prepare_patch_proposal_plan(
                settings.repo_root,
                ledger,
                str(repo),
                "Improve repo diagnostics and patch proposal planning",
                mode="full",
            )

        self.assertIn("failed", diagnosis["summary"])
        self.assertTrue(any(item.startswith("receipt=") and item != "receipt=" for item in diagnosis["provenance"]))
        self.assertIn("fatal: missing file", "\n".join(diagnosis["evidence"]))
        self.assertTrue(plan["details"]["approval_required"])
        self.assertEqual(plan["details"]["suggested_submission"]["kind"], "repo_patch_branch")
        self.assertGreaterEqual(len(plan["details"]["relevance"]["matches"]), 1)


if __name__ == "__main__":
    unittest.main()
