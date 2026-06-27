#!/usr/bin/env bash
# Compatibility wrapper for tooling that expects the installer under scripts/.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/../install.sh" "$@"
