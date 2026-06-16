"""Gateway hub: routes channel messages to per-conversation agent sessions."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..agent.agent import Agent
from ..config import Config
from ..session import Session, SessionStore
from .base import BasePlatformAdapter, MessageEvent


def _sync_control_session(proxy, session: Session, reply: str) -> str:
    proxy.session = session
    return reply


def _with_reply_pointer(ev: MessageEvent, text: str, *, limit: int = 500) -> str:
    quoted = " ".join(str(getattr(ev, "reply_to_text", "") or "").split())
    if not quoted:
        return text
    if len(quoted) > limit:
        quoted = quoted[:limit]
    quoted = quoted.replace('"', '\\"')
    return f'[Replying to: "{quoted}"]\n{text}'


def _memory_notification_mode(value: Any) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    raw = str(value or "").strip().lower()
    return raw if raw in {"off", "on", "verbose"} else "on"


def _memory_notification_preview(action: Any) -> str:
    if isinstance(action, dict):
        verb = str(action.get("action") or "").strip().lower()
        target = str(action.get("target") or "memory").strip().lower()
        label = "User profile" if target == "user" else "Memory"
        if verb == "remove":
            marker = "➖"
            text = action.get("old_text") or action.get("summary") or "removed"
            limit = 60
        elif verb == "replace":
            marker = "✏"
            text = action.get("content") or action.get("summary") or "updated"
            limit = 120
        elif verb == "add":
            marker = "➕"
            text = action.get("content") or action.get("summary") or "updated"
            limit = 120
        else:
            marker = "•"
            text = action.get("summary") or action.get("result") or "updated"
            limit = 120
        text = " ".join(str(text or "").split())
        if len(text) > limit:
            text = text[: limit - 1].rstrip() + "…"
        return f"💾 {label} {marker} {text}"

    text = " ".join(str(action or "").split())
    if not text:
        text = "updated"
    lower = text.lower()
    marker = "•"
    limit = 120
    if "removed" in lower or lower.startswith("remove"):
        marker = "➖"
        limit = 60
    elif "replace" in lower or "updated" in lower or "consolidated" in lower:
        marker = "✏"
    elif "remembered" in lower or lower.startswith("add"):
        marker = "➕"
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return f"💾 Memory {marker} {text}"


def _memory_notification_summaries(actions: list[Any]) -> list[str]:
    targets = {
        str(action.get("target") or "memory").strip().lower()
        for action in actions
        if isinstance(action, dict)
    }
    if not targets:
        return ["💾 Memory updated"] if actions else []
    out: list[str] = []
    if "memory" in targets:
        out.append("💾 Memory updated")
    if "user" in targets:
        out.append("💾 User profile updated")
    return out or ["💾 Memory updated"]


_GATEWAY_GENERATION_META = "_gateway_generation"
_RESUME_PENDING_META = "resume_pending"
_RESUME_REASON_META = "resume_reason"
_RESUME_MARKED_AT_META = "last_resume_marked_at"


class GatewayRunner:
    """Hub-and-spoke. Each (platform, chat[, user]) maps to one persistent session."""

    def __init__(self, config: Config, cwd: Path | None = None):
        self.config = config
        self.cwd = cwd or Path.cwd()
        self.store = SessionStore()
        self.adapters: list[BasePlatformAdapter] = []
        self._sessions: dict[str, Session] = {}
        from ..surface import SurfaceRunner
        self._surface_runner = SurfaceRunner(config, store=self.store, cwd=self.cwd, include_mcp=True)
        self._cron_runner = SurfaceRunner(config, store=self.store, cwd=self.cwd, include_mcp=True)
        self._lock = threading.RLock()
        self._key_locks: dict[str, threading.Lock] = {}   # per-session serialization
        self._agents: dict[str, object] = {}              # LRU agent cache (prefix-cache reuse)
        self._agent_signatures: dict[str, tuple[Any, ...]] = {}
        self._agent_last_used: dict[str, float] = {}
        self._generations: dict[str, int] = {}
        self._shutdown_recorded = False
        self._agent_cap = 32
        self.session_mode = config.get("gateway.session_mode", "per_channel_peer")
        self.require_mention = bool(config.get("gateway.require_mention", False))
        self.mention_triggers = [t.lower() for t in config.get("gateway.mention_triggers", []) or []]

    def add(self, adapter: BasePlatformAdapter) -> None:
        adapter._conversation_key_cb = self._key
        self.adapters.append(adapter)

    def _key(self, ev: MessageEvent) -> str:
        if getattr(ev, "internal", False) and getattr(ev, "session_key", None):
            return str(ev.session_key)
        uid = ev.user_id or "anon"
        mode = self.session_mode
        if mode == "main":
            return f"{ev.platform}:main"
        if mode == "per_channel":
            return f"{ev.platform}:{ev.chat_id}"
        if mode == "per_peer":
            return f"{ev.platform}:peer:{uid}"
        return f"{ev.platform}:{ev.chat_id}:{uid}"  # per_channel_peer (default)

    _ALWAYS_ALLOWED = {"/help", "/whoami", "/status"}

    def _is_admin(self, ev: MessageEvent) -> bool:
        admins = self.config.get("gateway.admins", []) or []
        if not admins:
            return True                      # no admin list configured => single-user, all admin
        ids = {ev.user_id, ev.user_name, f"@{ev.user_name}" if ev.user_name else None}
        return bool(ids & {str(a) for a in admins})

    def _user_commands(self) -> list[str]:
        allowed = self.config.get("gateway.user_commands", []) or []
        return sorted(self._ALWAYS_ALLOWED | {str(c) for c in allowed})

    def _command_allowed(self, ev: MessageEvent, text: str) -> bool:
        if self._is_admin(ev):
            return True
        cmd = text.split()[0].lower()
        return cmd in self._ALWAYS_ALLOWED or cmd in set(self.config.get("gateway.user_commands", []) or [])

    def interrupt(self, ev: MessageEvent) -> bool:
        """Cancel the run in progress for ev's session. True if an active agent was signalled."""
        key = self._key(ev)
        generation = self._bump_generation(key)
        self._persist_generation_marker(key, generation)
        agent = self._agents.get(key)
        cancel_event = getattr(agent, "cancel_event", None)
        if agent is not None and not (cancel_event is not None and cancel_event.is_set()):
            cancel = getattr(agent, "cancel", None)
            if callable(cancel):
                cancel()
            elif cancel_event is not None:
                agent.cancel_event.set()
            return True
        return False

    def steer(self, ev: MessageEvent, text: str) -> bool:
        """Inject mid-run guidance into the active agent for ev's session. True if queued."""
        agent = self._agents.get(self._key(ev))
        return bool(agent is not None and agent.steer(text))

    def _session(self, key: str) -> Session:
        if key not in self._sessions:
            self._sessions[key] = self.store.load(key) or Session(id=key, title=key)
        self._stamp_generation(key, self._sessions[key])
        return self._sessions[key]

    def _drop_agent(self, key: str):
        agent = self._agents.pop(key, None)
        self._agent_signatures.pop(key, None)
        self._agent_last_used.pop(key, None)
        if agent is not None:
            from ..surface import _close_agent
            _close_agent(agent)
        return agent

    def _bump_generation(self, key: str) -> int:
        with self._lock:
            value = int(self._generations.get(key, 0) or 0) + 1
            self._generations[key] = value
            return value

    def _generation(self, key: str) -> int:
        return int(self._generations.get(key, 0) or 0)

    def _stamp_generation(self, key: str, session: Session, generation: int | None = None) -> Session:
        session.meta[_GATEWAY_GENERATION_META] = int(self._generation(key) if generation is None else generation)
        return session

    def _persist_generation_marker(self, key: str, generation: int | None = None) -> None:
        try:
            with self._lock:
                session = self._sessions.get(key)
            if session is None:
                session = self.store.load(key)
            if session is None:
                return
            self._stamp_generation(key, session, generation)
            self.store.save(session)
        except Exception:  # noqa: BLE001
            pass

    def _resume_pending_directive(self, session: Session) -> str:
        if not session.meta.get(_RESUME_PENDING_META):
            return ""
        reason = str(session.meta.get(_RESUME_REASON_META) or "gateway_restart")
        return (
            "[Gateway recovery: the previous turn in this conversation may have "
            f"been interrupted by {reason}. Continue from the current transcript "
            "and answer the latest user message. Do not re-run previous tool calls, "
            "restart background work, or repeat outbound actions unless the user "
            "explicitly asks.]\n"
        )

    def _clear_resume_pending(self, session: Session) -> bool:
        if not session.meta.get(_RESUME_PENDING_META):
            return False
        for key in (_RESUME_PENDING_META, _RESUME_REASON_META, _RESUME_MARKED_AT_META):
            session.meta.pop(key, None)
        try:
            self.store.save(session)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _mark_running_sessions_resume_pending(self, cause: str) -> int:
        reason = str(cause or "shutdown")
        with self._lock:
            running = [
                (key, session)
                for key, session in self._sessions.items()
                if self._key_locks.get(key) is not None and self._key_locks[key].locked()
            ]
        if not running:
            return 0
        from ..util import now_iso
        marked = 0
        for _key, session in running:
            try:
                session.meta[_RESUME_PENDING_META] = True
                session.meta[_RESUME_REASON_META] = reason
                session.meta[_RESUME_MARKED_AT_META] = now_iso()
                self.store.save(session)
                marked += 1
            except Exception:  # noqa: BLE001
                continue
        return marked

    def _report_resume_pending_sessions(self, delivery_queue=None) -> int:
        try:
            pending = self.store.list_resume_pending(limit=20)
        except Exception:  # noqa: BLE001
            return 0
        if not pending:
            return 0
        count = len(pending)
        suffix = "" if count == 1 else "s"
        text = (
            f"{count} gateway session{suffix} pending restart recovery; "
            "the next user turn will resume on the same transcript."
        )
        print(f"  ! {text}")
        admins = [str(a) for a in (self.config.get("gateway.admins", []) or [])]
        if delivery_queue is not None:
            for adapter in self.adapters:
                for admin in admins:
                    delivery_queue.enqueue(adapter.name, admin, f"⚠️ AEGIS recovery: {text}")
        try:
            from ..eventbus import BUS
            BUS.publish({"type": "restart_notice", "text": text, "resume_pending": pending})
        except Exception:  # noqa: BLE001
            pass
        return count

    def _recover_stale_gateway_runs(self) -> int:
        try:
            from ..runs import RunStore

            runs = RunStore()
            stale = runs.list(surface="gateway", status="running", limit=100)
        except Exception:  # noqa: BLE001
            return 0
        recovered = 0
        for row in stale:
            sid = str(row.get("session_id") or "")
            marked = False
            if sid:
                try:
                    marked = self.store.mark_resume_pending(sid, "restart_interrupted")
                except Exception:  # noqa: BLE001
                    marked = False
            try:
                runs.finish(
                    row["id"],
                    status="interrupted",
                    error="Gateway restarted before this turn completed.",
                    data={"resume_pending": marked, "recovered_by_gateway_start": True},
                )
            except Exception:  # noqa: BLE001
                pass
            recovered += 1
        if recovered:
            suffix = "" if recovered == 1 else "s"
            print(f"  ! recovered {recovered} stale gateway run{suffix}")
        return recovered

    def _fresh_session_if_drifted(self, key: str, session: Session) -> Session:
        try:
            stamp = self.store.session_stamp(key)
        except Exception:  # noqa: BLE001
            return session
        if stamp is None:
            return session
        local_count = len(getattr(session, "messages", []) or [])
        stored_count = int(stamp.get("message_count", local_count) or 0)
        stored_updated = str(stamp.get("updated_at") or "")
        if not (
            stored_updated > getattr(session, "updated_at", "")
            or stored_count > local_count
        ):
            return session
        latest = self.store.load(key)
        if latest is None:
            return session
        self._sessions[key] = latest
        self._drop_agent(key)
        generation = self._bump_generation(key)
        self._stamp_generation(key, latest, generation)
        self.store.save(latest)
        return latest

    def _agent_signature(
        self,
        *,
        ev: MessageEvent,
        key: str,
        session: Session,
        run_config,
        profile: dict,
    ) -> tuple[Any, ...]:
        from ..surface import _cache_runtime_fingerprint, session_runtime_controls

        controls = session_runtime_controls(session)
        provider = controls.get("provider") or profile.get("provider") or run_config.get("model.provider", "")
        model = controls.get("model") or profile.get("model") or run_config.get("model.default", "")
        return (
            key,
            ev.platform,
            ev.chat_id,
            ev.thread_id or "",
            ev.user_id or "",
            provider,
            model,
            controls.get("reasoning_effort", ""),
            controls.get("reasoning_display", ""),
            controls.get("busy_mode", ""),
            *_cache_runtime_fingerprint(run_config, provider),
        )

    def _agent_expired(self, key: str) -> bool:
        import time

        ttl = float(self.config.get("gateway.agent_cache_ttl_seconds", 0) or 0)
        if ttl <= 0:
            return False
        last = float(self._agent_last_used.get(key, 0.0) or 0.0)
        return last > 0 and (time.time() - last) > ttl

    def _touch_agent(self, key: str) -> None:
        import time

        self._agent_last_used[key] = time.time()

    def _evict_agents_if_needed(self) -> None:
        import time

        ttl = float(self.config.get("gateway.agent_cache_ttl_seconds", 0) or 0)
        if ttl > 0:
            cutoff = time.time() - ttl
            for old_key, last in list(self._agent_last_used.items()):
                if last and last < cutoff:
                    self._drop_agent(old_key)
        while self._agent_cap and len(self._agents) > self._agent_cap:
            evict_key = min(self._agents, key=lambda k: self._agent_last_used.get(k, 0.0))
            self._drop_agent(evict_key)

    def _gateway_asker(self, ev: MessageEvent):
        adapter = next((a for a in self.adapters if a.name == ev.platform), None)
        if adapter is None or not hasattr(adapter, "ask_user"):
            return None
        timeout = float(self.config.get("gateway.clarify_timeout_seconds", 3600) or 3600)

        def ask(question: str, choices: list[str]) -> str:
            return str(adapter.ask_user(ev, question, choices, timeout=timeout) or "")

        return ask

    def _gateway_approver(self, ev: MessageEvent):
        adapter = next((a for a in self.adapters if a.name == ev.platform), None)
        if adapter is None:
            return None
        timeout = float(self.config.get("gateway.approval_timeout_seconds",
                                        self.config.get("gateway.clarify_timeout_seconds", 3600)) or 3600)

        def approve(prompt: str):
            ask_exec = getattr(adapter, "ask_exec_approval", None)
            if callable(ask_exec):
                answer = str(ask_exec(ev, prompt, timeout=timeout) or "")
            elif hasattr(adapter, "ask_user"):
                answer = str(adapter.ask_user(ev, prompt, ["approve", "deny"], timeout=timeout) or "")
            else:
                return False
            normalized = answer.strip().lower()
            if normalized in {"always", "approve always", "allow always"}:
                return "always"
            return normalized in {"y", "yes", "ok", "approve", "approved", "allow", "allowed"}

        return approve

    @staticmethod
    def _parse_model_override(arg: str) -> tuple[str, str]:
        raw = arg.strip()
        if "/" in raw:
            provider, model = raw.split("/", 1)
            return provider.strip(), model.strip()
        return "", raw

    def _gateway_identity(self, ev: MessageEvent, key: str, session: Session) -> str:
        from ..surface import session_runtime_controls

        controls = session_runtime_controls(session)
        provider = controls.get("provider") or self.config.get("model.provider")
        model = controls.get("model") or self.config.get("model.default")
        busy_mode = controls.get("busy_mode") or self.config.get("gateway.busy_mode", "queue")
        reasoning_display = controls.get("reasoning_display") or self.config.get("display.reasoning", "summary")
        reasoning_effort = controls.get("reasoning_effort") or self.config.get("agent.reasoning_effort", "medium")
        return (
            f"platform: {ev.platform}\nuser: {ev.user_id or '?'}"
            f"{f' (@{ev.user_name})' if ev.user_name else ''}\nchat: {ev.chat_id}\n"
            f"session: {key}\nprovider: {provider}\nmodel: {model}\n"
            f"busy_mode: {busy_mode}\nreasoning: display={reasoning_display} · effort={reasoning_effort}"
        )

    def _gateway_status(self, key: str, session: Session) -> str:
        from ..surface import session_runtime_controls

        controls = session_runtime_controls(session)
        agent = self._agents.get(key)
        provider_obj = getattr(agent, "provider", None)
        provider = (
            getattr(provider_obj, "name", "")
            or controls.get("provider")
            or self.config.get("model.provider")
        )
        model = (
            getattr(provider_obj, "model", "")
            or controls.get("model")
            or self.config.get("model.default")
        )
        try:
            from ..agent import compaction

            context_used = int(compaction.estimated_tokens(session.messages))
        except Exception:  # noqa: BLE001
            context_used = 0
        try:
            context_total = int(
                getattr(provider_obj, "context_length", 0)
                or self.config.get("model.context_length", 0)
                or 0
            )
        except (TypeError, ValueError):
            context_total = 0
        if context_total > 0:
            pct = min(100, round((context_used / context_total) * 100)) if context_used else 0
            context = f"context≈{context_used:,}/{context_total:,} tokens ({pct}%)"
        else:
            context = f"context≈{context_used:,} tokens"
        messages = len(session.messages)
        return (
            f"AEGIS gateway · provider={provider} · model={model} · session={key}\n"
            f"{context} · messages={messages}\n"
            "Commands: /new · /status · /whoami · /model [provider/model] · "
            "/provider [name] · /reasoning [mode] · /compress · /busy [mode] · "
            "/goal <text> · /subgoal <text> · /steer <text> · stop"
        )

    def _adopt_handoff(self, ev: MessageEvent, key: str) -> None:
        # A pending CLI handoff should affect the very next gateway message,
        # including control-plane commands like /status and /whoami.
        try:
            from ..handoff import pop_handoff

            ho = pop_handoff(ev.platform, ev.chat_id)
            if ho and (adopted := self.store.load(ho)) is not None:
                with self._lock:
                    self._stamp_generation(key, adopted)
                    self._sessions[key] = adopted
                self._drop_agent(key)
        except Exception:  # noqa: BLE001
            pass

    def _control_reply(
        self,
        ev: MessageEvent,
        key: str,
        command: str,
        action,
        *,
        data: dict | None = None,
    ) -> str:
        with self._lock:
            session = self._session(key)
            self._stamp_generation(key, session)
        proxy = SimpleNamespace(
            config=self.config,
            session=session,
            cwd=self.cwd,
            provider=None,
        )
        from ..surface import run_control_action

        run = run_control_action(
            proxy,
            lambda _emit: action(proxy),
            config=self.config,
            session=session,
            surface="gateway",
            kind="control",
            title=f"gateway {command}",
            prompt=ev.text.strip(),
            data={
                "command": command,
                "platform": ev.platform,
                "chat_id": ev.chat_id,
                "user_id": ev.user_id or "",
                "user_name": ev.user_name or "",
                **(data or {}),
            },
        )
        self._stamp_generation(key, run.session)
        self._sessions[key] = run.session
        self.store.save(run.session)
        return run.text

    def dispatch(self, ev: MessageEvent) -> str:
        text = ev.text.strip()
        command_started_turn = False
        is_internal = bool(getattr(ev, "internal", False))
        key = self._key(ev)
        # Authorization: unknown users must pair first.
        if not is_internal:
            from .pairing import PairingStore
            pairing = PairingStore()
            if not pairing.is_authorized(ev.platform, ev.user_id, ev.user_name):
                code = pairing.request_code(ev.platform, ev.user_id or "?")
                return (f"⛔ Not authorized. Ask the operator to run:\n"
                        f"  aegis pairing approve {ev.platform} {code}")
            self._adopt_handoff(ev, key)
        # Command tiers: admins get every command; regular users only an allowlisted
        # subset (+ the always-allowed floor). Unset admin list => everyone is admin
        # (backward-compatible single-user default).
        if text.startswith("/") and not self._command_allowed(ev, text):
            return ("⛔ That command is restricted to admins. Available to you: "
                    + ", ".join(self._user_commands()))
        # Intercept control commands before the agent.
        if text == "/stop":
            running = (lk := self._key_locks.get(key)) is not None and lk.locked()
            agent = self._agents.get(key) if running else None
            if agent is not None and not getattr(agent, "cancel_event", threading.Event()).is_set():
                generation = self._bump_generation(key)
                self._persist_generation_marker(key, generation)
                cancel = getattr(agent, "cancel", None)
                if callable(cancel):
                    cancel()
                else:
                    agent.cancel_event.set()
                return self._control_reply(
                    ev,
                    key,
                    "/stop",
                    lambda _proxy: "🛑 stop requested.",
                    data={"stopped": True},
                )
            return self._control_reply(
                ev,
                key,
                "/stop",
                lambda _proxy: "No active turn is running.",
                data={"stopped": False},
            )
        if text in ("/new", "/reset"):
            def action(proxy):
                generation = self._bump_generation(key)
                self._drop_agent(key)
                fresh = self._stamp_generation(key, Session(id=key, title=key), generation)
                self._sessions[key] = fresh
                proxy.session = fresh
                self.store.save(fresh)
                return "🔄 Started a fresh session."

            return self._control_reply(ev, key, text.split()[0], action)
        if text in ("/status", "/help"):
            return self._control_reply(
                ev,
                key,
                text.split()[0],
                lambda proxy: self._gateway_status(key, proxy.session),
            )
        if text == "/whoami":
            return self._control_reply(
                ev,
                key,
                "/whoami",
                lambda proxy: self._gateway_identity(ev, key, proxy.session),
            )
        if text == "/model" or text.startswith("/model "):
            arg = text[len("/model"):].strip()
            def action(proxy):
                session = proxy.session
                from ..surface import remember_session_runtime, session_runtime_controls
                if not arg:
                    controls = session_runtime_controls(session)
                    provider = controls.get("provider") or self.config.get("model.provider")
                    model = controls.get("model") or self.config.get("model.default")
                    return f"model: {provider}/{model}\nSwitch for this session with /model <id> or /model <provider>/<id>."
                provider, model = self._parse_model_override(arg)
                if not model:
                    return "usage: /model <model> or /model <provider>/<model>"
                from ..providers import registry
                controls = session_runtime_controls(session)
                target_provider = provider or controls.get("provider") or self.config.get("model.provider", "")
                validation = registry.validate_model_choice(target_provider, model, self.config)
                warning = registry.model_validation_message(validation)
                if not validation.get("ok", True):
                    return warning
                updates = {"model": model}
                if provider:
                    updates["provider"] = provider
                remember_session_runtime(type("A", (), {"session": session})(), **updates)
                self.store.save(session)
                self._drop_agent(key)        # rebuild with the new model next turn
                label = f"{provider}/" if provider else ""
                reply = f"✓ model for this session → {label}{model}"
                if warning and validation.get("warning"):
                    reply += f"\nwarning: {warning}"
                return reply

            return self._control_reply(ev, key, "/model", action, data={"model": arg})
        if text == "/provider" or text.startswith("/provider "):
            arg = text[len("/provider"):].strip()
            def action(proxy):
                session = proxy.session
                from ..surface import remember_session_runtime, session_runtime_controls
                if not arg:
                    controls = session_runtime_controls(session)
                    cur = controls.get("provider") or self.config.get("model.provider")
                    return f"provider: {cur}\nSwitch for this session with /provider <name>."
                from ..providers import registry
                controls = session_runtime_controls(session)
                model = controls.get("model") or self.config.get("model.default", "")
                validation = registry.validate_model_choice(arg, model, self.config)
                warning = registry.model_validation_message(validation)
                if not validation.get("ok", True):
                    return warning
                remember_session_runtime(type("A", (), {"session": session})(), provider=arg)
                self.store.save(session)
                self._drop_agent(key)
                reply = f"✓ provider for this session → {arg}"
                if warning and validation.get("warning"):
                    reply += f"\nwarning: {warning}"
                return reply

            return self._control_reply(ev, key, "/provider", action, data={"provider": arg})
        if text == "/reasoning" or text.startswith("/reasoning "):
            arg = text[len("/reasoning"):].strip()
            modes = {"off", "summary", "live"}
            efforts = {"minimal", "low", "medium", "high", "xhigh"}

            def action(proxy):
                session = proxy.session
                from ..surface import remember_session_runtime, session_runtime_controls
                controls = session_runtime_controls(session)
                if not arg:
                    display = controls.get("reasoning_display") or self.config.get("display.reasoning", "summary")
                    effort = controls.get("reasoning_effort") or self.config.get("agent.reasoning_effort", "medium")
                    return f"reasoning: display={display} · effort={effort}"
                if arg in modes:
                    remember_session_runtime(type("A", (), {"session": session})(), reasoning_display=arg)
                    self.store.save(session)
                    agent = self._agents.get(key)
                    if agent is not None:
                        from ..surface import apply_session_runtime
                        apply_session_runtime(agent, rebuild_provider=False)
                    return f"✓ reasoning display → {arg}"
                if arg in efforts or arg in {"off", "none"}:
                    value = "off" if arg == "none" else arg
                    remember_session_runtime(type("A", (), {"session": session})(), reasoning_effort=value)
                    self.store.save(session)
                    agent = self._agents.get(key)
                    if agent is not None:
                        from ..surface import apply_session_runtime
                        apply_session_runtime(agent, rebuild_provider=False)
                    return f"✓ reasoning effort → {value}"
                return "usage: /reasoning off|none|summary|live|minimal|low|medium|high|xhigh"

            return self._control_reply(ev, key, "/reasoning", action, data={"mode": arg})
        if text == "/busy" or text.startswith("/busy "):
            arg = text[len("/busy"):].strip()
            def action(proxy):
                session = proxy.session
                from ..surface import remember_session_runtime, session_runtime_controls
                if not arg:
                    controls = session_runtime_controls(session)
                    mode = controls.get("busy_mode") or self.config.get("gateway.busy_mode", "queue")
                    return (f"busy_mode: {mode} "
                            "(queue | steer | interrupt)")
                if arg not in ("queue", "steer", "interrupt"):
                    return "usage: /busy queue|steer|interrupt"
                self.config.set("gateway.busy_mode", arg)
                self.config.save()
                remember_session_runtime(type("A", (), {"session": session})(), busy_mode=arg)
                self.store.save(session)
                agent = self._agents.get(key)
                if agent is not None:
                    from ..surface import apply_session_runtime
                    apply_session_runtime(agent, rebuild_provider=False)
                return f"✓ busy_mode → {arg}"

            return self._control_reply(ev, key, "/busy", action, data={"mode": arg})
        if text == "/compress":
            with self._lock:
                session = self._session(key)
            from ..surface import apply_session_runtime, session_runtime_controls
            controls = session_runtime_controls(session)
            agent = self._agents.get(key)
            if agent is None:
                agent = Agent.create(
                    self.config,
                    session=session,
                    cwd=self.cwd,
                    store=self.store,
                    model=controls.get("model"),
                    provider_name=controls.get("provider"),
                )
            apply_session_runtime(agent)
            before = len(session.messages)

            def _compact(emit):
                from ..agent.loop import compact_now

                compact_now(
                    agent,
                    session,
                    emit,
                    reason="manual_context_compression",
                )
                after = len(agent.session.messages)
                return f"🗜 compressed: {before} → {after} messages"

            try:
                from ..surface import run_control_action

                run = run_control_action(
                    agent,
                    _compact,
                    config=self.config,
                    session=session,
                    surface="gateway",
                    kind="compaction",
                    title="gateway context compression",
                    prompt=text,
                    data={"platform": ev.platform, "chat_id": ev.chat_id,
                          "user_id": ev.user_id or ""},
                )
            except Exception as e:  # noqa: BLE001
                return f"⚠ compress failed: {type(e).__name__}: {e}"
            self._sessions[key] = run.session
            self._agents[key] = agent
            self.store.save(run.session)
            return run.text
        if text.startswith(("/goal", "/subgoal")):
            from .. import goals
            # Replacing the goal mid-run would race the active continuation loop — reject
            # like /goal status etc. stay safe (they only touch control-plane state).
            running = (lk := self._key_locks.get(key)) is not None and lk.locked()
            arg = text.split(None, 1)[1].strip().lower() if " " in text else ""
            if (running and text.startswith("/goal")
                    and arg not in ("", "status", "pause", "resume", "clear")):
                return self._control_reply(
                    ev,
                    key,
                    "/goal",
                    lambda _proxy: "⚠ a turn is running — send 'stop' first, then set the new goal.",
                    data={"rejected": True, "reason": "turn_running"},
                )
            with self._lock:
                session = self._session(key)
            reply, start_turn = goals.handle_command(session, text, self.config)
            self.store.save(session)
            if not start_turn:
                return self._control_reply(
                    ev,
                    key,
                    text.split()[0],
                    lambda proxy: _sync_control_session(proxy, session, reply or ""),
                    data={"start_turn": False},
                )
            command_started_turn = True
            text = goals.get(session)["text"]   # fall through: run the new goal as this turn

        # Mention gating: in shared channels only respond when a trigger is present.
        if (not is_internal and self.require_mention and self.mention_triggers
                and not text.startswith("/") and not command_started_turn):
            if not any(trig in text.lower() for trig in self.mention_triggers):
                return ""  # ignored — not addressed to the bot
            for trig in self.mention_triggers:
                text = text.replace(trig, "").replace(trig.title(), "").strip() or text

        # Voice memos / audio attachments -> transcribe and prepend.
        text = self._maybe_transcribe(ev, text)
        text = _with_reply_pointer(ev, text)

        # Very first message ever -> one-shot, consent-gated profile-build offer.
        if not is_internal:
            from ..firstrun import profile_build_directive
            text += profile_build_directive(self.config)

        # Serialize per session so one session isn't run concurrently (race on messages).
        with self._lock:
            lock = self._key_locks.setdefault(key, threading.Lock())
        with lock:
            with self._lock:
                session = self._session(key)
                session = self._fresh_session_if_drifted(key, session)
            # Reuse a cached agent for this session (keeps the provider object warm so the
            # model's prompt prefix stays cached); rebuild if the session was reset.
            generation = self._generation(key)
            self._stamp_generation(key, session, generation)
            prof = (self.config.get("gateway.profiles", {}) or {}).get(ev.platform, {}) or {}
            run_cfg = self.config
            if prof.get("personality"):       # isolated copy — must not leak across platforms
                import copy
                run_cfg = type(self.config)(copy.deepcopy(self.config.data))
                run_cfg.data.setdefault("agent", {})["personality"] = prof["personality"]
            signature = self._agent_signature(
                ev=ev,
                key=key,
                session=session,
                run_config=run_cfg,
                profile=prof,
            )
            agent = self._agents.get(key)
            if agent is not None and (self._agent_signatures.get(key) != signature or self._agent_expired(key)):
                self._drop_agent(key)
                agent = None
            if agent is None or agent.session is not session:
                from ..surface import apply_session_runtime, session_runtime_controls
                controls = session_runtime_controls(session)
                agent = Agent.create(run_cfg, session=session, cwd=self.cwd, store=self.store,
                                     model=controls.get("model") or prof.get("model"),
                                     provider_name=controls.get("provider") or prof.get("provider"),
                                     include_mcp=True)   # /model > profile
                apply_session_runtime(agent)
                self._agents[key] = agent
                self._agent_signatures[key] = signature
                self._touch_agent(key)
                self._evict_agents_if_needed()
            else:
                from ..surface import apply_session_runtime
                apply_session_runtime(agent)
                self._touch_agent(key)
            agent.platform = ev.platform   # channel-specific prompt behavior
            agent.chat_id = ev.chat_id     # current conversation (for the send_message tool)
            agent.user_id = ev.user_id or ""
            agent.user_name = ev.user_name or ""
            agent.thread_id = ev.thread_id or ""
            agent.message_id = ev.message_id or ""
            asker = self._gateway_asker(ev)
            approver = self._gateway_approver(ev)
            if asker is not None:
                agent.tool_context.asker = asker
            if approver is not None:
                agent.tool_context.approver = approver
            try:
                # Safety net: a gateway session can accumulate messages between turns
                # (overnight Telegram/Discord) and blow past the window before the agent's
                # in-loop compactor runs. Force a pre-turn compaction when it's grown large.
                session = self._gateway_hygiene(agent, session) or session
                self._sessions[key] = session
                resume_directive = self._resume_pending_directive(session)
                if resume_directive:
                    text = resume_directive + text

                learned: list[str] = []

                from ..eventbus import BUS
                BUS.publish({"platform": ev.platform, "chat_id": ev.chat_id,
                             "type": "internal_message" if is_internal else "user_message",
                             "text": text})

                def _collect(ev_: dict) -> None:
                    t = ev_.get("type")
                    if t in ("tool_start", "tool_result"):   # mirror tool activity to the dashboard
                        BUS.publish({"platform": ev.platform, "chat_id": ev.chat_id, "type": t,
                                     "name": ev_.get("name"), "summary": ev_.get("summary")})
                    if t == "tool_result" and not ev_.get("is_error") and ev_.get("name") == "memory":
                        learned.append(f"💾 {ev_.get('summary', 'remembered')}")
                    elif t == "tool_result" and not ev_.get("is_error") and ev_.get("name") == "skill":
                        learned.append(f"📝 {ev_.get('summary', 'skill')}")
                    elif t == "review_done":
                        details = ev_.get("action_details") or []
                        actions = details or ev_.get("actions") or []
                        if ev_.get("kind") == "memory":
                            mode = _memory_notification_mode(
                                self.config.get("display.memory_notifications", "on")
                            )
                            if mode == "verbose":
                                for action in actions:
                                    learned.append(_memory_notification_preview(action))
                            elif mode == "on":
                                learned.extend(_memory_notification_summaries(actions))
                        else:
                            for a in actions:
                                learned.append(f"🧠 {a}")

                run = self._surface_runner.run_prompt(
                    text,
                    session=session,
                    agent=agent,
                    approver=approver,
                    asker=asker,
                    surface="gateway",
                    meta={"platform": ev.platform, "chat_id": ev.chat_id, "user_id": ev.user_id or ""},
                    platform=ev.platform,
                    chat_id=ev.chat_id,
                    include_wakeups=not is_internal,
                    on_event=_collect,
                )
                if generation != self._generation(key):
                    return ""
                session = getattr(run, "session", getattr(agent, "session", session))
                self._stamp_generation(key, session, generation)
                self._sessions[key] = session
                final = run.message
                final_text = final.content or ""
                goal_notes: list[str] = []
                try:                       # standing /goal: judge + auto-continue (Ralph loop)
                    from .. import goals

                    def _run_goal_turn(prompt_text: str):
                        cont = self._surface_runner.run_prompt(
                            prompt_text,
                            session=agent.session,
                            agent=agent,
                            approver=approver,
                            asker=asker,
                            surface="gateway",
                            meta={
                                "platform": ev.platform,
                                "chat_id": ev.chat_id,
                                "user_id": ev.user_id or "",
                                "goal_continuation": True,
                            },
                            platform=ev.platform,
                            chat_id=ev.chat_id,
                            include_wakeups=not is_internal,
                            on_event=_collect,
                        )
                        if generation != self._generation(key):
                            return cont.message
                        cont_session = getattr(cont, "session", getattr(agent, "session", session))
                        self._stamp_generation(key, cont_session, generation)
                        self._sessions[key] = cont_session
                        self.store.save(cont_session)
                        return cont.message

                    final_text = goals.run_loop(
                        agent, final_text, goal_notes.append, _collect, run_turn=_run_goal_turn
                    )
                    if goal_notes:
                        session = agent.session
                        self._stamp_generation(key, agent.session, generation)
                        self._sessions[key] = agent.session
                        self.store.save(agent.session)
                except Exception:  # noqa: BLE001  (goal machinery must never eat the reply)
                    pass
                from ..redact import redact_secrets
                from .replies import shape_reply
                api_calls = getattr(getattr(agent, "budget", None), "api_call_count", 0)
                # secrets out, raw provider errors -> friendly one-liner, empty -> clear message
                reply = shape_reply(redact_secrets(final_text), api_calls=api_calls)
                if goal_notes:
                    reply += "\n\n" + "\n".join(goal_notes)
                if learned and self.config.get("gateway.show_learning", True):
                    reply += "\n\n— " + " · ".join(dict.fromkeys(learned))   # dedup, keep order
                if generation != self._generation(key):
                    return ""
                if self._clear_resume_pending(session):
                    self._sessions[key] = session
                BUS.publish({"platform": ev.platform, "chat_id": ev.chat_id,
                             "type": "assistant_message", "text": reply})
                if not is_internal:
                    self._drain_process_notifications()
                return reply
            except Exception as e:  # noqa: BLE001
                return f"⚠ error: {type(e).__name__}: {e}"

    def _gateway_hygiene(self, agent, session):
        """Pre-turn compaction safety net (AEGIS "session hygiene"). Fires only when a
        session has grown large between turns — crossing the hygiene token threshold OR a
        hard message ceiling — at a higher bar than the agent's own in-loop compactor so it
        doesn't compact on every turn. Returns the active session (may be a compaction child)."""
        comp = self.config.get("agent.compression", {}) or {}
        msgs = session.messages
        if len(msgs) < 4:
            return session
        hard = int(comp.get("hard_message_limit", 400) or 0)
        over_count = hard > 0 and len(msgs) >= hard
        over_tokens = False
        ctx = getattr(getattr(agent, "provider", None), "context_length", 0) or 0
        if ctx > 0:
            from ..agent import compaction
            frac = float(comp.get("gateway_hygiene_threshold", 0.85) or 0.85)
            over_tokens = compaction.estimated_tokens(msgs) > ctx * frac
        if not (over_count or over_tokens):
            return session
        try:
            from ..agent.loop import compact_now
            new_session = compact_now(agent, session=session, reason="gateway_hygiene")
            if new_session is not None:
                session = new_session
                self.store.save(session)
        except Exception:  # noqa: BLE001 — never let hygiene crash a turn
            pass
        return session

    def _maybe_transcribe(self, ev: MessageEvent, text: str) -> str:
        audio = next((a for a in (ev.attachments or [])
                      if str(a.get("type", "")).startswith("audio") or a.get("path", "").endswith(
                          (".ogg", ".mp3", ".m4a", ".wav"))), None)
        if not audio or not audio.get("path"):
            return text
        try:
            from ..tools.voice import TranscribeTool
            from ..tools.base import ToolContext
            res = TranscribeTool().run({"path": audio["path"]},
                                       ToolContext(cwd=self.cwd, config=self.config))
            if not res.is_error:
                return (text + "\n\n[voice memo transcript]\n" + res.content).strip()
        except Exception:  # noqa: BLE001
            pass
        return text

    def _submit_process_notification(self, event: dict, text: str) -> bool:
        platform = str(event.get("platform") or "")
        chat_id = str(event.get("chat_id") or "")
        if not platform or not chat_id:
            return False
        adapter = next((a for a in self.adapters if a.name == platform), None)
        if adapter is None:
            return False
        synth = MessageEvent(
            platform=platform,
            chat_id=chat_id,
            text=text,
            user_id=str(event.get("user_id") or "") or None,
            user_name=str(event.get("user_name") or "") or None,
            thread_id=str(event.get("thread_id") or "") or None,
            message_id=str(event.get("message_id") or "") or None,
            session_key=str(event.get("session_key") or "") or None,
            internal=True,
        )
        try:
            adapter._submit_inbound(synth)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _drain_process_notifications(self) -> int:
        try:
            from ..tools.process_registry import process_registry

            events = process_registry.drain_notifications()
        except Exception:  # noqa: BLE001
            return 0
        if not events:
            return 0
        try:
            from ..agent import wakeups

            wakeups.drain_wakeups(source="process")
        except Exception:  # noqa: BLE001
            pass
        submitted = 0
        for event, text in events:
            if self._submit_process_notification(event, text):
                submitted += 1
            else:
                try:
                    process_registry.requeue_notification(event)
                except Exception:  # noqa: BLE001
                    pass
        return submitted

    def _process_notification_loop(self, interval: float = 0.5) -> None:
        import time

        while True:
            self._drain_process_notifications()
            time.sleep(interval)

    def enqueue(self, platform: str, chat_id: str, text: str) -> None:
        """Durably queue an outbound message (used by cron + retry on send failure)."""
        from .queue import DeliveryQueue
        DeliveryQueue().enqueue(platform, chat_id, text)

    def _send_via_adapter(self, platform: str, chat_id: str, text: str) -> bool:
        adapter = next((a for a in self.adapters if a.name == platform), None)
        if adapter is None:
            return False
        try:
            deliver = getattr(adapter, "deliver", None)
            if callable(deliver):
                deliver(chat_id, text)
            else:
                adapter.send(chat_id, text)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _cron_sink(self, channel: str, text: str) -> None:
        """Deliver cron output. ``channel`` is 'platform:chat_id' -> queue to the outbox."""
        platform, _, chat_id = (channel or "").partition(":")
        if platform and chat_id:
            self.enqueue(platform, chat_id, text or "")

    def _cron_ticker(self, interval: int = 60) -> None:
        import time as _time

        from .. import cron
        while True:
            try:
                cron.tick(self.config, sink=self._cron_sink, verbose=False, runner=self._cron_runner)
            except Exception:  # noqa: BLE001 - a bad job must not kill the ticker
                pass
            _time.sleep(interval)

    def run(self) -> None:
        if not self.adapters:
            raise RuntimeError("No channels configured for the gateway.")
        from .queue import DeliveryQueue
        threads: list[threading.Thread] = []
        for adapter in self.adapters:
            adapter._interrupt_cb = self.interrupt    # adapters that poll concurrently use this
            adapter._steer_cb = self.steer            # mid-run /steer guidance
            adapter._config = self.config             # busy_mode + first-touch hints
            t = threading.Thread(target=adapter.start, args=(self.dispatch,), daemon=True)
            t.start()
            threads.append(t)
            print(f"  ▸ channel up: {adapter.name}")
        # durable delivery drainer (retries queued/failed sends across restarts)
        q = DeliveryQueue()
        threading.Thread(target=q.run, args=(self._send_via_adapter,), daemon=True).start()
        if q.pending_count():
            print(f"  ▸ delivery queue: {q.pending_count()} pending (will retry)")
        # in-process cron ticker so scheduled/one-shot jobs fire without a separate daemon
        threading.Thread(target=self._cron_ticker, daemon=True).start()
        print("  ▸ cron ticker up")
        threading.Thread(target=self._process_notification_loop, daemon=True).start()
        print("  ▸ process notification watcher up")
        from . import memory_monitor          # periodic RSS log to catch leaks
        memory_monitor.start()
        import signal
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, self._on_shutdown_signal)
            except (ValueError, OSError):     # not on the main thread / unsupported
                pass
        # Restart forensics: tell the operator when the PREVIOUS run died uncleanly.
        try:
            from ..doctor import crash_report, record_start
            report = crash_report()
            record_start()
            if report:
                print(f"  ! {report} (see logs/shutdowns.jsonl)")
                admins = [str(a) for a in (self.config.get("gateway.admins", []) or [])]
                for adapter in self.adapters:        # best-effort DM to admins
                    for admin in admins:
                        q.enqueue(adapter.name, admin, f"⚠️ AEGIS restarted: {report}")
                from ..eventbus import BUS
                BUS.publish({"type": "restart_notice", "text": report})
        except Exception:  # noqa: BLE001
            pass
        self._recover_stale_gateway_runs()
        self._report_resume_pending_sessions(q)
        print("Gateway running. Ctrl+C to stop.")
        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            self._record_shutdown("KeyboardInterrupt")
            print("\nGateway stopped.")

    def _on_shutdown_signal(self, signum, _frame) -> None:
        import signal
        cause = signal.Signals(signum).name
        try:
            from .status import consume_planned_stop_marker_for_self

            if consume_planned_stop_marker_for_self():
                cause = "planned_stop"
        except Exception:  # noqa: BLE001
            pass
        self._mark_running_sessions_resume_pending(cause)
        self._record_shutdown(cause)
        raise KeyboardInterrupt

    def _record_shutdown(self, cause: str) -> None:
        """Durably log who/what triggered shutdown so 'the gateway keeps dying' is
        diagnosable after the fact. Fast + best-effort — never blocks teardown."""
        if getattr(self, "_shutdown_recorded", False):
            return
        try:
            import json
            import os
            from .. import config as cfg
            from ..util import now_iso
            rec = {"at": now_iso(), "cause": cause, "pid": os.getpid(),
                   "ppid": os.getppid(), "channels": [a.name for a in self.adapters]}
            path = cfg.logs_dir() / "shutdowns.jsonl"
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            self._shutdown_recorded = True
        except Exception:  # noqa: BLE001
            pass
