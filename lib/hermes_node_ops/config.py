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


@dataclass
class Settings:
    repo_root: Path
    env_file: Path
    upstream_home: Path
    upstream_repo: Path
    hermes_bin: Path
    log_jsonl: Path
    upstream_install_spec: str
    upstream_ref: str
    systemd_scope: str
    service_user: str
    enable_api_server: bool
    api_base_url: str
    api_key: str
    default_provider: str
    default_model: str

    @classmethod
    def load(cls, repo_root: Path | None = None) -> "Settings":
        root = repo_root.resolve() if repo_root else Path.cwd().resolve()
        env_file = root / ".env"
        file_values = read_env_file(env_file)
        merged = {**file_values, **os.environ}

        upstream_home = _resolve(_get(merged, "HERMES_NODE_UPSTREAM_HOME", "~/.hermes"), root)
        upstream_repo = _resolve(_get(merged, "HERMES_NODE_UPSTREAM_REPO", str(upstream_home / "hermes-agent")), root)
        hermes_bin = _resolve(_get(merged, "HERMES_NODE_HERMES_BIN", "~/.local/bin/hermes"), root)
        log_jsonl = _resolve(_get(merged, "HERMES_NODE_LOG_JSONL", "logs/hermes-node.jsonl"), root)
        return cls(
            repo_root=root,
            env_file=env_file,
            upstream_home=upstream_home,
            upstream_repo=upstream_repo,
            hermes_bin=hermes_bin,
            log_jsonl=log_jsonl,
            upstream_install_spec=_get(merged, "HERMES_NODE_UPSTREAM_INSTALL_SPEC", ".[messaging]"),
            upstream_ref=_get(merged, "HERMES_NODE_UPSTREAM_REF", "main"),
            systemd_scope=_get(merged, "HERMES_NODE_SYSTEMD_SCOPE", "user").strip().lower(),
            service_user=_get(merged, "HERMES_NODE_SERVICE_USER", "ubuntu"),
            enable_api_server=_get(merged, "HERMES_NODE_ENABLE_API_SERVER", "true").lower() in {"1", "true", "yes", "on"},
            api_base_url=_get(merged, "HERMES_NODE_API_BASE_URL", "http://127.0.0.1:8642/v1"),
            api_key=_get(merged, "HERMES_NODE_API_KEY", "change-me-local-dev"),
            default_provider=_get(merged, "HERMES_NODE_DEFAULT_PROVIDER", ""),
            default_model=_get(merged, "HERMES_NODE_DEFAULT_MODEL", ""),
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
        self.log_jsonl.parent.mkdir(parents=True, exist_ok=True)
