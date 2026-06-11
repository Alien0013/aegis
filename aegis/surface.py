"""Shared entry-surface helpers.

This module has two jobs:

* inventory helpers for first-run/dashboard visibility, and
* a small surface runner/factory used by CLI/API/ACP/automation style entry
  points so they all reach the same Agent runtime with the same session, MCP,
  callback, platform, and trace wiring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .config import Config
from .session import Session, SessionStore
from .types import Message, new_id

OnEvent = Callable[[dict[str, Any]], None]
Approver = Callable[[str], bool | str]
Asker = Callable[[str, list[str]], str]


@dataclass
class ToolInventory:
    toolsets: list[str]
    enabled_count: int
    total_count: int
    enabled_names: list[str]
    disabled_sets: dict[str, int]


@dataclass
class SkillInventory:
    available_count: int
    bundled_count: int
    personal_count: int
    names: list[str]


@dataclass
class PluginInventory:
    path: Path
    files_count: int
    loaded_files: list[str]
    errors: list[tuple[str, str]]
    tools: list[str]
    channels: list[str]
    providers: list[str]


@dataclass
class SurfaceRun:
    """Structured result for one prompt run from any AEGIS surface."""

    text: str
    message: Message
    session: Session
    agent: Any
    trace_id: str = ""
    turn_id: str = ""
    run_id: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)


class SurfaceRunner:
    """Shared Agent factory/run helper for non-interactive entry surfaces.

    The class is deliberately small. It centralizes common surface behavior
    without hiding the Agent itself: callers can still set ACP filesystem
    delegates, gateway metadata, or UI callbacks before calling ``agent.run``.
    """

    def __init__(
        self,
        config: Config,
        *,
        store: SessionStore | None = None,
        cwd: str | Path | None = None,
        include_mcp: bool = True,
        reuse_agents: bool = True,
        agent_cache_size: int = 32,
    ) -> None:
        self.config = config
        self.store = store or SessionStore()
        self.cwd = Path(cwd).expanduser() if cwd is not None else Path.cwd()
        self.include_mcp = include_mcp
        self.reuse_agents = reuse_agents
        self.agent_cache_size = max(0, int(agent_cache_size))
        import threading

        self._cache_lock = threading.Lock()
        self._agents: dict[tuple[Any, ...], Any] = {}
        self._agent_locks: dict[tuple[Any, ...], threading.Lock] = {}

    def load_or_create_session(
        self,
        session_id: str | None = None,
        *,
        title: str | None = None,
        history: Iterable[Message] | None = None,
        surface: str = "",
        meta: dict[str, Any] | None = None,
    ) -> Session:
        if session_id:
            session = self.store.load(session_id) or Session(id=session_id, title=title or session_id)
        else:
            session = Session.create(title=title or "")
        if history is not None:
            session.messages = list(history)
        if surface:
            session.meta["surface"] = surface
        if meta:
            session.meta.update(meta)
        if title and session.title != title:
            session.title = title
        self.store.save(session)
        return session

    def make_agent(
        self,
        *,
        session: Session,
        cwd: str | Path | None = None,
        model: str | None = None,
        provider_name: str | None = None,
        approver: Approver | None = None,
        asker: Asker | None = None,
        platform: str | None = None,
        chat_id: str | None = None,
        include_mcp: bool | None = None,
        registry: Any = None,
        config: Config | None = None,
    ) -> Any:
        from .agent.agent import Agent

        run_config = config or self.config
        controls = session_runtime_controls(session)
        effective_model = model or controls.get("model")
        effective_provider = provider_name or controls.get("provider")
        agent = _agent_create(
            Agent,
            run_config,
            session=session,
            cwd=Path(cwd).expanduser() if cwd is not None else self.cwd,
            store=self.store,
            model=effective_model,
            provider_name=effective_provider,
            approver=approver,
            include_mcp=self.include_mcp if include_mcp is None else include_mcp,
            registry=registry,
        )
        apply_session_runtime(agent)
        if platform:
            agent.platform = platform
        if chat_id:
            agent.chat_id = chat_id
        if asker is not None:
            agent.tool_context.asker = asker
        return agent

    def run_prompt(
        self,
        prompt: str | Message,
        *,
        session: Session | None = None,
        session_id: str | None = None,
        title: str | None = None,
        history: Iterable[Message] | None = None,
        model: str | None = None,
        provider_name: str | None = None,
        cwd: str | Path | None = None,
        approver: Approver | None = None,
        asker: Asker | None = None,
        platform: str | None = None,
        chat_id: str | None = None,
        include_mcp: bool | None = None,
        surface: str = "",
        meta: dict[str, Any] | None = None,
        on_event: OnEvent | None = None,
        agent: Any = None,
        stream: bool | None = None,
        reuse_agent: bool | None = None,
        expand_refs: bool = True,
    ) -> SurfaceRun:
        session = session or self.load_or_create_session(
            session_id, title=title, history=history, surface=surface, meta=meta
        )
        if session is not None:
            changed = False
            if surface and session.meta.get("surface") != surface:
                session.meta["surface"] = surface
                changed = True
            if meta:
                for key, value in meta.items():
                    if session.meta.get(key) != value:
                        session.meta[key] = value
                        changed = True
            if title and session.title != title:
                session.title = title
                changed = True
            if changed:
                try:
                    self.store.save(session)
                except Exception:  # noqa: BLE001
                    pass
        run_cwd = Path(cwd).expanduser() if cwd is not None else (
            Path(getattr(agent, "cwd", self.cwd)).expanduser() if agent is not None else self.cwd
        )
        effective_prompt = prompt
        reference_meta: dict[str, Any] = {}
        if expand_refs:
            try:
                from .context_refs import expand_reference_result

                original_text = _prompt_text(prompt)
                expanded = expand_reference_result(original_text, run_cwd, config=self.config)
                if expanded.expanded:
                    if isinstance(prompt, Message):
                        data = prompt.to_dict()
                        data["content"] = expanded.text
                        effective_prompt = Message.from_dict(data)
                    else:
                        effective_prompt = expanded.text
                if expanded.references or expanded.warnings:
                    reference_meta = {
                        "count": len(expanded.references),
                        "injected_chars": expanded.injected_chars,
                        "warnings": list(expanded.warnings),
                        "references": [
                            {
                                "raw": r.raw,
                                "kind": r.kind,
                                "target": r.target,
                                "warning": r.warning,
                                "chars": r.chars,
                            }
                            for r in expanded.references
                        ],
                    }
                    session.meta.setdefault("context_references", []).append(reference_meta)
                    session.meta["last_context_references"] = reference_meta
                    try:
                        self.store.save(session)
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                effective_prompt = prompt
        run_store = None
        run_id = ""
        controls = session_runtime_controls(session)
        agent_provider = getattr(agent, "provider", None)
        cfg_get = getattr(self.config, "get", lambda _key, default="": default)
        effective_model = (
            model
            or controls.get("model")
            or str(getattr(agent_provider, "model", "") or "")
            or str(cfg_get("model.default", "") or "")
        )
        effective_provider = (
            provider_name
            or controls.get("provider")
            or str(getattr(agent_provider, "name", "") or "")
            or str(cfg_get("model.provider", "") or "")
        )
        try:
            from .runs import RunStore

            run_store = RunStore()
            run = run_store.start(
                surface=surface or "agent",
                kind=surface or "agent",
                title=title or getattr(session, "title", ""),
                session_id=getattr(session, "id", ""),
                prompt=_prompt_text(prompt),
                data={
                    **(meta or {}),
                    "context_references": reference_meta,
                    "model": effective_model,
                    "provider": effective_provider,
                    "platform": platform or "",
                    "chat_id": chat_id or "",
                    **_workspace_run_meta(run_cwd),
                },
            )
            run_id = run["id"]
        except Exception:  # noqa: BLE001
            run_store = None
        should_reuse = self.reuse_agents if reuse_agent is None else bool(reuse_agent)
        key = self._agent_key(
            session=session,
            cwd=cwd,
            model=model,
            provider_name=provider_name,
            approver=approver,
            asker=asker,
            include_mcp=include_mcp,
            config=self.config,
        )
        try:
            if agent is None and should_reuse and self.agent_cache_size:
                lock = self._lock_for(key)
                with lock:
                    agent = self._cached_agent(
                        key=key,
                        session=session,
                        cwd=cwd,
                        model=model,
                        provider_name=provider_name,
                        approver=approver,
                        asker=asker,
                        platform=platform,
                        chat_id=chat_id,
                        include_mcp=include_mcp,
                    )
                    result = self._run(agent, effective_prompt, session=session, platform=platform,
                                       chat_id=chat_id, stream=stream, on_event=on_event,
                                       run_id=run_id)
            else:
                agent = agent or self.make_agent(
                    session=session,
                    cwd=cwd,
                    model=model,
                    provider_name=provider_name,
                    approver=approver,
                    asker=asker,
                    platform=platform,
                    chat_id=chat_id,
                    include_mcp=include_mcp,
                )
                result = self._run(agent, effective_prompt, session=session, platform=platform,
                                   chat_id=chat_id, stream=stream, on_event=on_event,
                                   run_id=run_id)
        except Exception as exc:
            if run_store is not None and run_id:
                try:
                    run_store.finish(run_id, status="error", error=f"{type(exc).__name__}: {exc}")
                except Exception:  # noqa: BLE001
                    pass
            raise
        result.run_id = run_id
        if run_store is not None and run_id:
            try:
                run_store.finish(
                    run_id,
                    status="ok",
                    trace_id=result.trace_id,
                    result=result.text,
                    data={"turn_id": result.turn_id, "event_count": len(result.events)},
                )
            except Exception:  # noqa: BLE001
                pass
        self._remember_breadcrumbs(result)
        return result

    def close(self) -> None:
        with self._cache_lock:
            agents = list(self._agents.values())
            self._agents.clear()
            self._agent_locks.clear()
        for agent in agents:
            _close_agent(agent)

    def _run(
        self,
        agent: Any,
        prompt: str | Message,
        *,
        session: Session,
        platform: str | None,
        chat_id: str | None,
        stream: bool | None,
        on_event: OnEvent | None,
        run_id: str = "",
    ) -> SurfaceRun:
        _retarget_agent(agent, session=session)
        agent.platform = platform
        agent.chat_id = chat_id
        agent._surface_run_id = run_id
        if stream is not None:
            agent.stream = bool(stream)

        events: list[dict[str, Any]] = []

        def emit(event: dict[str, Any]) -> None:
            events.append(dict(event))
            if on_event is not None:
                on_event(event)

        message = _agent_run(agent, prompt, emit)
        trace_ctx = getattr(agent, "_trace_context", {}) or {}
        return SurfaceRun(
            text=getattr(message, "content", ""),
            message=message,
            session=getattr(agent, "session", session),
            agent=agent,
            trace_id=str(trace_ctx.get("trace_id", "")),
            turn_id=str(trace_ctx.get("turn_id", "")),
            events=events,
        )

    def _remember_breadcrumbs(self, result: SurfaceRun) -> None:
        session = getattr(result, "session", None)
        if session is None or not getattr(session, "id", ""):
            return
        changed = False
        if result.run_id and session.meta.get("last_run_id") != result.run_id:
            session.meta["last_run_id"] = result.run_id
            changed = True
        if result.trace_id:
            for key in ("trace_id", "last_trace_id"):
                if session.meta.get(key) != result.trace_id:
                    session.meta[key] = result.trace_id
                    changed = True
        if result.turn_id and session.meta.get("last_turn_id") != result.turn_id:
            session.meta["last_turn_id"] = result.turn_id
            changed = True
        if changed:
            try:
                self.store.save(session)
            except Exception:  # noqa: BLE001
                pass

    def _agent_key(
        self,
        *,
        session: Session,
        cwd: str | Path | None,
        model: str | None,
        provider_name: str | None,
        approver: Approver | None,
        asker: Asker | None,
        include_mcp: bool | None,
        config: Config,
    ) -> tuple[Any, ...]:
        run_cwd = str((Path(cwd).expanduser() if cwd is not None else self.cwd).resolve())
        mcp = self.include_mcp if include_mcp is None else bool(include_mcp)
        cfg_get = getattr(config, "get", lambda _key, default="": default)
        controls = session_runtime_controls(session)
        return (
            session.id,
            run_cwd,
            provider_name or controls.get("provider") or cfg_get("model.provider", ""),
            model or controls.get("model") or cfg_get("model.default", ""),
            controls.get("reasoning_effort", ""),
            controls.get("reasoning_display", ""),
            controls.get("busy_mode", ""),
            mcp,
            id(approver) if approver is not None else None,
            id(asker) if asker is not None else None,
            id(config),
        )

    def _lock_for(self, key: tuple[Any, ...]):
        import threading

        with self._cache_lock:
            lock = self._agent_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._agent_locks[key] = lock
            return lock

    def _cached_agent(self, *, key: tuple[Any, ...], session: Session, **kwargs: Any) -> Any:
        with self._cache_lock:
            agent = self._agents.get(key)
        if agent is None:
            agent = self.make_agent(session=session, **kwargs)
            with self._cache_lock:
                self._agents[key] = agent
                self._evict_locked()
        else:
            _retarget_agent(agent, session=session)
            apply_session_runtime(agent)
            asker = kwargs.get("asker")
            if asker is not None:
                agent.tool_context.asker = asker
        return agent

    def _evict_locked(self) -> None:
        while self.agent_cache_size and len(self._agents) > self.agent_cache_size:
            old_key = next(iter(self._agents))
            old = self._agents.pop(old_key)
            self._agent_locks.pop(old_key, None)
            _close_agent(old)


def _agent_create(agent_cls: Any, config: Config, **kwargs: Any) -> Any:
    """Call ``Agent.create`` while tolerating small test/plugin fakes."""

    import inspect

    create = agent_cls.create
    try:
        params = inspect.signature(create).parameters
    except (TypeError, ValueError):
        return create(config, **kwargs)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return create(config, **kwargs)
    filtered = {k: v for k, v in kwargs.items() if k in params}
    return create(config, **filtered)


def _agent_run(agent: Any, prompt: str | Message, on_event: OnEvent) -> Message:
    import inspect

    try:
        params = inspect.signature(agent.run).parameters
    except (TypeError, ValueError):
        return agent.run(prompt, on_event)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()) or "on_event" in params:
        return agent.run(prompt, on_event=on_event)
    # Bound method signatures omit ``self``. A real Agent exposes two parameters
    # (prompt, on_event); tiny fakes often expose only prompt.
    if len(params) >= 2:
        return agent.run(prompt, on_event)
    return agent.run(prompt)


def _prompt_text(prompt: str | Message) -> str:
    if isinstance(prompt, Message):
        text = prompt.content or ""
        if prompt.images:
            text += "\n" + "\n".join(f"[image: {p}]" for p in prompt.images)
        return text
    return str(prompt)


def _workspace_run_meta(cwd: Path) -> dict[str, str]:
    root = ""
    try:
        from .lsp.workspace import find_git_worktree
        root = find_git_worktree(str(cwd)) or ""
    except Exception:  # noqa: BLE001
        root = ""
    branch = ""
    if root:
        try:
            import subprocess
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout.strip()
        except Exception:  # noqa: BLE001
            branch = ""
    return {
        "cwd": str(cwd),
        "project": root or str(cwd),
        "worktree": root,
        "branch": branch,
    }


def _retarget_agent(agent: Any, *, session: Session) -> None:
    """Point a cached agent at the latest session object for this surface."""

    switch = getattr(agent, "switch_session", None)
    cur_id = getattr(getattr(agent, "session", None), "id", None)
    if callable(switch) and cur_id != getattr(session, "id", None):
        try:
            switch(session)            # fires the memory session-switch hook
            return
        except Exception:  # noqa: BLE001
            pass
    try:
        agent.session = session
    except Exception:  # noqa: BLE001
        return
    tool_context = getattr(agent, "tool_context", None)
    if tool_context is not None:
        try:
            tool_context.session = session
        except Exception:  # noqa: BLE001
            pass


def session_runtime_controls(session: Session | None) -> dict[str, str]:
    """Session-level runtime controls that should survive resume/branch/handoff."""
    if session is None:
        return {}
    meta = getattr(session, "meta", {}) or {}
    runtime = meta.get("runtime") if isinstance(meta.get("runtime"), dict) else {}
    controls = meta.get("runtime_controls") if isinstance(meta.get("runtime_controls"), dict) else {}
    out: dict[str, str] = {}
    for key in ("provider", "model"):
        value = controls.get(key) or meta.get(key) or runtime.get(key)
        if value:
            out[key] = str(value)
    for key in ("reasoning_effort", "reasoning_display", "busy_mode"):
        value = controls.get(key) or runtime.get(key)
        if value:
            out[key] = str(value)
    return out


def runtime_controls_meta(controls: dict[str, Any] | None) -> dict[str, Any]:
    """Session metadata shape for inherited runtime controls."""
    clean = {k: str(v) for k, v in (controls or {}).items() if v not in (None, "")}
    if not clean:
        return {}
    meta: dict[str, Any] = {"runtime_controls": clean}
    if clean.get("model"):
        meta["model"] = clean["model"]
    if clean.get("provider"):
        meta["provider"] = clean["provider"]
    runtime = {k: v for k, v in clean.items()
               if k in {"reasoning_effort", "reasoning_display", "busy_mode"}}
    if runtime:
        meta["runtime"] = runtime
    return meta


def inherit_session_runtime(parent: Session | None, child: Session | None) -> dict[str, str]:
    """Copy parent session runtime controls into a fresh delegated/forked session."""
    if child is None:
        return {}
    controls = session_runtime_controls(parent)
    child.meta.update(runtime_controls_meta(controls))
    return controls


def remember_session_runtime(agent: Any, **updates: Any) -> dict[str, str]:
    """Persist terminal/gateway runtime controls on the active session."""
    session = getattr(agent, "session", None)
    meta = getattr(session, "meta", None)
    if not isinstance(meta, dict):
        return {}
    controls = dict(meta.get("runtime_controls") or {})
    for key, value in updates.items():
        if value is None or value == "":
            controls.pop(key, None)
            continue
        controls[key] = str(value)
    if controls:
        meta["runtime_controls"] = controls
    else:
        meta.pop("runtime_controls", None)
    if "model" in updates:
        if updates.get("model"):
            meta["model"] = str(updates["model"])
        else:
            meta.pop("model", None)
    if "provider" in updates:
        if updates.get("provider"):
            meta["provider"] = str(updates["provider"])
        else:
            meta.pop("provider", None)
    runtime = dict(meta.get("runtime") or {})
    runtime.update({k: v for k, v in controls.items()
                    if k in {"reasoning_effort", "reasoning_display", "busy_mode"}})
    if runtime:
        meta["runtime"] = runtime
    return controls


def apply_session_runtime(agent: Any, *, rebuild_provider: bool = True) -> None:
    """Apply stored session runtime controls to an agent object."""
    session = getattr(agent, "session", None)
    controls = session_runtime_controls(session)
    config = getattr(agent, "config", None)
    if config is not None:
        if controls.get("reasoning_display"):
            config.data.setdefault("display", {})["reasoning"] = controls["reasoning_display"]
        if controls.get("busy_mode"):
            config.data.setdefault("gateway", {})["busy_mode"] = controls["busy_mode"]
    if controls.get("reasoning_effort"):
        try:
            agent.reasoning = controls["reasoning_effort"]
        except Exception:  # noqa: BLE001
            pass
    if not rebuild_provider or config is None:
        return
    model = controls.get("model")
    provider = controls.get("provider")
    if not (model or provider):
        return
    cur = getattr(agent, "provider", None)
    if (model and getattr(cur, "model", None) != model) or (
            provider and getattr(cur, "name", None) != provider):
        try:
            from .providers.fallback import build_with_fallbacks
            agent.provider = build_with_fallbacks(config, model=model, name=provider or None)
        except Exception:  # noqa: BLE001
            pass


def run_control_action(
    agent: Any,
    action: Callable[[OnEvent], str],
    *,
    config: Config | None = None,
    session: Session | None = None,
    surface: str = "agent",
    kind: str = "control",
    title: str = "",
    prompt: str = "",
    data: dict[str, Any] | None = None,
    on_event: OnEvent | None = None,
) -> SurfaceRun:
    """Record a non-chat control-plane action as a durable run and trace.

    REPL/TUI/gateway actions such as manual compaction do not call the model as
    ordinary user turns, but they still change runtime state. This wrapper makes
    those actions visible to runs, traces, dashboards, eval replay, and status
    breadcrumbs.
    """

    cfg = config or getattr(agent, "config", None)
    session = session or getattr(agent, "session", None)
    if session is None:
        raise ValueError("control action requires a session")
    start_session_id = getattr(session, "id", "")
    run_cwd = Path(getattr(agent, "cwd", Path.cwd())).expanduser()
    events: list[dict[str, Any]] = []

    def emit(event: dict[str, Any]) -> None:
        events.append(dict(event))
        if on_event is not None:
            on_event(event)

    run_store = None
    run_id = ""
    try:
        from .runs import RunStore

        run_store = RunStore()
        run = run_store.start(
            surface=surface or "agent",
            kind=kind or "control",
            title=title or kind,
            session_id=start_session_id,
            prompt=prompt,
            data={**(data or {}), **_workspace_run_meta(run_cwd)},
        )
        run_id = run["id"]
    except Exception:  # noqa: BLE001
        run_store = None

    trace_store = None
    turn_span = None
    trace_id = new_id("trace")
    turn_id = new_id("turn")
    from .tracing import should_trace
    if should_trace(cfg, trace_id):
        try:
            from .tracing import TraceStore

            trace_store = TraceStore.from_config(cfg) if cfg is not None else TraceStore()
            provider = getattr(agent, "provider", None)
            turn_span = trace_store.start_span(
                trace_id=trace_id,
                session_id=start_session_id,
                turn_id=turn_id,
                kind="turn",
                provider=getattr(provider, "name", ""),
                model=getattr(provider, "model", ""),
                data={"control": {"kind": kind, "surface": surface, "title": title},
                      "prompt": prompt, **(data or {})},
            )
            agent._trace_store = trace_store
            agent._trace_context = {
                "trace_id": trace_id,
                "turn_id": turn_id,
                "session_id": start_session_id,
                "turn_span_id": turn_span["span_id"],
            }
        except Exception:  # noqa: BLE001
            trace_store = None
            turn_span = None
            trace_id = ""
    else:
        trace_id = ""
        try:
            agent._trace_store = None
            agent._trace_context = {}
        except Exception:  # noqa: BLE001
            pass

    try:
        text = action(emit) or ""
    except Exception as exc:
        if trace_store is not None and turn_span is not None:
            try:
                trace_store.finish_span(
                    turn_span["span_id"],
                    status="error",
                    data={"error": f"{type(exc).__name__}: {exc}"},
                )
            except Exception:  # noqa: BLE001
                pass
        if run_store is not None and run_id:
            try:
                run_store.finish(run_id, status="error", error=f"{type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001
                pass
        raise

    final_session = getattr(agent, "session", session)
    final_session_id = getattr(final_session, "id", start_session_id)
    if trace_store is not None and turn_span is not None:
        try:
            trace_store.finish_span(
                turn_span["span_id"],
                status="ok",
                data={"session_id_before": start_session_id, "session_id_after": final_session_id},
            )
            if final_session_id and final_session_id != start_session_id:
                trace_store.retarget_session(trace_id, final_session_id)
        except Exception:  # noqa: BLE001
            pass
    if run_store is not None and run_id:
        try:
            if final_session_id and final_session_id != start_session_id:
                run = run_store.get(run_id)
                if run is not None:
                    run["session_id"] = final_session_id
                    run_store.write(run)
            run_store.finish(
                run_id,
                status="ok",
                trace_id=trace_id,
                result=text,
                data={"turn_id": turn_id, "event_count": len(events),
                      "session_id_before": start_session_id,
                      "session_id_after": final_session_id},
            )
        except Exception:  # noqa: BLE001
            pass

    meta = getattr(final_session, "meta", None)
    if isinstance(meta, dict):
        if run_id:
            meta["last_run_id"] = run_id
        if trace_id:
            meta["last_trace_id"] = trace_id
            meta["trace_id"] = trace_id
        if turn_id:
            meta["last_turn_id"] = turn_id
            meta["turn_id"] = turn_id

    return SurfaceRun(
        text=text,
        message=Message.assistant(text),
        session=final_session,
        agent=agent,
        trace_id=trace_id,
        turn_id=turn_id,
        run_id=run_id,
        events=events,
    )


def _close_agent(agent: Any) -> None:
    end = getattr(agent, "end_session", None)      # memory session-end hook
    if callable(end):
        try:
            end()
        except Exception:  # noqa: BLE001
            pass
    memory = getattr(agent, "memory", None)
    if memory is not None:
        try:
            memory.shutdown()
        except Exception:  # noqa: BLE001
            pass
    try:
        mcp = getattr(agent, "_mcp", None)
        if mcp is not None:
            mcp.close_all()
    except Exception:  # noqa: BLE001
        pass
    try:
        provider = getattr(agent, "provider", None)
        transport = getattr(provider, "transport", None)
        close = getattr(transport, "close", None)
        if callable(close):
            close()
    except Exception:  # noqa: BLE001
        pass


def tool_inventory(config: Config) -> ToolInventory:
    from .tools.registry import default_registry

    reg = default_registry()
    toolsets = list(config.get("tools.toolsets", []) or ["core"])
    enabled = reg.available(toolsets)
    enabled_ids = {id(t) for t in enabled}
    disabled_sets: dict[str, int] = {}
    for tool in reg.all():
        if id(tool) in enabled_ids:
            continue
        disabled_sets[tool.toolset] = disabled_sets.get(tool.toolset, 0) + 1
    return ToolInventory(
        toolsets=toolsets,
        enabled_count=len(enabled),
        total_count=len(reg.all()),
        enabled_names=sorted(t.name for t in enabled),
        disabled_sets=disabled_sets,
    )


def skill_inventory(config: Config, cwd: Path | None = None) -> SkillInventory:
    from .skills import SkillsLoader

    skills = SkillsLoader(config, cwd=cwd).available()
    return SkillInventory(
        available_count=len(skills),
        bundled_count=sum(1 for s in skills if s.tier == 4),
        personal_count=sum(1 for s in skills if s.tier < 4),
        names=sorted(s.name for s in skills),
    )


def plugin_inventory() -> PluginInventory:
    from . import config as cfg
    from .plugins import load_plugins

    api = load_plugins(quiet=True)
    return PluginInventory(
        path=cfg.sub("plugins"),
        files_count=len(api.files),
        loaded_files=[str(p) for p in api.files if p not in {e[0] for e in api.errors}],
        errors=[(str(p), msg) for p, msg in api.errors],
        tools=sorted(getattr(t, "name", str(t)) for t in api.tools),
        channels=sorted(api.channels),
        providers=sorted(api.providers),
    )
