#!/usr/bin/env bash
# Full local release gate. Keep this as the one command CI and humans can
# use before cutting a release.
set -euo pipefail
cd "$(dirname "$0")/.."

ulimit -n 4096 2>/dev/null || true

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x ".venv/bin/python" ]; then PYTHON=".venv/bin/python"; else PYTHON="python3"; fi
fi

run() {
  printf '\n==> %s\n' "$*"
  "$@"
}

run "$PYTHON" scripts/generate_reference_docs.py --check
run bash scripts/run_tests.sh

release_smoke_artifacts="$(mktemp -d)"
release_smoke_out="$(mktemp -d)"
trap 'rm -rf "$release_smoke_artifacts" "$release_smoke_out"' EXIT
printf 'aegis release provenance smoke\n' > "$release_smoke_artifacts/aegis-smoke-artifact.txt"
run "$PYTHON" scripts/release_provenance.py --artifact-dir "$release_smoke_artifacts" --out "$release_smoke_out"
run "$PYTHON" scripts/release_provenance.py --artifact-dir "$release_smoke_artifacts" --out "$release_smoke_out" --check

if command -v npm >/dev/null 2>&1; then
  if [ -f apps/shared/package.json ]; then
    (cd apps/shared && run npm run typecheck)
  fi
  if [ -f web/package.json ]; then
    (cd web && run npm run typecheck && run npm run build)
  fi
  if [ -f desktop/package.json ]; then
    (cd desktop && run npm run test:desktop)
  fi
else
  echo "npm not found; cannot run web/desktop verification" >&2
  exit 1
fi

run "$PYTHON" -m compileall -q aegis scripts
run git diff --check
