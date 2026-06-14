"""Background self-improvement review.

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

_REVIEW_TOOLS = {"memory", "skill", "skill_manage", "session_search"}

_SKILL_PROMPT = (
    "Review the conversation above and update the skill library. Be ACTIVE: most "
    "substantial sessions produce at least one small skill improvement. A pass that "
    "does nothing is only right when the session had no durable technique, correction, "
    "or reusable workflow.\n\n"
    "Target shape: CLASS-LEVEL skills with rich SKILL.md bodies and optional "
    "`references/`, `templates/`, `scripts/`, or `assets/` support files. Avoid a flat pile of "
    "one-session skills.\n\n"
    "Signals that warrant action:\n"
    "  1. The user corrected your style, tone, format, legibility, verbosity, or "
    "workflow. Frustration and explicit 'remember this' instructions are FIRST-CLASS "
    "skill signals, not only memory signals. Put the lesson in the skill that governs "
    "that class of task.\n"
    "  2. A non-trivial technique, fix, workaround, debugging path, or tool-use pattern "
    "emerged that would help a future session.\n"
    "  3. A skill loaded or consulted this session was wrong, missing a step, or stale. "
    "Patch it now.\n\n"
    "Preference order:\n"
    "  1. Update a currently loaded/used skill first. Use `skill` or `skill_manage` "
    "view/list as needed, then patch or improve it.\n"
    "  2. Else update an existing umbrella skill that covers this territory.\n"
    "  3. Else add a support file under an existing umbrella with `skill_manage` "
    "action=write_file and file_path under `references/`, `templates/`, `scripts/`, or `assets/`. "
    "Add a one-line pointer in SKILL.md so future agents know it exists.\n"
    "  4. Create a new class-level umbrella only when no existing skill covers the "
    "class. The name must not be a PR number, error string, feature codename, "
    "library-only label, or today's one-off task.\n\n"
    "Support-file meaning:\n"
    "  - `references/<topic>.md`: concise task-focused detail, error transcripts, "
    "reproduction notes, provider quirks, quoted research/API excerpts, or domain notes.\n"
    "  - `templates/<name>.<ext>`: starter files meant to be copied and modified.\n"
    "  - `scripts/<name>.<ext>`: deterministic reusable probes, fixture generators, "
    "or verification scripts the skill can run directly.\n\n"
    "  - `assets/<name>`: small static assets the skill needs.\n\n"
    "Do NOT edit bundled or hub-installed skills. Pinned skills can be improved; pinning "
    "only blocks archival/consolidation, not content fixes.\n\n"
    "If two existing skills clearly overlap, note it in your reply — the background "
    "curator handles consolidation at scale (merging the weaker into the stronger).\n\n"
    "Do NOT capture environment-dependent failures, missing binaries, unconfigured "
    "credentials, negative claims like 'tool X is broken', transient errors that "
    "resolved, or one-off task narratives. If setup state caused a failure, capture the "
    "fix under an existing setup/troubleshooting skill, not a durable refusal.\n\n"
    "If nothing qualifies, say 'Nothing to save.' and stop."
)
_MEMORY_PROMPT = (
    "Review the conversation above and consider saving to memory.\n\n"
    "Memory is for who the user is, what they prefer, durable project state, stable "
    "environment facts, and decisions that should survive across sessions. Look for "
    "persona, desires, preferences, personal details, expectations about how you should "
    "behave, project conventions, and settled decisions.\n\n"
    "Do not save secrets, tokens, credentials, transient setup failures, one-off task "
    "details, resolved temporary errors, or facts already obvious from the repository. "
    "If a correction is about how to do a class of task, it may belong in a skill as "
    "well as memory.\n\n"
    "Consolidate as you go: if the store already holds entries that overlap or are "
    "superseded by what you're saving, merge them — use the memory tool's `replace` to "
    "fold several notes into one durable fact, or `remove` a now-redundant entry. Memory "
    "is a small budget; keep it deduplicated rather than letting near-duplicates pile up.\n\n"
    "If something stands out, save it with the `memory` tool. If nothing qualifies, "
    "say 'Nothing to save.' and stop."
)
_COMBINED_PROMPT = (
    "Review the conversation above and update two durable layers when there is signal.\n\n"
    "Memory: who the user is, what they prefer, durable project state, stable environment "
    "facts, and decisions that should survive across sessions.\n\n"
    "Skills: how to do this class of task for this user. User corrections about style, "
    "format, workflow, or approach belong in the relevant skill, not only in memory.\n\n"
    + _SKILL_PROMPT
    + "\n\n"
    + _MEMORY_PROMPT
)


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


def _local_review_memory(agent, session_id: str):
    """Review forks may write local MEMORY.md/USER.md but must not touch providers."""
    if getattr(agent, "memory", None) is None:
        return None
    from ..memory import MemoryManager

    memory = MemoryManager(agent.config, load_external=False)
    memory.initialize(session_id)
    return memory


def run_review(agent, kind: str, on_event=None) -> list[str]:
    """Run one forked review synchronously. ``kind`` ∈ {memory, skill, combined}."""
    from ..session import Session
    from .agent import Agent
    prompt = {"memory": _MEMORY_PROMPT, "skill": _SKILL_PROMPT, "combined": _COMBINED_PROMPT}[kind]
    snapshot = _transcript(agent.session.messages)
    if not snapshot.strip():
        return []
    child_session = Session.create(
        title="[review]",
        parent_id=getattr(getattr(agent, "session", None), "id", ""),
    )
    child = Agent(
        config=agent.config, provider=agent.provider, session=child_session,
        registry=_restricted_registry(), memory=_local_review_memory(agent, child_session.id),
        skills=agent.skills, cwd=agent.cwd,
    )
    child._no_review = True                                # never let a review fork its own review
    child.tool_context.approver = lambda *a, **k: False   # never block on input in a thread
    actions: list[str] = []
    if on_event:
        on_event({"type": "review_started", "kind": kind})

    def _capture(ev):
        if ev.get("type") == "tool_result" and ev.get("name") in ("memory", "skill", "skill_manage"):
            actions.append(ev.get("summary", ev["name"]))

    with provenance.origin_scope("agent"):     # skills written here are curatable
        from ..surface import SurfaceRunner
        SurfaceRunner(agent.config, cwd=agent.cwd, include_mcp=False, reuse_agents=False).run_prompt(
            f"{prompt}\n\nCONVERSATION:\n{snapshot}",
            session=child.session,
            agent=child,
            surface="review",
            title=f"{kind} review",
            meta={"review_kind": kind},
            on_event=_capture,
        )
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

    memory_every = int(cfg.get("learn.memory_every", 10) or 0)
    skill_iters = int(cfg.get("learn.skill_every_iters", 10) or 0)
    review_memory = memory_every > 0 and turns >= memory_every
    review_skill = skill_iters > 0 and tools_this_turn >= skill_iters
    if not (review_memory or review_skill):
        return False
    if review_memory:
        meta["_turns_since_memory"] = 0
    # Memory and skills gate independently: memory is low-risk (auto by default); skills are
    # human-gated by default to avoid writing executable instructions to disk unattended.
    auto_apply = bool(cfg.get("learn.auto_apply", False))
    auto_apply_skills = bool(cfg.get("learn.auto_apply_skills", False))

    on_ev = getattr(agent.tool_context, "emit", None)   # surface what it saves to the user

    def _run():
        try:
            if review_memory:
                _consolidate_memory(agent, on_ev)                 # deterministic dedup first
                if auto_apply:
                    run_review(agent, "memory", on_event=on_ev)   # writes directly + reports back
                else:
                    _propose_only(agent, "memory")                # queue candidate
            if review_skill:
                if auto_apply_skills:
                    run_review(agent, "skill", on_event=on_ev)
                else:
                    _propose_only(agent, "skill")                 # safer default: queue candidate
        except Exception:  # noqa: BLE001
            from .._log import log_exc
            log_exc("background review failed")

    threading.Thread(target=_run, daemon=True).start()
    return True


def _propose_only(agent, kind: str) -> None:
    """Human-gated default: use the candidate reviewer instead of writing directly."""
    from .. import learn
    learn.review_session(agent.config, agent.session.id)


def _consolidate_memory(agent, on_ev=None) -> None:
    """Deterministic memory dedup before the LLM memory review: drop near-duplicate
    entries from MEMORY.md/USER.md so the small budget never fills with redundancy
    (the LLM review then merges what remains semantically)."""
    store = getattr(getattr(agent, "memory", None), "store", None)
    if store is None:
        return
    for target in ("memory", "user"):
        try:
            res = store.consolidate(target)
        except Exception:  # noqa: BLE001
            continue
        if res.get("removed") and on_ev:
            on_ev({"type": "review_done", "kind": "memory",
                   "actions": [f"consolidated {len(res['removed'])} redundant memory entr"
                               f"{'y' if len(res['removed']) == 1 else 'ies'}"]})
