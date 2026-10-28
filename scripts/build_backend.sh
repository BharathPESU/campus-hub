#!/usr/bin/env bash
# Build backend Lambda deployment package.
# See docs/pseudo_code.md section 8.2 and docs/plan.md section 12.

set -euo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

require_cmd python3 pip find

OUT="$ROOT/build/lambda/package"
log "Building Lambda package in $OUT"

rm -rf "$OUT"
mkdir -p "$OUT"

pip install -r "$ROOT/backend/requirements.txt" --target "$OUT" --upgrade \
  --platform manylinux2014_aarch64 --implementation cp --python-version 3.12 --only-binary=:all:

# Do not bundle boto3 or botocore (provided by Lambda runtime)
rm -rf "$OUT/boto3" "$OUT/boto3-"* "$OUT/botocore" "$OUT/botocore-"*

cp -R "$ROOT/backend/src/." "$OUT/"
find "$OUT" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$OUT" -exec touch -d '2020-01-01 00:00:00' {} +

log "built $OUT"
