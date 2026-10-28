#!/usr/bin/env bash
# Download and install a verified version of Terraform in CI environments.
# See docs/pseudo_code.md section 8.7 and docs/plan.md section 12.

set -euo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

V="${1:?usage: $(basename "$0") <version>}"
INSTALL_DIR="${INSTALL_DIR:-/usr/local/bin}"

require_cmd curl grep sha256sum unzip

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

log "Downloading Terraform $V and SHA256SUMS..."
curl -fsSLo "$TMP_DIR/tf.zip" "https://releases.hashicorp.com/terraform/$V/terraform_${V}_linux_amd64.zip"
curl -fsSLo "$TMP_DIR/SUMS" "https://releases.hashicorp.com/terraform/$V/terraform_${V}_SHA256SUMS"

log "Verifying checksum..."
(cd "$TMP_DIR" && grep "terraform_${V}_linux_amd64.zip" SUMS | sha256sum -c -)

log "Installing terraform into $INSTALL_DIR..."
unzip -o "$TMP_DIR/tf.zip" -d "$INSTALL_DIR"

terraform version
log "Terraform $V installation verified"
