#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
exec "$root/aegis/builtin_skills/creative/comfyui/scripts/comfyui_setup.sh" "$@"
