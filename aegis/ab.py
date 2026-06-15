"""Record/replay A/B — re-run a past session against a different model and diff the result.

"Did that change actually help?" turns from vibes into an experiment. Take a stored
session, extract the user turns that drove it (variant A = what happened), replay those same
turns through a fresh session on a different model/provider (variant B), then diff the
outcomes: final answer, which tools each used, and turn counts.

The replay is provider-dependent, so the runner is injectable — the extraction and diff
logic are pure and tested without a model.
"""

from __future__ import annotations

import difflib
from typing import Any, Callable

# A runner takes (prompts, model, provider, config) and returns a result dict with at least
# {"final_text": str, "tools": [str], "turns": int, "session_id": str}.
Runner = Callable[[list[str], str, str, Any], dict]


def extract_user_prompts(session) -> list[str]:
    """The user-authored turns that drove a session (skips system/tool/assistant)."""
    out: list[str] = []
    for m in session.messages:
        if m.role == "user" and (m.content or "").strip():
            out.append(m.content)
    return out


def final_text(session) -> str:
    for m in reversed(session.messages):
        if m.role == "assistant" and (m.content or "").strip():
            return m.content
    return ""


def tools_used(session) -> list[str]:
    """Tool names invoked across a session (in order, with repeats)."""
    names: list[str] = []
    for m in session.messages:
        for tc in (m.tool_calls or []):
            name = getattr(tc, "name", None) or (tc.get("name") if isinstance(tc, dict) else None)
            if name:
                names.append(name)
    return names


def session_result(session) -> dict:
    """Normalize a stored session into the same shape a replay produces (variant A)."""
    return {
        "final_text": final_text(session),
        "tools": tools_used(session),
        "turns": sum(1 for m in session.messages if m.role == "assistant"),
        "session_id": session.id,
    }


def compare_results(a: dict, b: dict, *, label_a: str = "A", label_b: str = "B") -> dict:
    """Diff two result dicts: text similarity + tool-usage delta + turn delta."""
    ta, tb = a.get("final_text", ""), b.get("final_text", "")
    similarity = difflib.SequenceMatcher(None, ta, tb).ratio()
    tools_a, tools_b = set(a.get("tools", [])), set(b.get("tools", []))
    return {
        "label_a": label_a, "label_b": label_b,
        "text_similarity": round(similarity, 3),
        "identical": ta.strip() == tb.strip(),
        "tools_only_a": sorted(tools_a - tools_b),
        "tools_only_b": sorted(tools_b - tools_a),
        "turns_a": a.get("turns", 0), "turns_b": b.get("turns", 0),
        "len_a": len(ta), "len_b": len(tb),
    }


def _default_runner(prompts: list[str], model: str, provider: str, config) -> dict:
    """Replay prompts through a fresh SDK session on the chosen model/provider."""
    from .sdk import AegisClient

    client = AegisClient(config=config)
    session_id = None
    last = None
    try:
        for p in prompts:
            res = client.run(p, session_id=session_id, model=model or None,
                             provider=provider or None, expand_refs=False)
            session_id = res.session.id
            last = res
        sess = client.store.load(session_id) if session_id else None
        if sess is not None:
            return session_result(sess)
        return {"final_text": last.message.content if last and last.message else "",
                "tools": [], "turns": len(prompts), "session_id": session_id or ""}
    finally:
        client.close()


def run_ab(session_id: str, *, model: str = "", provider: str = "", config=None,
           store=None, runner: Runner | None = None) -> dict:
    """Replay a stored session's user turns on a different model and diff against the original."""
    from .session import SessionStore

    store = store or SessionStore()
    original = store.load(session_id)
    if original is None:
        raise LookupError(f"session not found: {session_id}")
    prompts = extract_user_prompts(original)
    if not prompts:
        raise ValueError("session has no user turns to replay")
    variant_a = session_result(original)
    run = runner or _default_runner
    variant_b = run(prompts, model, provider, config)
    label_b = model or provider or "variant-B"
    label_a = (original.meta.get("model") if hasattr(original, "meta") else "") or "original"
    comparison = compare_results(variant_a, variant_b, label_a=str(label_a), label_b=str(label_b))
    return {"prompts": prompts, "a": variant_a, "b": variant_b, "comparison": comparison}


def cmd_ab(args, config) -> int:
    """`aegis ab <session_id> --model X [--provider Y]` — replay + diff."""
    import json

    session_id = getattr(args, "session_id", None)
    if not session_id:
        print("usage: aegis ab <session_id> --model <model> [--provider <name>]")
        return 1
    try:
        result = run_ab(session_id, model=getattr(args, "model", "") or "",
                        provider=getattr(args, "provider", "") or "", config=config)
    except (LookupError, ValueError) as e:
        print(str(e))
        return 1
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
        return 0
    cmp = result["comparison"]
    print(f"A ({cmp['label_a']}) vs B ({cmp['label_b']}) — {len(result['prompts'])} turn(s) replayed")
    print(f"  text similarity: {cmp['text_similarity']:.0%}  "
          f"({'identical' if cmp['identical'] else 'differs'})")
    print(f"  length: A={cmp['len_a']}  B={cmp['len_b']}   turns: A={cmp['turns_a']} B={cmp['turns_b']}")
    if cmp["tools_only_a"]:
        print(f"  tools only in A: {', '.join(cmp['tools_only_a'])}")
    if cmp["tools_only_b"]:
        print(f"  tools only in B: {', '.join(cmp['tools_only_b'])}")
    return 0
