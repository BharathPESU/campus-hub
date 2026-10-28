#!/usr/bin/env bash
# Tear down deployed environment resources.
# See docs/pseudo_code.md section 8.6 and docs/plan.md section 12.

set -euo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

ENV="${1:?usage: $(basename "$0") <env>}"

require_cmd terraform aws

read -rp "Type the environment name to confirm destroy: " ans
[ "$ans" = "$ENV" ] || die "aborted"

BUCKET="$(tf_output "$ENV" frontend_bucket)"
log "Emptying S3 bucket s3://$BUCKET"
aws s3 rm "s3://$BUCKET" --recursive

log "Running terraform destroy for $ENV"
terraform -chdir="$(tf_dir "$ENV")" destroy

log "Verifying reminder schedules group"
aws scheduler list-schedules --group-name "campus-hub-$ENV-reminders" || true

log "Environment $ENV destroyed"
