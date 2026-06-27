#!/usr/bin/env bash
set -euo pipefail
cat <<'EOF'
AEGIS does not require a separate Open WebUI bootstrap for the local control panel.
Use the native dashboard instead:
  aegis ui
  aegis dashboard --host 127.0.0.1 --port 8000
EOF
