"""Background self-improvement review (Hermes Tier-1).

After a substantial turn, fork a child Agent that inherits the parent's provider/model
but runs with a **memory + skill-only tool whitelist**, reviews the conversation, and
writes durable memory/skills **directly** (auto-applied). It runs in a daemon thread so
it never blocks the user or touches the main session's prompt cache.

Triggers (config ``learn.background``, off by default):
  - skills  → fired when the turn used >= ``learn.skill_every_iters`` tool iterations
              (substantial work is the signal, not elapsed turns)
  - memory  → fired every ``learn.memory_every`` turns
"""

from __future__ import annotations

import threading

from .. import provenance
from ..types import Message

_REVIEW_TOOLS = {"memory", "skill", "session_search"}

_SKILL_PROMPT = (
    "Review the conversation above and update your skill library. Be ACTIVE — most "
    "substantial sessions produce at least one skill update.\n"
    "Aim for CLASS-LEVEL skills: a few rich, reusable umbrella skills, NOT a long flat "
    "list of one-session entries. A user correction or an explicit 'remember this' is a "
    "FIRST-CLASS skill signal — encode it as a pitfall or an explicit step in the skill body.\n"
    "Order of preference:\n"
    "  1. PATCH a skill that was loaded/used this session (use `skill` view, then improve).\n"
    "  2. Else PATCH an existing umbrella skill that covers this territory.\n"
    "  3. Only CREATE a new skill if this is a genuinely new class of task.\n"
    "Do not edit bundled or hub-installed skills. Use the `skill` tool to make changes, "
    "then stop. If nothing is worth saving, say so and stop."
)
_MEMORY_PROMPT = (
    "Review the conversation above. If it revealed a durable, non-obvious fact about the "
    "USER or the PROJECT (a preference, convention, decision, or environment detail) worth "
    "remembering across sessions, save it with the `memory` tool. Do not save secrets, "
    "transient details, or things already obvious from the code. If nothing qualifies, stop."
)
_COMBINED_PROMPT = _SKILL_PROMPT + "\n\nALSO: " + _MEMORY_PROMPT


def _restricted_registry():
    from ..tools.registry import ToolRegistry, default_registry
    reg = ToolRegistry()
    for t in default_registry().all():
        if t.name in _REVIEW_TOOLS:
            reg.register(t)
    return reg


def _transcript(messages: list[Message], limit: int = 12_000) -> str:
    lines = [f"{m.role}: {m.content}" for m in messages
             if m.role in ("user", "assistant") and m.content]
    return "\n".join(lines)[-limit:]


def run_review(agent, kind: str, on_event=None) -> list[str]:
    """Run one forked review synchronously. ``kind`` ∈ {memory, skill, combined}."""
    from ..session import Session
    from .agent import Agent
    prompt = {"memory": _MEMORY_PROMPT, "skill": _SKILL_PROMPT, "combined": _COMBINED_PROMPT}[kind]
    snapshot = _transcript(agent.session.messages)
    if not snapshot.strip():
        return []
    child = Agent(
        config=agent.config, provider=agent.provider, session=Session.create(title="[review]"),
        registry=_restricted_registry(), memory=agent.memory, skills=agent.skills, cwd=agent.cwd,
    )
    child._no_review = True                                # never let a review fork its own review
    child.tool_context.approver = lambda *a, **k: False   # never block on input in a thread
    actions: list[str] = []
    if on_event:
        on_event({"type": "review_started", "kind": kind})

    def _capture(ev):
        if ev.get("type") == "tool_result" and ev.get("name") in ("memory", "skill"):
            actions.append(ev.get("summary", ev["name"]))

    with provenance.origin_scope("agent"):     # skills written here are curatable
        child.run(f"{prompt}\n\nCONVERSATION:\n{snapshot}", _capture)
    if on_event:
        on_event({"type": "review_done", "kind": kind, "actions": actions})
    return actions


def maybe_review(agent, tools_this_turn: int) -> bool:
    """Decide from config + this turn's activity whether to spawn a background review."""
    cfg = agent.config
    if getattr(agent, "_no_review", False):
        return False
    if not cfg.get("learn.background", False) or agent.provider is None:
        return False
    meta = agent.session.meta
    turns = int(meta.get("_turns_since_memory", 0)) + 1
    meta["_turns_since_memory"] = turns

    memory_every = int(cfg.get("learn.memory_every", 5) or 0)
    skill_iters = int(cfg.get("learn.skill_every_iters", 4) or 0)
    review_memory = memory_every > 0 and turns >= memory_every
    review_skill = skill_iters > 0 and tools_this_turn >= skill_iters
    if not (review_memory or review_skill):
        return False
    if review_memory:
        meta["_turns_since_memory"] = 0
    kind = "combined" if (review_memory and review_skill) else ("memory" if review_memory else "skill")
    auto_apply = bool(cfg.get("learn.auto_apply", False))

    def _run():
        try:
            if auto_apply:
                run_review(agent, kind)            # writes directly
            else:
                _propose_only(agent, kind)         # safer default: queue candidates
        except Exception:  # noqa: BLE001
            from .._log import log_exc
            log_exc("background review failed")

    threading.Thread(target=_run, daemon=True).start()
    return True


def _propose_only(agent, kind: str) -> None:
    """Human-gated default: use the candidate reviewer instead of writing directly."""
    from .. import learn
    learn.review_session(agent.config, agent.session.id)
