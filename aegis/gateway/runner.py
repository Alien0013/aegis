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
        if not pairing.is_authorized(ev.platform, ev.user_id):
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

        # Serialize per session so an agent isn't re-entered concurrently.
        with self._lock:
            session = self._session(key)
        agent = Agent.create(self.config, session=session, cwd=self.cwd, store=self.store)
        try:
            final = agent.run(text)
            return final.content or "(no response)"
        except Exception as e:  # noqa: BLE001
            return f"⚠ error: {type(e).__name__}: {e}"

    def run(self) -> None:
        if not self.adapters:
            raise RuntimeError("No channels configured for the gateway.")
        threads: list[threading.Thread] = []
        for adapter in self.adapters:
            t = threading.Thread(target=adapter.start, args=(self.dispatch,), daemon=True)
            t.start()
            threads.append(t)
            print(f"  ▸ channel up: {adapter.name}")
        print("Gateway running. Ctrl+C to stop.")
        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            print("\nGateway stopped.")
