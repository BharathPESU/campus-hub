#!/usr/bin/env bash
# Deploy built frontend assets to S3 and invalidate CloudFront distribution cache.
# See docs/pseudo_code.md section 8.5 and docs/plan.md section 12.

set -euo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

ENV="${1:?usage: $(basename "$0") <env>}"

require_cmd aws terraform

BUCKET="$(tf_output "$ENV" frontend_bucket)"
DIST="$(tf_output "$ENV" distribution_id)"

log "Syncing static assets to s3://$BUCKET"
aws s3 sync "$ROOT/frontend/dist" "s3://$BUCKET" --delete \
  --exclude "index.html" --exclude "config.json" \
  --cache-control "public,max-age=31536000,immutable"

log "Uploading index.html and config.json to s3://$BUCKET with no-cache"
aws s3 cp "$ROOT/frontend/dist/index.html" "s3://$BUCKET/index.html" \
  --cache-control "no-cache" --content-type "text/html"
aws s3 cp "$ROOT/frontend/dist/config.json" "s3://$BUCKET/config.json" \
  --cache-control "no-cache" --content-type "application/json"

log "Creating CloudFront invalidation for $DIST"
ID="$(aws cloudfront create-invalidation --distribution-id "$DIST" --paths "/index.html" "/config.json" --query "Invalidation.Id" --output text)"

log "Waiting for invalidation $ID to complete..."
aws cloudfront wait invalidation-completed --distribution-id "$DIST" --id "$ID"

log "Frontend deployment completed successfully"
