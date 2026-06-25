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
from typing import Any

from .. import provenance
from ..types import Message

_REVIEW_TOOLS = {"memory", "skill", "skill_manage", "session_search"}

_SKILL_PROMPT = (
    "Review the conversation above and update the skill library. Be ACTIVE: most "
    "substantial sessions produce at least one small skill improvement. A pass that does "
    "nothing is a missed learning opportunity, not a neutral outcome — it is only right "
    "when the session had no durable technique, correction, or reusable workflow.\n\n"
    "Target shape: CLASS-LEVEL skills with rich SKILL.md bodies and optional "
    "`references/`, `templates/`, `scripts/`, or `assets/` support files. Avoid a flat pile of "
    "one-session skills. This shapes HOW you update, not WHETHER you update.\n\n"
    "Signals that warrant action:\n"
    "  1. The user corrected your style, tone, format, legibility, verbosity, or "
    "workflow. Frustration signals — 'stop doing X', 'this is too verbose', 'don't format "
    "like this', 'why are you explaining', 'just give me the answer', 'you always do Y and "
    "I hate it' — and explicit 'remember this' instructions are FIRST-CLASS skill signals, "
    "not only memory signals. Put the lesson in the skill that governs that class of task.\n"
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
    "or verification scripts the skill can run directly.\n"
    "  - `assets/<name>`: small static assets the skill needs.\n\n"
    "User-preference embedding (important): when the user expressed a style, format, or "
    "workflow preference, the update belongs in the SKILL.md body, not only in memory. "
    "Memory captures who the user is and the current state of operations; skills capture "
    "how to do this class of task for this user. When they complain about how you handled "
    "a task, the skill that governs that task must carry the lesson.\n\n"
    "Do NOT edit bundled or hub-installed skills. Pinned skills can be improved; pinning "
    "only blocks archival/consolidation, not content fixes. If the only skills that need "
    "updating are protected, say 'Nothing to save.' and stop.\n\n"
    "If two existing skills clearly overlap, note it in your reply — the background "
    "curator handles consolidation at scale (merging the weaker into the stronger).\n\n"
    "Do NOT capture environment-dependent failures, missing binaries, unconfigured "
    "credentials, negative claims like 'tool X is broken' or 'cannot use Y', transient "
    "errors that resolved, or one-off task narratives. These harden into self-imposed "
    "refusals the agent cites against itself for months after the real problem was fixed. "
    "If setup state caused a failure, capture the FIX (install command, config step, env "
    "var to set) under an existing setup/troubleshooting skill, never a durable refusal.\n\n"
    "'Nothing to save.' is a real option but should NOT be the default. If the session ran "
    "smoothly with no corrections and produced no new technique, say 'Nothing to save.' and "
    "stop. Otherwise, act."
)
_MEMORY_PROMPT = (
    "Review the conversation above and consider saving to memory.\n\n"
    "Memory is for who the user is, what they prefer, durable project state, stable "
    "environment facts, and decisions that should survive across sessions. Look for "
    "persona, desires, preferences, personal details, expectations about how you should "
    "behave, project conventions, and settled decisions.\n\n"
    "Use the two memory targets precisely:\n"
    "  - target=`user`: who the user is, preferences, communication style, pet peeves, "
    "workflow expectations.\n"
    "  - target=`memory`: your notes about AEGIS behavior, project/tool facts, stable "
    "environment details, conventions, and system diagnoses.\n"
    "If one correction contains both a user preference and a stable AEGIS/project/tool "
    "fact, save two entries with two memory tool calls — one to `user`, one to `memory`.\n\n"
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


def _review_tool_is_notifiable(name: str, args: dict[str, Any]) -> bool:
    action = str(args.get("action") or "").strip()
    if name == "memory":
        return bool(action)
    if name == "skill":
        return action in {"create", "improve"}
    if name == "skill_manage":
        return action in {"create", "patch", "write_file", "delete", "consolidate"}
    return False


def _skill_change_detail(name: str, args: dict[str, Any], ev: dict[str, Any], summary: str) -> dict[str, Any]:
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    change = data.get("_change") if isinstance(data.get("_change"), dict) else {}
    action = str(args.get("action") or change.get("action") or "").strip()
    detail: dict[str, Any] = {
        "tool": name,
        "summary": summary,
        "action": action,
        "name": args.get("name") or change.get("name") or "",
        "file_path": args.get("file_path") or change.get("file_path") or "",
        "old_string": args.get("old_string") or args.get("old_text") or change.get("old") or "",
        "new_string": args.get("new_string") or args.get("new_text") or change.get("new") or "",
        "result": ev.get("preview") or summary,
    }
    if change:
        detail["change"] = change
    elif name == "skill" and action == "create":
        detail["change"] = {
            "action": "create",
            "name": detail["name"],
            "description": " ".join(str(args.get("description") or "").split())[:120],
        }
    elif name == "skill" and action == "improve":
        detail["change"] = {
            "action": "patch",
            "name": detail["name"],
            "new": " ".join(str(args.get("body") or "").split())[:200],
        }
    return detail


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
    from ..auxiliary import router_for

    prompt = {"memory": _MEMORY_PROMPT, "skill": _SKILL_PROMPT, "combined": _COMBINED_PROMPT}[kind]
    snapshot = _transcript(agent.session.messages)
    if not snapshot.strip():
        return []
    review_route = router_for(agent).route("background_review")
    review_provider = review_route.provider
    child_session = Session.create(
        title="[review]",
        parent_id=getattr(getattr(agent, "session", None), "id", ""),
    )
    child = Agent(
        config=agent.config, provider=review_provider, session=child_session,
        registry=_restricted_registry(), memory=_local_review_memory(agent, child_session.id),
        skills=agent.skills, cwd=agent.cwd,
    )
    child._no_review = True                                # never let a review fork its own review
    child._background_review_aux_route = {
        "purpose": review_route.purpose,
        "source": review_route.source,
        "provider": getattr(review_provider, "name", ""),
        "model": getattr(review_provider, "model", ""),
    }
    child.tool_context.approver = lambda *a, **k: False   # never block on input in a thread
    actions: list[str] = []
    action_details: list[dict[str, Any]] = []
    tool_args: dict[str, dict[str, Any]] = {}
    if on_event:
        on_event({
            "type": "review_started",
            "kind": kind,
            "aux_route": getattr(child, "_background_review_aux_route", {}),
        })

    def _capture(ev):
        tool_id = str(ev.get("id") or "")
        name = ev.get("name")
        if ev.get("type") == "tool_start" and name in ("memory", "skill", "skill_manage"):
            args = ev.get("args")
            if tool_id and isinstance(args, dict):
                tool_args[tool_id] = args
            return
        if ev.get("type") == "tool_result" and name in ("memory", "skill", "skill_manage"):
            summary = ev.get("summary", name)
            args = tool_args.pop(tool_id, {}) if tool_id else {}
            if ev.get("is_error") or not _review_tool_is_notifiable(str(name or ""), args):
                return
            actions.append(summary)
            if name == "memory":
                action_details.append({
                    "tool": "memory",
                    "summary": summary,
                    "action": args.get("action", ""),
                    "target": args.get("target", "memory"),
                    "content": args.get("content", ""),
                    "old_text": args.get("old_text") or args.get("match", ""),
                    "result": ev.get("preview") or summary,
                })
            else:
                action_details.append(_skill_change_detail(str(name), args, ev, summary))

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
        on_event({
            "type": "review_done",
            "kind": kind,
            "actions": actions,
            "action_details": action_details,
        })
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
    skill_iters = int(cfg.get("learn.skill_every_iters", 15) or 0)
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
