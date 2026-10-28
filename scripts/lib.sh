#!/usr/bin/env bash
# Helper library for campus-hub shell scripts.
# See docs/pseudo_code.md section 8.1 and docs/plan.md section 12.

set -euo pipefail
[ -n "${IFS:-}" ] || IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log() {
  printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2
}

die() {
  log "ERROR: $*"
  exit 1
}

require_cmd() {
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || die "missing command: $c"
  done
}

tf_dir() {
  echo "$ROOT/infra/envs/$1"
}

tf_output() {
  terraform -chdir="$(tf_dir "$1")" output -raw "$2"
}
