#!/usr/bin/env bash
# Build the React dashboard into aegis/static/web_dist (shipped as package data).
# Requires Node 18+ / npm. The build output is committed so installs don't need Node.
set -euo pipefail
cd "$(dirname "$0")/../web"
echo "▸ installing web deps…"; npm install --silent
echo "▸ building dashboard…"; npm run build
echo "✓ built -> aegis/static/web_dist/"
