"""Tests for the file-backed operator harness."""

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
from hermes_node_ops.operator.harness import (
    complete_todo_item,
    ensure_harness_layout,
    list_subagents,
    read_plan,
    read_scratch,
    read_subagent_result,
    read_todo_markdown,
    spawn_subagent,
    update_plan,
    update_todo_item,
    write_plan,
    write_scratch,
    write_todo_items,
)
from hermes_node_ops.operator.session import create_session


def _make_settings() -> Settings:
    tmpdir = tempfile.mkdtemp()
    root = Path(tmpdir)
    (root / ".env").write_text("", encoding="utf-8")
    settings = Settings.load(root)
    settings.ensure_wrapper_dirs()
    with Ledger(settings.ledger_db):
        pass
    return settings


class HarnessFileTests(unittest.TestCase):
    def test_plan_and_todo_roundtrip(self) -> None:
        settings = _make_settings()
        sid = create_session(settings.repo_root, model="test-model")

        root = ensure_harness_layout(settings.repo_root, sid)
        self.assertTrue((root / "plan.md").exists())
        self.assertTrue((root / "todo.md").exists())
        self.assertTrue((root / "scratch").is_dir())
        self.assertTrue((root / "subagents").is_dir())

        write_plan(settings.repo_root, sid, "Inspect queue\n\n- Check failures")
        self.assertIn("Inspect queue", read_plan(settings.repo_root, sid))

        updated = update_plan(settings.repo_root, sid, "Focus on rate-limit pressure next.")
        self.assertIn("## Update", updated)
        self.assertIn("Focus on rate-limit pressure next.", updated)

        items = write_todo_items(settings.repo_root, sid, ["triage queue", "inspect repo"], replace=True)
        self.assertEqual([item["item_id"] for item in items], ["t1", "t2"])

        updated_item = update_todo_item(settings.repo_root, sid, "t1", "triage queue carefully")
        self.assertEqual(updated_item["text"], "triage queue carefully")

        completed = complete_todo_item(settings.repo_root, sid, "t2", note="done")
        self.assertTrue(completed["done"])
        todo_md = read_todo_markdown(settings.repo_root, sid)
        self.assertIn("- [ ] t1: triage queue carefully", todo_md)
        self.assertIn("- [x] t2: inspect repo (done)", todo_md)

    def test_scratch_roundtrip(self) -> None:
        settings = _make_settings()
        sid = create_session(settings.repo_root, model="test-model")
        written = write_scratch(settings.repo_root, sid, "step 1", "First pass notes")
        self.assertEqual(written["step_id"], "step_1")

        loaded = read_scratch(settings.repo_root, sid, "step 1")
        self.assertIn("First pass notes", loaded["content"])


class SubagentTests(unittest.TestCase):
    def test_spawn_rate_limit_subagent_writes_artifacts(self) -> None:
        settings = _make_settings()
        sid = create_session(settings.repo_root, model="test-model")

        spawned = spawn_subagent(
            settings,
            settings.repo_root,
            sid,
            role="rate-limit-triage",
            goal="Check whether rate-limit pressure is visible.",
        )
        self.assertEqual(spawned["status"], "completed")

        subagents = list_subagents(settings.repo_root, sid)
        self.assertEqual(len(subagents), 1)
        self.assertEqual(subagents[0]["role"], "rate-limit-triage")

        result = read_subagent_result(settings.repo_root, sid, spawned["subagent_id"])
        self.assertIn("# Rate-Limit Triage", result["result"])
        self.assertEqual(len(result["thread"]), 2)

    def test_spawn_repo_diagnostician_reads_repo(self) -> None:
        settings = _make_settings()
        sid = create_session(settings.repo_root, model="test-model")

        with tempfile.TemporaryDirectory() as repo_tmp:
            repo = Path(repo_tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
            (repo / "README.md").write_text("hello\n", encoding="utf-8")

            spawned = spawn_subagent(
                settings,
                settings.repo_root,
                sid,
                role="repo-diagnostician",
                goal="Summarize repo state.",
                repo_path=str(repo),
            )

        self.assertEqual(spawned["status"], "completed")
        result = read_subagent_result(settings.repo_root, sid, spawned["subagent_id"])
        self.assertIn("# Repo Diagnostician", result["result"])
        self.assertIn("## Git State", result["result"])


if __name__ == "__main__":
    unittest.main()
