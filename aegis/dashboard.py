"""Local web dashboard — a self-contained single-page app (no build step).

`aegis dashboard` serves a control UI at http://127.0.0.1:9119: chat, sessions,
memory, skills, tools, and status. The frontend lives in static/dashboard.html
(shipped as package data — a plain file, no node toolchain) and talks to the
JSON API below. Binds loopback by default and can require the configured
dashboard token; do not expose it publicly without trusted network controls.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

from . import __version__
from .config import Config


def _page() -> bytes:
    """The SPA frontend, read from package data (falls back to a stub if missing)."""
    try:
        from importlib import resources
        return (resources.files("aegis") / "static" / "dashboard.html").read_bytes()
    except Exception:  # noqa: BLE001 - broken install; keep the API usable
        return b"<h1>AEGIS</h1><p>dashboard.html missing from install; API is at /api/*</p>"





def _redacted_config(config: Config) -> dict:
    """Flattened config for the UI, with secret-looking values masked (never echo keys)."""
    import re as _re
    secret = _re.compile(r"key|token|secret|password|client_secret", _re.IGNORECASE)
    out: dict[str, str] = {}

    def walk(prefix, node):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(f"{prefix}.{k}" if prefix else k, v)
        else:
            val = node
            if secret.search(prefix) and val:
                val = "••••••" + str(val)[-4:] if len(str(val)) > 4 else "••••••"
            out[prefix] = val

    walk("", getattr(config, "data", {}) or {})
    return out


_COMMON_KEYS = [
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY",
    "GROQ_API_KEY", "DEEPSEEK_API_KEY", "XAI_API_KEY", "MISTRAL_API_KEY",
    "TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN",
    "NTFY_TOPIC", "TAVILY_API_KEY", "BRAVE_API_KEY",
]


def _env_keys() -> list:
    """Known + present env keys with set-status only — values are NEVER returned."""
    from . import config as cfg
    present = set()
    p = cfg.env_path()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                present.add(line.split("=", 1)[0].strip())
    names = list(dict.fromkeys(_COMMON_KEYS + sorted(present)))
    return [{"key": k, "set": k in present} for k in names]


def _profiles(config: Config) -> dict:
    """Available personality files (workspace/personalities/*.md) + the active one."""
    from . import config as cfg
    d = cfg.workspace_dir() / "personalities"
    names = sorted(p.stem for p in d.glob("*.md")) if d.exists() else []
    return {"active": config.get("agent.personality") or "", "available": names}


def _system_info() -> dict:
    """Host + install facts and recent checkpoints for the System tab (no psutil dependency)."""
    import platform
    import shutil
    from . import __version__
    from . import config as cfg
    home = cfg.get_home()
    du = shutil.disk_usage(str(home))
    try:
        from .checkpoints import CheckpointStore
        cps = [{"id": c.id, "label": c.label, "at": getattr(c, "created_at", "")}
               for c in CheckpointStore().list()[:20]]
    except Exception:  # noqa: BLE001
        cps = []
    return {
        "version": __version__,
        "python": platform.python_version(),
        "platform": f"{platform.system()} ({platform.machine()})",
        "aegis_home": str(home),
        "disk_free_gb": round(du.free / 1e9, 1),
        "disk_total_gb": round(du.total / 1e9, 1),
        "checkpoints": cps,
    }


def _int_param(query: dict, name: str, default: int, *, lo: int = 1, hi: int = 200) -> int:
    try:
        n = int((query.get(name, [str(default)])[0]) or default)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def _str_param(query: dict, *names: str) -> str:
    for name in names:
        value = query.get(name, [""])
        raw = value[0] if isinstance(value, list) and value else value
        text = str(raw or "").strip()
        if text:
            return text
    return ""


def _jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()
                if k != "agent" and not str(k).startswith("_")}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)


def _session_metrics(sess) -> dict:
    counts: dict[str, int] = {}
    tool_counts: dict[str, int] = {}
    for m in sess.messages:
        counts[m.role] = counts.get(m.role, 0) + 1
        for tc in getattr(m, "tool_calls", []) or []:
            tool_counts[tc.name] = tool_counts.get(tc.name, 0) + 1
    goal = sess.meta.get("goal") if isinstance(sess.meta, dict) else None
    comps = sess.meta.get("compactions", []) if isinstance(sess.meta, dict) else []
    return {
        "messages": len(sess.messages),
        "roles": counts,
        "tool_calls": sum(tool_counts.values()),
        "tools": [{"name": name, "calls": n} for name, n in sorted(tool_counts.items())],
        "compactions": len(comps) if isinstance(comps, list) else 0,
        "goal": {
            "text": goal.get("text", ""),
            "status": goal.get("status", ""),
            "turns_used": goal.get("turns_used", 0),
            "max_turns": goal.get("max_turns", 0),
        } if isinstance(goal, dict) and goal.get("text") else None,
    }


def _recent_sessions(limit: int = 50) -> list[dict]:
    from .session import SessionStore
    store = SessionStore()
    out = []
    for meta in store.list(limit):
        sess = store.load(meta["id"])
        if sess is None:
            continue
        out.append({**meta, **_session_metrics(sess),
                    "summary": sess.meta.get("summary", "") if isinstance(sess.meta, dict) else "",
                    "runtime": _jsonable(sess.meta.get("runtime", {})) if isinstance(sess.meta, dict) else {},
                    "usage": _jsonable(sess.meta.get("usage", {})) if isinstance(sess.meta, dict) else {}})
    return out


def _trace_db_path(config: Config | None = None) -> Path:
    from . import config as cfg
    raw = config.get("tracing.path", "traces.db") if config else "traces.db"
    path = Path(str(raw)).expanduser()
    return path if path.is_absolute() else cfg.sub(str(raw))


def _eval_db_path(config: Config | None = None) -> Path:
    from . import config as cfg
    raw = str(config.get("evals.path", "evals") if config else "evals")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = cfg.sub(raw)
    return path if path.suffix else path / "runs.db"


def _normalize_trace_row(row: dict) -> dict:
    if "trace_id" not in row:
        return row
    raw_spans = row.get("spans")
    raw_span_summary = raw_spans if isinstance(raw_spans, dict) else {}
    span_count = int(row.get("span_count") or raw_span_summary.get("span_count")
                     or raw_span_summary.get("messages") or (raw_spans if isinstance(raw_spans, int) else 0) or 0)
    kind_counts = row.get("kind_counts") if isinstance(row.get("kind_counts"), dict) else {}
    provider_calls = int(row.get("provider_calls") or raw_span_summary.get("provider_calls")
                         or kind_counts.get("provider_call") or kind_counts.get("model") or 0)
    tool_calls = int(row.get("tool_calls") or raw_span_summary.get("tool_calls") or kind_counts.get("tool") or 0)
    compactions = int(row.get("compactions") or raw_span_summary.get("compactions")
                      or kind_counts.get("compaction") or kind_counts.get("compact") or 0)
    errors = int(row.get("error_spans") or raw_span_summary.get("errors") or 0)
    return {
        **row,
        "id": row.get("trace_id"),
        "title": row.get("trace_id"),
        "source": "trace_store",
        "updated_at": row.get("ended_at") or row.get("started_at") or "",
        "spans": {
            "messages": span_count,
            "span_count": span_count,
            "provider_calls": provider_calls,
            "tool_calls": tool_calls,
            "compactions": compactions,
            "errors": errors,
        },
        "providers": row.get("providers") or [],
        "models": row.get("models") or [],
        "tools": row.get("tools") or [],
        "duration_ms": int(row.get("duration_ms") or row.get("latency_ms") or 0),
        "latency_ms": int(row.get("latency_ms") or row.get("duration_ms") or 0),
        "input_tokens": int(row.get("input_tokens") or 0),
        "output_tokens": int(row.get("output_tokens") or 0),
        "cache_read": int(row.get("cache_read") or 0),
        "cache_write": int(row.get("cache_write") or 0),
        "cost": float(row.get("cost") or 0),
    }


def _runtime_trace_rows(
    limit: int,
    config: Config | None = None,
    *,
    session_id: str = "",
) -> tuple[str, list[dict]] | None:
    """Use a future runtime trace module if it is installed; otherwise return None."""
    import importlib

    for modname in ("aegis.tracing", "aegis.runtime.traces", "aegis.runtime_trace", "aegis.traces"):
        try:
            mod = importlib.import_module(modname)
        except Exception:  # noqa: BLE001 - optional integration point
            continue
        store_cls = getattr(mod, "TraceStore", None)
        if store_cls is not None:
            path = _trace_db_path(config)
            if not path.exists():
                continue
            try:
                rows = store_cls(path).list_traces(session_id=session_id or None, limit=limit)
            except Exception:  # noqa: BLE001
                rows = []
            return modname, [_normalize_trace_row(_jsonable(r)) for r in list(rows or [])[:limit]]
        for name in ("list_traces", "recent_traces", "list"):
            fn = getattr(mod, name, None)
            if not callable(fn):
                continue
            try:
                rows = fn(limit=limit)
            except TypeError:
                rows = fn(limit)
            except Exception:  # noqa: BLE001
                rows = []
            if isinstance(rows, dict):
                rows = rows.get("traces") or rows.get("items") or rows.get("spans") or []
            return modname, [_jsonable(r) for r in list(rows or [])[:limit]]
    return None


def _dashboard_traces(query: dict, config: Config | None = None) -> dict:
    limit = _int_param(query, "limit", 50)
    session_id = _str_param(query, "session_id", "session")
    status = _str_param(query, "status")
    source_filter = _str_param(query, "source")
    runtime = _runtime_trace_rows(limit, config, session_id=session_id)
    if runtime is not None:
        source, rows = runtime
        rows = _filter_trace_rows(rows, status=status, source=source_filter)
        return {"available": True, "source": source, "traces": rows,
                "summary": {"total": len(rows), "source": source,
                            "filters": {"session_id": session_id, "status": status,
                                        "source": source_filter}}}
    rows = []
    for s in _recent_sessions(limit):
        if session_id and session_id not in str(s.get("id", "")):
            continue
        rows.append({
            "id": s["id"],
            "title": s.get("title", ""),
            "source": "session",
            "started_at": s.get("created_at", ""),
            "updated_at": s.get("updated_at", ""),
            "status": "goal_active" if (s.get("goal") or {}).get("status") == "active" else "recorded",
            "spans": {
                "messages": s.get("messages", 0),
                "span_count": s.get("messages", 0),
                "provider_calls": 0,
                "tool_calls": s.get("tool_calls", 0),
                "compactions": s.get("compactions", 0),
                "errors": 0,
            },
            "tools": s.get("tools", []),
            "providers": [s.get("runtime", {}).get("provider")] if s.get("runtime", {}).get("provider") else [],
            "models": [s.get("runtime", {}).get("model")] if s.get("runtime", {}).get("model") else [],
            "cache_read": int(s.get("usage", {}).get("cache_read", 0) or 0),
            "cache_write": int(s.get("usage", {}).get("cache_write", 0) or 0),
        })
    rows = _filter_trace_rows(rows, status=status, source=source_filter)
    try:
        from .trajectory import stats
        summary = stats()
    except Exception:  # noqa: BLE001
        summary = {"trajectories": len(rows)}
    summary["filters"] = {"session_id": session_id, "status": status, "source": source_filter}
    return {"available": bool(rows), "source": "sessions", "traces": rows, "summary": summary,
            "note": "Runtime trace store not present; showing session-derived traces."}


def _filter_trace_rows(rows: list[dict], *, status: str = "", source: str = "") -> list[dict]:
    status_l = status.lower()
    source_l = source.lower()
    filtered: list[dict] = []
    for row in rows:
        if status_l and status_l not in str(row.get("status", "")).lower():
            continue
        if source_l and source_l not in str(row.get("source", "")).lower():
            continue
        filtered.append(row)
    return filtered


def _dashboard_runs(query: dict) -> dict:
    limit = _int_param(query, "limit", 50)
    surface = _str_param(query, "surface")
    status = _str_param(query, "status")
    session_id = _str_param(query, "session_id", "session")
    try:
        from .runs import RunStore
        rows = RunStore().list(
            limit=limit,
            surface=surface or None,
            status=status or None,
            session_id=session_id or None,
        )
        if rows:
            return {"runs": [_dashboard_run_row(r) for r in rows],
                    "summary": {"total": len(rows), "source": "runs",
                                "filters": {"surface": surface, "status": status,
                                            "session_id": session_id}}}
    except Exception:  # noqa: BLE001
        pass
    runs = []
    for s in _recent_sessions(limit):
        goal = s.get("goal") or {}
        runs.append({
            "id": s["id"],
            "kind": "session",
            "title": s.get("title", ""),
            "status": "active_goal" if goal.get("status") == "active" else "completed",
            "started_at": s.get("created_at", ""),
            "updated_at": s.get("updated_at", ""),
            "turns": s.get("roles", {}).get("user", 0),
            "messages": s.get("messages", 0),
            "tool_calls": s.get("tool_calls", 0),
            "summary": s.get("summary", ""),
        })
    try:
        from .background import get_manager
        for t in get_manager().list():
            runs.append({"id": t["id"], "kind": "background", "title": t["prompt"],
                         "status": t["status"], "preview": t.get("result_preview", "")})
    except Exception:  # noqa: BLE001
        pass
    return {"runs": runs[:limit], "summary": {"total": len(runs)}}


def _dashboard_run_row(run: dict) -> dict:
    return {
        "id": run.get("id", ""),
        "kind": run.get("kind", ""),
        "surface": run.get("surface", ""),
        "title": run.get("title", "") or run.get("prompt_preview", "")[:60],
        "status": run.get("status", ""),
        "started_at": run.get("started_at", ""),
        "updated_at": run.get("ended_at") or run.get("started_at", ""),
        "ended_at": run.get("ended_at", ""),
        "session_id": run.get("session_id", ""),
        "trace_id": run.get("trace_id", ""),
        "summary": run.get("result_preview", ""),
        "preview": run.get("prompt_preview", ""),
        "error": run.get("error", ""),
        "data": run.get("data", {}),
    }


def _latest_trace_id_for_session(config: Config | None, session_id: str) -> str:
    if not session_id:
        return ""
    try:
        from .tracing import TraceStore
        store = TraceStore.from_config(config or Config.load())
        traces = store.list_traces(session_id=session_id, limit=1)
    except Exception:  # noqa: BLE001
        return ""
    if not traces:
        return ""
    return str(traces[0].get("trace_id") or traces[0].get("id") or "")


def _dashboard_agent_from_run(run: dict, config: Config | None = None) -> dict:
    row = _dashboard_run_row(run)
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    trace_id = row.get("trace_id") or _latest_trace_id_for_session(config, row.get("session_id", ""))
    return {
        "id": row["id"],
        "kind": "active_run" if row.get("status") == "running" else "run",
        "type": row.get("surface") or row.get("kind") or "agent",
        "status": row.get("status") or "recorded",
        "task": row.get("title") or row.get("preview") or "",
        "preview": row.get("summary") or row.get("preview") or "",
        "run_id": row["id"],
        "session_id": row.get("session_id", ""),
        "trace_id": trace_id,
        "surface": row.get("surface", ""),
        "model": data.get("model") or "",
        "provider": data.get("provider") or "",
        "started_at": row.get("started_at", ""),
        "updated_at": row.get("updated_at", ""),
    }


def _dashboard_active_run_agents(config: Config) -> list[dict]:
    try:
        from .runs import RunStore
        rows = RunStore().list(limit=50, status="running")
    except Exception:  # noqa: BLE001
        return []
    return [_dashboard_agent_from_run(row, config) for row in rows]


def _chat_event_row(event: dict) -> dict:
    etype = str(event.get("type") or "")
    row = {
        "type": etype,
        "name": str(event.get("name") or event.get("tool_name") or ""),
        "summary": str(event.get("summary") or event.get("message") or event.get("reason") or ""),
        "status": "error" if event.get("is_error") else str(event.get("status") or ""),
    }
    if etype == "tool_start":
        args = event.get("args") if isinstance(event.get("args"), dict) else {}
        row["target"] = str(
            args.get("command") or args.get("path") or args.get("url") or args.get("query") or ""
        )[:240]
    elif etype == "tool_result":
        row["target"] = str(event.get("preview") or event.get("summary") or "")[:240]
    elif etype == "iteration":
        row["summary"] = f"{event.get('n', '')}/{event.get('max', '')}".strip("/")
    elif etype == "reasoning_delta":
        row["summary"] = "thinking"
    return row


def _dashboard_chat_cwd(body: dict) -> str:
    raw = body.get("cwd") or body.get("project") or body.get("worktree") or ""
    text = str(raw).strip()
    return str(Path(text).expanduser()) if text else ""


def _dashboard_chat_meta(body: dict, route: str) -> dict:
    cwd = _dashboard_chat_cwd(body)
    meta = {"surface_route": route}
    if cwd:
        meta["dashboard_cwd"] = cwd
    return meta


def _dashboard_chat_runtime(body: dict) -> dict:
    model = str(body.get("model") or "").strip()
    provider = str(body.get("provider") or body.get("provider_name") or "").strip()
    out = {}
    if model:
        out["model"] = model
    if provider:
        out["provider_name"] = provider
    return out


def _publish_dashboard_chat_event(
    body: dict,
    etype: str,
    *,
    row: dict | None = None,
    result: Any | None = None,
    cwd: str = "",
) -> None:
    try:
        from .eventbus import BUS

        session_id = str(
            getattr(getattr(result, "session", None), "id", "")
            or body.get("session_id")
            or ""
        )
        event = {
            "platform": "dashboard",
            "chat_id": session_id,
            "session_id": session_id,
            "type": etype,
            "surface": "dashboard",
            "cwd": cwd,
        }
        if row:
            event.update({k: v for k, v in row.items() if v not in ("", None)})
            event["type"] = str(row.get("type") or etype)
        if result is not None:
            for attr, key in (("run_id", "run_id"), ("trace_id", "trace_id"), ("turn_id", "turn_id")):
                value = str(getattr(result, attr, "") or "")
                if value:
                    event[key] = value
        if etype == "chat_start":
            event["text"] = str(body.get("message", ""))[:240]
        elif etype == "chat_final":
            event["text"] = str(getattr(result, "text", "") or "")[:240]
        BUS.publish(event)
    except Exception:  # noqa: BLE001
        pass


def _dashboard_chat_response(body: dict, chat_runner) -> dict:
    events: list[dict] = []
    result = None
    cwd = _dashboard_chat_cwd(body)
    _publish_dashboard_chat_event(body, "chat_start", cwd=cwd)

    def on_event(event: dict) -> None:
        events.append(dict(event))
        _publish_dashboard_chat_event(body, str(event.get("type") or "event"),
                                      row=_chat_event_row(event), cwd=cwd)

    try:
        result = chat_runner.run_prompt(
            body.get("message", ""),
            session_id=body.get("session_id") or None,
            cwd=cwd or None,
            **_dashboard_chat_runtime(body),
            surface="dashboard",
            meta=_dashboard_chat_meta(body, "/api/chat"),
            on_event=on_event,
        )
        reply = result.text
        _publish_dashboard_chat_event(body, "chat_final", result=result, cwd=cwd)
    except Exception as e:  # noqa: BLE001
        reply = f"error: {e}"
        _publish_dashboard_chat_event(body, "chat_error", cwd=cwd,
                                      row={"type": "chat_error", "summary": str(e)})
    return {
        "reply": reply or "(no response)",
        "session_id": result.session.id if result else (body.get("session_id") or ""),
        "trace_id": result.trace_id if result else "",
        "turn_id": result.turn_id if result else "",
        "run_id": result.run_id if result else "",
        "cwd": cwd,
        "events": [_chat_event_row(e) for e in events[-80:]],
    }


def _dashboard_chat_stream(body: dict, chat_runner, send) -> dict:
    events: list[dict] = []
    result = None
    cwd = _dashboard_chat_cwd(body)
    _publish_dashboard_chat_event(body, "chat_start", cwd=cwd)
    send({"type": "start", "session_id": body.get("session_id") or "", "cwd": cwd})

    def on_event(event: dict) -> None:
        events.append(dict(event))
        row = _chat_event_row(event)
        _publish_dashboard_chat_event(body, str(event.get("type") or "event"), row=row, cwd=cwd)
        send({"type": "event", "event": row})

    try:
        result = chat_runner.run_prompt(
            body.get("message", ""),
            session_id=body.get("session_id") or None,
            cwd=cwd or None,
            **_dashboard_chat_runtime(body),
            surface="dashboard",
            meta=_dashboard_chat_meta(body, "/api/chat/stream"),
            on_event=on_event,
        )
        final = {
            "type": "final",
            "reply": result.text or "(no response)",
            "session_id": result.session.id,
            "trace_id": result.trace_id,
            "turn_id": result.turn_id,
            "run_id": result.run_id,
            "cwd": cwd,
            "events": [_chat_event_row(e) for e in events[-80:]],
        }
        _publish_dashboard_chat_event(body, "chat_final", result=result, cwd=cwd)
        send(final)
        return final
    except Exception as e:  # noqa: BLE001
        final = {
            "type": "error",
            "reply": f"error: {e}",
            "session_id": body.get("session_id") or "",
            "trace_id": "",
            "turn_id": "",
            "run_id": "",
            "cwd": cwd,
            "events": [_chat_event_row(ev) for ev in events[-80:]],
        }
        _publish_dashboard_chat_event(body, "chat_error", cwd=cwd,
                                      row={"type": "chat_error", "summary": str(e)})
        send(final)
        return final


def _dashboard_models(config: Config) -> dict:
    from .providers import registry
    from .providers.registry import provider_report

    report = provider_report(config)
    provider_names = sorted(({
        str(row.get("name")) for row in report.get("provider_catalog", [])
        if row.get("name")
    } | {str(config.get("model.provider") or "")}) - {""})
    report.update({
        "provider": config.get("model.provider"),
        "model": config.get("model.default"),
        "providers": provider_names,
        "presets": {p: registry.known_models_for(p, config) for p in provider_names},
    })
    return report


def _message_detail(message, index: int) -> dict:
    row = {
        "index": index,
        "role": getattr(message, "role", ""),
        "content": getattr(message, "content", ""),
    }
    tool_calls = getattr(message, "tool_calls", []) or []
    if tool_calls:
        row["tool_calls"] = [_jsonable(tc.to_dict() if hasattr(tc, "to_dict") else tc)
                             for tc in tool_calls]
    for attr in ("tool_call_id", "name", "reasoning", "images"):
        value = getattr(message, attr, None)
        if value:
            row[attr] = _jsonable(value)
    return row


def _session_prompt_detail(sess) -> dict:
    meta = getattr(sess, "meta", {}) if hasattr(sess, "meta") else {}
    meta = meta if isinstance(meta, dict) else {}
    parts = [_jsonable(p) for p in (meta.get("prompt_parts") or []) if isinstance(p, dict)]
    by_tier: dict[str, list[dict]] = {}
    for part in parts:
        by_tier.setdefault(str(part.get("tier") or "other"), []).append(part)
    prompt_text = ""
    for message in getattr(sess, "messages", []) or []:
        if getattr(message, "role", "") == "system":
            prompt_text = getattr(message, "content", "") or ""
            break
    runtime = meta.get("runtime") if isinstance(meta.get("runtime"), dict) else {}
    controls = meta.get("runtime_controls") if isinstance(meta.get("runtime_controls"), dict) else {}
    last_refs = meta.get("last_context_references") if isinstance(meta.get("last_context_references"), dict) else {}
    ref_history = [r for r in (meta.get("context_references") or []) if isinstance(r, dict)]
    return {
        "hash": meta.get("system_prompt_hash", ""),
        "chars": int(meta.get("system_prompt_chars", 0) or 0),
        "tokens": int(meta.get("system_prompt_tokens", 0) or 0),
        "part_count": len(parts),
        "parts": parts,
        "tiers": by_tier,
        "runtime": _jsonable(runtime),
        "runtime_controls": _jsonable(controls),
        "context_references": _jsonable(last_refs),
        "context_reference_history": _jsonable(ref_history[-10:]),
        "preview": prompt_text[:1200],
    }


def _dashboard_session_links(sess, config: Config | None = None, *, limit: int = 10) -> dict:
    try:
        from .runs import RunStore
        runs = [_dashboard_run_row(r) for r in RunStore().list(session_id=sess.id, limit=limit)]
    except Exception:  # noqa: BLE001
        runs = []
    try:
        trace_payload = _dashboard_traces({"session_id": [sess.id], "limit": [str(limit)]}, config)
        traces = list(trace_payload.get("traces") or [])[:limit]
        trace_source = str(trace_payload.get("source") or "")
    except Exception:  # noqa: BLE001
        traces = []
        trace_source = ""

    meta = getattr(sess, "meta", {}) if hasattr(sess, "meta") else {}
    meta = meta if isinstance(meta, dict) else {}
    meta_trace_ids = [
        str(meta.get(key) or "") for key in ("last_trace_id", "trace_id")
        if meta.get(key)
    ]
    trace_ids = []
    for value in [r.get("trace_id", "") for r in runs] + \
            [t.get("trace_id") or t.get("id", "") for t in traces] + meta_trace_ids:
        value = str(value or "")
        if value and value not in trace_ids:
            trace_ids.append(value)
    run_ids = [str(r.get("id") or "") for r in runs if r.get("id")]
    return {
        "runs": runs,
        "traces": _jsonable(traces),
        "links": {
            "run_ids": run_ids,
            "trace_ids": trace_ids,
            "latest_run_id": run_ids[0] if run_ids else str(meta.get("last_run_id", "")),
            "latest_trace_id": trace_ids[0] if trace_ids else str(meta.get("last_trace_id", "")),
            "trace_source": trace_source,
        },
    }


def _dashboard_session_detail(session_id: str, config: Config | None = None) -> dict:
    from .session import SessionStore
    store = SessionStore()
    sess = store.load(session_id)
    if sess is None:
        return {"found": False, "id": session_id, "error": "session not found"}
    metrics = _session_metrics(sess)
    children = store.children(sess.id)
    parent = store.load(sess.parent_id) if sess.parent_id else None
    linked = _dashboard_session_links(sess, config)
    return {
        "found": True,
        "id": sess.id,
        "kind": "session",
        "title": sess.title,
        "created_at": sess.created_at,
        "updated_at": sess.updated_at,
        "parent_id": sess.parent_id,
        "parent": {
            "id": parent.id,
            "title": parent.title,
            "updated_at": parent.updated_at,
        } if parent is not None else None,
        "children": _jsonable(children),
        "metrics": metrics,
        "prompt": _session_prompt_detail(sess),
        "messages": [_message_detail(m, i) for i, m in enumerate(sess.messages)],
        "runs": linked["runs"],
        "traces": linked["traces"],
        "links": linked["links"],
        "todos": _jsonable(sess.todos),
        "meta": _jsonable(sess.meta),
    }


def _dashboard_branch_session(session_id: str, *, title: str = "", reason: str = "dashboard") -> dict:
    from .session import SessionStore
    store = SessionStore()
    parent = store.load(session_id)
    if parent is None:
        return {"ok": False, "found": False, "id": session_id, "error": "session not found"}
    child = store.fork(parent)
    if title.strip():
        child.title = title.strip()
    child.meta["creator_kind"] = "dashboard_branch"
    child.meta["reason"] = reason or "dashboard"
    child.meta["branch_root"] = parent.meta.get("lineage_root") or parent.parent_id or parent.id
    parent.meta.setdefault("child_sessions", [])
    if child.id not in parent.meta["child_sessions"]:
        parent.meta["child_sessions"].append(child.id)
    store.save(parent)
    store.save(child)
    return {
        "ok": True,
        "session_id": child.id,
        "parent_id": parent.id,
        "session": _dashboard_session_detail(child.id),
        "parent": _dashboard_session_detail(parent.id),
    }


def _session_span_rows(session_detail: dict) -> list[dict]:
    spans = []
    call_parents: dict[str, str] = {}
    for msg in session_detail.get("messages", []):
        idx = msg.get("index", len(spans))
        span_id = f"msg_{idx}"
        for call in msg.get("tool_calls", []) or []:
            if isinstance(call, dict) and call.get("id"):
                call_parents[str(call["id"])] = span_id
        tool_call_id = str(msg.get("tool_call_id") or "")
        spans.append({
            "span_id": span_id,
            "trace_id": session_detail.get("id", ""),
            "session_id": session_detail.get("id", ""),
            "turn_id": str(idx),
            "parent_span_id": call_parents.get(tool_call_id, ""),
            "kind": "tool" if msg.get("role") == "tool" else "message",
            "status": "ok",
            "started_at": "",
            "ended_at": "",
            "provider": "",
            "model": "",
            "tool_name": msg.get("name", ""),
            "role": msg.get("role", ""),
            "duration_ms": 0,
            "latency_ms": 0,
            "data": {
                "role": msg.get("role", ""),
                "content": msg.get("content", ""),
                "tool_calls": msg.get("tool_calls", []),
                "tool_call_id": tool_call_id,
                "tool_name": msg.get("name", ""),
            },
        })
    return spans


def _dashboard_trace_detail(query: dict, config: Config | None = None) -> dict:
    trace_id = ((query.get("id", [""])[0]) or "").strip()
    if not trace_id:
        return {"found": False, "error": "missing id"}

    trace_path = _trace_db_path(config)
    if trace_path.exists():
        try:
            from .tracing import TraceStore
            store = TraceStore(trace_path)
            trace = store.get_trace(trace_id)
        except Exception:  # noqa: BLE001
            trace = None
            store = None
        if trace:
            detail = _jsonable(trace)
            summary = _normalize_trace_row({k: v for k, v in detail.items() if k != "spans"})
            try:
                from . import evals as eval_mod
                evaluation = _jsonable(eval_mod.evaluate_trace(trace_id, store=store))
                replay = _jsonable(eval_mod.replay_trace(trace_id, store=store).to_dict())
            except Exception:  # noqa: BLE001
                evaluation = {}
                replay = {}
            return {
                "found": True,
                "id": trace_id,
                "source": "aegis.tracing",
                "trace": summary,
                "spans": detail.get("spans", []),
                "replay": replay,
                "grades": evaluation.get("grades", []),
                "evaluation": evaluation,
            }

    session = _dashboard_session_detail(trace_id, config)
    if session.get("found"):
        spans = _session_span_rows(session)
        try:
            from . import evals as eval_mod
            evaluation = _jsonable(eval_mod.evaluate_session(trace_id))
            replay = _jsonable(eval_mod.replay_session(trace_id).to_dict())
        except Exception:  # noqa: BLE001
            evaluation = {}
            replay = {}
        metrics = session.get("metrics", {})
        usage = session.get("meta", {}).get("usage", {}) if isinstance(session.get("meta"), dict) else {}
        runtime = session.get("meta", {}).get("runtime", {}) if isinstance(session.get("meta"), dict) else {}
        return {
            "found": True,
            "id": trace_id,
            "source": "session",
            "trace": {
                "id": trace_id,
                "title": session.get("title", trace_id),
                "status": "recorded",
                "started_at": session.get("created_at", ""),
                "updated_at": session.get("updated_at", ""),
                "spans": {
                    "messages": metrics.get("messages", 0),
                    "span_count": metrics.get("messages", 0),
                    "provider_calls": 0,
                    "tool_calls": metrics.get("tool_calls", 0),
                    "compactions": metrics.get("compactions", 0),
                    "errors": 0,
                },
                "providers": [runtime.get("provider")] if runtime.get("provider") else [],
                "models": [runtime.get("model")] if runtime.get("model") else [],
                "cache_read": int(usage.get("cache_read", 0) or 0),
                "cache_write": int(usage.get("cache_write", 0) or 0),
            },
            "spans": spans,
            "messages": session.get("messages", []),
            "replay": replay,
            "grades": evaluation.get("grades", []),
            "evaluation": evaluation,
        }
    return {"found": False, "id": trace_id, "error": "trace not found"}


def _dashboard_run_detail(query: dict, config: Config | None = None) -> dict:
    run_id = ((query.get("id", [""])[0]) or "").strip()
    if not run_id:
        return {"found": False, "error": "missing id"}

    try:
        from .runs import RunStore
        stored = RunStore().get(run_id)
    except Exception:  # noqa: BLE001
        stored = None
    if stored:
        session_id = stored.get("session_id", "")
        trace_id = stored.get("trace_id", "")
        session = _dashboard_session_detail(session_id, config) if session_id else None
        trace = _dashboard_trace_detail({"id": [trace_id]}, config) if trace_id else None
        return {
            "found": True,
            "id": stored["id"],
            "run": _dashboard_run_row(stored),
            "session": session,
            "trace": trace,
            "messages": (session or {}).get("messages", []),
        }

    session = _dashboard_session_detail(run_id, config)
    if session.get("found"):
        goal = (session.get("metrics") or {}).get("goal") or {}
        run = {
            "id": run_id,
            "kind": "session",
            "title": session.get("title", ""),
            "status": "active_goal" if goal.get("status") == "active" else "completed",
            "started_at": session.get("created_at", ""),
            "updated_at": session.get("updated_at", ""),
            "turns": (session.get("metrics") or {}).get("roles", {}).get("user", 0),
            "messages": (session.get("metrics") or {}).get("messages", 0),
            "tool_calls": (session.get("metrics") or {}).get("tool_calls", 0),
            "summary": (session.get("meta") or {}).get("summary", ""),
        }
        return {"found": True, "id": run_id, "run": run, "session": session,
                "messages": session.get("messages", [])}

    try:
        from .background import get_manager
        for task in get_manager().list():
            if task.get("id") == run_id:
                return {"found": True, "id": run_id, "run": _jsonable(task),
                        "messages": [], "note": "background task details are limited to task metadata"}
    except Exception:  # noqa: BLE001
        pass
    return {"found": False, "id": run_id, "error": "run not found"}


def _dashboard_agents(config: Config) -> dict:
    active_runs = _dashboard_active_run_agents(config)
    agents = [{
        "id": "primary",
        "kind": "agent",
        "type": "general",
        "status": "running" if active_runs else "configured",
        "provider": config.get("model.provider"),
        "model": config.get("model.default"),
        "toolsets": config.get("tools.toolsets", []),
        "active_runs": len(active_runs),
    }]
    agents.extend(active_runs)
    types = []
    try:
        from .tools.agentic import AGENT_TYPES, _REGISTRY, _REG_LOCK
        for name, spec in AGENT_TYPES.items():
            tools = spec.get("tools")
            types.append({"name": name, "tools": "all" if tools is None else sorted(tools),
                          "readonly": tools is not None})
        with _REG_LOCK:
            for sid, entry in list(_REGISTRY.items())[-50:]:
                safe = _jsonable({k: v for k, v in entry.items() if k != "agent"})
                safe.update({"id": sid, "kind": "subagent", "continuable": "agent" in entry})
                agents.append(safe)
    except Exception:  # noqa: BLE001
        types = []
    try:
        from .background import get_manager
        for task in get_manager().list():
            run_id = task.get("run_id", "")
            trace_id = ""
            session_id = f"background:{task['id']}"
            if run_id:
                try:
                    from .runs import RunStore
                    run = RunStore().get(run_id)
                    if run:
                        trace_id = run.get("trace_id", "")
                        session_id = run.get("session_id", session_id) or session_id
                except Exception:  # noqa: BLE001
                    pass
            agents.append({"id": task["id"], "kind": "background", "type": "general",
                           "status": task["status"], "task": task["prompt"],
                           "preview": task.get("result_preview", ""),
                           "run_id": run_id, "session_id": session_id, "trace_id": trace_id})
    except Exception:  # noqa: BLE001
        pass
    return {"agents": agents, "active_runs": active_runs, "types": types}


def _dashboard_agent_detail(query: dict, config: Config) -> dict:
    agent_id = ((query.get("id", [""])[0]) or "").strip()
    if not agent_id:
        return {"found": False, "error": "missing id"}
    if agent_id == "primary":
        try:
            from .runs import RunStore
            recent = RunStore().list(limit=10)
        except Exception:  # noqa: BLE001
            recent = []
        active_runs = _dashboard_active_run_agents(config)
        return {
            "found": True,
            "id": "primary",
            "agent": {
                "id": "primary",
                "kind": "agent",
                "type": "general",
                "status": "running" if active_runs else "configured",
                "provider": config.get("model.provider"),
                "model": config.get("model.default"),
                "toolsets": config.get("tools.toolsets", []),
                "active_runs": len(active_runs),
            },
            "active_runs": active_runs,
            "runs": [_dashboard_run_row(r) for r in recent],
        }

    run_detail = None
    try:
        from .runs import RunStore
        run = RunStore().get(agent_id)
    except Exception:  # noqa: BLE001
        run = None
    if run is not None:
        agent = _dashboard_agent_from_run(run, config)
        run_detail = _dashboard_run_detail({"id": [run["id"]]}, config)
        session_id = str(agent.get("session_id") or "")
        trace_id = str(agent.get("trace_id") or "")
        session = _dashboard_session_detail(session_id, config) if session_id else None
        trace = _dashboard_trace_detail({"id": [trace_id]}, config) if trace_id else None
        return {
            "found": True,
            "id": agent_id,
            "agent": agent,
            "run": (run_detail or {}).get("run") if run_detail else None,
            "session": session,
            "trace": trace,
            "messages": (session or {}).get("messages", []),
        }

    agent: dict | None = None
    try:
        from .tools.agentic import _REGISTRY, _REG_LOCK
        with _REG_LOCK:
            entry = _REGISTRY.get(agent_id)
            if entry is None:
                entry = next((v for k, v in _REGISTRY.items() if k.startswith(agent_id)), None)
                if entry is not None:
                    agent_id = next(k for k, v in _REGISTRY.items() if v is entry)
            if entry is not None:
                agent = _jsonable({k: v for k, v in entry.items() if k != "agent"})
                agent.update({"id": agent_id, "kind": "subagent", "continuable": "agent" in entry})
    except Exception:  # noqa: BLE001
        agent = None

    if agent is None:
        try:
            from .background import get_manager
            task = get_manager().get(agent_id)
            if task is not None:
                agent = {
                    "id": task.id,
                    "kind": "background",
                    "type": "general",
                    "status": task.status,
                    "task": task.prompt,
                    "preview": (task.result or task.error)[:500],
                    "run_id": task.run_id,
                    "session_id": f"background:{task.id}",
                }
        except Exception:  # noqa: BLE001
            agent = None

    if agent is None:
        return {"found": False, "id": agent_id, "error": "agent not found"}

    run_id = str(agent.get("run_id") or "")
    session_id = str(agent.get("session_id") or "")
    trace_id = str(agent.get("trace_id") or "")
    run_detail = _dashboard_run_detail({"id": [run_id]}, config) if run_id else None
    if run_detail and run_detail.get("found"):
        run = run_detail.get("run") or {}
        session_id = session_id or str(run.get("session_id") or "")
        trace_id = trace_id or str(run.get("trace_id") or "")
    session = _dashboard_session_detail(session_id, config) if session_id else None
    trace = _dashboard_trace_detail({"id": [trace_id]}, config) if trace_id else None
    return {
        "found": True,
        "id": agent_id,
        "agent": agent,
        "run": (run_detail or {}).get("run") if run_detail else None,
        "session": session,
        "trace": trace,
        "messages": (session or {}).get("messages", []),
    }


def _git(cwd: str | Path, *args: str) -> str | None:
    import subprocess
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                           timeout=2)
    except Exception:  # noqa: BLE001
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _project_marker(path: Path) -> str:
    for name in ("pyproject.toml", "package.json", "go.mod", "Cargo.toml", "pom.xml",
                 "requirements.txt"):
        if (path / name).exists():
            return name
    return ""


def _workspace_run_stats() -> dict[str, dict]:
    try:
        from .runs import RunStore
        rows = RunStore().list(limit=500)
    except Exception:  # noqa: BLE001
        return {}
    stats: dict[str, dict] = {}
    for row in rows:
        data = row.get("data") or {}
        key = str(data.get("project") or data.get("cwd") or "").strip()
        if not key:
            continue
        item = stats.setdefault(key, {
            "run_count": 0,
            "surfaces": set(),
            "last_run_at": "",
            "worktree": str(data.get("worktree") or ""),
            "branch": str(data.get("branch") or ""),
        })
        item["run_count"] += 1
        if row.get("surface"):
            item["surfaces"].add(str(row["surface"]))
        updated = str(row.get("ended_at") or row.get("started_at") or "")
        if updated > item["last_run_at"]:
            item["last_run_at"] = updated
    for item in stats.values():
        item["surfaces"] = sorted(item["surfaces"])
    return stats


def _dashboard_projects() -> dict:
    from . import config as cfg
    from .lsp.workspace import find_git_worktree

    cwd = Path.cwd().resolve()
    root = find_git_worktree(str(cwd))
    projects = []
    run_stats = _workspace_run_stats()
    if root:
        r = Path(root)
        projects.append({
            "id": str(r),
            "name": r.name or str(r),
            "path": str(r),
            "kind": "git",
            "current": str(cwd).startswith(str(r)),
            "branch": (_git(r, "branch", "--show-current") or
                       _git(r, "rev-parse", "--short", "HEAD") or ""),
            "marker": _project_marker(r),
            **run_stats.pop(str(r), {}),
        })
    else:
        projects.append({"id": str(cwd), "name": cwd.name or str(cwd), "path": str(cwd),
                         "kind": "directory", "current": True, "marker": _project_marker(cwd),
                         **run_stats.pop(str(cwd), {})})
    for path, stats in sorted(run_stats.items(), key=lambda item: item[1].get("last_run_at", ""),
                              reverse=True)[:20]:
        p = Path(path)
        projects.append({
            "id": path,
            "name": p.name or path,
            "path": path,
            "kind": "recent",
            "current": False,
            "branch": stats.get("branch", ""),
            "marker": _project_marker(p) if p.exists() else "",
            **stats,
        })
    workspace = cfg.sub("workspace")
    projects.append({"id": "aegis-workspace", "name": "AEGIS workspace",
                     "path": str(workspace), "kind": "aegis_workspace", "current": False})
    return {"projects": projects}


def _parse_worktree_porcelain(text: str) -> list[dict]:
    out, cur = [], {}
    for line in text.splitlines() + [""]:
        if not line.strip():
            if cur:
                out.append(cur)
                cur = {}
            continue
        key, _, value = line.partition(" ")
        cur[key] = value or True
    return out


def _dashboard_worktrees() -> dict:
    from .lsp.workspace import find_git_worktree
    root = find_git_worktree(str(Path.cwd()))
    if not root:
        return {"available": False, "worktrees": [], "note": "Current directory is not a git worktree."}
    run_stats = _workspace_run_stats()
    raw = _git(root, "worktree", "list", "--porcelain")
    rows = _parse_worktree_porcelain(raw or "")
    if not rows:
        rows = [{"worktree": root, "branch": _git(root, "branch", "--show-current") or ""}]
    worktrees = []
    for row in rows:
        path = str(row.get("worktree", ""))
        if not path:
            continue
        dirty = _git(path, "status", "--porcelain")
        branch = str(row.get("branch", "")).removeprefix("refs/heads/")
        stats = run_stats.get(path, {})
        worktrees.append({"path": path, "branch": branch, "head": row.get("HEAD", ""),
                          "detached": bool(row.get("detached")), "bare": bool(row.get("bare")),
                          "dirty": bool(dirty), "run_count": stats.get("run_count", 0),
                          "last_run_at": stats.get("last_run_at", ""),
                          "surfaces": stats.get("surfaces", [])})
    return {"available": True, "worktrees": worktrees}


def _cron_run_history(job_id: str, limit: int = 5) -> list[dict]:
    try:
        from .runs import RunStore
        rows = RunStore().list(surface="cron", limit=200)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for row in rows:
        data = row.get("data") or {}
        if data.get("cron_job_id") == job_id:
            out.append(_dashboard_run_row(row))
        if len(out) >= limit:
            break
    return out


def _dashboard_cron_jobs() -> list[dict]:
    from .cron import CronStore
    rows = []
    for job in CronStore().list():
        history = _cron_run_history(job.id)
        rows.append({
            "id": job.id,
            "schedule": job.schedule,
            "prompt": job.prompt,
            "enabled": job.enabled,
            "one_shot": bool(job.run_at),
            "last_run": job.last_run,
            "run_count": len(history),
            "last_run_id": history[0]["id"] if history else "",
            "last_status": history[0]["status"] if history else "",
            "history": history,
        })
    return rows


def _dashboard_evals(config: Config | None = None) -> dict:
    from . import config as cfg
    paths = [cfg.sub("evals.jsonl"), cfg.sub("evals", "runs.jsonl"), cfg.sub("evals", "results.jsonl")]
    eval_dir = cfg.sub("evals")
    if eval_dir.exists():
        paths.extend(sorted(eval_dir.glob("*.jsonl")))
    seen, rows = set(), []
    for p in paths:
        if p in seen or not p.exists():
            continue
        seen.add(p)
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines[-50:]:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                rec = _jsonable(rec)
                rec.setdefault("source", str(p))
                rows.append(rec)
    try:
        from .evals import EvalStore
        store = EvalStore.from_config(config) if config else EvalStore()
        for rec in store.list_runs(limit=50):
            rows.append({**_jsonable(rec), "name": rec.get("suite", ""), "status": "recorded",
                         "source": "eval_store"})
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import evals as eval_mod
        from .session import SessionStore
        ids = [s["id"] for s in SessionStore().list(20)]
        rows.extend(_jsonable(r) for r in eval_mod.evaluate_sessions(ids))
        trace_path = _trace_db_path(config)
        if trace_path.exists():
            from .tracing import TraceStore
            trace_store = TraceStore(trace_path)
            trace_ids = [t["trace_id"] for t in trace_store.list_traces(limit=20)]
            rows.extend(_jsonable(r) for r in eval_mod.evaluate_traces(trace_ids, store=trace_store))
    except Exception:  # noqa: BLE001
        pass
    try:
        from .trajectory import stats
        traj = stats()
    except Exception:  # noqa: BLE001
        traj = {}
    return {"available": bool(rows), "evals": rows[-100:], "summary": {"records": len(rows),
            "trajectory": traj}, "note": "" if rows else "No eval result store found yet."}


def _dashboard_eval_detail(query: dict, config: Config | None = None) -> dict:
    eval_id = ((query.get("id", [""])[0]) or "").strip()
    if not eval_id:
        return {"found": False, "error": "missing id"}

    db_path = _eval_db_path(config)
    if db_path.exists():
        try:
            from .evals import EvalStore
            run = EvalStore(db_path).get_run(eval_id)
        except Exception:  # noqa: BLE001
            run = None
        if run:
            run = _jsonable(run)
            summary = run.get("summary") or {
                "total": run.get("total", 0),
                "passed": run.get("passed", 0),
                "failed": max(0, int(run.get("total") or 0) - int(run.get("passed") or 0)),
                "score": run.get("score", 0),
            }
            return {
                "found": True,
                "id": eval_id,
                "source": "eval_store",
                "eval": {**run, "name": run.get("suite", run.get("name", "")),
                         "status": "recorded", "summary": summary},
                "summary": summary,
                "results": run.get("results", []),
            }

    for rec in _dashboard_evals(config).get("evals", []):
        keys = {str(rec.get(k, "")) for k in ("id", "run_id", "name", "suite", "case", "task")}
        if eval_id in keys:
            return {
                "found": True,
                "id": eval_id,
                "source": rec.get("source", "eval_record"),
                "eval": rec,
                "summary": rec.get("summary", {}),
                "results": rec.get("results", rec.get("grades", [])),
            }
    return {"found": False, "id": eval_id, "error": "eval run not found"}


def _dashboard_run_eval(body: dict, config: Config | None = None) -> dict:
    path = str(body.get("path") or "").strip()
    if not path:
        return {"ok": False, "error": "missing path"}
    try:
        from .evals import run_suite

        run = _jsonable(run_suite(path, config=config))
        return {"ok": True, "eval": run}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _redact_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.query and any(k in parsed.query.lower()
                            for k in ("key", "token", "secret", "password", "auth")):
        return urlunparse(parsed._replace(query="redacted=1"))
    return value


def _dashboard_mcp_catalog(config: Config, *, live: bool = False) -> dict:
    try:
        from .mcp.client import _server_configs, catalog as _mcp_catalog
        raw = _server_configs(config)
    except Exception:  # noqa: BLE001
        raw = config.get("mcp.servers", {}) or {}
        _mcp_catalog = None
    servers, malformed = [], []
    for name, spec in (raw or {}).items():
        if not isinstance(spec, dict) or not (spec.get("command") or spec.get("url")):
            malformed.append(str(name))
            continue
        transport = "http" if spec.get("url") else "stdio"
        servers.append({
            "name": str(name),
            "transport": transport,
            "command": spec.get("command", ""),
            "args": list(spec.get("args") or []),
            "url": _redact_url(str(spec.get("url", ""))) if spec.get("url") else "",
            "cwd": spec.get("cwd", ""),
            "env_keys": sorted((spec.get("env") or {}).keys()),
            "header_keys": sorted((spec.get("headers") or {}).keys()),
            "status": "configured",
            **(_mcp_live_inventory(str(name), spec) if live else {}),
        })
    recipes = []
    if _mcp_catalog is not None:
        try:
            for entry in _mcp_catalog(config):
                target = entry.get("url") or " ".join([entry.get("command", ""), *(entry.get("args") or [])])
                recipes.append({
                    "name": str(entry.get("name", "")),
                    "description": str(entry.get("description", "")),
                    "transport": "http" if entry.get("url") else "stdio",
                    "target": target.strip(),
                    "installed": str(entry.get("name", "")) in (raw or {}),
                })
        except Exception:  # noqa: BLE001
            recipes = []
    return {"enabled": bool(config.get("mcp.enabled", True)), "available": bool(servers),
            "servers": servers, "catalog": recipes, "malformed": malformed}


def _mcp_live_inventory(name: str, spec: dict) -> dict:
    try:
        from .mcp.client import MCPClient

        client = MCPClient(
            name,
            command=spec.get("command"),
            args=spec.get("args"),
            env=spec.get("env"),
            url=spec.get("url"),
            headers=spec.get("headers"),
            cwd=spec.get("cwd"),
            tool_filter=spec.get("tool_filter"),
        )
        try:
            client.connect()
            return {
                "status": "ok",
                "tools": client.list_tools(),
                "resources": client.list_resources(),
                "prompts": client.list_prompts(),
            }
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)}


def make_handler(config: Config):
    from .session import SessionStore
    from .surface import SurfaceRunner

    chat_store = SessionStore()
    chat_runner = SurfaceRunner(config, store=chat_store, include_mcp=True)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _authorized(self) -> bool:
            token = config.get("server.dashboard_token")
            if not token:
                return True
            parsed = urlparse(self.path)
            query_token = parse_qs(parsed.query).get("token", [""])[0]
            auth = self.headers.get("Authorization", "")
            header_token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
            return token in (query_token, header_token, self.headers.get("X-Aegis-Token", ""))

        def _json(self, obj):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(obj).encode())

        def _unauthorized(self):
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "unauthorized"}).encode())

        def do_GET(self):  # noqa: N802
            from .session import SessionStore
            u = urlparse(self.path)
            path, q = u.path, parse_qs(u.query)
            if path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(_page())
            elif not self._authorized():
                self._unauthorized()
            elif path == "/api/status":
                from .skills import SkillsLoader
                from .tools.registry import default_registry
                self._json({"version": __version__, "provider": config.get("model.provider"),
                            "model": config.get("model.default"),
                            "sessions": len(SessionStore().list(9999)),
                            "skills": len(SkillsLoader(config).available()),
                            "tools": len(default_registry().all()),
                            "exec_mode": config.get("tools.exec_mode")})
            elif path == "/events":
                self._stream_events()
            elif path == "/api/kanban":
                from .kanban import KanbanStore
                ks = KanbanStore()
                self._json({s: [{"id": t.id, "title": t.title, "body": t.body,
                                 "assignee": t.assignee, "priority": t.priority,
                                 "run_id": t.run_id, "session_id": t.session_id,
                                 "trace_id": t.trace_id}
                                for t in ks.list(status=s)]
                            for s in ("ready", "in_progress", "done", "blocked")})
            elif path == "/api/cron":
                self._json(_dashboard_cron_jobs())
            elif path == "/api/config":
                self._json(_redacted_config(config))
            elif path == "/api/models":
                self._json(_dashboard_models(config))
            elif path == "/api/analytics":
                from . import ratelimit
                from .usage_log import cost_report, daily_series
                days = int((q.get("days", ["30"])[0]) or 30)
                rep = cost_report(days, config)
                rep["series"] = daily_series(days, config)
                rep["balance"] = ratelimit.balance()
                self._json(rep)
            elif path == "/api/keys":
                self._json(_env_keys())
            elif path == "/api/pairing":
                from .gateway.pairing import PairingStore
                self._json(PairingStore().list())
            elif path == "/api/mcp":
                servers = config.get("mcp.servers", {}) or {}
                self._json([{"name": n, "command": (s or {}).get("command", ""),
                             "args": (s or {}).get("args", [])} for n, s in servers.items()])
            elif path == "/api/mcp/catalog":
                self._json(_dashboard_mcp_catalog(config, live=(q.get("live", ["0"])[0] in {"1", "true", "yes"})))
            elif path == "/api/webhooks":
                from .webhook import WebhookStore
                self._json([{"name": w.name, "prompt": w.prompt} for w in WebhookStore().list()])
            elif path == "/api/curator":
                from .curator import apply_transitions
                self._json(apply_transitions(dry_run=True))      # preview
            elif path == "/api/plugins":
                from .plugins import list_manifests, load_plugins
                api = load_plugins(quiet=True, config=config)
                self._json({"loaded": [p.name for p in api.files],
                            "errors": [{"file": f.name, "error": e} for f, e in api.errors],
                            "tools": len(api.tools),
                            "tool_names": sorted(getattr(t, "name", str(t)) for t in api.tools),
                            "channels": sorted(api.channels),
                            "providers": sorted(api.providers),
                            "manifests": [m.to_dict() for m in list_manifests(config)]})
            elif path == "/api/profiles":
                self._json(_profiles(config))
            elif path == "/api/system":
                self._json(_system_info())
            elif path == "/api/traces":
                self._json(_dashboard_traces(q, config))
            elif path == "/api/trace":
                self._json(_dashboard_trace_detail(q, config))
            elif path == "/api/runs":
                self._json(_dashboard_runs(q))
            elif path == "/api/run":
                self._json(_dashboard_run_detail(q, config))
            elif path == "/api/agents":
                self._json(_dashboard_agents(config))
            elif path == "/api/agent":
                self._json(_dashboard_agent_detail(q, config))
            elif path == "/api/projects":
                self._json(_dashboard_projects())
            elif path == "/api/worktrees":
                self._json(_dashboard_worktrees())
            elif path == "/api/evals":
                self._json(_dashboard_evals(config))
            elif path == "/api/eval":
                self._json(_dashboard_eval_detail(q, config))
            elif path == "/api/logs":
                from . import config as _cfg
                lp = _cfg.logs_dir() / "aegis.log"
                lines = lp.read_text(errors="replace").splitlines()[-200:] if lp.exists() else []
                self._json({"path": str(lp), "lines": lines})
            elif path == "/api/sessions":
                self._json(SessionStore().list(100))
            elif path == "/api/session":
                sid = q.get("id", [""])[0]
                s = SessionStore().load(sid)
                detail = _dashboard_session_detail(sid, config) if sid else {"found": False}
                self._json({
                    "messages": [{"role": m.role, "content": m.content}
                                 for m in (s.messages if s else []) if m.content],
                    "detail": detail,
                    "runs": detail.get("runs", []),
                    "traces": detail.get("traces", []),
                    "links": detail.get("links", {}),
                    "lineage": {
                        "parent": detail.get("parent"),
                        "children": detail.get("children", []),
                    } if detail.get("found") else {"parent": None, "children": []},
                })
            elif path == "/api/memory":
                from .memory import MemoryStore
                ms = MemoryStore()
                self._json({"memory": ms.raw("memory"), "user": ms.raw("user")})
            elif path == "/api/skills":
                from .skills import SkillsLoader
                self._json([{"name": s.name, "description": s.description}
                            for s in sorted(SkillsLoader(config).available(), key=lambda s: s.name)])
            elif path == "/api/tools":
                from .tools.registry import default_registry
                self._json([{"name": t.name, "description": t.description.splitlines()[0],
                             "groups": t.groups} for t in default_registry().all()])
            else:
                self._json({"error": "not found"})

        def _stream_events(self):
            """Server-Sent Events: live mirror of gateway/agent activity (EventSource client)."""
            import queue as _queue

            from .eventbus import BUS
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            sub = BUS.subscribe()
            try:
                while True:
                    try:
                        ev = sub.get(timeout=15)
                        self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                    except _queue.Empty:
                        self.wfile.write(b": keepalive\n\n")   # hold the connection open
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ValueError):
                pass                                            # client disconnected
            finally:
                BUS.unsubscribe(sub)

        def _stream_chat(self, body: dict) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def send(obj: dict) -> None:
                self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode())
                self.wfile.flush()

            try:
                _dashboard_chat_stream(body, chat_runner, send)
            except (BrokenPipeError, ConnectionResetError, ValueError):
                pass

        def do_POST(self):  # noqa: N802
            if not self._authorized():
                self._unauthorized()
                return
            n = int(self.headers.get("content-length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            ppath = urlparse(self.path).path
            if ppath == "/api/chat/stream":
                return self._stream_chat(body)
            if ppath == "/api/kanban":
                from .kanban import KanbanStore
                ks = KanbanStore()
                act = body.get("action")
                if act == "create":
                    t = ks.create((body.get("title") or "untitled").strip(), body.get("body", ""))
                    return self._json({"id": t.id})
                if act == "move" and body.get("id") and \
                        body.get("status") in ("ready", "in_progress", "done", "blocked"):
                    ks._set_status(body["id"], body["status"])
                    return self._json({"ok": True})
                if act == "decompose" and body.get("goal"):
                    from .kanban_auto import decompose
                    cards = decompose(body["goal"], config, store=ks)
                    return self._json({"ok": True, "created": len(cards)})
                if act == "run":
                    import threading
                    from .kanban_auto import run_board
                    threading.Thread(target=run_board, args=(config,),
                                     kwargs={"store": ks}, daemon=True).start()
                    return self._json({"ok": True, "started": True})
                return self._json({"error": "bad kanban request"})
            if ppath == "/api/cron":
                from .cron import CronStore, build_delivery_sink, run_job
                cs = CronStore()
                act = body.get("action")
                if act == "add" and body.get("schedule") and body.get("prompt"):
                    j = cs.add(body["schedule"], body["prompt"], body.get("channel", ""))
                    return self._json({"id": j.id})
                if act == "remove" and body.get("id"):
                    return self._json({"ok": cs.remove(body["id"])})
                if act == "toggle" and body.get("id"):
                    return self._json({"ok": cs.set_enabled(body["id"], bool(body.get("enabled", True)))})
                if act in {"run", "run_now"} and body.get("id"):
                    sink = build_delivery_sink(config, verbose=False)
                    result = run_job(config, str(body["id"]), sink=sink, store=cs, verbose=False)
                    return self._json(result)
                return self._json({"error": "bad cron request"})
            if ppath == "/api/config":
                key, val = body.get("key"), body.get("value")
                if key:
                    config.set(key, val)
                    return self._json({"ok": True})
                return self._json({"error": "missing key"})
            if ppath == "/api/models":
                from .providers import registry
                prov, model = body.get("provider"), body.get("model")
                target_provider = prov or config.get("model.provider")
                target_model = model or config.get("model.default")
                validation = registry.validate_model_choice(target_provider, target_model, config)
                if not validation.get("ok", True):
                    return self._json({
                        "ok": False,
                        "error": registry.model_validation_message(validation),
                        "validation": validation,
                    })
                if prov:
                    config.set("model.provider", prov)
                if model:
                    config.set("model.default", model)
                validation = registry.validate_model_choice(
                    config.get("model.provider"),
                    config.get("model.default"),
                    config,
                )
                return self._json({"ok": True, "provider": config.get("model.provider"),
                                   "model": config.get("model.default"),
                                   "warning": registry.model_validation_message(validation),
                                   "validation": validation})
            if ppath == "/api/keys":
                from .config import set_env_var
                if body.get("key"):
                    set_env_var(body["key"].strip(), body.get("value", ""))
                    return self._json({"ok": True})
                return self._json({"error": "missing key"})
            if ppath == "/api/pairing":
                from .gateway.pairing import PairingStore
                ps = PairingStore()
                act, plat = body.get("action"), body.get("platform", "")
                if act == "approve" and body.get("code"):
                    return self._json({"ok": ps.approve(plat, body["code"])})
                if act == "revoke" and body.get("user_id"):
                    return self._json({"ok": ps.revoke(plat, body["user_id"])})
                return self._json({"error": "bad pairing request"})
            if ppath == "/api/system":
                if body.get("action") == "backup":
                    from .backup import create_backup
                    return self._json({"ok": True, "path": str(create_backup())})
                return self._json({"error": "unknown system action"})
            if ppath == "/api/session":
                act = body.get("action")
                sid = (body.get("id") or body.get("session_id") or "").strip()
                if act == "branch" and sid:
                    return self._json(_dashboard_branch_session(
                        sid,
                        title=str(body.get("title") or ""),
                        reason=str(body.get("reason") or "dashboard"),
                    ))
                return self._json({"error": "bad session request"})
            if ppath == "/api/eval":
                if body.get("action") in {"run", "run_suite"}:
                    return self._json(_dashboard_run_eval(body, config))
                return self._json({"error": "bad eval request"})
            if ppath == "/api/curator":
                from .curator import apply_transitions
                return self._json(apply_transitions(dry_run=False))   # apply
            if ppath == "/api/profiles":
                config.set("agent.personality", body.get("name") or "")
                return self._json({"ok": True, "active": config.get("agent.personality")})
            if ppath == "/api/mcp":
                servers = dict(config.get("mcp.servers", {}) or {})
                act = body.get("action")
                if act == "install" and body.get("name"):
                    try:
                        from .mcp.client import install_from_catalog
                        spec = install_from_catalog(config, str(body["name"]))
                        target = spec.get("url") or " ".join([spec.get("command", ""), *(spec.get("args") or [])])
                        return self._json({"ok": True, "name": body["name"], "target": target.strip()})
                    except KeyError:
                        return self._json({"ok": False, "error": "catalog entry not found"})
                    except Exception as exc:  # noqa: BLE001
                        return self._json({"ok": False, "error": str(exc)})
                if act == "add" and body.get("name") and body.get("command"):
                    parts = str(body["command"]).split()
                    servers[body["name"]] = {"command": parts[0], "args": parts[1:]}
                    config.data.setdefault("mcp", {})["servers"] = servers
                    config.save()
                    return self._json({"ok": True})
                if act == "remove" and body.get("name") in servers:
                    servers.pop(body["name"])
                    config.data.setdefault("mcp", {})["servers"] = servers
                    config.save()
                    return self._json({"ok": True})
                return self._json({"error": "bad mcp request"})
            if ppath == "/api/plugins":
                act = body.get("action")
                name = str(body.get("name") or "").strip()
                try:
                    from . import plugins as plugin_runtime
                    if act == "install" and body.get("source"):
                        installed = plugin_runtime.install(
                            str(body["source"]),
                            config,
                            force=bool(body.get("force", False)),
                        )
                        return self._json({"ok": True, "name": installed})
                    if act == "enable" and name:
                        return self._json({"ok": plugin_runtime.enable(name, config)})
                    if act == "disable" and name:
                        return self._json({"ok": plugin_runtime.disable(name, config)})
                    if act == "remove" and name:
                        return self._json({"ok": plugin_runtime.remove(name, config)})
                except Exception as exc:  # noqa: BLE001
                    return self._json({"ok": False, "error": str(exc)})
                return self._json({"error": "bad plugins request"})
            if ppath == "/api/webhooks":
                from .webhook import WebhookStore
                ws = WebhookStore()
                act = body.get("action")
                if act == "add" and body.get("name") and body.get("prompt"):
                    ws.add(body["name"], body["prompt"])
                    return self._json({"ok": True})
                if act == "remove" and body.get("name"):
                    return self._json({"ok": ws.remove(body["name"])})
                return self._json({"error": "bad webhook request"})
            self._json(_dashboard_chat_response(body, chat_runner))

    return H


def _dashboard_url(config: Config, host: str, port: int) -> str:
    token = config.get("server.dashboard_token")
    base = f"http://{host}:{port}"
    return f"{base}/?token={token}" if token else base


def serve_dashboard(config: Config, host: str = "127.0.0.1", port: int = 9119,
                    open_browser: bool = False) -> None:
    handler = make_handler(config)
    requested = port
    for candidate in range(port, port + 50):       # auto-select if the port is occupied
        try:
            httpd = ThreadingHTTPServer((host, candidate), handler)
            break
        except OSError:
            continue
    else:
        raise OSError(f"no free port in {requested}–{requested + 49} on {host}")
    port = httpd.server_address[1]
    if port != requested:
        print(f"  (port {requested} busy — using {port})")
    url = _dashboard_url(config, host, port)
    print(f"AEGIS control panel → {url}")
    print("  (leave this running; press Ctrl+C to stop)")
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\ndashboard stopped.")


def cmd_dashboard(args, config: Config) -> int:
    host = getattr(args, "host", None) or config.get("server.dashboard_host", "127.0.0.1")
    port = getattr(args, "port", None) or config.get("server.dashboard_port", 9119)
    # Beginner-friendly default: open the browser unless asked not to.
    open_browser = not getattr(args, "no_open", False)
    serve_dashboard(config, host=host, port=int(port), open_browser=open_browser)
    return 0
