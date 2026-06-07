"""Static defaults and limits. No runtime state here."""

from __future__ import annotations

APP_NAME = "aegis"

# --- Agent loop -------------------------------------------------------------
DEFAULT_MAX_ITERATIONS = 50
COMPRESS_PRESERVE_FIRST = 3
COMPRESS_PRESERVE_LAST = 20
MAX_PARALLEL_TOOLS = 8

# --- Context window ---------------------------------------------------------
# Match Hermes: models need a real working window for multi-step tool loops.
MIN_CONTEXT_LENGTH = 64_000          # reject models below this at startup (override per-model)
DEFAULT_CONTEXT_LENGTH = 128_000
# Fraction of the context window we allow message history to fill before
# triggering compaction.
COMPACT_THRESHOLD = 0.75

# --- Memory -----------------------------------------------------------------
MEMORY_CHAR_LIMIT = 4_000
USER_CHAR_LIMIT = 2_000
MEMORY_DELIM = "\n§\n"

# --- Skills -----------------------------------------------------------------
# Auto-generate a skill suggestion after this many successful tool calls in a turn.
SKILL_AUTOGEN_THRESHOLD = 6

# --- Defaults ---------------------------------------------------------------
DEFAULT_PROVIDER = "anthropic"
DEFAULT_MODEL = "claude-sonnet-4-5"

# Rough token estimate: ~4 chars per token.
CHARS_PER_TOKEN = 4
