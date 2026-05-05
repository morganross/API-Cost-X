#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

sqlite_database_path() {
  local db_url="${API_COST_X_DATABASE_URL:-${DATABASE_URL:-sqlite+aiosqlite:///./data/api-cost-x.db}}"
  case "$db_url" in
    sqlite+aiosqlite:///*)
      printf '%s\n' "${db_url#sqlite+aiosqlite:///}"
      ;;
    sqlite:///*)
      printf '%s\n' "${db_url#sqlite:///}"
      ;;
    *)
      echo "Only SQLite database URLs are supported in self-hosted mode: $db_url" >&2
      exit 1
      ;;
  esac
}

abspath_from_root() {
  local value="$1"
  case "$value" in
    /*) printf '%s\n' "$value" ;;
    *) printf '%s\n' "$ROOT/$value" ;;
  esac
}

copy_seed_artifacts() {
  local source_root="$1"
  local target_root="$2"

  mkdir -p "$target_root"
  (
    cd "$source_root"
    find . -type d -exec sh -c '
      target_root="$1"
      shift
      for source_dir do
        rel_path="${source_dir#./}"
        mkdir -p "$target_root/$rel_path"
      done
    ' sh "$target_root" {} +
    find . -type f -exec sh -c '
      target_root="$1"
      shift
      for source_path do
        rel_path="${source_path#./}"
        target_path="$target_root/$rel_path"
        if [ ! -e "$target_path" ]; then
          mkdir -p "$(dirname "$target_path")"
          cp "$source_path" "$target_path"
        fi
      done
    ' sh "$target_root" {} +
  )
}

main_db="$(abspath_from_root "$(sqlite_database_path)")"
data_dir="$(abspath_from_root "${API_COST_X_DATA_DIR:-./data}")"
base_db="$ROOT/api/app/seed/api-cost-x.seed.db"
base_artifacts="$ROOT/api/app/seed/artifacts"

mkdir -p "$(dirname "$main_db")" "$data_dir"

if [[ -s "$main_db" ]]; then
  echo "Main SQLite database already exists: $main_db"
elif [[ -f "$base_db" ]]; then
  cp "$base_db" "$main_db"
  chmod 600 "$main_db" 2>/dev/null || true
  echo "Initialized main SQLite database from bundled base DB: $main_db"
else
  echo "No bundled base DB found; the API service will create an empty SQLite database."
fi

if [[ -d "$base_artifacts/user_local" ]]; then
  copy_seed_artifacts "$base_artifacts/user_local" "$data_dir/user_local"
  echo "Installed bundled sample artifacts under: $data_dir/user_local"
fi
