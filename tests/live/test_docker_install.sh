
#!/usr/bin/env bash
set -euo pipefail
if [[ "${AEGIS_LIVE_DOCKER:-}" != "1" ]]; then
  echo "skipped: set AEGIS_LIVE_DOCKER=1 on a Docker runner"
  exit 0
fi
test -f scripts/verify_all.sh
test -f pyproject.toml
echo "docker install runner preflight ok"
