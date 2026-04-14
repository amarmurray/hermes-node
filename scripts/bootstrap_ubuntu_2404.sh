#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -f "${REPO_ROOT}/.env" ]]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.env"
fi

UPSTREAM_HOME="${HERMES_NODE_UPSTREAM_HOME:-$HOME/.hermes}"
UPSTREAM_REPO="${HERMES_NODE_UPSTREAM_REPO:-$HOME/apps/hermes-agent}"
HERMES_BIN="${HERMES_NODE_HERMES_BIN:-$HOME/.local/bin/hermes}"
DB_PATH="${HERMES_NODE_DB_PATH:-${REPO_ROOT}/state/hermes-node.db}"
LOG_PATH="${HERMES_NODE_LOG_JSONL:-${REPO_ROOT}/logs/hermes-node.jsonl}"
INSTALL_SYSTEMD="${HERMES_NODE_INSTALL_SYSTEMD:-false}"
SYSTEMD_UNIT_NAME="${HERMES_NODE_SYSTEMD_UNIT_NAME:-hermes-node.service}"
SYSTEMD_UNIT_SOURCE="${REPO_ROOT}/systemd/hermes-node.service"
SYSTEMD_UNIT_TARGET="/etc/systemd/system/${SYSTEMD_UNIT_NAME}"

echo "== hermes-node wrapper bootstrap =="
echo "repo root:      ${REPO_ROOT}"
echo "upstream home:  ${UPSTREAM_HOME}"
echo "upstream repo:  ${UPSTREAM_REPO}"
echo "hermes binary:  ${HERMES_BIN}"
echo "ledger db:      ${DB_PATH}"
echo "wrapper logs:   ${LOG_PATH}"

sudo apt-get update -y
sudo apt-get install -y python3 sqlite3 ripgrep

if [[ ! -f "${REPO_ROOT}/.env" ]]; then
  cp "${REPO_ROOT}/.env.example" "${REPO_ROOT}/.env"
fi

mkdir -p "$(dirname "${DB_PATH}")"
mkdir -p "$(dirname "${LOG_PATH}")"
mkdir -p "${HOME}/.local/bin"

if [[ ! -x "${HERMES_BIN}" ]]; then
  echo "Missing upstream Hermes binary at ${HERMES_BIN}" >&2
  exit 1
fi

export HERMES_HOME="${UPSTREAM_HOME}"

ln -sf "${REPO_ROOT}/bin/hermes-node" "${HOME}/.local/bin/hermes-node"
ln -sf "${REPO_ROOT}/bin/hermes-operator" "${HOME}/.local/bin/hermes-operator"

"${HERMES_BIN}" version
"${REPO_ROOT}/bin/hermes-node" status || true

if [[ "${INSTALL_SYSTEMD}" == "true" ]]; then
  sudo install -m 0644 "${SYSTEMD_UNIT_SOURCE}" "${SYSTEMD_UNIT_TARGET}"
  sudo systemctl daemon-reload
  echo "Installed ${SYSTEMD_UNIT_TARGET}"
fi

echo
echo "Bootstrap complete."
echo "Next steps:"
echo "  1. Verify upstream:      ${HERMES_BIN} status"
echo "  2. Submit a task:        ${REPO_ROOT}/bin/hermes-node submit --kind summary --query 'Reply with exactly: ALIVE'"
echo "  3. Run one task:         ${REPO_ROOT}/bin/hermes-node run"
echo "  4. Review ledger:        ${REPO_ROOT}/bin/hermes-node status"
echo "  5. Start operator:       ${REPO_ROOT}/bin/hermes-operator chat"
echo "  6. Install service:      sudo install -m 0644 ${SYSTEMD_UNIT_SOURCE} ${SYSTEMD_UNIT_TARGET}"
