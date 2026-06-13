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
#   AEGIS_EXTRAS       pip extras         (default "all"; set "" or use --core for core only)
#   AEGIS_REPO         git URL to install (default: local dir if present, else PyPI)
#   AEGIS_ONBOARD      run onboarding     (default 1; set 0 to skip)
#   AEGIS_NO_PROMPT    disable interactive prompts/onboarding (default 0)
#   AEGIS_NONINTERACTIVE_ONBOARD run onboarding with safe defaults (default 0)
#   AEGIS_VERIFY_INSTALL run `aegis doctor` after install (default 0)
#   AEGIS_DRY_RUN      print the install plan without making changes (default 0)
#   AEGIS_BRANCH       branch for the default GitHub source (default main)
#   AEGIS_SKIP_BROWSER skip Playwright Chromium install for full/browser profiles
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
AEGIS_HOME_DIR="${AEGIS_HOME:-$HOME/.aegis}"
INSTALL_DIR="${AEGIS_INSTALL_DIR:-$AEGIS_HOME_DIR/venv}"
BIN_DIR="${AEGIS_BIN_DIR:-$HOME/.local/bin}"
EXTRAS="${AEGIS_EXTRAS-all}"
REPO="${AEGIS_REPO:-}"
RUN_ONBOARD="${AEGIS_ONBOARD:-1}"
NO_PROMPT="${AEGIS_NO_PROMPT:-0}"
NONINTERACTIVE_ONBOARD="${AEGIS_NONINTERACTIVE_ONBOARD:-0}"
VERIFY_INSTALL="${AEGIS_VERIFY_INSTALL:-0}"
DRY_RUN="${AEGIS_DRY_RUN:-0}"
BRANCH="${AEGIS_BRANCH:-main}"
SKIP_BROWSER="${AEGIS_SKIP_BROWSER:-0}"
PYTHON_OVERRIDE="${AEGIS_PYTHON:-}"
ONBOARD_ARGS="${AEGIS_ONBOARD_ARGS:-}"
STAGE=0
TOTAL_STAGES=8
BROWSER_STATUS="not selected"
ONBOARD_FAILED=0
ONBOARD_RC=0

