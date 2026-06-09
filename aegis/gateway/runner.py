"""Gateway hub: routes channel messages to per-conversation agent sessions."""

from __future__ import annotations

import threading
from pathlib import Path

from ..agent.agent import Agent
from ..config import Config
from ..session import Session, SessionStore
from .base import BasePlatformAdapter, MessageEvent


class GatewayRunner:
    """Hub-and-spoke. Each (platform, chat[, user]) maps to one persistent session."""

    def __init__(self, config: Config, cwd: Path | None = None):
        self.config = config
        self.cwd = cwd or Path.cwd()
        self.store = SessionStore()
        self.adapters: list[BasePlatformAdapter] = []
        self._sessions: dict[str, Session] = {}
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
        # Intercept control commands before the agent.
        if text in ("/stop", "/new", "/reset"):
            with self._lock:
                self._sessions[key] = Session(id=key, title=key)
                self.store.save(self._sessions[key])
            return "🔄 Started a fresh session."
        if text in ("/status", "/help"):
            return (f"AEGIS gateway · provider={self.config.get('model.provider')} · "
                    f"model={self.config.get('model.default')} · session={key}\n"
                    f"Commands: /new (reset), /status")

        # Mention gating: in shared channels only respond when a trigger is present.
        if self.require_mention and self.mention_triggers and not text.startswith("/"):
            if not any(trig in text.lower() for trig in self.mention_triggers):
                return ""  # ignored — not addressed to the bot
            for trig in self.mention_triggers:
                text = text.replace(trig, "").replace(trig.title(), "").strip() or text

        # Voice memos / audio attachments -> transcribe and prepend.
        text = self._maybe_transcribe(ev, text)

        # Serialize per session so one session isn't run concurrently (race on messages).
        with self._lock:
            lock = self._key_locks.setdefault(key, threading.Lock())
        with lock:
            with self._lock:
                session = self._session(key)
            # Reuse a cached agent for this session (keeps the provider object warm so the
            # model's prompt prefix stays cached); rebuild if the session was reset.
            agent = self._agents.get(key)
            if agent is None or agent.session is not session:
                agent = Agent.create(self.config, session=session, cwd=self.cwd, store=self.store)
                self._agents[key] = agent
                if len(self._agents) > self._agent_cap:
                    del self._agents[next(iter(self._agents))]
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

                final = agent.run(text, _collect)
                from ..redact import redact_secrets
                from .replies import shape_reply
                api_calls = getattr(getattr(agent, "budget", None), "api_call_count", 0)
                # secrets out, raw provider errors -> friendly one-liner, empty -> clear message
                reply = shape_reply(redact_secrets(final.content or ""), api_calls=api_calls)
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
                cron.tick(self.config, sink=self._cron_sink, verbose=False)
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
        print("Gateway running. Ctrl+C to stop.")
        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            print("\nGateway stopped.")
