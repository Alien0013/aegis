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
        self.group_per_user = bool(config.get("gateway.group_sessions_per_user", True))

    def add(self, adapter: BasePlatformAdapter) -> None:
        self.adapters.append(adapter)

    def _key(self, ev: MessageEvent) -> str:
        key = f"{ev.platform}:{ev.chat_id}"
        if self.group_per_user and ev.user_id:
            key += f":{ev.user_id}"
        return key

    def _session(self, key: str) -> Session:
        if key not in self._sessions:
            self._sessions[key] = self.store.load(key) or Session(id=key, title=key)
        return self._sessions[key]

    def dispatch(self, ev: MessageEvent) -> str:
        text = ev.text.strip()
        key = self._key(ev)
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

        # Serialize per session so an agent isn't re-entered concurrently.
        with self._lock:
            session = self._session(key)
        agent = Agent.create(self.config, session=session, cwd=self.cwd, store=self.store)
        try:
            final = agent.run(ev.text)
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