say()  { printf '\033[1;35m▸\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
kv()    { printf '  \033[2m%s:\033[0m %s\n' "$1" "$2"; }

banner() {
  printf '\n\033[1;35m'
  printf '┌─────────────────────────────────────────────────────────┐\n'
  printf '│                  AEGIS Installer                        │\n'
  printf '├─────────────────────────────────────────────────────────┤\n'
  printf '│        One-line install, onboarding, and verify.         │\n'
  printf '└─────────────────────────────────────────────────────────┘\n'
  printf '\033[0m\n'
}

stage() {
  STAGE=$((STAGE + 1))
  printf '\n\033[1;35m[%s/%s] %s\033[0m\n' "$STAGE" "$TOTAL_STAGES" "$1"
}

print_plan() {
  printf '\n\033[1;35mInstall plan\033[0m\n'
  kv "Python" "$("$PYTHON" --version 2>/dev/null) ($(command -v "$PYTHON"))"
  kv "Source" "$SOURCE"
  kv "Extras" "${EXTRAS:-core only}"
  kv "Browser engine" "$(wants_browser && [ "$SKIP_BROWSER" != "1" ] && echo enabled || echo skipped)"
  kv "Data home" "$AEGIS_HOME_DIR"
  kv "Venv" "$INSTALL_DIR"
  kv "Launcher" "$BIN_DIR/$APP"
  kv "Onboarding" "$([ "$RUN_ONBOARD" = "0" ] && echo skipped || ([ "$NONINTERACTIVE_ONBOARD" = "1" ] && echo noninteractive || echo enabled))"
  kv "Verify" "$([ "$VERIFY_INSTALL" = "1" ] && echo enabled || echo skipped)"
}

print_success() {
  printf '\n\033[1;32m'
  printf '┌─────────────────────────────────────────────────────────┐\n'
  printf '│              ✓ AEGIS installation complete              │\n'
  printf '└─────────────────────────────────────────────────────────┘\n'
  printf '\033[0m\n'
  printf '\033[1;35mYour files\033[0m\n'
  kv "Config" "$AEGIS_HOME_DIR/config.yaml"
  kv "Secrets" "$AEGIS_HOME_DIR/.env"
  kv "Workspace" "$AEGIS_HOME_DIR/workspace"
  kv "Venv" "$INSTALL_DIR"
  kv "Launcher" "$BIN_DIR/$APP"
  kv "Browser engine" "$BROWSER_STATUS"
  printf '\n\033[1;35mCommands\033[0m\n'
  kv "Start" "aegis"
  kv "Desktop" "aegis desktop"
  kv "Setup" "aegis setup"
  kv "Status" "aegis status"
  kv "Doctor" "aegis doctor"
  kv "Tools" "aegis tools"
  kv "Skills" "aegis skills"
  kv "Plugins" "aegis plugins"
  kv "Update" "aegis update"
  kv "Uninstall" "aegis uninstall --purge  # or repo ./uninstall.sh --purge"
}

print_incomplete() {
  printf '\n\033[1;33m'
  printf '┌─────────────────────────────────────────────────────────┐\n'
  printf '│        AEGIS installed, but onboarding is incomplete     │\n'
  printf '└─────────────────────────────────────────────────────────┘\n'
  printf '\033[0m\n'
  printf 'The launcher was installed, but first-run setup did not finish.\n'
  printf 'Run one of these after fixing the reported issue:\n'
  printf '  aegis setup\n'
  printf '  aegis doctor\n'
  printf '\n'
  printf 'To remove this partial install:\n'
  printf '  aegis uninstall --purge\n'
}

usage() {
  cat <<EOF
Usage: install.sh [options]

Options:
  --skip-onboard, --no-onboard  Skip first-run onboarding
  --quick                       Pass --quick to onboarding
  --advanced                    Pass --advanced to onboarding
  --no-probe                    Pass --no-probe to onboarding
  --no-services                 Pass --no-services to onboarding
  --no-prompt                   Disable prompts and run safe noninteractive onboarding
  --non-interactive             Run safe noninteractive onboarding
  --full                        Install the full curated extras set (default)
  --core, --minimal             Install only the core CLI
  --extras <names>              Install explicit extras, e.g. browser,discord
  --skip-browser, --no-browser  Skip Playwright Chromium download
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

wants_browser() {
  case ",$EXTRAS," in
    *,all,*|*,browser,*) return 0 ;;
    *) return 1 ;;
  esac
}

check_network() {
  if ! command -v curl >/dev/null 2>&1; then
    warn "curl not found; skipping network probe"
    return 0
  fi
  local failed=0
  for url in "https://pypi.org/simple/" "https://github.com/"; do
    if ! curl -fsSI --max-time 8 "$url" >/dev/null 2>&1; then
      warn "Could not reach $url"
      failed=1
    fi
  done
  if [ "$failed" = "0" ]; then
    ok "Internet connectivity looks good"
  else
    warn "Network probe failed; install may still work if pip can reach the source."
  fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    --skip-onboard|--no-onboard)
      RUN_ONBOARD=0; shift ;;
    --no-prompt)
      NO_PROMPT=1; NONINTERACTIVE_ONBOARD=1; shift ;;
    --non-interactive)
      NO_PROMPT=1; NONINTERACTIVE_ONBOARD=1; shift ;;
    --full)
      EXTRAS="all"; shift ;;
    --core|--minimal)
      EXTRAS=""; shift ;;
    --extras)
      [ $# -ge 2 ] || die "missing value for --extras"
      EXTRAS="$2"; shift 2 ;;
    --skip-browser|--no-browser)
      SKIP_BROWSER=1; shift ;;
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

if [ "$NO_PROMPT" = "1" ] && [ "$RUN_ONBOARD" != "0" ]; then
  NONINTERACTIVE_ONBOARD=1
fi

# Termux (Android): link into $PREFIX/bin (already on PATH) and use pkg for system tools.
IS_TERMUX=""
if [ -n "${PREFIX:-}" ] && printf '%s' "$PREFIX" | grep -q "com.termux"; then
  IS_TERMUX=1
  BIN_DIR="${AEGIS_BIN_DIR:-$PREFIX/bin}"
fi

banner
stage "Preparing environment"

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
check_network
print_plan

if [ "$DRY_RUN" = "1" ]; then
  echo ""
  ok "Dry run complete. No files changed."
  exit 0
fi

stage "Creating Python virtual environment"
say "Creating venv at $INSTALL_DIR…"
mkdir -p "$INSTALL_DIR"
"$PYTHON" -m venv "$INSTALL_DIR"
"$INSTALL_DIR/bin/pip" install -q --upgrade pip wheel

