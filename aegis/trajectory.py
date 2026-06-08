"""Trajectory / research tooling: record sessions as trajectories, export JSONL,
compress, and report metrics (à la Hermes batch trajectory generation).
"""

from __future__ import annotations

import json
from pathlib import Path

from .session import SessionStore
from .util import estimate_tokens


def traj_from_session(sess) -> dict:
    """Convert a Session object into a trajectory dict (messages + token metrics)."""
    steps = []
    for m in sess.messages:
        step = {"role": m.role, "content": m.content}
        if m.tool_calls:
            step["tool_calls"] = [{"name": tc.name, "arguments": tc.arguments} for tc in m.tool_calls]
        if m.tool_call_id:
            step["tool_call_id"] = m.tool_call_id
        if getattr(m, "reasoning", ""):
            step["reasoning"] = m.reasoning
        steps.append(step)
    tokens = sum(estimate_tokens(m.content or "") for m in sess.messages)
    return {"id": sess.id, "title": sess.title, "created_at": sess.created_at,
            "summary": sess.meta.get("summary", ""), "n_steps": len(steps),
            "approx_tokens": tokens, "messages": steps}


def record(session_id: str) -> dict | None:
    """Load a stored session by id and convert it to a trajectory dict."""
    sess = SessionStore().load(session_id)
    return traj_from_session(sess) if sess else None


def _openai_finetune(traj: dict) -> dict:
    """OpenAI chat fine-tune line: {"messages": [...]} with tool_calls/tool roles."""
    msgs = []
    for s in traj.get("messages", []):
        m: dict = {"role": s["role"], "content": s.get("content") or ""}
        if s.get("tool_calls"):
            m["content"] = s.get("content") or None
            m["tool_calls"] = [
                {"id": f"call_{i}", "type": "function",
                 "function": {"name": tc.get("name", ""),
                              "arguments": json.dumps(tc.get("arguments", {}))}}
                for i, tc in enumerate(s["tool_calls"])]
        if s.get("tool_call_id"):
            m["tool_call_id"] = s["tool_call_id"]
        msgs.append(m)
    return {"messages": msgs}


def _sharegpt(traj: dict) -> dict:
    """HuggingFace/ShareGPT conversational line: {"conversations": [{from, value}]}."""
    role_map = {"system": "system", "user": "human", "assistant": "gpt", "tool": "tool"}
    convs = [{"from": role_map.get(s["role"], s["role"]), "value": s.get("content") or ""}
             for s in traj.get("messages", [])]
    return {"conversations": convs}


_FORMATTERS = {
    "aegis": lambda t: t,            # native (full fidelity + metrics)
    "openai": _openai_finetune,      # OpenAI fine-tune JSONL
    "hf": _sharegpt,                 # HuggingFace / ShareGPT
    "sharegpt": _sharegpt,
}


def export(out_path: str, session_ids: list[str] | None = None, fmt: str = "aegis") -> int:
    """Write one trajectory per line to a JSONL file, in the requested format. Returns count."""
    formatter = _FORMATTERS.get(fmt)
    if formatter is None:
        raise ValueError(f"unknown export format '{fmt}' (choose: {', '.join(_FORMATTERS)})")
    store = SessionStore()
    ids = session_ids or [s["id"] for s in store.list(limit=1000)]
    n = 0
    with open(Path(out_path).expanduser(), "w", encoding="utf-8") as f:
        for sid in ids:
            traj = record(sid)
            if traj:
                f.write(json.dumps(formatter(traj)) + "\n")
                n += 1
    return n


def capture_turn(config, session) -> bool:
    """Auto-append the current session as a trajectory when `trajectory.enabled`.

    Honors trajectory.path / trajectory.format / trajectory.include_reasoning /
    trajectory.include_tool_results. Off by default. Returns True if it wrote a line."""
    if not config.get("trajectory.enabled", False) or session is None:
        return False
    from . import config as cfg
    fmt = config.get("trajectory.format", "jsonl")
    # accept friendly aliases; 'jsonl' is the native shape
    fmt = {"jsonl": "aegis", "hf_dataset": "hf", "openai_finetune": "openai"}.get(fmt, fmt)
    formatter = _FORMATTERS.get(fmt, _FORMATTERS["aegis"])
    traj = traj_from_session(session)      # use the live session, no disk reload
    if not traj or not traj["messages"]:
        return False
    if not config.get("trajectory.include_tool_results", True):
        traj["messages"] = [m for m in traj["messages"] if m.get("role") != "tool"]
    if not config.get("trajectory.include_reasoning", False):
        for m in traj["messages"]:
            m.pop("reasoning", None)
    if config.get("trajectory.compress", False):
        traj = compress(traj)
    path = config.get("trajectory.path", "trajectories.jsonl")
    p = Path(path)
    if not p.is_absolute():
        p = Path(cfg.sub(path))                      # default under the AEGIS home
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(formatter(traj)) + "\n")
        return True
    except Exception:  # noqa: BLE001
        from ._log import log_exc
        log_exc("trajectory capture failed")
        return False


