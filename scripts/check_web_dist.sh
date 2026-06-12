#!/usr/bin/env bash
# Verify the committed dashboard bundle matches the React/Vite source.
set -euo pipefail
cd "$(dirname "$0")/.."

scripts/build_web.sh

if [ -n "$(git status --porcelain -- aegis/static/web_dist)" ]; then
  echo "dashboard bundle is stale; run scripts/build_web.sh and commit the result" >&2
  git status --short -- aegis/static/web_dist >&2
  git diff --stat -- aegis/static/web_dist >&2
  exit 1
fi

echo "✓ dashboard bundle matches web/"
