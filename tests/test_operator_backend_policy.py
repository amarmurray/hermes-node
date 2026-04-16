"""Tests for the operator backend policy."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hermes_node_ops.config import Settings
from hermes_node_ops.operator.backend_policy import (
    BackendPolicy,
    BackendSlot,
    OperatorIntent,
    load_backend_policy,
    resolve_backend,
    render_backend_policy,
)


def _make_settings() -> Settings:
    tmpdir = tempfile.mkdtemp()
    root = Path(tmpdir)
    (root / ".env").write_text("", encoding="utf-8")
    return Settings.load(root)


class BackendPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = _make_settings()

    def test_resident_local_default_configured(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in list(os.environ):
                if k.startswith("HERMES_OPERATOR_"):
                    del os.environ[k]
            policy = load_backend_policy(self.settings)
        self.assertTrue(policy.resident_local.is_configured)
        self.assertEqual(policy.resident_local.slot, BackendSlot.RESIDENT_LOCAL)
        self.assertFalse(policy.escalation_remote.is_configured)
        self.assertFalse(policy.fallback.is_configured)

    def test_chat_intent_picks_resident(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in list(os.environ):
                if k.startswith("HERMES_OPERATOR_"):
                    del os.environ[k]
            policy = load_backend_policy(self.settings)
        sel = policy.select(OperatorIntent.CHAT)
        self.assertEqual(sel.descriptor.slot, BackendSlot.RESIDENT_LOCAL)
        self.assertIn("preferred", sel.reason)

    def test_repo_intent_falls_through_to_resident_when_no_escalation(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in list(os.environ):
                if k.startswith("HERMES_OPERATOR_"):
                    del os.environ[k]
            policy = load_backend_policy(self.settings)
        sel = policy.select(OperatorIntent.REPO_DIAGNOSTIC)
        self.assertEqual(sel.descriptor.slot, BackendSlot.RESIDENT_LOCAL)
        self.assertIn("escalation_remote: not configured", sel.tried)
        self.assertIn("fell through", sel.reason)

    def test_repo_intent_picks_escalation_when_configured(self) -> None:
        env = {
            "HERMES_OPERATOR_REMOTE_BASE_URL": "https://remote.example/v1",
            "HERMES_OPERATOR_REMOTE_MODEL": "remote-model-large",
            "HERMES_OPERATOR_REMOTE_API_KEY": "secret-not-logged",
        }
        with mock.patch.dict(os.environ, env):
            policy = load_backend_policy(self.settings)
        sel = policy.select(OperatorIntent.REPO_DIAGNOSTIC)
        self.assertEqual(sel.descriptor.slot, BackendSlot.ESCALATION_REMOTE)
        self.assertEqual(sel.descriptor.model, "remote-model-large")
        self.assertEqual(sel.tried, ())

    def test_string_intent_accepted(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            policy = load_backend_policy(self.settings)
        sel = policy.select("chat")
        self.assertEqual(sel.intent, OperatorIntent.CHAT)

    def test_unknown_intent_raises(self) -> None:
        policy = load_backend_policy(self.settings)
        with self.assertRaises(ValueError):
            policy.select("not_a_real_intent")

    def test_render_policy_shape(self) -> None:
        policy = load_backend_policy(self.settings)
        rendered = render_backend_policy(policy)
        self.assertEqual(len(rendered["slots"]), 3)
        slot_names = {s["slot"] for s in rendered["slots"]}
        self.assertEqual(
            slot_names,
            {"resident_local", "escalation_remote", "fallback"},
        )
        # API keys must NOT leak into the rendered policy.
        for s in rendered["slots"]:
            self.assertNotIn("api_key", s)

    def test_no_slot_configured_returns_resident_with_reason(self) -> None:
        with mock.patch.dict(os.environ, {"HERMES_OPERATOR_BASE_URL": "", "HERMES_OPERATOR_MODEL": ""}):
            policy = load_backend_policy(self.settings)
            sel = policy.select(OperatorIntent.PLAN)
        # No slot configured at all; we still return resident as placeholder
        # with an explicit reason rather than crashing.
        self.assertEqual(sel.descriptor.slot, BackendSlot.RESIDENT_LOCAL)
        self.assertIn("no backend slot is configured", sel.reason)

    def test_resolve_backend_uses_selected_slot(self) -> None:
        env = {
            "HERMES_OPERATOR_REMOTE_BASE_URL": "https://remote.example/v1",
            "HERMES_OPERATOR_REMOTE_MODEL": "remote-model-large",
        }
        with mock.patch.dict(os.environ, env):
            with mock.patch("hermes_node_ops.operator.backend.create_backend") as create_backend:
                backend = object()
                create_backend.return_value = backend
                resolved, selection = resolve_backend(self.settings, intent=OperatorIntent.REPO_DIAGNOSTIC)
        self.assertIs(resolved, backend)
        self.assertEqual(selection.descriptor.slot, BackendSlot.ESCALATION_REMOTE)
        create_backend.assert_called_once_with(
            base_url="https://remote.example/v1",
            api_key="",
            model="remote-model-large",
            timeout_seconds=180,
        )

    def test_resolve_backend_applies_explicit_overrides(self) -> None:
        with mock.patch("hermes_node_ops.operator.backend.create_backend") as create_backend:
            backend = object()
            create_backend.return_value = backend
            resolved, selection = resolve_backend(
                self.settings,
                intent=OperatorIntent.CHAT,
                model_override="override-model",
                base_url_override="http://override.local",
                timeout_seconds=45,
            )
        self.assertIs(resolved, backend)
        self.assertEqual(selection.descriptor.model, "override-model")
        self.assertIn("applied explicit overrides", selection.reason)
        create_backend.assert_called_once_with(
            base_url="http://override.local",
            api_key="",
            model="override-model",
            timeout_seconds=45,
        )


if __name__ == "__main__":
    unittest.main()
