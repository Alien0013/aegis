
#!/usr/bin/env bash
set -euo pipefail
if [[ "${AEGIS_LIVE_DESKTOP_MACOS:-}" != "1" ]]; then
  echo "skipped: set AEGIS_LIVE_DESKTOP_MACOS=1 on a macOS desktop runner"
  exit 0
fi
test -f desktop/package.json
test -f desktop/electron/main.js
echo "macOS desktop runner preflight ok"
