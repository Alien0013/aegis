#!/usr/bin/env bash
# Remove the AEGIS install (keeps ~/.aegis data unless --purge is passed).
set -euo pipefail
INSTALL_DIR="${AEGIS_INSTALL_DIR:-$HOME/.aegis/venv}"
BIN_DIR="${AEGIS_BIN_DIR:-$HOME/.local/bin}"

rm -f "$BIN_DIR/aegis" && echo "removed launcher $BIN_DIR/aegis" || true
rm -rf "$INSTALL_DIR" && echo "removed venv $INSTALL_DIR" || true
if [ "${1:-}" = "--purge" ]; then
  rm -rf "$HOME/.aegis"
  echo "purged ~/.aegis (config, sessions, memory, skills)"
else
  echo "kept ~/.aegis data — pass --purge to delete config/sessions/memory/skills."
fi
