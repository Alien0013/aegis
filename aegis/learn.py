"""Closed learning loop: review sessions, extract memory + skill candidates, and
promote them after approval.

Flow:  aegis learn review [session]   -> LLM reviews a session, proposes candidates
       aegis learn list               -> show pending candidates
       aegis learn apply <id>         -> promote (memory -> MEMORY.md, skill -> SKILL.md)
       aegis learn reject <id>

Secrets are redacted before a candidate is ever stored. Skill promotion versions
the skill; re-promoting an existing skill bumps its version and records the change.
"""

from __future__ import annotations

import json

from . import config as cfg
from .types import new_id
from .util import atomic_write, now_iso, read_text

# redact common secret shapes before storing/promoting a candidate (shared with the gateway)
from .redact import redact_secrets as _redact


def _store_path():
    return cfg.sub("learn", "candidates.json")


def _load() -> list[dict]:
    raw = read_text(_store_path())
    return json.loads(raw) if raw.strip() else []


def _save(items: list[dict]) -> None:
    atomic_write(_store_path(), json.dumps(items, indent=2))


def _extract_json(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}


REVIEW_PROMPT = """\
You are reviewing a completed work session to help the agent learn. Extract:
1. memories — durable facts about the USER or PROJECT worth remembering across sessions
   (preferences, conventions, environment, decisions). Only non-obvious, lasting facts.
2. skills — reusable step-by-step procedures demonstrated in this session that would help
   next time (only if the session solved a non-trivial, repeatable task).

Reply with ONLY JSON:
{"memories": ["fact", ...],
 "skills": [{"name": "kebab-case-name", "description": "what it does and WHEN to use it",
             "body": "## When to Use\\n...\\n## Procedure\\n1. ..."}]}
Use [] when there is nothing worth saving. Do not include secrets or API keys."""


def review_session(config, session_id: str | None = None) -> list[dict]:
    """Run the reviewer over a session; store redacted candidates as pending."""
    from .providers.registry import build_provider
    from .session import SessionStore
    from .types import Message

    store = SessionStore()
    sess = store.load(session_id) if session_id else store.latest()
    if not sess:
        return []
    transcript = "\n".join(f"{m.role}: {m.content}" for m in sess.messages
                           if m.role in ("user", "assistant") and m.content)[:16_000]
    if not transcript.strip():
        return []
    resp = build_provider(config).complete(
        [Message.system(REVIEW_PROMPT), Message.user(transcript)], tools=None, stream=False)
    data = _extract_json(resp.text)

    items = _load()
    new_items: list[dict] = []
    for mem in data.get("memories", []) or []:
        if isinstance(mem, str) and mem.strip():
            new_items.append({"id": new_id("cand"), "type": "memory", "session": sess.id,
                              "payload": _redact(mem.strip()), "status": "pending",
                              "created_at": now_iso()})
    for sk in data.get("skills", []) or []:
        if isinstance(sk, dict) and sk.get("name") and sk.get("description") and sk.get("body"):
            new_items.append({"id": new_id("cand"), "type": "skill", "session": sess.id,
                              "payload": {"name": _redact(sk["name"]),
                                          "description": _redact(sk["description"]),
                                          "body": _redact(sk["body"])},
                              "status": "pending", "created_at": now_iso()})
    items.extend(new_items)
    _save(items)
    return new_items


def list_candidates(status: str = "pending") -> list[dict]:
    return [c for c in _load() if c.get("status") == status]


def background_tick(config, session) -> bool:
    """Background learning: every N assistant turns, review the session off-thread.

    Enable with `learn.background: true` + `learn.background_every: <N>`. Memory candidates
    auto-apply via `learn.auto_apply` (low risk). Skills stay pending for human review by
    default — set `learn.auto_apply_skills: true` to let them auto-apply too (full autonomy).
    Returns True if a review was kicked off."""
    every = int(config.get("learn.background_every", 0) or 0)
    if every <= 0 or not config.get("learn.background", False) or session is None:
        return False
    turns = sum(1 for m in session.messages if m.role == "assistant" and m.content)
    last = session.meta.get("_last_bg_review", 0)
    if turns - last < every:
        return False
    session.meta["_last_bg_review"] = turns
    auto = bool(config.get("learn.auto_apply", False))
    auto_skills = bool(config.get("learn.auto_apply_skills", False))

    def _run():
        try:
            found = review_session(config, session.id)
            for c in found:
                kind = c.get("type")
                if kind == "memory" and auto:                  # low risk -> auto
                    apply_candidate(c["id"], config)
                elif kind == "skill" and auto_skills:          # opt-in full autonomy
                    apply_candidate(c["id"], config)
        except Exception:  # noqa: BLE001
            from ._log import log_exc
            log_exc("background learn review failed")

    import threading
    threading.Thread(target=_run, daemon=True).start()
    return True


def apply_candidate(cand_id: str, config) -> str:
    from .memory import MemoryStore
    from .skills import SkillsLoader

    items = _load()
    cand = next((c for c in items if c["id"].startswith(cand_id) and c["status"] == "pending"), None)
    if not cand:
        return "candidate not found"
    if cand["type"] == "memory":
        MemoryStore().add("memory", cand["payload"])
        result = f"promoted memory: {cand['payload'][:60]}"
    else:
        p = cand["payload"]
        loader = SkillsLoader(config)
        existing = loader.discover().get(p["name"])
        if existing:                                  # versioning: improve instead of overwrite
            loader.improve(p["name"], f"(learned {now_iso()}) " + p["description"])
            result = f"improved existing skill '{p['name']}'"
        else:
            loader.create(p["name"], p["description"], p["body"])
            result = f"promoted new skill '{p['name']}'"
    cand["status"] = "applied"
    _save(items)
    return result


def reject_candidate(cand_id: str) -> str:
    items = _load()
    cand = next((c for c in items if c["id"].startswith(cand_id) and c["status"] == "pending"), None)
    if not cand:
        return "candidate not found"
    cand["status"] = "rejected"
    _save(items)
    return "rejected"


def cmd_learn(args, config) -> int:
    action = getattr(args, "action", None) or "list"
    if action == "review":
        try:
            found = review_session(config, getattr(args, "id", None))
        except Exception as e:  # noqa: BLE001
            print(f"review failed (needs a working provider/key): {e}")
            return 1
        print(f"proposed {len(found)} candidate(s):")
        for c in found:
            label = c["payload"] if c["type"] == "memory" else c["payload"]["name"]
            print(f"  {c['id']}  [{c['type']}]  {label}")
        print("review with `aegis learn list`, then `aegis learn apply <id>`")
        return 0
    if action == "apply":
        print(apply_candidate(args.id, config) if getattr(args, "id", None) else "usage: aegis learn apply <id>")
        return 0
    if action == "reject":
        print(reject_candidate(args.id) if getattr(args, "id", None) else "usage: aegis learn reject <id>")
        return 0
    # list
    pending = list_candidates()
    if not pending:
        print("(no pending candidates — run `aegis learn review`)")
        return 0
    for c in pending:
        label = c["payload"] if c["type"] == "memory" else c["payload"]["name"]
        print(f"  {c['id']}  [{c['type']}]  {label}")
    return 0
