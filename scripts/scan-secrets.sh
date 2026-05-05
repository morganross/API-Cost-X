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
tmp_hit="${TMPDIR:-/tmp}/api-cost-x-scan-hit.$$"
trap 'rm -f "$tmp_hit"' EXIT

example_filter='(^|[^A-Za-z0-9])(example|placeholder|changeme|change-me|replace-me|your[_-]?|dummy|sample|fake|not[-_ ]?real|redacted|xxx|xxxx|<[^>]+>|\.\.\.)([^A-Za-z0-9]|$)'

echo "== file denylist =="
denylist_hits=()
for file in "${candidate_files[@]}"; do
  case "$file" in
    .env|.env.*|*.env|*.pem|*.key|*.p12|*.pfx|*.db|*.sqlite|*.sqlite3|*.bak|*.bak*|.git-credentials|.netrc|.npmrc|.pypirc|.aws|.aws/*|.oci|.oci/*|.azure|.azure/*|.gcloud|.gcloud/*|.config/gcloud|.config/gcloud/*|.config/gh|.config/gh/*)
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
  'gh[pousr]_[A-Za-z0-9_]{20,}'
  'github_pat_[A-Za-z0-9_]{20,}'
  'x-access-token:[A-Za-z0-9_:-]{20,}'
  'AIza[0-9A-Za-z_-]{35}'
  '(A3T[A-Z0-9]|AKIA|ASIA)[A-Z0-9]{16}'
  'npm_[A-Za-z0-9]{36}'
  '//registry\.npmjs\.org/:_authToken=[A-Za-z0-9_-]{20,}'
  'pypi-[A-Za-z0-9_-]{50,}'
  '(CF_API_TOKEN|CF_AUTH_KEY|CLOUDFLARE_API_TOKEN|CLOUDFLARE_AUTH_KEY)[^A-Za-z0-9]{0,20}[A-Za-z0-9_-]{20,}'
  "(api[_-]?token|auth[_-]?token|secret[_-]?key|client[_-]?secret)[\"' ]*[:=][\"' ]*[A-Za-z0-9_./+=-]{24,}"
  'BEGIN (RSA |OPENSSH |EC |DSA |PGP |ENCRYPTED )?PRIVATE KEY'
  'BEGIN PRIVATE KEY'
  'BEGIN OCI API KEY'
  'private_key_file[[:space:]]*='
  'key_file[[:space:]]*='
  'tenancy[[:space:]]*=[[:space:]]*ocid1\.'
  'user[[:space:]]*=[[:space:]]*ocid1\.user'
)

content_hits=0
for file in "${candidate_files[@]}"; do
  [[ -f "$file" ]] || continue
  case "$file" in
    scripts/scan-secrets.sh|scripts/check-base-database.py)
      continue
      ;;
  esac
  is_binary "$file" || continue
  for pattern in "${content_patterns[@]}"; do
    if grep -En "$pattern" "$file" >"$tmp_hit" 2>/dev/null; then
      if grep -Eiv "$example_filter" "$tmp_hit" | sed "s#^#$file:#"; then
        content_hits=1
      fi
      : >"$tmp_hit"
    fi
  done
done

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
    .env.example|docs/public-copy-policy.md|scripts/scan-secrets.sh|scripts/check-base-database.py)
      continue
      ;;
  esac
  for pattern in "${advisory_patterns[@]}"; do
    grep -En "$pattern" "$file" 2>/dev/null | sed "s#^#$file:#" || true
  done
done

echo "== external scanners =="
if command -v gitleaks >/dev/null 2>&1; then
  gitleaks detect --redact --source .
else
  if [[ "${CI:-}" == "true" ]]; then
    echo "gitleaks is required in CI"
    exit 1
  fi
  echo "gitleaks not installed; install it for git-history scanning"
fi

if command -v trufflehog >/dev/null 2>&1; then
  trufflehog git "file://$ROOT" --no-update --fail --only-verified
else
  if [[ "${CI:-}" == "true" ]]; then
    echo "trufflehog is required in CI"
    exit 1
  fi
  echo "trufflehog not installed; install it for git-history scanning"
fi

echo "secret scan passed"
