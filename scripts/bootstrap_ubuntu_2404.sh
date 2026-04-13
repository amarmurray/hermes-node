#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -f "${REPO_ROOT}/.env" ]]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.env"
fi

UPSTREAM_HOME="${HERMES_NODE_UPSTREAM_HOME:-$HOME/.hermes}"
UPSTREAM_REPO="${HERMES_NODE_UPSTREAM_REPO:-$UPSTREAM_HOME/hermes-agent}"
UPSTREAM_REF="${HERMES_NODE_UPSTREAM_REF:-main}"
UPSTREAM_INSTALL_SPEC="${HERMES_NODE_UPSTREAM_INSTALL_SPEC:-.[messaging]}"
HERMES_BIN="${HERMES_NODE_HERMES_BIN:-$HOME/.local/bin/hermes}"
ENABLE_API_SERVER="${HERMES_NODE_ENABLE_API_SERVER:-true}"
API_BASE_URL="${HERMES_NODE_API_BASE_URL:-http://127.0.0.1:8642/v1}"
API_KEY="${HERMES_NODE_API_KEY:-change-me-local-dev}"

API_HOST="$(python3 - "${API_BASE_URL}" <<'PY'
from urllib.parse import urlparse
import sys
url = urlparse(sys.argv[1])
print(url.hostname or "127.0.0.1")
PY
)"
API_PORT="$(python3 - "${API_BASE_URL}" <<'PY'
from urllib.parse import urlparse
import sys
url = urlparse(sys.argv[1])
print(url.port or 8642)
PY
)"

echo "== Hermes Node bootstrap =="
echo "repo root:      ${REPO_ROOT}"
echo "upstream home:  ${UPSTREAM_HOME}"
echo "upstream repo:  ${UPSTREAM_REPO}"
echo "install spec:   ${UPSTREAM_INSTALL_SPEC}"

sudo apt-get update -y
sudo apt-get install -y git curl ca-certificates build-essential ffmpeg ripgrep python3 python3-venv

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

export PATH="$HOME/.local/bin:$PATH"
export HERMES_HOME="${UPSTREAM_HOME}"

mkdir -p "${UPSTREAM_HOME}"

if [[ -d "${UPSTREAM_REPO}/.git" ]]; then
  git -C "${UPSTREAM_REPO}" fetch origin
  git -C "${UPSTREAM_REPO}" checkout "${UPSTREAM_REF}"
  git -C "${UPSTREAM_REPO}" pull --ff-only origin "${UPSTREAM_REF}"
  git -C "${UPSTREAM_REPO}" submodule update --init --recursive
else
  git clone --recurse-submodules https://github.com/NousResearch/hermes-agent.git "${UPSTREAM_REPO}"
  git -C "${UPSTREAM_REPO}" checkout "${UPSTREAM_REF}"
fi

cd "${UPSTREAM_REPO}"
uv venv venv --python 3.11
export VIRTUAL_ENV="${UPSTREAM_REPO}/venv"
uv pip install -e "${UPSTREAM_INSTALL_SPEC}"

mkdir -p "${UPSTREAM_HOME}"/{cron,sessions,logs,memories,skills,pairing,hooks,image_cache,audio_cache,whatsapp/session}

if [[ ! -f "${UPSTREAM_HOME}/config.yaml" ]]; then
  cp "${UPSTREAM_REPO}/cli-config.yaml.example" "${UPSTREAM_HOME}/config.yaml"
fi

if [[ ! -f "${UPSTREAM_HOME}/.env" ]]; then
  touch "${UPSTREAM_HOME}/.env"
fi

if [[ ! -f "${UPSTREAM_HOME}/SOUL.md" ]]; then
  cat > "${UPSTREAM_HOME}/SOUL.md" <<'EOF'
# Hermes Agent Persona

You are the upstream Hermes runtime running privately on an Ubuntu VPS.
EOF
fi

mkdir -p "$HOME/.local/bin"
ln -sf "${UPSTREAM_REPO}/venv/bin/hermes" "${HERMES_BIN}"

set_env_value() {
  local key="$1"
  local value="$2"
  local file="$3"
  if grep -q "^${key}=" "${file}"; then
    python3 - "$key" "$value" "$file" <<'PY'
from pathlib import Path
import sys
key, value, file_path = sys.argv[1], sys.argv[2], sys.argv[3]
path = Path(file_path)
lines = path.read_text(encoding="utf-8").splitlines()
updated = []
replaced = False
for line in lines:
    if line.startswith(f"{key}="):
        updated.append(f"{key}={value}")
        replaced = True
    else:
        updated.append(line)
if not replaced:
    updated.append(f"{key}={value}")
path.write_text("\n".join(updated) + "\n", encoding="utf-8")
PY
  else
    printf "%s=%s\n" "${key}" "${value}" >> "${file}"
  fi
}

if [[ "${ENABLE_API_SERVER}" == "true" ]]; then
  set_env_value "API_SERVER_ENABLED" "true" "${UPSTREAM_HOME}/.env"
  set_env_value "API_SERVER_HOST" "${API_HOST}" "${UPSTREAM_HOME}/.env"
  set_env_value "API_SERVER_PORT" "${API_PORT}" "${UPSTREAM_HOME}/.env"
  set_env_value "API_SERVER_KEY" "${API_KEY}" "${UPSTREAM_HOME}/.env"
fi

if [[ ! -f "${REPO_ROOT}/.env" ]]; then
  cp "${REPO_ROOT}/.env.example" "${REPO_ROOT}/.env"
fi

"${HERMES_BIN}" version
"${HERMES_BIN}" status || true
"${HERMES_BIN}" doctor || true

echo
echo "Bootstrap complete."
echo "Next steps:"
echo "  1. Edit upstream keys: ${UPSTREAM_HOME}/.env"
echo "  2. Smoke check:        ${REPO_ROOT}/bin/hermes-node smoke"
echo "  3. User service:       ${REPO_ROOT}/bin/hermes-node service install"
echo "  4. Start service:      ${REPO_ROOT}/bin/hermes-node service start"
echo "  5. API health:         ${REPO_ROOT}/bin/hermes-node api-health"
