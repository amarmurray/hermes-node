"""Tests for operator.project_memory."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.operator.project_memory import (  # noqa: E402
    MAX_LAYER_CHARS,
    detect_project,
    load_project_memory,
    render_memory_layers,
)


class ProjectMemoryTests(unittest.TestCase):
    def test_non_git_dir_returns_empty_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = detect_project(tmp)
            self.assertEqual(ctx.repo_root, "")
            self.assertEqual(ctx.branch, "")
            self.assertFalse(ctx.in_git_repo)
            self.assertEqual(ctx.memory_layers, ())

    def test_load_repo_root_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("# project memory\nalpha\n", encoding="utf-8")
            layers = load_project_memory(root)
            self.assertEqual(len(layers), 1)
            self.assertEqual(layers[0].scope, "repo_root")
            self.assertIn("alpha", layers[0].content)

    def test_nested_agents_layers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "AGENTS.md").write_text("root-memory\n", encoding="utf-8")
            nested = root / "sub" / "deeper"
            nested.mkdir(parents=True)
            (root / "sub" / "AGENTS.md").write_text("sub-memory\n", encoding="utf-8")
            (nested / "AGENTS.md").write_text("deeper-memory\n", encoding="utf-8")

            # Init a minimal git repo so detect_project works.
            import subprocess
            subprocess.run(
                ["git", "init", "-q"], cwd=str(root), check=False, timeout=5
            )
            # detect_project walks ancestors from nested to root.
            ctx = detect_project(nested)
            if not ctx.in_git_repo:
                self.skipTest("git not available for detect_project test")
            sources = [layer.source for layer in ctx.memory_layers]
            contents = [layer.content for layer in ctx.memory_layers]
            # Deepest first, root last.
            joined = "\n---\n".join(contents)
            self.assertIn("deeper-memory", joined)
            self.assertIn("sub-memory", joined)
            self.assertIn("root-memory", joined)
            # Deepest ancestor should come before root.
            first_source = sources[0]
            last_source = sources[-1]
            self.assertIn("deeper", first_source)
            self.assertIn(str(root / "AGENTS.md"), last_source)

    def test_render_empty(self) -> None:
        self.assertEqual(render_memory_layers(()), "")

    def test_render_non_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("content-zed", encoding="utf-8")
            layers = load_project_memory(root)
            out = render_memory_layers(layers)
            self.assertIn("## Project memory", out)
            self.assertIn("content-zed", out)
            self.assertFalse(out.endswith("\n"))

    def test_truncation_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            big = "x" * 10_000
            (root / "AGENTS.md").write_text(big, encoding="utf-8")
            layers = load_project_memory(root)
            self.assertEqual(len(layers), 1)
            self.assertIn("…(truncated)", layers[0].content)
            self.assertLessEqual(len(layers[0].content), MAX_LAYER_CHARS + 100)


if __name__ == "__main__":
    unittest.main()
