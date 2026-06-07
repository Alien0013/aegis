"""Trajectory / research tooling: record sessions as trajectories, export JSONL,
compress, and report metrics (à la Hermes batch trajectory generation).
"""

from __future__ import annotations

import json
from pathlib import Path

from .session import SessionStore
from .util import estimate_tokens


def record(session_id: str) -> dict | None:
    """Convert a stored session into a trajectory dict (messages + token metrics)."""
    sess = SessionStore().load(session_id)
    if not sess:
        return None
    steps = []
    for m in sess.messages:
        step = {"role": m.role, "content": m.content}
        if m.tool_calls:
            step["tool_calls"] = [{"name": tc.name, "arguments": tc.arguments} for tc in m.tool_calls]
        if m.tool_call_id:
            step["tool_call_id"] = m.tool_call_id
        steps.append(step)
    tokens = sum(estimate_tokens(m.content or "") for m in sess.messages)
    return {"id": sess.id, "title": sess.title, "created_at": sess.created_at,
            "summary": sess.meta.get("summary", ""), "n_steps": len(steps),
            "approx_tokens": tokens, "messages": steps}


def export(out_path: str, session_ids: list[str] | None = None) -> int:
    """Write one trajectory per line to a JSONL file. Returns count."""
    store = SessionStore()
    ids = session_ids or [s["id"] for s in store.list(limit=1000)]
    n = 0
    with open(Path(out_path).expanduser(), "w", encoding="utf-8") as f:
        for sid in ids:
            traj = record(sid)
            if traj:
                f.write(json.dumps(traj) + "\n")
                n += 1
    return n


def compress(traj: dict, provider, max_tool_chars: int = 400) -> dict:
    """Shrink a trajectory: truncate long tool outputs, keep the shape."""
    out = dict(traj)
    new_steps = []
    for step in traj.get("messages", []):
        s = dict(step)
        if s.get("role") == "tool" and len(s.get("content", "")) > max_tool_chars:
            s["content"] = s["content"][:max_tool_chars] + " …[truncated]"
        new_steps.append(s)
    out["messages"] = new_steps
    out["approx_tokens"] = sum(estimate_tokens(s.get("content", "")) for s in new_steps)
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
        n = export(out)
        print(f"exported {n} trajectory(ies) -> {out}")
        return 0
    if action == "compress":
        out = getattr(args, "out", None) or "trajectories.compressed.jsonl"
        from .session import SessionStore
        ids = [s["id"] for s in SessionStore().list(limit=1000)]
        n = 0
        with open(out, "w", encoding="utf-8") as f:
            for sid in ids:
                t = record(sid)
                if t:
                    f.write(json.dumps(compress(t, None)) + "\n")
                    n += 1
        print(f"compressed {n} trajectory(ies) -> {out}")
        return 0
    # stats
    s = stats()
    for k, v in s.items():
        print(f"  {k:<22} {v}")
    return 0
