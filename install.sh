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
#   AEGIS_NO_PROMPT    disable interactive prompts/onboarding (default 0)
#   AEGIS_VERIFY_INSTALL run `aegis doctor` after install (default 0)
#   AEGIS_DRY_RUN      print the install plan without making changes (default 0)
#   AEGIS_BRANCH       branch for the default GitHub source (default main)
set -euo pipefail

if [ -n "${PYTHONPATH:-}" ]; then
  echo "⚠ Ignoring inherited PYTHONPATH during install to avoid module shadowing"
  unset PYTHONPATH
fi
if [ -n "${PYTHONHOME:-}" ]; then
  echo "⚠ Ignoring inherited PYTHONHOME during install"
  unset PYTHONHOME
fi
export PIP_DISABLE_PIP_VERSION_CHECK=1

APP="aegis"
PACKAGE="aegis-agent-harness"
INSTALL_DIR="${AEGIS_INSTALL_DIR:-$HOME/.aegis/venv}"
BIN_DIR="${AEGIS_BIN_DIR:-$HOME/.local/bin}"
EXTRAS="${AEGIS_EXTRAS:-}"
REPO="${AEGIS_REPO:-}"
RUN_ONBOARD="${AEGIS_ONBOARD:-1}"
NO_PROMPT="${AEGIS_NO_PROMPT:-0}"
VERIFY_INSTALL="${AEGIS_VERIFY_INSTALL:-0}"
DRY_RUN="${AEGIS_DRY_RUN:-0}"
BRANCH="${AEGIS_BRANCH:-main}"
PYTHON_OVERRIDE="${AEGIS_PYTHON:-}"
ONBOARD_ARGS="${AEGIS_ONBOARD_ARGS:-}"

say()  { printf '\033[1;35m▸\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
Usage: install.sh [options]

Options:
  --skip-onboard, --no-onboard  Skip first-run onboarding
  --quick                       Pass --quick to onboarding
  --advanced                    Pass --advanced to onboarding
  --no-probe                    Pass --no-probe to onboarding
  --no-services                 Pass --no-services to onboarding
  --no-prompt                   Disable interactive prompts/onboarding
  --verify                      Run 'aegis doctor' after install
  --dry-run                     Print the install plan without changing files
  --branch <name>               Install default GitHub source from branch
  -h, --help                    Show this help
EOF
}

has_tty() {
  [ "$NO_PROMPT" != "1" ] || return 1
  [ -r /dev/tty ] && [ -w /dev/tty ] || return 1
  (: </dev/tty) 2>/dev/null
}

while [ $# -gt 0 ]; do
  case "$1" in
    --skip-onboard|--no-onboard)
      RUN_ONBOARD=0; shift ;;
    --no-prompt)
      NO_PROMPT=1; RUN_ONBOARD=0; shift ;;
    --verify)
      VERIFY_INSTALL=1; shift ;;
    --dry-run)
      DRY_RUN=1; shift ;;
    --branch)
      [ $# -ge 2 ] || die "missing value for --branch"
      BRANCH="$2"; shift 2 ;;
    --quick)
      ONBOARD_ARGS="$ONBOARD_ARGS --quick"; shift ;;
    --advanced)
      ONBOARD_ARGS="$ONBOARD_ARGS --advanced"; shift ;;
    --no-probe)
      ONBOARD_ARGS="$ONBOARD_ARGS --no-probe"; shift ;;
    --no-services)
      ONBOARD_ARGS="$ONBOARD_ARGS --no-services"; shift ;;
    -h|--help)
      usage
      exit 0 ;;
    *)
      die "unknown installer argument: $1" ;;
  esac
done

# Termux (Android): link into $PREFIX/bin (already on PATH) and use pkg for system tools.
IS_TERMUX=""
if [ -n "${PREFIX:-}" ] && printf '%s' "$PREFIX" | grep -q "com.termux"; then
  IS_TERMUX=1
  BIN_DIR="${AEGIS_BIN_DIR:-$PREFIX/bin}"
fi

# --- 1. find a suitable python (>=3.10) ------------------------------------
say "Looking for Python >= 3.10…"
PYTHON=""
if [ -n "$PYTHON_OVERRIDE" ]; then
  if command -v "$PYTHON_OVERRIDE" >/dev/null 2>&1 || [ -x "$PYTHON_OVERRIDE" ]; then
    if "$PYTHON_OVERRIDE" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      PYTHON="$PYTHON_OVERRIDE"
    fi
  fi
