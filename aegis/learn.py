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
1. memories — durable facts worth remembering across sessions. Use target "user" for
   who the user is, preferences, communication style, pet peeves, and workflow
   expectations. Use target "memory" for project/tool/environment facts, stable
   AEGIS behavior diagnoses, conventions, and decisions. If one correction contains
   both a user preference and a system/project fact, emit TWO memory objects.
2. skills — reusable step-by-step procedures demonstrated in this session that would help
   next time (only if the session solved a non-trivial, repeatable task).

Reply with ONLY JSON:
{"memories": [{"target": "user|memory", "content": "fact"}, ...],
 "skills": [{"name": "kebab-case-name", "description": "what it does and WHEN to use it",
             "body": "## When to Use\\n...\\n## Procedure\\n1. ..."}]}
Use [] when there is nothing worth saving. Do not include secrets or API keys.
For compatibility, a memory string is allowed and means {"target": "memory", "content": string}."""


def _memory_candidate(mem) -> dict | None:
    if isinstance(mem, str):
        content = mem.strip()
        target = "memory"
    elif isinstance(mem, dict):
        content = str(mem.get("content") or mem.get("text") or mem.get("fact") or "").strip()
        target = str(mem.get("target") or "memory").strip().lower()
    else:
        return None
    if not content:
        return None
    if target not in {"memory", "user"}:
        target = "memory"
    return {"target": target, "content": _redact(content)}


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
        payload = _memory_candidate(mem)
        if payload:
            new_items.append({"id": new_id("cand"), "type": "memory", "session": sess.id,
                              "payload": payload, "status": "pending",
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


# NOTE: the live background-learning path is agent/review.py::maybe_review (spawned per turn
# from Agent.run). This module provides the candidate store + apply_candidate it delegates to.


def apply_candidate(cand_id: str, config) -> str:
    from .memory import MemoryStore
    from .skills import SkillsLoader

    items = _load()
    cand = next((c for c in items if c["id"].startswith(cand_id) and c["status"] == "pending"), None)
    if not cand:
        return "candidate not found"
    if cand["type"] == "memory":
        payload = cand["payload"]
        if isinstance(payload, dict):
            target = str(payload.get("target") or "memory")
            content = str(payload.get("content") or "")
        else:
            target = "memory"
            content = str(payload or "")
        if target not in {"memory", "user"}:
            target = "memory"
        MemoryStore().add(target, content)
        result = f"promoted {target} memory: {content[:60]}"
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
            label = _candidate_label(c)
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
        label = _candidate_label(c)
        print(f"  {c['id']}  [{c['type']}]  {label}")
    return 0


def _candidate_label(c: dict) -> str:
    if c.get("type") != "memory":
        payload = c.get("payload") or {}
        return str(payload.get("name") if isinstance(payload, dict) else payload)
    payload = c.get("payload")
    if isinstance(payload, dict):
        target = str(payload.get("target") or "memory")
        content = str(payload.get("content") or "")
        return f"{target}: {content}"
    return str(payload or "")
