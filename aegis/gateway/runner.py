"""Gateway hub: routes channel messages to per-conversation agent sessions."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

from ..agent.agent import Agent
from ..config import Config
from ..session import Session, SessionStore
from .base import BasePlatformAdapter, MessageEvent


def _sync_control_session(proxy, session: Session, reply: str) -> str:
    proxy.session = session
    return reply


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
        self._lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}   # per-session serialization
        self._agents: dict[str, object] = {}              # LRU agent cache (prefix-cache reuse)
        self._agent_cap = 32
        self.session_mode = config.get("gateway.session_mode", "per_channel_peer")
        self.require_mention = bool(config.get("gateway.require_mention", False))
        self.mention_triggers = [t.lower() for t in config.get("gateway.mention_triggers", []) or []]

    def add(self, adapter: BasePlatformAdapter) -> None:
        self.adapters.append(adapter)

    def _key(self, ev: MessageEvent) -> str:
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
        """Cancel the run in progress for ev's session (sets the agent's cancel_event). True if
        an active agent was signalled."""
        agent = self._agents.get(self._key(ev))
        if agent is not None and not agent.cancel_event.is_set():
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
        return self._sessions[key]

    def _drop_agent(self, key: str):
        agent = self._agents.pop(key, None)
        if agent is not None:
            from ..surface import _close_agent
            _close_agent(agent)
        return agent

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
        reasoning_effort = controls.get("reasoning_effort") or self.config.get("agent.reasoning_effort", "off")
        return (
            f"platform: {ev.platform}\nuser: {ev.user_id or '?'}"
            f"{f' (@{ev.user_name})' if ev.user_name else ''}\nchat: {ev.chat_id}\n"
            f"session: {key}\nprovider: {provider}\nmodel: {model}\n"
            f"busy_mode: {busy_mode}\nreasoning: display={reasoning_display} · effort={reasoning_effort}"
        )

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
        self._sessions[key] = run.session
        self.store.save(run.session)
        return run.text

    def dispatch(self, ev: MessageEvent) -> str:
        text = ev.text.strip()
        key = self._key(ev)
        # Authorization: unknown users must pair first.
        from .pairing import PairingStore
        pairing = PairingStore()
        if not pairing.is_authorized(ev.platform, ev.user_id, ev.user_name):
            code = pairing.request_code(ev.platform, ev.user_id or "?")
            return (f"⛔ Not authorized. Ask the operator to run:\n"
                    f"  aegis pairing approve {ev.platform} {code}")
        # Command tiers: admins get every command; regular users only an allowlisted
        # subset (+ the always-allowed floor). Unset admin list => everyone is admin
        # (backward-compatible single-user default).
        if text.startswith("/") and not self._command_allowed(ev, text):
            return ("⛔ That command is restricted to admins. Available to you: "
                    + ", ".join(self._user_commands()))
        # Intercept control commands before the agent.
        if text in ("/stop", "/new", "/reset"):
            def action(proxy):
                self._drop_agent(key)
                fresh = Session(id=key, title=key)
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
                lambda _proxy: (
                    f"AEGIS gateway · provider={self.config.get('model.provider')} · "
                    f"model={self.config.get('model.default')} · session={key}\n"
                    "Commands: /new · /status · /whoami · /model [provider/model] · "
                    "/provider [name] · /reasoning [mode] · /compress · /busy [mode] · "
                    "/goal <text> · /subgoal <text> · /steer <text> · stop"
                ),
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
                target_provider = provider or self.config.get("model.provider", "")
                validation = registry.validate_model_choice(target_provider, model, self.config)
                warning = registry.model_validation_message(validation)
                if not validation.get("ok", True):
                    return warning
                remember_session_runtime(
                    type("A", (), {"session": session})(),
                    provider=provider or None,
                    model=model,
                )
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
                    effort = controls.get("reasoning_effort") or self.config.get("agent.reasoning_effort", "off")
                    return f"reasoning: display={display} · effort={effort}"
                if arg in modes:
                    remember_session_runtime(type("A", (), {"session": session})(), reasoning_display=arg)
                    self.store.save(session)
                    agent = self._agents.get(key)
                    if agent is not None:
                        from ..surface import apply_session_runtime
                        apply_session_runtime(agent, rebuild_provider=False)
                    return f"✓ reasoning display → {arg}"
                if arg in efforts or arg == "off":
                    remember_session_runtime(type("A", (), {"session": session})(), reasoning_effort=arg)
                    self.store.save(session)
                    agent = self._agents.get(key)
                    if agent is not None:
                        from ..surface import apply_session_runtime
                        apply_session_runtime(agent, rebuild_provider=False)
                    return f"✓ reasoning effort → {arg}"
                return "usage: /reasoning off|summary|live|minimal|low|medium|high|xhigh"

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
            text = goals.get(session)["text"]   # fall through: run the new goal as this turn

        # Mention gating: in shared channels only respond when a trigger is present.
        if self.require_mention and self.mention_triggers and not text.startswith("/"):
            if not any(trig in text.lower() for trig in self.mention_triggers):
                return ""  # ignored — not addressed to the bot
            for trig in self.mention_triggers:
                text = text.replace(trig, "").replace(trig.title(), "").strip() or text

        # Voice memos / audio attachments -> transcribe and prepend.
        text = self._maybe_transcribe(ev, text)

        # Very first message ever -> one-shot, consent-gated profile-build offer.
        from ..firstrun import profile_build_directive
        text += profile_build_directive(self.config)

        # Serialize per session so one session isn't run concurrently (race on messages).
        with self._lock:
            lock = self._key_locks.setdefault(key, threading.Lock())
        with lock:
            with self._lock:
                # A pending /handoff from the CLI adopts that session (full history) here.
                try:
                    from ..handoff import pop_handoff
                    ho = pop_handoff(ev.platform, ev.chat_id)
                    if ho and (adopted := self.store.load(ho)) is not None:
                        self._sessions[key] = adopted
                        self._drop_agent(key)
                except Exception:  # noqa: BLE001
                    pass
                session = self._session(key)
            # Reuse a cached agent for this session (keeps the provider object warm so the
            # model's prompt prefix stays cached); rebuild if the session was reset.
            agent = self._agents.get(key)
            if agent is None or agent.session is not session:
                prof = (self.config.get("gateway.profiles", {}) or {}).get(ev.platform, {}) or {}
                run_cfg = self.config
                if prof.get("personality"):       # isolated copy — must not leak across platforms
                    import copy
                    run_cfg = type(self.config)(copy.deepcopy(self.config.data))
                    run_cfg.data.setdefault("agent", {})["personality"] = prof["personality"]
                from ..surface import apply_session_runtime, session_runtime_controls
                controls = session_runtime_controls(session)
                agent = Agent.create(run_cfg, session=session, cwd=self.cwd, store=self.store,
                                     model=controls.get("model") or prof.get("model"),
                                     provider_name=controls.get("provider") or prof.get("provider"),
                                     include_mcp=True)   # /model > profile
                apply_session_runtime(agent)
                self._agents[key] = agent
                if len(self._agents) > self._agent_cap:
                    evict_key = next(iter(self._agents))
                    self._drop_agent(evict_key)
            else:
                from ..surface import apply_session_runtime
                apply_session_runtime(agent)
            agent.platform = ev.platform   # channel-specific prompt behavior
            agent.chat_id = ev.chat_id     # current conversation (for the send_message tool)
            try:
                learned: list[str] = []

                from ..eventbus import BUS
                BUS.publish({"platform": ev.platform, "chat_id": ev.chat_id,
                             "type": "user_message", "text": text})

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
                        for a in ev_.get("actions") or []:
                            learned.append(f"🧠 {a}")

                run = self._surface_runner.run_prompt(
                    text,
                    session=session,
                    agent=agent,
                    surface="gateway",
                    meta={"platform": ev.platform, "chat_id": ev.chat_id, "user_id": ev.user_id or ""},
                    platform=ev.platform,
                    chat_id=ev.chat_id,
                    on_event=_collect,
                )
                final = run.message
                final_text = final.content or ""
                goal_notes: list[str] = []
                try:                       # standing /goal: judge + auto-continue (Ralph loop)
                    from .. import goals
                    final_text = goals.run_loop(agent, final_text, goal_notes.append, _collect)
                    if goal_notes:
                        self.store.save(session)
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
                BUS.publish({"platform": ev.platform, "chat_id": ev.chat_id,
                             "type": "assistant_message", "text": reply})
                return reply
            except Exception as e:  # noqa: BLE001
                return f"⚠ error: {type(e).__name__}: {e}"

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

    def enqueue(self, platform: str, chat_id: str, text: str) -> None:
        """Durably queue an outbound message (used by cron + retry on send failure)."""
        from .queue import DeliveryQueue
        DeliveryQueue().enqueue(platform, chat_id, text)

    def _send_via_adapter(self, platform: str, chat_id: str, text: str) -> bool:
        adapter = next((a for a in self.adapters if a.name == platform), None)
        if adapter is None:
            return False
        try:
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
        print("Gateway running. Ctrl+C to stop.")
        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            self._record_shutdown("KeyboardInterrupt")
            print("\nGateway stopped.")

    def _on_shutdown_signal(self, signum, _frame) -> None:
        import signal
        self._record_shutdown(signal.Signals(signum).name)
        raise KeyboardInterrupt

    def _record_shutdown(self, cause: str) -> None:
        """Durably log who/what triggered shutdown so 'the gateway keeps dying' is
        diagnosable after the fact. Fast + best-effort — never blocks teardown."""
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
        except Exception:  # noqa: BLE001
            pass
