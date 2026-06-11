"""Persistent goals: a standing objective the agent keeps working toward.

``/goal <text>`` sets a goal on the session. After every turn a small judge
call decides done-or-continue; on continue, a continuation prompt is fed back
into the same session automatically — until the goal is achieved, you pause or
clear it, or the turn budget runs out (``goals.max_turns``, default 20). Any
real user message preempts the loop. State lives in ``session.meta["goal"]``
so it survives resume. (The Ralph-loop pattern, as shipped by Codex CLI's
``/goal``.)
"""

from __future__ import annotations

import json

from ._log import info

DEFAULT_MAX_TURNS = 20


# -- state (session.meta["goal"]) --------------------------------------------
def get(session) -> dict | None:
    g = session.meta.get("goal")
    return g if isinstance(g, dict) and g.get("text") else None


def set_goal(session, text: str, max_turns: int = DEFAULT_MAX_TURNS) -> dict:
    g = {"text": text.strip(), "subgoals": [], "status": "active",
         "turns_used": 0, "max_turns": int(max_turns)}
    session.meta["goal"] = g
    return g


def clear(session) -> None:
    session.meta.pop("goal", None)


def status_line(g: dict) -> str:
    subs = "".join(f"\n  {i + 1}. {s}" for i, s in enumerate(g.get("subgoals", [])))
    return (f"⊙ Goal [{g['status']}] ({g['turns_used']}/{g['max_turns']} turns): "
            f"{g['text']}{subs}")


# -- slash commands ------------------------------------------------------------
def handle_command(session, text: str, config=None) -> tuple[str | None, bool]:
    """Handle ``/goal …`` and ``/subgoal …``. Returns (reply, start_turn) —
    ``start_turn`` is True when a new goal was set and the caller should run the
    goal text as a turn immediately."""
    parts = text.strip().split(None, 1)
    cmd, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")
    g = get(session)

    if cmd == "/subgoal":
        if g is None:
            return "No active goal — set one first with /goal <text>.", False
        if not arg:
            subs = g.get("subgoals", [])
            return ("No subgoals." if not subs else
                    "Subgoals:" + "".join(f"\n  {i+1}. {s}" for i, s in enumerate(subs))), False
        if arg.lower() == "clear":
            g["subgoals"] = []
            return "Subgoals cleared (goal kept).", False
        if arg.lower().startswith("remove"):
            try:
                idx = int(arg.split()[1]) - 1
                removed = g["subgoals"].pop(idx)
                return f"Removed subgoal: {removed}", False
            except (IndexError, ValueError):
                return "Usage: /subgoal remove <N>", False
        g.setdefault("subgoals", []).append(arg)
        return f"Added subgoal {len(g['subgoals'])}: {arg}", False

    # /goal …
    if not arg or arg.lower() == "status":
        return (status_line(g) if g else
                "No active goal. Set one with /goal <text>."), False
    if arg.lower() == "pause":
        if g is None:
            return "No active goal.", False
        g["status"] = "paused"
        return "⏸ Goal paused — /goal resume to continue.", False
    if arg.lower() == "resume":
        if g is None:
            return "No active goal.", False
        g["status"], g["turns_used"] = "active", 0
        return "▶ Goal resumed (turn counter reset). It continues after your next message — or send 'continue'.", False
    if arg.lower() == "clear":
        clear(session)
        return "Goal cleared.", False

    max_turns = int(config.get("goals.max_turns", DEFAULT_MAX_TURNS)) if config else DEFAULT_MAX_TURNS
    g = set_goal(session, arg, max_turns)
    return f"⊙ Goal set ({g['max_turns']}-turn budget): {g['text']}", True


# -- judge + continuation -------------------------------------------------------
def judge(config, g: dict, last_response: str) -> tuple[bool, str]:
    """One small model call: is the goal satisfied by the last response?
    Fail-open: any error means continue — the turn budget is the backstop."""
    subs = "".join(f"\n- {s}" for s in g.get("subgoals", []))
    crit = f"\nAdditional criteria (ALL must also be met):{subs}" if subs else ""
    prompt = (
        "You judge whether a standing goal is COMPLETE based on the agent's latest reply.\n"
        f"GOAL: {g['text']}{crit}\n\n"
        f"AGENT'S LATEST REPLY (may be truncated):\n{(last_response or '')[-4000:]}\n\n"
        'Reply with STRICT JSON only: {"done": true|false, "reason": "<one sentence>"}. '
        "Be conservative: done only when the reply explicitly confirms completion, the "
        "deliverable is clearly produced, or the goal is impossible/blocked (count "
        "blocked as done so we stop burning turns)."
    )
    try:
        from .providers import build_provider
        from .types import Message
        provider = build_provider(config)
        resp = provider.complete([Message.user(prompt)], tools=None, stream=False, max_tokens=200)
        raw = (resp.text or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        verdict = json.loads(raw[start:end + 1])
        return bool(verdict.get("done")), str(verdict.get("reason", ""))[:300]
    except Exception as e:  # noqa: BLE001
        info(f"goal judge failed ({type(e).__name__}); continuing")
        return False, "judge unavailable — continuing toward the goal"


def continuation_prompt(g: dict, reason: str) -> str:
    subs = "".join(f"\n- {s}" for s in g.get("subgoals", []))
    crit = f"\nAdditional criteria the user added mid-loop:{subs}" if subs else ""
    return (f"[Continuing toward your standing goal]\nGOAL: {g['text']}{crit}\n"
            f"Judge's assessment of remaining work: {reason}\n"
            "Take the next concrete step now. When the goal is fully met, state so explicitly.")


def run_loop(agent, last_text: str, notify, on_event=None, run_turn=None) -> str:
    """After a normal turn: judge and auto-continue until done/paused/budget.
    ``notify(line)`` surfaces progress; returns the final assistant text.

    ``run_turn`` may be supplied by entry surfaces that need each automatic
    continuation to flow through their shared runner/run log instead of calling
    ``agent.run`` directly.
    """
    session = agent.session
    g = get(session)
    if g is None or g.get("status") != "active":
        return last_text
    while True:
        done, reason = judge(agent.config, g, last_text)
        if done:
            clear(session)
            notify(f"✓ Goal achieved: {reason}")
            return last_text
        if g["turns_used"] >= g["max_turns"]:
            g["status"] = "paused"
            notify(f"⏸ Goal paused — {g['turns_used']}/{g['max_turns']} turns used. "
                   "/goal resume to keep going, /goal clear to stop.")
            return last_text
        g["turns_used"] += 1
        notify(f"↻ Continuing toward goal ({g['turns_used']}/{g['max_turns']}): {reason}")
        prompt = continuation_prompt(g, reason)
        result = run_turn(prompt) if run_turn is not None else agent.run(prompt, on_event)
        last_text = result.content or ""
        g = get(session)                      # re-read: the turn may have cleared/changed it
        if g is None or g.get("status") != "active" or agent.cancel_event.is_set():
            return last_text
