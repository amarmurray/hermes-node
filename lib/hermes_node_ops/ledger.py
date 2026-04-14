from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .jsonl import utcnow
from .models import (
    APPROVAL_GRANTED,
    APPROVAL_NOT_REQUIRED,
    APPROVAL_REJECTED,
    APPROVAL_REQUESTED,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_PENDING,
    TASK_STATE_REJECTED,
    TASK_STATE_RUNNING,
    TASK_STATE_WAITING_APPROVAL,
    TaskSubmission,
    get_task_profile,
    new_id,
)


def _dump_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True)


def _load_json(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    return json.loads(raw)


class Ledger:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 30000")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self.ensure_schema()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def ensure_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                title TEXT NOT NULL,
                kind TEXT NOT NULL,
                prompt TEXT NOT NULL,
                state TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                max_turns INTEGER NOT NULL,
                repo_path TEXT NOT NULL,
                toolsets_json TEXT NOT NULL,
                require_approval INTEGER NOT NULL DEFAULT 0,
                approval_state TEXT NOT NULL,
                created_by TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                latest_run_id TEXT
            );

            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                state TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                max_turns INTEGER NOT NULL,
                command_json TEXT NOT NULL,
                working_dir TEXT NOT NULL,
                exit_code INTEGER,
                stdout TEXT NOT NULL DEFAULT '',
                stderr TEXT NOT NULL DEFAULT '',
                duration_ms INTEGER,
                hermes_session_id TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                run_id TEXT REFERENCES runs(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                data_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS summaries (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                run_id TEXT REFERENCES runs(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                summary_type TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                decided_at TEXT,
                status TEXT NOT NULL,
                actor TEXT NOT NULL,
                note TEXT NOT NULL,
                data_json TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def create_task(self, submission: TaskSubmission) -> dict[str, Any]:
        profile = get_task_profile(submission.kind)
        if profile.requires_repo_path and not submission.repo_path:
            raise ValueError(f"Task kind '{submission.kind}' requires --repo")

        created_at = utcnow()
        task_id = new_id("task")
        approval_state = APPROVAL_REQUESTED if submission.require_approval else APPROVAL_NOT_REQUIRED
        state = TASK_STATE_WAITING_APPROVAL if submission.require_approval else TASK_STATE_PENDING
        max_turns = submission.max_turns or profile.default_max_turns
        self._conn.execute(
            """
            INSERT INTO tasks (
                id, created_at, updated_at, title, kind, prompt, state, provider, model,
                max_turns, repo_path, toolsets_json, require_approval, approval_state,
                created_by, metadata_json, latest_run_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')
            """,
            (
                task_id,
                created_at,
                created_at,
                submission.title.strip(),
                submission.kind,
                submission.prompt,
                state,
                submission.provider.strip(),
                submission.model.strip(),
                max_turns,
                submission.repo_path.strip(),
                _dump_json(list(profile.toolsets)),
                1 if submission.require_approval else 0,
                approval_state,
                submission.created_by.strip() or "operator",
                _dump_json(submission.metadata or {}),
            ),
        )
        self.add_event(
            task_id,
            event_type="task.submitted",
            message=f"Task submitted as {submission.kind}",
            data={
                "kind": submission.kind,
                "provider": submission.provider.strip(),
                "model": submission.model.strip(),
            },
        )
        self.record_approval(
            task_id,
            status=approval_state,
            actor=submission.created_by.strip() or "operator",
            note=(
                "Explicit approval required before execution."
                if submission.require_approval
                else "Read-only task kind; approval not required."
            ),
        )
        self._conn.commit()
        return self.get_task(task_id)

    def record_approval(
        self,
        task_id: str,
        *,
        status: str,
        actor: str,
        note: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        created_at = utcnow()
        decided_at = created_at if status in {APPROVAL_GRANTED, APPROVAL_REJECTED, APPROVAL_NOT_REQUIRED} else None
        approval_id = new_id("approval")
        self._conn.execute(
            """
            INSERT INTO approvals (id, task_id, created_at, decided_at, status, actor, note, data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval_id,
                task_id,
                created_at,
                decided_at,
                status,
                actor,
                note,
                _dump_json(data or {}),
            ),
        )
        task = self.get_task(task_id)
        new_state = task["state"]
        if status == APPROVAL_GRANTED and task["state"] == TASK_STATE_WAITING_APPROVAL:
            new_state = TASK_STATE_PENDING
        elif status == APPROVAL_REJECTED:
            new_state = TASK_STATE_REJECTED
        self._conn.execute(
            "UPDATE tasks SET approval_state = ?, state = ?, updated_at = ? WHERE id = ?",
            (status, new_state, created_at, task_id),
        )
        self.add_event(
            task_id,
            event_type=f"approval.{status}",
            message=note,
            data={"actor": actor},
        )
        self._conn.commit()
        return self.get_task(task_id)

    def add_event(
        self,
        task_id: str,
        *,
        event_type: str,
        message: str,
        run_id: str | None = None,
        level: str = "info",
        data: dict[str, Any] | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO events (task_id, run_id, created_at, event_type, level, message, data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, run_id, utcnow(), event_type, level, message, _dump_json(data or {})),
        )

    def begin_run(
        self,
        task_id: str,
        *,
        command: list[str],
        working_dir: str,
        provider: str,
        model: str,
        max_turns: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        task = self.get_task(task_id)
        if task["approval_state"] not in {APPROVAL_NOT_REQUIRED, APPROVAL_GRANTED}:
            raise ValueError(f"Task {task_id} is not approved")
        if task["state"] not in {TASK_STATE_PENDING, TASK_STATE_WAITING_APPROVAL}:
            raise ValueError(f"Task {task_id} is not runnable from state {task['state']}")

        run_id = new_id("run")
        started_at = utcnow()
        self._conn.execute(
            """
            INSERT INTO runs (
                id, task_id, created_at, started_at, finished_at, state, provider, model,
                max_turns, command_json, working_dir, exit_code, stdout, stderr, duration_ms, hermes_session_id
            )
            VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, NULL, '', '', NULL, '')
            """,
            (
                run_id,
                task_id,
                started_at,
                started_at,
                TASK_STATE_RUNNING,
                provider,
                model,
                max_turns,
                _dump_json(command),
                working_dir,
            ),
        )
        self._conn.execute(
            "UPDATE tasks SET state = ?, updated_at = ?, latest_run_id = ? WHERE id = ?",
            (TASK_STATE_RUNNING, started_at, run_id, task_id),
        )
        self.add_event(
            task_id,
            run_id=run_id,
            event_type="run.started",
            message="Task execution started",
            data={"command": command, "working_dir": working_dir},
        )
        self._conn.commit()
        return self.get_task(task_id), self.get_run(run_id)

    def finish_run(
        self,
        run_id: str,
        *,
        exit_code: int,
        stdout: str,
        stderr: str,
        duration_ms: int,
        hermes_session_id: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        run = self.get_run(run_id)
        finished_at = utcnow()
        state = TASK_STATE_COMPLETED if exit_code == 0 else TASK_STATE_FAILED
        self._conn.execute(
            """
            UPDATE runs
            SET finished_at = ?, state = ?, exit_code = ?, stdout = ?, stderr = ?,
                duration_ms = ?, hermes_session_id = ?
            WHERE id = ?
            """,
            (finished_at, state, exit_code, stdout, stderr, duration_ms, hermes_session_id, run_id),
        )
        self._conn.execute(
            "UPDATE tasks SET state = ?, updated_at = ? WHERE id = ?",
            (state, finished_at, run["task_id"]),
        )
        self.add_event(
            run["task_id"],
            run_id=run_id,
            event_type=f"run.{state}",
            message=f"Task execution finished with exit code {exit_code}",
            level="error" if exit_code else "info",
            data={"exit_code": exit_code, "duration_ms": duration_ms},
        )
        self._conn.commit()
        return self.get_task(run["task_id"]), self.get_run(run_id)

    def add_summary(
        self,
        task_id: str,
        *,
        run_id: str | None,
        content: str,
        summary_type: str = "result",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        summary_id = new_id("summary")
        created_at = utcnow()
        self._conn.execute(
            """
            INSERT INTO summaries (id, task_id, run_id, created_at, summary_type, content, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (summary_id, task_id, run_id, created_at, summary_type, content, _dump_json(metadata or {})),
        )
        self.add_event(
            task_id,
            run_id=run_id,
            event_type="summary.created",
            message="Stored run summary",
            data={"summary_type": summary_type},
        )
        self._conn.commit()
        return self.get_summary(summary_id)

    def get_task(self, task_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown task: {task_id}")
        return self._decode_task(row)

    def get_run(self, run_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown run: {run_id}")
        return self._decode_run(row)

    def get_summary(self, summary_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM summaries WHERE id = ?", (summary_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown summary: {summary_id}")
        return self._decode_summary(row)

    def latest_summary(self, *, task_id: str = "", run_id: str = "") -> dict[str, Any] | None:
        if run_id:
            row = self._conn.execute(
                "SELECT * FROM summaries WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        elif task_id:
            row = self._conn.execute(
                "SELECT * FROM summaries WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        else:
            row = self._conn.execute("SELECT * FROM summaries ORDER BY created_at DESC LIMIT 1").fetchone()
        return self._decode_summary(row) if row else None

    def latest_run_for_task(self, task_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE task_id = ? ORDER BY started_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        return self._decode_run(row) if row else None

    def next_runnable_task(self) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT * FROM tasks
            WHERE state = ? AND approval_state IN (?, ?)
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (TASK_STATE_PENDING, APPROVAL_NOT_REQUIRED, APPROVAL_GRANTED),
        ).fetchone()
        return self._decode_task(row) if row else None

    def task_counts(self) -> dict[str, int]:
        rows = self._conn.execute("SELECT state, COUNT(*) AS total FROM tasks GROUP BY state").fetchall()
        return {row["state"]: int(row["total"]) for row in rows}

    def recent_tasks(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._decode_task(row) for row in rows]

    def recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._decode_run(row) for row in rows]

    def approvals_for_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM approvals WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        return [self._decode_approval(row) for row in rows]

    def events_for_task(self, task_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE task_id = ? ORDER BY id DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
        return [self._decode_event(row) for row in rows]

    def _decode_task(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["toolsets"] = _load_json(payload.pop("toolsets_json"), [])
        payload["metadata"] = _load_json(payload.pop("metadata_json"), {})
        payload["require_approval"] = bool(payload["require_approval"])
        return payload

    def _decode_run(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["command"] = _load_json(payload.pop("command_json"), [])
        return payload

    def _decode_summary(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["metadata"] = _load_json(payload.pop("metadata_json"), {})
        return payload

    def _decode_approval(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["data"] = _load_json(payload.pop("data_json"), {})
        return payload

    def _decode_event(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["data"] = _load_json(payload.pop("data_json"), {})
        return payload
