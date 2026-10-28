#!/usr/bin/env bash
# Build frontend web application.
# See docs/pseudo_code.md section 8.3 and docs/plan.md section 12.

set -euo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

require_cmd npm

log "Building frontend in $ROOT/frontend"
cd "$ROOT/frontend"

if [ -f "package-lock.json" ]; then
  npm ci
else
  npm install
fi

npm run build
log "built frontend/dist/"
