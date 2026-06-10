"""Contextual first-touch hints.

Instead of front-loading everything into the setup wizard, show a one-time tip
the *first* time the user hits a behavior fork — messaging while the agent is
busy, the very first message ever, etc. Each hint is shown once per install,
tracked in config under ``onboarding.seen.<flag>``.

Kept dependency-free so the CLI, gateway, and dashboard can all import it.
"""

from __future__ import annotations

from .config import Config

BUSY_FLAG = "busy_message"
PROFILE_FLAG = "profile_build_offered"


def is_seen(config: Config, flag: str) -> bool:
    return bool((config.get("onboarding.seen", {}) or {}).get(flag))


def mark_seen(config: Config, flag: str) -> None:
    """Persist ``onboarding.seen.<flag> = True``. Best-effort — a write failure
    just means the hint shows again next time."""
    try:
        seen = dict(config.get("onboarding.seen", {}) or {})
        if seen.get(flag):
            return
        seen[flag] = True
        config.set("onboarding.seen", seen)
        config.save()
    except Exception:  # noqa: BLE001
        pass


def busy_hint(mode: str) -> str:
    """Tip shown the first time a message arrives while the agent is busy,
    matched to what just happened so the message reflects reality."""
    if mode == "steer":
        return ("💡 First-time tip — I folded your message into the task I'm already running. "
                "Set gateway.busy_mode to 'queue' (run it after) or 'interrupt' (stop and "
                "restart) to change this. Won't show again.")
    if mode == "interrupt":
        return ("💡 First-time tip — your message interrupted the task I was running. "
                "Set gateway.busy_mode to 'queue' or 'steer' to change this. Won't show again.")
    return ("💡 First-time tip — I'm mid-task, so your message is queued and runs next. "
            "Send 'stop' to cancel the current task, or '/steer <text>' to guide it "
            "without restarting. Won't show again.")


# Contextual feature-discovery tips: shown once each, at the moment they're relevant.
# trigger -> (flag, tip). The REPL calls maybe_tip(config, trigger) after a turn.
_TIPS = {
    "many_tools": ("tip_goal",
                   "that was a long multi-step task — `/goal <objective>` lets me keep "
                   "working toward a goal across turns without you re-prompting."),
    "edit_failed": ("tip_atref",
                    "you can pull a file (or part of one) straight into a message with "
                    "`@file:path:10-20`, `@diff`, or `@staged`."),
    "repeated_approve": ("tip_always",
                         "answer `a` at an approval prompt to allow that command for the "
                         "rest of the session instead of confirming each time."),
    "long_session": ("tip_compress",
                     "long session — `/compress here 3` keeps the last 3 exchanges verbatim "
                     "and summarizes the rest, or `/compress focus <topic>` to bias it."),
}


def maybe_tip(config: Config, trigger: str) -> str:
    """Return a one-time contextual tip for ``trigger`` (and mark it seen), else ''."""
    entry = _TIPS.get(trigger)
    if not entry or not config.get("onboarding.tips", True):
        return ""
    flag, text = entry
    if is_seen(config, flag):
        return ""
    mark_seen(config, flag)
    return "💡 " + text


def profile_build_directive(config: Config) -> str:
    """One-shot system note appended to the user's very first message ever:
    offer (never assume) to build a short user profile, consent-gated at every
    step. Returns "" once seen or when disabled via onboarding.profile_build=off."""
    if str(config.get("onboarding.profile_build", "ask")).lower() == "off":
        return ""
    if is_seen(config, PROFILE_FLAG):
        return ""
    mark_seen(config, PROFILE_FLAG)
    return (
        "\n\n[System note: this is the user's very first message. After a one-line "
        "introduction, OFFER — do not assume — to build a short profile of them so you "
        "can be more useful, and say they can decline or do it later. If and ONLY if "
        "they accept: ask what they're comfortable sharing (name, what they do, how "
        "they like you to work); before ANY external lookup say what you'll look up and "
        "get explicit consent; never read connected accounts silently. Save each "
        "confirmed durable fact with the memory tool. If they decline, drop it "
        "immediately and continue normally. Keep it light — not an interrogation.]"
    )
