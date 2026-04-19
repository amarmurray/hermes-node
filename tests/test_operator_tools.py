"""Tests for operator tool dispatch and hermes-node integration."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.config import Settings
from hermes_node_ops.ledger import Ledger
from hermes_node_ops.operator.session import create_session
from hermes_node_ops.models import TaskSubmission
from hermes_node_ops.operator.tools import TOOL_DEFINITIONS, ToolExecutor, get_tool_definitions
from hermes_node_ops.workspaces import write_input_artifact, write_run_output, write_run_receipt


def _make_settings() -> Settings:
    tmpdir = tempfile.mkdtemp()
    root = Path(tmpdir)
    (root / ".env").write_text("", encoding="utf-8")
    return Settings.load(root)


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "README").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def _register_temp_repo(repo_root: Path, name: str, path: Path) -> None:
    """Write a minimal config/repos.json pointing at a real temp git repo."""
    cfg = repo_root / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "repos.json").write_text(
        json.dumps(
            {
                "version": 1,
                "repos": [
                    {
                        "name": name,
                        "path": str(path),
                        "allowed_kinds": ["repo_patch_branch", "summary"],
                        "branch_policy": {
                            "default_branch": "main",
                            "branch_prefix": f"{name}/auto/",
                            "require_clean_worktree": True,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


class ToolDefinitionsTests(unittest.TestCase):
    def test_all_tools_have_names(self) -> None:
        defs = get_tool_definitions()
        self.assertGreater(len(defs), 0)
        for td in defs:
            self.assertIn("function", td)
            self.assertIn("name", td["function"])
            self.assertIn("parameters", td["function"])

    def test_tool_names_are_unique(self) -> None:
        defs = get_tool_definitions()
        names = [td["function"]["name"] for td in defs]
        self.assertEqual(len(names), len(set(names)))

    def test_expected_tools_present(self) -> None:
        defs = get_tool_definitions()
        names = {td["function"]["name"] for td in defs}
        expected = {
            "get_queue_status", "get_task_status", "submit_task",
            "approve_task", "list_recent_tasks", "list_recent_runs",
            "inspect_workspace", "read_run_receipt", "get_routing_policy",
            "repo_ls", "repo_read_file", "repo_grep", "git_status",
            "summarize_repo_map", "summarize_repo_state", "summarize_changed_files",
            "find_repo_relevance", "summarize_repo_activity",
            "compare_repo_to_last_successful_run", "diagnose_failed_task",
            "prepare_patch_proposal_plan",
            "save_note", "write_plan", "read_plan", "update_plan",
            "write_todo", "update_todo", "complete_todo_item",
            "write_scratch", "read_scratch", "spawn_subagent",
            "read_subagent_result", "list_subagents",
        }
        self.assertTrue(expected.issubset(names), f"Missing tools: {expected - names}")

    def test_default_tool_definitions_are_compact(self) -> None:
        compact_defs = get_tool_definitions()
        verbose_defs = TOOL_DEFINITIONS
        self.assertLess(len(json.dumps(compact_defs)), len(json.dumps(verbose_defs)))
        for td in compact_defs:
            self.assertIn("function", td)
            self.assertIn("name", td["function"])
            self.assertIn("parameters", td["function"])


class ToolDispatchTests(unittest.TestCase):
    def test_unknown_tool_returns_error(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        result = executor.dispatch("nonexistent_tool", {})
        data = json.loads(result)
        self.assertIn("error", data)

    def test_get_queue_status(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        result = executor.dispatch("get_queue_status", {"limit": 3})
        data = json.loads(result)
        self.assertIn("counts", data)

    def test_get_queue_status_omits_heavy_run_payloads(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        with Ledger(settings.ledger_db) as ledger:
            task = ledger.create_task(TaskSubmission(kind="summary", prompt="Test prompt from operator"))
            _, run = ledger.begin_run(
                task["id"],
                command=["echo", "hello"],
                working_dir=str(settings.repo_root),
                provider="openrouter",
                model="openai/gpt-oss-20b:free",
                max_turns=2,
                executor="hermes_chat",
            )
            ledger.finish_run(
                run["id"],
                exit_code=0,
                stdout="x" * 1000,
                stderr="y" * 1000,
                duration_ms=10,
            )

        result = executor.dispatch("get_queue_status", {"limit": 3})
        data = json.loads(result)
        self.assertLess(len(result), 2500)
        self.assertGreaterEqual(len(data["recent_runs"]), 1)
        run_view = data["recent_runs"][0]
        self.assertEqual(
            {
                "id",
                "task_id",
                "state",
                "executor",
                "provider",
                "model",
                "exit_code",
                "finished_at",
            },
            set(run_view),
        )
        self.assertNotIn("stdout", run_view)
        self.assertNotIn("stderr", run_view)
        self.assertNotIn("command", run_view)

    def test_submit_task_and_get_status(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)

        # Submit
        result = executor.dispatch("submit_task", {
            "kind": "summary",
            "prompt": "Test prompt from operator",
        })
        data = json.loads(result)
        self.assertTrue(data["submitted"])
        task_id = data["task_id"]
        self.assertEqual(data["kind"], "summary")
        self.assertFalse(data["approval_required"])

        # Get status
        result = executor.dispatch("get_task_status", {"task_id": task_id})
        data = json.loads(result)
        self.assertEqual(data["task"]["id"], task_id)
        self.assertEqual(data["task"]["state"], "pending")

    def test_submit_patch_requires_approval(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo_path = Path(repo_tmp)
            _init_git_repo(repo_path)
            _register_temp_repo(settings.repo_root, "fixture", repo_path)
            result = executor.dispatch("submit_task", {
                "kind": "repo_patch_branch",
                "prompt": "Fix the bug",
                "repo_path": repo_tmp,
            })
        data = json.loads(result)
        self.assertTrue(data["submitted"])
        self.assertTrue(data["approval_required"])
        self.assertEqual(data["state"], "pending_approval")

    def test_approve_task(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo_path = Path(repo_tmp)
            _init_git_repo(repo_path)
            _register_temp_repo(settings.repo_root, "fixture", repo_path)
            result = executor.dispatch("submit_task", {
                "kind": "repo_patch_branch",
                "prompt": "Fix the bug",
                "repo_path": repo_tmp,
            })
            task_id = json.loads(result)["task_id"]

            # Approve
            result = executor.dispatch("approve_task", {
                "task_id": task_id,
                "note": "Human confirmed",
            })
            data = json.loads(result)
            self.assertTrue(data["approved"])
            self.assertEqual(data["new_state"], "pending")

    def test_registry_and_patch_proposal_tools(self) -> None:
        settings = _make_settings()
        sid = create_session(settings.repo_root, model="test-model")
        executor = ToolExecutor(settings, settings.repo_root, session_id=sid)
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo_path = Path(repo_tmp)
            _init_git_repo(repo_path)
            _register_temp_repo(settings.repo_root, "fixture", repo_path)

            # list_registered_repos
            data = json.loads(executor.dispatch("list_registered_repos", {}))
            self.assertEqual(data["counts"]["total"], 1)
            self.assertEqual(data["counts"]["present"], 1)

            # get_repo_registry_entry
            data = json.loads(
                executor.dispatch(
                    "get_repo_registry_entry", {"name_or_alias": "fixture"}
                )
            )
            self.assertEqual(data["name"], "fixture")
            self.assertTrue(data["presence"]["ready_for_mutation"])

            # grounded_repo_summary
            data = json.loads(
                executor.dispatch(
                    "grounded_repo_summary", {"name_or_alias": "fixture"}
                )
            )
            self.assertTrue(data["grounded"])
            self.assertIn("state", data)

            # list_active_worktrees
            data = json.loads(
                executor.dispatch(
                    "list_active_worktrees", {"name_or_alias": "fixture"}
                )
            )
            self.assertGreaterEqual(data["count"], 1)

            # write_patch_proposal
            data = json.loads(
                executor.dispatch(
                    "write_patch_proposal",
                    {
                        "repo_name": "fixture",
                        "goal": "Add a thing",
                        "files_in_scope": ["README"],
                        "risk_notes": ["low risk"],
                    },
                )
            )
            self.assertTrue(data["written"])
            patch_id = data["proposal"]["patch_id"]
            self.assertTrue(data["proposal"]["branch"].startswith("fixture/auto/"))

            # list_patch_proposals
            props = json.loads(executor.dispatch("list_patch_proposals", {}))
            self.assertEqual(len(props), 1)

            # submit_patch_from_proposal
            data = json.loads(
                executor.dispatch(
                    "submit_patch_from_proposal", {"patch_id": patch_id}
                )
            )
            self.assertTrue(data["submitted"])
            self.assertEqual(data["proposal"]["state"], "submitted")
            self.assertTrue(data["approval_required"])
            task_id = data["task_id"]

            # synthesize_patch_result — no run yet, should return grounded=False
            data = json.loads(
                executor.dispatch(
                    "synthesize_patch_result", {"task_id": task_id}
                )
            )
            self.assertFalse(data["grounded"])
            self.assertIn("no runs", data["error"])

    def test_patch_proposal_refuses_unregistered_repo(self) -> None:
        settings = _make_settings()
        sid = create_session(settings.repo_root, model="test-model")
        executor = ToolExecutor(settings, settings.repo_root, session_id=sid)
        data = json.loads(
            executor.dispatch(
                "write_patch_proposal",
                {"repo_name": "nope", "goal": "x"},
            )
        )
        self.assertFalse(data["written"])
        self.assertIn("not in the repo registry", data["error"])

    def test_approve_non_pending_task_returns_error(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        result = executor.dispatch("submit_task", {
            "kind": "summary",
            "prompt": "Test",
        })
        task_id = json.loads(result)["task_id"]

        # Task is already in 'pending' (not pending_approval), approval should fail
        result = executor.dispatch("approve_task", {"task_id": task_id})
        data = json.loads(result)
        self.assertFalse(data["approved"])
        self.assertIn("error", data)

    def test_harness_tools_roundtrip(self) -> None:
        settings = _make_settings()
        sid = create_session(settings.repo_root, model="test-model")
        executor = ToolExecutor(settings, settings.repo_root, session_id=sid)

        result = executor.dispatch("write_plan", {"content": "Three-step plan"})
        self.assertTrue(json.loads(result)["written"])

        result = executor.dispatch("read_plan", {})
        self.assertIn("Three-step plan", json.loads(result)["content"])

        result = executor.dispatch("write_todo", {"items": ["check queue", "spawn helper"], "replace": True})
        data = json.loads(result)
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual(data["items"][0]["item_id"], "t1")

        result = executor.dispatch("update_todo", {"item_id": "t1", "text": "check queue carefully"})
        self.assertEqual(json.loads(result)["item"]["text"], "check queue carefully")

        result = executor.dispatch("complete_todo_item", {"item_id": "t2", "note": "done"})
        self.assertTrue(json.loads(result)["completed"])

        result = executor.dispatch("write_scratch", {"step_id": "triage", "content": "scratch text"})
        self.assertEqual(json.loads(result)["step_id"], "triage")

        result = executor.dispatch("spawn_subagent", {"role": "rate-limit-triage", "goal": "Inspect queue pressure"})
        spawned = json.loads(result)
        self.assertEqual(spawned["status"], "completed")

        result = executor.dispatch("list_subagents", {})
        listed = json.loads(result)
        self.assertEqual(len(listed), 1)

        result = executor.dispatch("read_subagent_result", {"subagent_id": spawned["subagent_id"]})
        loaded = json.loads(result)
        self.assertIn("# Rate-Limit Triage", loaded["result"])

    def test_list_recent_tasks(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        executor.dispatch("submit_task", {"kind": "summary", "prompt": "one"})
        executor.dispatch("submit_task", {"kind": "summary", "prompt": "two"})

        result = executor.dispatch("list_recent_tasks", {"limit": 5})
        data = json.loads(result)
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 2)

    def test_get_routing_policy(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        result = executor.dispatch("get_routing_policy", {})
        data = json.loads(result)
        self.assertIn("routes", data)
        self.assertIn("available_executors", data)

    def test_inspect_workspace_nonexistent(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        result = executor.dispatch("inspect_workspace", {"task_id": "task_fake"})
        data = json.loads(result)
        self.assertFalse(data["exists"])


class RepoToolTests(unittest.TestCase):
    @staticmethod
    def _git(repo: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def _init_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        self._git(repo, "init")
        self._git(repo, "config", "user.email", "ops@example.com")
        self._git(repo, "config", "user.name", "Hermes Node Tests")
        (repo / "README.md").write_text("# Repo\n", encoding="utf-8")
        (repo / "src").mkdir()
        (repo / "src" / "repo_tools.py").write_text(
            "def summarize_repo_state():\n    return 'ok'\n",
            encoding="utf-8",
        )
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-m", "initial")
        return repo

    def test_repo_ls(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "hello.txt").write_text("hi", encoding="utf-8")
            result = executor.dispatch("repo_ls", {"path": tmpdir})
            data = json.loads(result)
            self.assertGreater(data["count"], 0)

    def test_repo_read_file(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "test.py"
            f.write_text("print('hello')\n", encoding="utf-8")
            result = executor.dispatch("repo_read_file", {"path": str(f)})
            data = json.loads(result)
            self.assertIn("print('hello')", data["content"])
            self.assertFalse(data["truncated"])

    def test_repo_read_file_nonexistent(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        result = executor.dispatch("repo_read_file", {"path": "/nonexistent/file.py"})
        data = json.loads(result)
        self.assertIn("error", data)

    def test_repo_grep(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "code.py"
            f.write_text("def hello():\n    return 'world'\n", encoding="utf-8")
            result = executor.dispatch("repo_grep", {"pattern": "hello", "path": tmpdir})
            data = json.loads(result)
            self.assertGreater(data["count"], 0)

    def test_git_status_non_repo(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = executor.dispatch("git_status", {"repo_path": tmpdir})
            data = json.loads(result)
            # Should still return something (maybe empty branch)
            self.assertIn("repo_path", data)

    def test_path_taking_tools_accept_registered_alias(self) -> None:
        """Tools that take repo_path must also accept a registered
        name/alias, so small LLMs can call them without first chaining
        a registry lookup."""
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo_path = Path(repo_tmp)
            _init_git_repo(repo_path)
            _register_temp_repo(settings.repo_root, "fixture", repo_path)

            # git_status with the alias — should resolve to repo_path and not error
            data = json.loads(
                executor.dispatch("git_status", {"repo_path": "fixture"})
            )
            self.assertNotIn("error", data)
            self.assertIn("branch", data)
            self.assertEqual(Path(data["repo_path"]).resolve(), repo_path.resolve())

            # summarize_repo_state with the alias — should return a mapping
            # with state keys (branch, etc.), not an error payload.
            data = json.loads(
                executor.dispatch(
                    "summarize_repo_state",
                    {"repo_path": "fixture", "mode": "brief"},
                )
            )
            self.assertNotIn("error", data)
            # unresolved alias would have produced a path-not-found style error

    def test_repo_awareness_tool_dispatch(self) -> None:
        settings = _make_settings()
        executor = ToolExecutor(settings, settings.repo_root)
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._init_repo(Path(tmpdir))
            (repo / "README.md").write_text("# Repo\n\nchanged\n", encoding="utf-8")
            (repo / "notes.txt").write_text("note\n", encoding="utf-8")

            with Ledger(settings.ledger_db) as ledger:
                task = ledger.create_task(
                    TaskSubmission(
                        kind="repo_read",
                        prompt="Inspect repo",
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
                    stdout="partial",
                    stderr="boom\ntrace\n",
                    duration_ms=11,
                )
                finished_run = ledger.get_run(run["id"])
                ledger.add_summary(
                    task["id"],
                    run_id=run["id"],
                    content="repo read failed",
                )
                write_run_output(
                    settings.repo_root,
                    task["id"],
                    run["id"],
                    stdout="partial",
                    stderr="boom\ntrace\n",
                    workspace_dir=task["workspace_dir"],
                )
                write_run_receipt(
                    settings.repo_root,
                    task["id"],
                    run["id"],
                    exit_code=2,
                    duration_ms=11,
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

            state = json.loads(executor.dispatch("summarize_repo_state", {"repo_path": str(repo)}))
            self.assertIn("summary", state)

            changed = json.loads(executor.dispatch("summarize_changed_files", {"repo_path": str(repo), "mode": "full"}))
            self.assertTrue(changed["details"]["dirty"])

            relevance = json.loads(
                executor.dispatch(
                    "find_repo_relevance",
                    {"repo_path": str(repo), "query": "repo state", "mode": "full"},
                )
            )
            self.assertGreaterEqual(len(relevance["details"]["matches"]), 1)

            activity = json.loads(
                executor.dispatch(
                    "summarize_repo_activity",
                    {"repo_path": str(repo), "mode": "full"},
                )
            )
            self.assertEqual(activity["details"]["tasks"][0]["id"], task["id"])

            diagnosis = json.loads(
                executor.dispatch(
                    "diagnose_failed_task",
                    {"task_id": task["id"], "mode": "full"},
                )
            )
            self.assertIn("failed", diagnosis["summary"])

            plan = json.loads(
                executor.dispatch(
                    "prepare_patch_proposal_plan",
                    {
                        "repo_path": str(repo),
                        "goal": "Improve repo diagnostics",
                        "mode": "full",
                    },
                )
            )
            self.assertEqual(plan["details"]["suggested_submission"]["kind"], "repo_patch_branch")


if __name__ == "__main__":
    unittest.main()