def _boundary_truncate(text: str, max_tokens: int) -> str:
    """Truncate near a token budget but on a line/sentence boundary (boundary-aware)."""
    limit = max_tokens * 4
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind("\n"), head.rfind(". "))
    if cut > limit // 2:
        head = head[:cut + 1]
    return head.rstrip() + " …[truncated]"


def compress(traj: dict, provider=None, max_tool_tokens: int = 120) -> dict:
    """Shrink a trajectory token-aware: summarize long tool outputs (LLM) or
    boundary-truncate them. Returns the trajectory with a ``metrics`` block."""
    out = dict(traj)
    steps, before, after, summarized = [], 0, 0, 0
    for step in traj.get("messages", []):
        s = dict(step)
        content = s.get("content", "") or ""
        before += estimate_tokens(content)
        if s.get("role") == "tool" and estimate_tokens(content) > max_tool_tokens:
            if provider is not None:
                try:
                    from .types import Message
                    r = provider.complete([
                        Message.system("Summarize this tool output in ONE terse sentence, "
                                       "preserving key facts, paths, numbers, and errors."),
                        Message.user(content[:8000])], tools=None, stream=False)
                    s["content"] = "[summarized] " + r.text.strip()
                    summarized += 1
                except Exception:  # noqa: BLE001
                    s["content"] = _boundary_truncate(content, max_tool_tokens)
            else:
                s["content"] = _boundary_truncate(content, max_tool_tokens)
        after += estimate_tokens(s["content"])
        steps.append(s)
    out["messages"] = steps
    out["approx_tokens"] = after
    out["metrics"] = {"tokens_before": before, "tokens_after": after,
                      "ratio": round(after / max(1, before), 3), "summarized": summarized}
    return out


def stats() -> dict:
    store = SessionStore()
    ids = [s["id"] for s in store.list(limit=1000)]
    trajs = [t for t in (record(i) for i in ids) if t]
    total_tokens = sum(t["approx_tokens"] for t in trajs)
    total_steps = sum(t["n_steps"] for t in trajs)
    return {"trajectories": len(trajs), "total_steps": total_steps,
            "approx_total_tokens": total_tokens,
            "avg_steps": round(total_steps / max(1, len(trajs)), 1)}


def cmd_trajectory(args, config) -> int:
    action = getattr(args, "action", None) or "stats"
    if action == "export":
        out = getattr(args, "out", None) or "trajectories.jsonl"
        fmt = getattr(args, "format", None) or "aegis"
        try:
            n = export(out, fmt=fmt)
        except ValueError as e:
            print(e)
            return 2
        print(f"exported {n} trajectory(ies) [{fmt}] -> {out}")
        return 0
    if action == "compress":
        out = getattr(args, "out", None) or "trajectories.compressed.jsonl"
        from .session import SessionStore
        provider = None
        if getattr(args, "summarize", False):
            try:
                from .providers.registry import build_aux_provider
                provider = build_aux_provider(config)
            except Exception:  # noqa: BLE001
                provider = None
        ids = [s["id"] for s in SessionStore().list(limit=1000)]
        n, saved = 0, 0
        with open(out, "w", encoding="utf-8") as f:
            for sid in ids:
                t = record(sid)
                if t:
                    c = compress(t, provider)
                    saved += c["metrics"]["tokens_before"] - c["metrics"]["tokens_after"]
                    f.write(json.dumps(c) + "\n")
                    n += 1
        print(f"compressed {n} trajectory(ies) -> {out}  (~{saved:,} tokens saved"
              f"{', LLM-summarized' if provider else ', truncated'})")
        return 0
    # stats
    s = stats()
    for k, v in s.items():
        print(f"  {k:<22} {v}")
    return 0
