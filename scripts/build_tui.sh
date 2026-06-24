#!/usr/bin/env bash
# Build the AEGIS Node/Ink terminal UI bundle.
#
# pip installs ship the prebuilt bundle (aegis/tui_ink/dist/entry.js), so end users do not
# need Node at install time — only `node` on PATH at runtime. Run this when you change the
# Ink client (aegis/tui_ink/src) to regenerate the bundle and its third-party license file.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ink_dir="$here/aegis/tui_ink"

cd "$ink_dir"
echo "==> npm install ($ink_dir)"
npm install --no-audit --no-fund
echo "==> typecheck"
npm run typecheck
echo "==> bundle"
npm run build
echo "==> built:"
ls -la dist/
echo "Done. The bundle (dist/entry.js) and dist/entry.js.LEGAL.txt are committed to the repo."
