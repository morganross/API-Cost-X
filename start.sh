#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Missing .env. Run ./install.sh first."
  exit 1
fi

set -a
. ./.env
set +a

if [[ -n "${API_COST_X_DATABASE_URL:-}" && -z "${DATABASE_URL:-}" ]]; then
  export DATABASE_URL="$API_COST_X_DATABASE_URL"
fi

scripts/initialize-database.sh

api_port="${API_COST_X_API_PORT:-8000}"
web_port="${API_COST_X_WEB_PORT:-5173}"

if [[ ! -d api || ! -d web-gui ]]; then
  echo "Application code has not been migrated yet. Expected api/ and web-gui/."
  echo "API target port: $api_port"
  echo "Web target port: $web_port"
  exit 2
fi

if [[ ! -d .venv ]]; then
  echo "Missing .venv. Run ./install.sh first."
  exit 1
fi

. .venv/bin/activate

app_host="${API_COST_X_HOST:-127.0.0.1}"
case "$app_host" in
  localhost|127.0.0.1|::1)
    ;;
  *)
    if [[ "${API_COST_X_ALLOW_UNSAFE_BIND:-}" != "1" ]]; then
      echo "Refusing to bind APICostX to non-localhost host '$app_host'."
      echo "Set API_COST_X_ALLOW_UNSAFE_BIND=1 only if you understand this exposes the app on the network."
      exit 1
    fi
    ;;
esac

python -m uvicorn app.main:app --app-dir api --host "$app_host" --port "$api_port" &
api_pid=$!

(cd web-gui && npm run dev -- --host "$app_host" --port "$web_port") &
web_pid=$!

trap 'kill "$api_pid" "$web_pid" 2>/dev/null || true' EXIT
wait
