"""The canonical agent event contract.

Every surface (REPL, dashboard, gateway, serve, ACP/IDE) consumes the same event
dicts emitted by the agent loop via ``on_event``. Each event is a plain dict with a
``"type"`` from ``EventType`` plus the documented payload keys. Keeping this in one
place lets new consumers rely on a stable contract instead of reverse-engineering
``loop.py``.
"""

from __future__ import annotations


class EventType:
    # --- lifecycle ---
    ITERATION = "iteration"            # {n, max}
    ASSISTANT_DELTA = "assistant_delta"  # {text}  streamed token chunk
    REASONING_DELTA = "reasoning_delta"  # {text}  streamed thinking/CoT chunk
    ASSISTANT_MESSAGE = "assistant_message"  # {text, tool_calls}
    FINAL = "final"                    # {text}  the turn's answer
    CANCELLED = "cancelled"            # {}       interrupted by the user
    BUDGET_EXHAUSTED = "budget_exhausted"  # {}
    CONTINUATION = "continuation"      # {n}      auto-continue after length cut
    ERROR = "error"                    # {message}

    # --- tools ---
    TOOL_START = "tool_start"          # {id, name, args}
    TOOL_RESULT = "tool_result"        # {id, name, summary, is_error, classification}

    # --- context / learning ---
    COMPACTING = "compacting"          # {}
    COMPACTED = "compacted"            # {messages_before, messages_after, tokens_before, tokens_after, reason}
    SESSION_COMPRESS = "session:compress"  # {session_id, old_session_id, compression_count}
    REVIEW_STARTED = "review_started"  # {kind: memory|skill|combined}
    REVIEW_DONE = "review_done"        # {kind, actions: [str]}

    # --- interactive ---
    CLARIFY = "clarify"                # {question, choices}

    # --- delegation ---
    SUBAGENT_START = "subagent_start"  # {id, task, agent_type}
    SUBAGENT_TEXT = "subagent_text"    # {id, subagent_id, task, text}
    SUBAGENT_REASONING = "subagent_reasoning"  # {id, subagent_id, task, text}
    SUBAGENT_DONE = "subagent_done"    # {id, status}


#: All known event type strings (for validation / docs / consumer tests).
ALL: frozenset[str] = frozenset(
    v for k, v in vars(EventType).items() if not k.startswith("_") and isinstance(v, str)
)


def is_known(event: dict) -> bool:
    """True if ``event`` carries a recognized type from the contract."""
    return isinstance(event, dict) and event.get("type") in ALL
