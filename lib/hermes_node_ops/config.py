from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'").strip('"')
    return values


def _resolve(raw: str, repo_root: Path) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(raw))
    path = Path(expanded)
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def _get(merged: dict[str, str], key: str, default: str) -> str:
    return os.environ.get(key, merged.get(key, default))


def _parse_chat_ids(raw: str) -> tuple[int, ...]:
    """Parse a comma-separated list of chat IDs into a tuple of ints."""
    if not raw:
        return ()
    result: list[int] = []
    for piece in raw.replace(" ", "").split(","):
        if not piece:
            continue
        try:
            result.append(int(piece))
        except ValueError:
            continue
    return tuple(result)


@dataclass
class Settings:
    repo_root: Path
    env_file: Path
    upstream_home: Path
    upstream_repo: Path
    hermes_bin: Path
    ledger_db: Path
    log_jsonl: Path
    service_user: str
    default_provider: str
    default_model: str
    default_max_turns: int
    poll_interval_seconds: int
    source_tag: str
    max_attempts: int
    base_backoff_seconds: int
    max_backoff_seconds: int
    codex_bin: str
    telegram_bot_token: str
    telegram_allowed_chat_ids: tuple[int, ...]
    telegram_poll_timeout: int
    telegram_alert_poll_seconds: int

    @classmethod
    def load(cls, repo_root: Path | None = None) -> "Settings":
        root = repo_root.resolve() if repo_root else Path.cwd().resolve()
        env_file = root / ".env"
        file_values = read_env_file(env_file)
        merged = {**file_values, **os.environ}

        upstream_home = _resolve(_get(merged, "HERMES_NODE_UPSTREAM_HOME", "~/.hermes"), root)
        upstream_repo = _resolve(_get(merged, "HERMES_NODE_UPSTREAM_REPO", "~/apps/hermes-agent"), root)
        hermes_bin = _resolve(_get(merged, "HERMES_NODE_HERMES_BIN", "~/.local/bin/hermes"), root)
        ledger_db = _resolve(_get(merged, "HERMES_NODE_DB_PATH", "state/hermes-node.db"), root)
        log_jsonl = _resolve(_get(merged, "HERMES_NODE_LOG_JSONL", "logs/hermes-node.jsonl"), root)

        return cls(
            repo_root=root,
            env_file=env_file,
            upstream_home=upstream_home,
            upstream_repo=upstream_repo,
            hermes_bin=hermes_bin,
            ledger_db=ledger_db,
            log_jsonl=log_jsonl,
            service_user=_get(merged, "HERMES_NODE_SERVICE_USER", "ubuntu"),
            default_provider=_get(merged, "HERMES_NODE_DEFAULT_PROVIDER", "openrouter"),
            default_model=_get(merged, "HERMES_NODE_DEFAULT_MODEL", "openai/gpt-oss-20b:free"),
            default_max_turns=int(_get(merged, "HERMES_NODE_DEFAULT_MAX_TURNS", "4")),
            poll_interval_seconds=int(_get(merged, "HERMES_NODE_POLL_INTERVAL_SECONDS", "15")),
            source_tag=_get(merged, "HERMES_NODE_SOURCE_TAG", "tool"),
            max_attempts=int(_get(merged, "HERMES_NODE_MAX_ATTEMPTS", "5")),
            base_backoff_seconds=int(_get(merged, "HERMES_NODE_BASE_BACKOFF_SECONDS", "30")),
            max_backoff_seconds=int(_get(merged, "HERMES_NODE_MAX_BACKOFF_SECONDS", "900")),
            codex_bin=_get(merged, "HERMES_NODE_CODEX_BIN", ""),
            telegram_bot_token=_get(merged, "HERMES_OPERATOR_TELEGRAM_TOKEN", ""),
            telegram_allowed_chat_ids=_parse_chat_ids(
                _get(merged, "HERMES_OPERATOR_TELEGRAM_ALLOWED_CHAT_IDS", "")
            ),
            telegram_poll_timeout=int(
                _get(merged, "HERMES_OPERATOR_TELEGRAM_POLL_TIMEOUT", "30")
            ),
            telegram_alert_poll_seconds=int(
                _get(merged, "HERMES_OPERATOR_TELEGRAM_ALERT_POLL_SECONDS", "60")
            ),
        )

    @property
    def resolved_hermes_bin(self) -> Path:
        if self.hermes_bin.exists():
            return self.hermes_bin
        return self.upstream_repo / "venv" / "bin" / "hermes"

    @property
    def upstream_env_file(self) -> Path:
        return self.upstream_home / ".env"

    def ensure_wrapper_dirs(self) -> None:
        self.ledger_db.parent.mkdir(parents=True, exist_ok=True)
        self.log_jsonl.parent.mkdir(parents=True, exist_ok=True)
