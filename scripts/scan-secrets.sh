#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mapfile -t candidate_files < <(git ls-files --cached --others --exclude-standard)

if [[ "${#candidate_files[@]}" -eq 0 ]]; then
  echo "No tracked or unignored files to scan"
  exit 0
fi

is_binary() {
  local file="$1"
  grep -Iq . "$file"
}

allowed_bundled_db="api/app/seed/api-cost-x.seed.db"

echo "== file denylist =="
denylist_hits=()
for file in "${candidate_files[@]}"; do
  case "$file" in
    .env|.env.*|*.env|*.pem|*.key|*.p12|*.pfx|*.db|*.sqlite|*.sqlite3|*.bak|*.bak*|.git-credentials|.netrc)
      if [[ "$file" != ".env.example" && "$file" != "$allowed_bundled_db" ]]; then
        denylist_hits+=("$file")
      fi
      ;;
  esac
done

if [[ "${#denylist_hits[@]}" -gt 0 ]]; then
  printf '%s
' "${denylist_hits[@]}"
  echo "Denied files are present."
  exit 1
fi

echo "== content denylist =="
content_patterns=(
  'ghp_[A-Za-z0-9_]+'
  'github_pat_[A-Za-z0-9_]+'
  'x-access-token'
  'sk-[A-Za-z0-9][A-Za-z0-9_-]{20,}'
  'BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY'
)

content_hits=0
for file in "${candidate_files[@]}"; do
  [[ -f "$file" ]] || continue
  [[ "$file" != "scripts/scan-secrets.sh" ]] || continue
  is_binary "$file" || continue
  for pattern in "${content_patterns[@]}"; do
    if grep -En "$pattern" "$file" >/tmp/api-cost-x-scan-hit 2>/dev/null; then
      sed "s#^#$file:#" /tmp/api-cost-x-scan-hit
      content_hits=1
    fi
  done
done
rm -f /tmp/api-cost-x-scan-hit

if [[ "$content_hits" -ne 0 ]]; then
  echo "Denied content patterns were found."
  exit 1
fi

echo "== advisory project-term review =="
advisory_patterns=(
  'APICostX_PLUGIN_SECRET'
  'PLUGIN_SECRET'
  'SESSION_TOKEN'
  'DB_KEY'
  'SQLCIPHER'
  'OPENAI_API_KEY'
  'ANTHROPIC_API_KEY'
  'OPENROUTER_API_KEY'
  'TAVILY_API_KEY'
  'PERPLEXITY_API_KEY'
  'CLOUDFLARE'
  'OCI_'
)

for file in "${candidate_files[@]}"; do
  [[ -f "$file" ]] || continue
  is_binary "$file" || continue
  case "$file" in
    .env.example|docs/public-copy-policy.md|scripts/scan-secrets.sh)
      continue
      ;;
  esac
  for pattern in "${advisory_patterns[@]}"; do
    grep -En "$pattern" "$file" 2>/dev/null | sed "s#^#$file:#" || true
  done
done

echo "== optional scanners =="
if command -v gitleaks >/dev/null 2>&1; then
  gitleaks detect --no-git --redact --source .
else
  echo "gitleaks not installed; skipped"
fi

if command -v trufflehog >/dev/null 2>&1; then
  trufflehog filesystem --no-update --fail --only-verified .
else
  echo "trufflehog not installed; skipped"
fi

echo "secret scan passed"