stage "Installing AEGIS package"
say "Installing AEGIS (this can take a minute)…"
# --no-cache-dir + --force-reinstall so a reinstall always builds the CURRENT commit:
# the package version stays 0.1.0, so pip would otherwise reuse a stale cached wheel.
"$INSTALL_DIR/bin/pip" install -q --upgrade --force-reinstall --no-cache-dir "$SOURCE"
ok "Installed."

stage "Installing browser engine"
if wants_browser; then
  if [ "$SKIP_BROWSER" = "1" ]; then
    BROWSER_STATUS="skipped"
    warn "Browser engine skipped. Run '$INSTALL_DIR/bin/python -m playwright install chromium' later."
  else
    say "Installing Playwright Chromium…"
    if "$INSTALL_DIR/bin/python" -m playwright install chromium; then
      BROWSER_STATUS="installed"
      ok "Browser engine installed."
    else
      BROWSER_STATUS="failed"
      warn "Browser engine install failed; browser tools may not work yet."
      warn "Try later: $INSTALL_DIR/bin/python -m playwright install chromium"
    fi
  fi
else
  BROWSER_STATUS="not selected"
  warn "Browser engine skipped (core/browser extra not selected)."
fi

stage "Installing command launcher"
mkdir -p "$BIN_DIR"
rm -f "$BIN_DIR/$APP"
cat > "$BIN_DIR/$APP" <<EOF
#!/usr/bin/env bash
unset PYTHONPATH
unset PYTHONHOME
$(if [ "${AEGIS_HOME:-}" ]; then printf 'export AEGIS_HOME=%q\n' "$AEGIS_HOME_DIR"; fi)
exec "$INSTALL_DIR/bin/$APP" "\$@"
EOF
chmod +x "$BIN_DIR/$APP"
ok "Installed launcher $BIN_DIR/$APP -> $INSTALL_DIR/bin/$APP"

stage "Checking PATH and optional tools"
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

stage "Running onboarding"
if [ "$RUN_ONBOARD" != "0" ] && { has_tty || [ "$NONINTERACTIVE_ONBOARD" = "1" ]; }; then
  say "Starting first-run onboarding…"
  RUN_ARGS="$ONBOARD_ARGS"
  if [ "$NONINTERACTIVE_ONBOARD" = "1" ]; then
    RUN_ARGS="$RUN_ARGS --non-interactive --accept-risk --json"
  fi
  if [ "$NONINTERACTIVE_ONBOARD" = "1" ]; then
    if "$BIN_DIR/$APP" onboard $RUN_ARGS; then
      onboard_rc=0
    else
      onboard_rc=$?
    fi
  else
    if "$BIN_DIR/$APP" onboard $RUN_ARGS < /dev/tty; then
      onboard_rc=0
    else
      onboard_rc=$?
    fi
  fi
  if [ "$onboard_rc" = "0" ]; then
    ok "Onboarding complete."
    echo "  Ways to use AEGIS:"
    echo "    aegis                       # chat in the terminal"
    echo "    aegis ui                    # clickable web control panel"
    echo "    aegis desktop               # native desktop app"
  else
    ONBOARD_FAILED=1
    ONBOARD_RC="$onboard_rc"
    warn "Onboarding did not finish. Run 'aegis setup' when ready."
  fi
else
  warn "Onboarding skipped. Run 'aegis setup' when ready."
  echo "  Next:"
  echo "    aegis setup                 # first-run onboarding"
  echo "    aegis                       # start chatting (terminal)"
  echo "    aegis ui                    # clickable control panel in your browser"
  echo "    aegis desktop               # native desktop app"
  echo "    aegis doctor                # verify the install"
  if wants_browser && [ "$BROWSER_STATUS" != "installed" ]; then
    echo "    playwright install chromium # if you installed the 'browser'/'all' extra"
  fi
fi

stage "Verifying installation"
if [ "$VERIFY_INSTALL" = "1" ]; then
  say "Running install verify…"
  "$BIN_DIR/$APP" doctor
  ok "Install verify complete."
else
  warn "Verify skipped. Run 'aegis doctor' to check the install."
fi

if [ "$ONBOARD_FAILED" = "1" ]; then
  print_incomplete
  exit "$ONBOARD_RC"
fi

print_success
exit 0
