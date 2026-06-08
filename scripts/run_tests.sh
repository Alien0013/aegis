#!/usr/bin/env bash
# Hermetic test runner — use this, not bare `pytest`, for parity with CI.
# Strips real credentials, pins UTC, and runs against a throwaway AEGIS_HOME so a
# developer's local keys or timezone can never change the outcome.
set -euo pipefail
cd "$(dirname "$0")/.."

export TZ=UTC
export AEGIS_HOME="$(mktemp -d -t aegis-tests-XXXXXX)"
trap 'rm -rf "$AEGIS_HOME"' EXIT

# Unset anything that could make a test hit a real provider or read real auth.
for v in ANTHROPIC_API_KEY OPENAI_API_KEY GOOGLE_API_KEY GEMINI_API_KEY \
         OPENROUTER_API_KEY GROQ_API_KEY DEEPSEEK_API_KEY XAI_API_KEY \
         MISTRAL_API_KEY TOGETHER_API_KEY GOOGLE_OAUTH_CLIENT_SECRET \
         AEGIS_ONBOARD_DIALOGS AEGIS_PROFILE; do
  unset "$v" 2>/dev/null || true
done

# Use the chosen interpreter's pytest so we never grab an unrelated system one.
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x ".venv/bin/python" ]; then PYTHON=".venv/bin/python"; else PYTHON="python3"; fi
fi
exec "$PYTHON" -m pytest -q "$@"
