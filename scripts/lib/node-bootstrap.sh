#!/usr/bin/env bash
# Sourceable AEGIS installer helper for Node-dependent local surfaces.
# It only activates an existing compatible Node.js binary; installers may keep
# going when Node is absent because Python-only AEGIS still works.

AEGIS_NODE_MIN_VERSION="${AEGIS_NODE_MIN_VERSION:-20}"
AEGIS_HOME="${AEGIS_HOME:-$HOME/.aegis}"
AEGIS_NODE_BIN="${AEGIS_NODE_BIN:-}"
AEGIS_NODE_AVAILABLE=false

_aegis_nb_warn() {
  if declare -F warn >/dev/null 2>&1; then
    warn "$*"
  else
    printf '! %s\n' "$*" >&2
  fi
}

_aegis_nb_major_for() {
  local node_bin="$1"
  local version major
  version="$($node_bin --version 2>/dev/null || true)"
  version="${version#v}"
  major="${version%%.*}"
  case "$major" in
    ''|*[!0-9]*) printf '0\n' ;;
    *) printf '%s\n' "$major" ;;
  esac
}

_aegis_nb_accepts() {
  local node_bin="$1"
  [ -x "$node_bin" ] || return 1
  [ "$(_aegis_nb_major_for "$node_bin")" -ge "$AEGIS_NODE_MIN_VERSION" ]
}

ensure_node() {
  local node_bin=""
  if command -v node >/dev/null 2>&1; then
    node_bin="$(command -v node)"
    if _aegis_nb_accepts "$node_bin"; then
      AEGIS_NODE_BIN="$node_bin"
      AEGIS_NODE_AVAILABLE=true
      export AEGIS_NODE_BIN AEGIS_NODE_AVAILABLE
      return 0
    fi
  fi

  node_bin="$AEGIS_HOME/node/bin/node"
  if _aegis_nb_accepts "$node_bin"; then
    PATH="$AEGIS_HOME/node/bin:$PATH"
    AEGIS_NODE_BIN="$node_bin"
    AEGIS_NODE_AVAILABLE=true
    export PATH AEGIS_NODE_BIN AEGIS_NODE_AVAILABLE
    return 0
  fi

  AEGIS_NODE_AVAILABLE=false
  export AEGIS_NODE_AVAILABLE
  _aegis_nb_warn "Node.js >= $AEGIS_NODE_MIN_VERSION not found; desktop/TUI JavaScript surfaces may need npm install later."
  return 1
}
