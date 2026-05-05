#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  echo "Missing .venv. Run ./install.sh first."
  exit 1
fi

. .venv/bin/activate

echo "== api health =="
python -m uvicorn app.main:app --app-dir api --host 127.0.0.1 --port 8000 >/tmp/api-cost-x-uvicorn.log 2>&1 &
api_pid=$!
cleanup() {
  kill "$api_pid" 2>/dev/null || true
  wait "$api_pid" 2>/dev/null || true
}
trap cleanup EXIT

for _ in 1 2 3 4 5 6 7 8; do
  if curl -fsS http://127.0.0.1:8000/api/health 2>/dev/null; then
    echo
    break
  fi
  sleep 1
done

if ! curl -fsS http://127.0.0.1:8000/api/health/safe-to-restart >/dev/null; then
  echo "API restart-safety smoke check failed"
  tail -80 /tmp/api-cost-x-uvicorn.log || true
  exit 1
fi

cleanup
trap - EXIT

echo "== web-gui build =="
(cd web-gui && npm run build)

echo "smoke checks passed"
