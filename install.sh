#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

NODE_MAJOR_REQUIRED=20

node_major_version() {
  if command -v node >/dev/null 2>&1; then
    node -p "Number.parseInt(process.versions.node.split('.')[0], 10)" 2>/dev/null || echo 0
  else
    echo 0
  fi
}

install_node_20_apt() {
  local current_major
  current_major="$(node_major_version)"
  if (( current_major >= NODE_MAJOR_REQUIRED )); then
    return
  fi

  echo "Installing Node.js ${NODE_MAJOR_REQUIRED}.x from NodeSource"
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl gnupg
  sudo install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | sudo gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg
  sudo chmod 0644 /etc/apt/keyrings/nodesource.gpg
  echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR_REQUIRED}.x nodistro main" | sudo tee /etc/apt/sources.list.d/nodesource.list >/dev/null
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
}

install_system_deps() {
  if [[ "${API_COST_X_SKIP_SYSTEM_DEPS:-}" == "1" ]]; then
    echo "Skipping system dependency install because API_COST_X_SKIP_SYSTEM_DEPS=1"
    return
  fi

  if [[ "$(uname -s)" != "Linux" ]]; then
    echo "Non-Linux host detected; install Python 3, venv support, Node.js ${NODE_MAJOR_REQUIRED}+ and npm manually."
    return
  fi

  if command -v apt-get >/dev/null 2>&1; then
    echo "Ensuring apt-based system dependencies are present"
    sudo apt-get update
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv
    install_node_20_apt
  else
    echo "No apt-get found; install Python 3 venv support, Node.js ${NODE_MAJOR_REQUIRED}+ and npm with your OS package manager."
  fi
}

ensure_python() {
  if command -v python3 >/dev/null 2>&1; then
    echo "python: $(python3 --version)"
  else
    echo "python3 is required"
    exit 1
  fi
}

ensure_node() {
  if command -v node >/dev/null 2>&1; then
    echo "node: $(node --version)"
  else
    echo "node is required for the web-gui"
    exit 1
  fi

  local node_major
  node_major="$(node_major_version)"
  if (( node_major < NODE_MAJOR_REQUIRED )); then
    echo "Node.js ${NODE_MAJOR_REQUIRED}+ is required for the web GUI; found $(node --version). Re-run ./install.sh without API_COST_X_SKIP_SYSTEM_DEPS=1 or upgrade Node.js."
    exit 1
  fi

  if command -v npm >/dev/null 2>&1; then
    echo "npm: $(npm --version)"
  else
    echo "npm is required for the web-gui"
    exit 1
  fi
}

prepare_env() {
  mkdir -p data logs

  if [[ ! -f .env ]]; then
    cp .env.example .env
    echo "Created .env from .env.example"
  else
    echo ".env already exists"
  fi
}

install_api() {
  if [[ ! -d api ]]; then
    echo "api/ not present yet; api install skipped"
    return
  fi

  if [[ -d .venv && ! -x .venv/bin/python ]]; then
    echo "Removing incomplete .venv"
    rm -rf .venv
  fi

  python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip

  if [[ -f api/pyproject.toml ]]; then
    python -m pip install -e ./api
  fi
}

install_web_gui() {
  if [[ -d web-gui && -f web-gui/package.json ]]; then
    (cd web-gui && npm install)
  else
    echo "web-gui/ not present yet; web-gui install skipped"
  fi
}

install_system_deps
ensure_python
ensure_node
prepare_env
scripts/initialize-database.sh
install_api
install_web_gui

echo "Install complete"
