#!/usr/bin/env bash
# Run HTTP smoke tests against deployed environment.
# See docs/pseudo_code.md section 8.5b and docs/plan.md section 12.

set -euo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

ENV="${1:?usage: $(basename "$0") <env>}"

require_cmd curl jq grep terraform

BASE="$(tf_output "$ENV" site_url)"
BASE="${BASE%/}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
BODY_FILE="$TMP_DIR/body"

check() {
  local expected="$1"
  local method="$2"
  local path="$3"
  local jq_filter="${4:-}"
  local code=""

  log "Checking $method $path (expected: $expected)..."
  for attempt in {1..10}; do
    code="$(curl -s -o "$BODY_FILE" -w '%{http_code}' -X "$method" "$BASE$path")"
    if [ "$code" = "$expected" ]; then
      break
    fi
    log "Attempt $attempt/10: got $code, retrying in 5s..."
    sleep 5
  done

  [ "$code" = "$expected" ] || die "$method $path expected $expected got $code"

  if [ -n "$jq_filter" ]; then
    jq -e "$jq_filter" "$BODY_FILE" >/dev/null 2>&1 || die "bad body for $path: $(cat "$BODY_FILE")"
  fi
}

log "Running smoke tests against $BASE"

check 200 GET "/"
grep -qi '<html' "$BODY_FILE" || die "expected HTML content at /"

check 200 GET "/api/health" '.status == "ok"'
check 200 GET "/api/events" '.events | type == "array"'
check 401 POST "/api/events"
check 401 GET "/api/me/registrations"

log "smoke OK"
