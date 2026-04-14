from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.routing import (
    ALL_EXECUTORS,
    ALL_RISK_CLASSES,
    EXECUTOR_CODEX,
    EXECUTOR_HERMES_CHAT,
    RISK_BRANCH_WRITE,
    RISK_DESTRUCTIVE,
    RISK_READ_ONLY,
    ROUTE_TABLE,
    format_route_table,
    get_route,
    is_approval_required,
    is_kind_allowed,
    resolve_executor,
)
from hermes_node_ops.models import TASK_PROFILES


class RouteTableTests(unittest.TestCase):
    def test_all_task_profiles_have_routes(self) -> None:
        for kind in TASK_PROFILES:
            self.assertIn(kind, ROUTE_TABLE, f"TaskProfile '{kind}' has no route")

    def test_all_routes_have_task_profiles(self) -> None:
        for kind in ROUTE_TABLE:
            self.assertIn(kind, TASK_PROFILES, f"Route '{kind}' has no TaskProfile")

    def test_route_risk_classes_are_valid(self) -> None:
        for kind, route in ROUTE_TABLE.items():
            self.assertIn(route.risk_class, ALL_RISK_CLASSES, f"Invalid risk class on {kind}")

    def test_route_executors_are_valid(self) -> None:
        for kind, route in ROUTE_TABLE.items():
            self.assertIn(route.default_executor, ALL_EXECUTORS, f"Invalid executor on {kind}")
            for fb in route.fallback_executors:
                self.assertIn(fb, ALL_EXECUTORS, f"Invalid fallback executor on {kind}")


class RoutingDecisionTests(unittest.TestCase):
    def test_summary_routes_to_hermes_chat(self) -> None:
        executor = resolve_executor("summary", available_executors={EXECUTOR_HERMES_CHAT})
        self.assertEqual(executor, EXECUTOR_HERMES_CHAT)

    def test_repo_read_prefers_codex(self) -> None:
        executor = resolve_executor("repo_read", available_executors={EXECUTOR_HERMES_CHAT, EXECUTOR_CODEX})
        self.assertEqual(executor, EXECUTOR_CODEX)

    def test_repo_read_falls_back_to_hermes(self) -> None:
        executor = resolve_executor("repo_read", available_executors={EXECUTOR_HERMES_CHAT})
        self.assertEqual(executor, EXECUTOR_HERMES_CHAT)

    def test_repo_patch_branch_requires_codex(self) -> None:
        executor = resolve_executor("repo_patch_branch", available_executors={EXECUTOR_HERMES_CHAT})
        self.assertIsNone(executor)

    def test_repo_patch_branch_works_with_codex(self) -> None:
        executor = resolve_executor("repo_patch_branch", available_executors={EXECUTOR_HERMES_CHAT, EXECUTOR_CODEX})
        self.assertEqual(executor, EXECUTOR_CODEX)

    def test_unknown_kind_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_executor("nonexistent_kind", available_executors={EXECUTOR_HERMES_CHAT})


class ApprovalPolicyTests(unittest.TestCase):
    def test_summary_no_approval(self) -> None:
        self.assertFalse(is_approval_required("summary"))

    def test_research_no_approval(self) -> None:
        self.assertFalse(is_approval_required("research"))

    def test_repo_read_no_approval(self) -> None:
        self.assertFalse(is_approval_required("repo_read"))

    def test_repo_patch_branch_requires_approval(self) -> None:
        self.assertTrue(is_approval_required("repo_patch_branch"))


class BlockedKindTests(unittest.TestCase):
    def test_destructive_kinds_not_in_table(self) -> None:
        for kind in ["repo_write_main", "shell_exec", "destructive"]:
            self.assertFalse(is_kind_allowed(kind))

    def test_allowed_kinds_are_in_table(self) -> None:
        for kind in ["summary", "research", "repo_read", "repo_patch_branch"]:
            self.assertTrue(is_kind_allowed(kind))


class RiskClassTests(unittest.TestCase):
    def test_read_only_kinds(self) -> None:
        for kind in ["summary", "research", "repo_read"]:
            route = get_route(kind)
            self.assertEqual(route.risk_class, RISK_READ_ONLY)

    def test_branch_write_kinds(self) -> None:
        route = get_route("repo_patch_branch")
        self.assertEqual(route.risk_class, RISK_BRANCH_WRITE)

    def test_no_destructive_routes_exist(self) -> None:
        for route in ROUTE_TABLE.values():
            self.assertNotEqual(route.risk_class, RISK_DESTRUCTIVE)


class FormatRouteTableTests(unittest.TestCase):
    def test_format_returns_all_routes(self) -> None:
        table = format_route_table()
        self.assertEqual(len(table), len(ROUTE_TABLE))
        kinds = {r["kind"] for r in table}
        self.assertEqual(kinds, set(ROUTE_TABLE.keys()))


if __name__ == "__main__":
    unittest.main()
