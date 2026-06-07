#!/usr/bin/env bash
# AEGIS one-line installer (Hermes-style): isolated venv + global `aegis` command.
#
#   curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash
#   # or, from a clone:
#   ./install.sh
#
# Env overrides:
#   AEGIS_INSTALL_DIR  venv location      (default ~/.aegis/venv)
#   AEGIS_BIN_DIR      launcher location  (default ~/.local/bin)
#   AEGIS_EXTRAS       pip extras         (e.g. "all" -> .[all]; default none)
#   AEGIS_REPO         git URL to install (default: local dir if present, else PyPI)
#   AEGIS_ONBOARD      run onboarding     (default 1; set 0 to skip)
set -euo pipefail

APP="aegis"
INSTALL_DIR="${AEGIS_INSTALL_DIR:-$HOME/.aegis/venv}"
BIN_DIR="${AEGIS_BIN_DIR:-$HOME/.local/bin}"
EXTRAS="${AEGIS_EXTRAS:-}"
REPO="${AEGIS_REPO:-}"
RUN_ONBOARD="${AEGIS_ONBOARD:-1}"

while [ $# -gt 0 ]; do
  case "$1" in
    --skip-onboard|--no-onboard)
      RUN_ONBOARD=0; shift ;;
    --quick)
      AEGIS_ONBOARD_ARGS="${AEGIS_ONBOARD_ARGS:-} --quick"; shift ;;
    --advanced)
      AEGIS_ONBOARD_ARGS="${AEGIS_ONBOARD_ARGS:-} --advanced"; shift ;;
    --no-probe)
      AEGIS_ONBOARD_ARGS="${AEGIS_ONBOARD_ARGS:-} --no-probe"; shift ;;
    --no-services)
      AEGIS_ONBOARD_ARGS="${AEGIS_ONBOARD_ARGS:-} --no-services"; shift ;;
    -h|--help)
      echo "Usage: install.sh [--skip-onboard] [--quick|--advanced] [--no-probe] [--no-services]"
      exit 0 ;;
    *)
      warn "Ignoring unknown installer argument: $1"; shift ;;
  esac
done

# Termux (Android): link into $PREFIX/bin (already on PATH) and use pkg for system tools.
IS_TERMUX=""
if [ -n "${PREFIX:-}" ] && printf '%s' "$PREFIX" | grep -q "com.termux"; then
  IS_TERMUX=1
  BIN_DIR="${AEGIS_BIN_DIR:-$PREFIX/bin}"
fi

say()  { printf '\033[1;35m▸\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# --- 1. find a suitable python (>=3.10) ------------------------------------
say "Looking for Python >= 3.10…"
PYTHON=""
for cand in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      PYTHON="$cand"; break
    fi
  fi
done
[ -n "$PYTHON" ] || die "Python 3.10+ not found. Install it (e.g. 'brew install python' or 'apt install python3') and re-run."
ok "Using $($PYTHON --version) ($(command -v $PYTHON))"

# --- 2. resolve the install source ----------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$REPO" ]; then
  SOURCE="git+$REPO"
elif [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
  SOURCE="$SCRIPT_DIR"
else
  # Piped install (curl | bash) with no local checkout: install straight from git.
  SOURCE="git+https://github.com/Alien0013/aegis.git"
fi
[ -n "$EXTRAS" ] && SOURCE="${SOURCE}[${EXTRAS}]"
say "Install source: $SOURCE"

# --- 3. create the isolated venv ------------------------------------------
say "Creating venv at $INSTALL_DIR…"
mkdir -p "$INSTALL_DIR"
"$PYTHON" -m venv "$INSTALL_DIR"
"$INSTALL_DIR/bin/pip" install -q --upgrade pip wheel
say "Installing AEGIS (this can take a minute)…"
"$INSTALL_DIR/bin/pip" install -q "$SOURCE"
ok "Installed."

# --- 4. global launcher ----------------------------------------------------
mkdir -p "$BIN_DIR"
ln -sf "$INSTALL_DIR/bin/$APP" "$BIN_DIR/$APP"
ok "Linked $BIN_DIR/$APP -> $INSTALL_DIR/bin/$APP"

# --- 5. PATH wiring --------------------------------------------------------
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    warn "$BIN_DIR is not on your PATH."
    for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
      [ -f "$rc" ] || continue
      if ! grep -qs "$BIN_DIR" "$rc"; then
        printf '\n# added by AEGIS installer\nexport PATH="%s:$PATH"\n' "$BIN_DIR" >> "$rc"
        ok "Added $BIN_DIR to PATH in $rc"
      fi
    done
    warn "Run 'export PATH=\"$BIN_DIR:\$PATH\"' or restart your shell to use 'aegis'."
    ;;
esac

# --- 6. optional system tool: ripgrep (faster search) ----------------------
if ! command -v rg >/dev/null 2>&1; then
  if   [ "$IS_TERMUX" = 1 ] && command -v pkg >/dev/null 2>&1; then pkg install -y ripgrep >/dev/null 2>&1 && ok "installed ripgrep" || true
  elif command -v brew   >/dev/null 2>&1; then brew install ripgrep   >/dev/null 2>&1 && ok "installed ripgrep" || true
  elif command -v apt-get>/dev/null 2>&1; then sudo apt-get install -y ripgrep >/dev/null 2>&1 && ok "installed ripgrep" || warn "skip ripgrep (optional)"
  elif command -v dnf    >/dev/null 2>&1; then sudo dnf install -y ripgrep >/dev/null 2>&1 && ok "installed ripgrep" || true
  elif command -v pacman >/dev/null 2>&1; then sudo pacman -S --noconfirm ripgrep >/dev/null 2>&1 && ok "installed ripgrep" || true
  fi
fi

# --- 7. first-run onboarding ----------------------------------------------
echo
ok "AEGIS installed."
if [ "$RUN_ONBOARD" != "0" ] && [ -r /dev/tty ]; then
  say "Starting first-run onboarding…"
  if "$BIN_DIR/$APP" onboard ${AEGIS_ONBOARD_ARGS:-} < /dev/tty; then
    ok "Onboarding complete."
  else
    warn "Onboarding did not finish. Run 'aegis setup' when ready."
  fi
else
  echo "  Next:"
  echo "    aegis setup                 # first-run onboarding"
  echo "    aegis                       # start chatting"
  echo "    aegis doctor                # verify the install"
  if [ -n "$EXTRAS" ]; then
    echo "    playwright install chromium # if you installed the 'browser'/'all' extra"
  fi
fi
exit 0
