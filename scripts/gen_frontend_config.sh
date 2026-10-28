#!/usr/bin/env bash
# Generate frontend configuration JSON from Terraform outputs.
# See docs/pseudo_code.md section 8.4 and docs/plan.md section 12.

set -euo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

ENV="${1:?usage: $(basename "$0") <env> [--local]}"
MODE="${2:-}"

require_cmd jq terraform

LOCAL=""
TARGET="$ROOT/frontend/dist"

if [ "$MODE" = "--local" ]; then
  LOCAL="true"
  TARGET="$ROOT/frontend/public"
fi

mkdir -p "$TARGET"

REGION="$(tf_output "$ENV" region)"
POOL_ID="$(tf_output "$ENV" user_pool_id)"
CLIENT_ID="$(tf_output "$ENV" app_client_id)"
DOMAIN="$(tf_output "$ENV" cognito_domain_url)"

if [ -n "$LOCAL" ]; then
  REDIRECT="http://localhost:5173/"
else
  SITE_URL="$(tf_output "$ENV" site_url)"
  REDIRECT="${SITE_URL%/}/"
fi

log "Generating frontend config for $ENV ($TARGET/config.json)"

jq -n \
  --arg region "$REGION" \
  --arg pool "$POOL_ID" \
  --arg client "$CLIENT_ID" \
  --arg domain "$DOMAIN" \
  --arg redirect "$REDIRECT" \
  '{region: $region, userPoolId: $pool, clientId: $client, cognitoDomain: $domain, redirectUri: $redirect}' \
  >"$TARGET/config.json"

log "Generated $TARGET/config.json"