else
  for cand in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
      if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
        PYTHON="$cand"; break
      fi
    fi
  done
fi
[ -n "$PYTHON" ] || die "Python 3.10+ not found. Install it (e.g. 'brew install python' or 'apt install python3') and re-run."
ok "Using $("$PYTHON" --version) ($(command -v "$PYTHON"))"

# --- 2. resolve the install source ----------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
EXTRAS_APPLIED=0
if [ -n "$REPO" ]; then
  SOURCE="git+$REPO"
elif [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
  SOURCE="$SCRIPT_DIR"
else
  # Piped install (curl | bash) with no local checkout: install straight from git.
  PKG_SPEC="$PACKAGE"
  [ -n "$EXTRAS" ] && PKG_SPEC="$PKG_SPEC[$EXTRAS]"
  SOURCE="$PKG_SPEC @ git+https://github.com/Alien0013/aegis.git@$BRANCH"
  EXTRAS_APPLIED=1
fi
[ -n "$EXTRAS" ] && [ "$EXTRAS_APPLIED" = "0" ] && SOURCE="${SOURCE}[${EXTRAS}]"
say "Install source: $SOURCE"

if [ "$DRY_RUN" = "1" ]; then
  echo ""
  ok "Dry run complete. No files changed."
  echo "  Python:       $PYTHON"
  echo "  Source:       $SOURCE"
  echo "  Venv:         $INSTALL_DIR"
  echo "  Launcher:     $BIN_DIR/$APP"
  echo "  Onboarding:   $([ "$RUN_ONBOARD" = "0" ] && echo skipped || echo enabled)"
  echo "  Verify:       $([ "$VERIFY_INSTALL" = "1" ] && echo enabled || echo skipped)"
  exit 0
fi

# --- 3. create the isolated venv ------------------------------------------
say "Creating venv at $INSTALL_DIR…"
mkdir -p "$INSTALL_DIR"
"$PYTHON" -m venv "$INSTALL_DIR"
"$INSTALL_DIR/bin/pip" install -q --upgrade pip wheel
say "Installing AEGIS (this can take a minute)…"
"$INSTALL_DIR/bin/pip" install -q --upgrade "$SOURCE"
ok "Installed."

# --- 4. global launcher ----------------------------------------------------
mkdir -p "$BIN_DIR"
rm -f "$BIN_DIR/$APP"
cat > "$BIN_DIR/$APP" <<EOF
#!/usr/bin/env bash
unset PYTHONPATH
unset PYTHONHOME
exec "$INSTALL_DIR/bin/$APP" "\$@"
EOF
chmod +x "$BIN_DIR/$APP"
ok "Installed launcher $BIN_DIR/$APP -> $INSTALL_DIR/bin/$APP"

# --- 5. PATH wiring --------------------------------------------------------
hash -r 2>/dev/null || true
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
  elif command -v apt-get>/dev/null 2>&1 && [ "$(id -u)" -eq 0 ]; then apt-get install -y ripgrep >/dev/null 2>&1 && ok "installed ripgrep" || warn "skip ripgrep (optional)"
  elif command -v apt-get>/dev/null 2>&1 && command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then sudo apt-get install -y ripgrep >/dev/null 2>&1 && ok "installed ripgrep" || warn "skip ripgrep (optional)"
  elif command -v dnf    >/dev/null 2>&1 && [ "$(id -u)" -eq 0 ]; then dnf install -y ripgrep >/dev/null 2>&1 && ok "installed ripgrep" || true
  elif command -v dnf    >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then sudo dnf install -y ripgrep >/dev/null 2>&1 && ok "installed ripgrep" || true
  elif command -v pacman >/dev/null 2>&1 && [ "$(id -u)" -eq 0 ]; then pacman -S --noconfirm ripgrep >/dev/null 2>&1 && ok "installed ripgrep" || true
  elif command -v pacman >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then sudo pacman -S --noconfirm ripgrep >/dev/null 2>&1 && ok "installed ripgrep" || true
  else warn "ripgrep not found; install it later for faster file search"
  fi
fi

# --- 7. first-run onboarding ----------------------------------------------
echo
ok "AEGIS installed."
if [ "$RUN_ONBOARD" != "0" ] && has_tty; then
  say "Starting first-run onboarding…"
  if "$BIN_DIR/$APP" onboard $ONBOARD_ARGS < /dev/tty; then
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

if [ "$VERIFY_INSTALL" = "1" ]; then
  echo
  say "Running install verify…"
  "$BIN_DIR/$APP" doctor
  ok "Install verify complete."
fi
exit 0
