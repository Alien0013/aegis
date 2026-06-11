"""Programmatic embedding API for AEGIS.

The CLI, dashboard, gateway, and this SDK all drive the same Agent runtime. The
SDK is intentionally thin: it gives Python callers session continuity, trace
access, and eval replay without introducing a second orchestration path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .agent.agent import Agent
from .config import Config
from .session import Session, SessionStore
from .surface import _workspace_run_meta, apply_session_runtime
from .types import Message, Usage

EventHandler = Callable[[dict[str, Any]], None]
ProviderFactory = Callable[..., Any]
Approver = Callable[[str], bool | str]
Asker = Callable[[str, list[str]], str]


def _sdk_runtime_controls(session: Session | None) -> dict[str, str]:
    if session is None:
        return {}
    meta = getattr(session, "meta", {}) or {}
    controls = meta.get("runtime_controls") if isinstance(meta.get("runtime_controls"), dict) else {}
    out: dict[str, str] = {}
    for key in ("provider", "model", "reasoning_effort", "reasoning_display", "busy_mode"):
        value = controls.get(key)
        if value:
            out[key] = str(value)
    for key in ("provider", "model"):
        if key not in out and meta.get(key):
            out[key] = str(meta[key])
    return out


@dataclass
class AegisResult:
    """Result returned by :class:`AegisClient.run`.

    ``message`` is the normalized assistant message stored in the session.
    ``events`` contains the same provider/tool/trace events the terminal renderer
    receives, so embedding applications can build progress UIs without scraping
    stdout.
    """

    text: str
    message: Message
    session_id: str
    trace_id: str = ""
    turn_id: str = ""
    run_id: str = ""
    provider: str = ""
    model: str = ""
    usage: Usage = field(default_factory=Usage)
    tools_used: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)


class AegisClient:
    """Small Python facade over the AEGIS runtime.

    Parameters are deliberately close to the CLI flags: callers can pick a
    provider/model, resume a session, attach images, stream progress events, and
    run with or without MCP. Tool authorization behavior remains whatever the
    active config already says; this class does not change permission defaults.
    """

    def __init__(
        self,
        *,
        config: Config | None = None,
        store: SessionStore | None = None,
        provider_factory: ProviderFactory | None = None,
        cwd: str | Path | None = None,
        include_mcp: bool = True,
        approver: Approver | None = None,
        asker: Asker | None = None,
        agent_cache_size: int = 32,
    ) -> None:
        self.config = config or Config.load()
        self.store = store or SessionStore()
        self.provider_factory = provider_factory
        self.cwd = Path(cwd).expanduser() if cwd is not None else Path.cwd()
        self.include_mcp = include_mcp
        self.approver = approver
        self.asker = asker
        self.agent_cache_size = max(0, int(agent_cache_size))
        import threading

        self._cache_lock = threading.Lock()
        self._agent_locks: dict[tuple[Any, ...], threading.Lock] = {}
        self._agents: dict[tuple[Any, ...], Agent] = {}

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        title: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        cwd: str | Path | None = None,
        images: Iterable[str | Path] | None = None,
        stream: bool = False,
        on_event: EventHandler | None = None,
        auto: bool = False,
        include_mcp: bool | None = None,
        expand_refs: bool = True,
    ) -> AegisResult:
        """Run one prompt through the normal agent loop and return a structured result."""

        run_cwd = Path(cwd).expanduser() if cwd is not None else self.cwd
        session = self._session(session_id, title=title)
        controls = _sdk_runtime_controls(session)
        agent = self._agent(
            session=session,
            model=model or controls.get("model"),
            provider_name=provider or controls.get("provider"),
            cwd=run_cwd,
            auto=auto,
            include_mcp=self.include_mcp if include_mcp is None else include_mcp,
        )
        agent.stream = bool(stream)
        reference_meta: dict[str, Any] = {}
        if expand_refs:
            expanded = self._expand_result(prompt, run_cwd)
            text = expanded.text
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
                self.store.save(session)
        else:
            text = prompt
        user_input: str | Message
        encoded_images = self._images(images)
        if encoded_images:
            user_input = Message.user(text, images=encoded_images)
        else:
            user_input = text

        events: list[dict[str, Any]] = []

        def emit(event: dict[str, Any]) -> None:
            events.append(dict(event))
            if on_event is not None:
                on_event(event)

        before_tools = agent.tools_used
        run_store = None
        run_id = ""
        try:
            from .runs import RunStore

            run_store = RunStore()
            run = run_store.start(
                surface="sdk",
                kind="sdk",
                title=title or session.title,
                session_id=session.id,
                prompt=text,
                data={"model": model or "", "provider": provider or "",
                      "context_references": reference_meta,
                      **_workspace_run_meta(run_cwd)},
            )
            run_id = run["id"]
        except Exception:  # noqa: BLE001
            run_store = None
        try:
            message = agent.run(user_input, emit)
        except Exception as exc:
            if run_store is not None and run_id:
                try:
                    run_store.finish(run_id, status="error", error=f"{type(exc).__name__}: {exc}")
                except Exception:  # noqa: BLE001
                    pass
            raise
        trace_ctx = getattr(agent, "_trace_context", {}) or {}
        result = AegisResult(
            text=message.content,
            message=message,
            session_id=agent.session.id,
            trace_id=str(trace_ctx.get("trace_id", "")),
            turn_id=str(trace_ctx.get("turn_id", "")),
            run_id=run_id,
            provider=getattr(agent.provider, "name", ""),
            model=getattr(agent.provider, "model", ""),
            usage=agent.budget.usage,
            tools_used=max(0, agent.tools_used - before_tools),
            events=events,
        )
        agent.session.meta["last_run_id"] = run_id
        if result.trace_id:
            agent.session.meta["last_trace_id"] = result.trace_id
        if result.turn_id:
            agent.session.meta["last_turn_id"] = result.turn_id
        try:
            self.store.save(agent.session)
        except Exception:  # noqa: BLE001
            pass
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
        return result

    def resume(self, session_id: str) -> Session:
        """Load a saved session by id, title, or id prefix."""

        session = self.store.load(session_id)
        if session is None:
            raise LookupError(f"session not found: {session_id}")
        return session

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list(limit=limit)

    def branch_session(self, session_id: str, *, title: str | None = None) -> Session:
        """Create a child session linked to an existing one."""

        parent = self.resume(session_id)
        child = self.store.fork(parent)
        if title:
            child.title = title
            self.store.save(child)
        return child

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        from .tracing import TraceStore

        return TraceStore.from_config(self.config).get_trace(trace_id)

    def list_traces(self, *, session_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        from .tracing import TraceStore

        return TraceStore.from_config(self.config).list_traces(session_id=session_id, limit=limit)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        from .runs import RunStore

        return RunStore().get(run_id)

    def list_runs(
        self,
        *,
        limit: int = 50,
        surface: str | None = None,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        from .runs import RunStore

        return RunStore().list(limit=limit, surface=surface, session_id=session_id, status=status)

    def replay_session(self, session_id: str) -> dict[str, Any]:
        from .evals import replay_session

        return replay_session(session_id, self.store).to_dict()

    def replay_trace(self, trace_id: str) -> dict[str, Any]:
        from .evals import replay_trace
        from .tracing import TraceStore

        return replay_trace(trace_id, TraceStore.from_config(self.config)).to_dict()

    def evaluate_session(self, session_id: str) -> dict[str, Any]:
        from .evals import evaluate_session

        return evaluate_session(session_id, store=self.store)

    def evaluate_trace(self, trace_id: str) -> dict[str, Any]:
        from .evals import evaluate_trace
        from .tracing import TraceStore

        return evaluate_trace(trace_id, store=TraceStore.from_config(self.config))

    def run_eval_suite(self, path: str | Path) -> dict[str, Any]:
        from .evals import run_suite

        return run_suite(path, config=self.config)

    def list_eval_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        from .evals import EvalStore

        return EvalStore.from_config(self.config).list_runs(limit=limit)

    def get_eval_run(self, run_id: str) -> dict[str, Any] | None:
        from .evals import EvalStore

        return EvalStore.from_config(self.config).get_run(run_id)

    def close(self) -> None:
        """Close cached providers/MCP clients owned by this SDK client."""

        from .surface import _close_agent

        with self._cache_lock:
            agents = list(self._agents.values())
            self._agents.clear()
            self._agent_locks.clear()
        for agent in agents:
            _close_agent(agent)

    def _session(self, session_id: str | None, *, title: str | None = None) -> Session:
        if session_id:
            session = self.store.load(session_id)
            if session is None:
                raise LookupError(f"session not found: {session_id}")
        else:
            session = Session.create(title=title or "")
            self.store.save(session)
        if title and session.title != title:
            session.title = title
            self.store.save(session)
        return session

    def _agent(
        self,
        *,
        session: Session,
        model: str | None,
        provider_name: str | None,
        cwd: Path,
        auto: bool,
        include_mcp: bool,
    ) -> Agent:
        key = self._agent_key(
            session=session,
            model=model,
            provider_name=provider_name,
            cwd=cwd,
            auto=auto,
            include_mcp=include_mcp,
        )
        if self.agent_cache_size:
            lock = self._lock_for(key)
            with lock:
                with self._cache_lock:
                    cached = self._agents.get(key)
                if cached is not None:
                    from .surface import _retarget_agent

                    _retarget_agent(cached, session=session)
                    apply_session_runtime(cached)
                    if self.asker is not None:
                        cached.tool_context.asker = self.asker
                    return cached
                agent = self._new_agent(
                    session=session,
                    model=model,
                    provider_name=provider_name,
                    cwd=cwd,
                    auto=auto,
                    include_mcp=include_mcp,
                )
                with self._cache_lock:
                    self._agents[key] = agent
                    self._evict_locked()
                return agent
        return self._new_agent(
            session=session,
            model=model,
            provider_name=provider_name,
            cwd=cwd,
            auto=auto,
            include_mcp=include_mcp,
        )

    def _new_agent(
        self,
        *,
        session: Session,
        model: str | None,
        provider_name: str | None,
        cwd: Path,
        auto: bool,
        include_mcp: bool,
    ) -> Agent:
        approver = _always_approve if auto else self.approver
        controls = _sdk_runtime_controls(session)
        effective_model = model or controls.get("model")
        effective_provider = provider_name or controls.get("provider")
        if self.provider_factory is None:
            agent = Agent.create(
                self.config,
                session=session,
                model=effective_model,
                provider_name=effective_provider,
                cwd=cwd,
                approver=approver,
                store=self.store,
                include_mcp=include_mcp,
            )
        else:
            provider = self._provider(model=effective_model, provider_name=effective_provider)
            agent = Agent(
                config=self.config,
                provider=provider,
                session=session,
                cwd=cwd,
                approver=approver,
                store=self.store,
            )
            if include_mcp:
                agent.load_mcp()
        if self.asker is not None:
            agent.tool_context.asker = self.asker
        apply_session_runtime(agent, rebuild_provider=False)
        return agent

    def _agent_key(
        self,
        *,
        session: Session,
        model: str | None,
        provider_name: str | None,
        cwd: Path,
        auto: bool,
        include_mcp: bool,
    ) -> tuple[Any, ...]:
        controls = _sdk_runtime_controls(session)
        return (
            session.id,
            str(cwd.resolve()),
            provider_name or controls.get("provider") or self.config.get("model.provider", ""),
            model or controls.get("model") or self.config.get("model.default", ""),
            controls.get("reasoning_effort", ""),
            controls.get("reasoning_display", ""),
            controls.get("busy_mode", ""),
            include_mcp,
            auto,
            id(self.approver) if self.approver is not None else None,
            id(self.asker) if self.asker is not None else None,
            id(self.provider_factory) if self.provider_factory is not None else None,
            id(self.config),
        )

    def _lock_for(self, key: tuple[Any, ...]):
        import threading

        with self._cache_lock:
            lock = self._agent_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._agent_locks[key] = lock
            return lock

    def _evict_locked(self) -> None:
        from .surface import _close_agent

        while self.agent_cache_size and len(self._agents) > self.agent_cache_size:
            old_key = next(iter(self._agents))
            old = self._agents.pop(old_key)
            self._agent_locks.pop(old_key, None)
            _close_agent(old)

    def _provider(self, *, model: str | None, provider_name: str | None) -> Any:
        assert self.provider_factory is not None
        try:
            return self.provider_factory(config=self.config, model=model, provider_name=provider_name)
        except TypeError:
            try:
                return self.provider_factory(self.config, model, provider_name)
            except TypeError:
                return self.provider_factory()

    @staticmethod
    def _expand(prompt: str, cwd: Path) -> str:
        from .context_refs import expand_references

        return expand_references(prompt, cwd)

    @staticmethod
    def _expand_result(prompt: str, cwd: Path):
        from .context_refs import expand_reference_result

        return expand_reference_result(prompt, cwd)

    @staticmethod
    def _images(images: Iterable[str | Path] | None) -> list[str]:
        if not images:
            return []
        from .util import encode_image

        out: list[str] = []
        for image in images:
            text = str(image)
            if text.startswith(("data:", "http://", "https://")):
                out.append(text)
            else:
                out.append(encode_image(Path(text).expanduser()))
        return out


def run(prompt: str, **kwargs: Any) -> AegisResult:
    """Convenience one-shot: ``aegis.sdk.run("...")``."""

    client_keys = {
        "config", "store", "provider_factory", "cwd", "include_mcp",
        "approver", "asker", "agent_cache_size",
    }
    client_kwargs = {k: kwargs.pop(k) for k in list(kwargs) if k in client_keys}
    return AegisClient(**client_kwargs).run(prompt, **kwargs)


def _always_approve(_: str) -> bool:
    return True


__all__ = ["AegisClient", "AegisResult", "run"]
