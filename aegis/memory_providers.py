"""Pluggable external memory backends implementing the MemoryProvider interface.

Selected via ``memory.provider`` in config: "" (builtin only), "jsonl", or "mem0".
The builtin file memory (MEMORY.md/USER.md) is always active alongside these.
"""

from __future__ import annotations

import json

from . import config as cfg
from .memory import MemoryProvider
from .util import append_line, read_text


class JSONLMemoryProvider(MemoryProvider):
    """Zero-dependency external memory: appends turn notes to ext_memory.jsonl."""

    def __init__(self, max_recent: int = 12):
        self.path = cfg.sub("ext_memory.jsonl")
        self.max_recent = max_recent

    def system_prompt_block(self) -> str:
        raw = read_text(self.path)
        if not raw.strip():
            return ""
        lines = raw.strip().splitlines()[-self.max_recent:]
        notes = []
        for ln in lines:
            try:
                note = json.loads(ln).get("note", "")
            except json.JSONDecodeError:
                continue
            if note:
                notes.append("- " + note)
        return "# Recalled context\n" + "\n".join(notes) if notes else ""

    def sync_turn(self, messages) -> None:
        # store the last user/assistant exchange as a compact note
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        last_asst = next((m.content for m in reversed(messages) if m.role == "assistant"), "")
        if last_user:
            note = f"user asked: {last_user[:160]}"
            if last_asst:
                note += f" | replied: {last_asst[:160]}"
            append_line(self.path, json.dumps({"note": note}))

    def on_memory_write(self, *, action: str, target: str, content: str = "",
                        old_text: str = "", result: str = "",
                        session_id: str = "", **kw) -> None:
        if action in {"add", "replace"} and content:
            note = f"{target} {action}: {content[:240]}"
        elif action == "remove" and old_text:
            note = f"{target} remove: {old_text[:240]}"
        else:
            note = f"{target} {action}".strip()
        append_line(self.path, json.dumps({
            "event": "memory_write",
            "action": action,
            "target": target,
            "note": note,
            "content": content,
            "old_text": old_text,
            "result": result,
            "session_id": session_id,
        }))


class Mem0Provider(MemoryProvider):
    """Vector memory via the `mem0ai` package (optional dependency)."""

    def __init__(self, user_id: str = "aegis"):
        try:
            from mem0 import Memory
        except ImportError as e:  # noqa: BLE001
            raise RuntimeError("mem0 provider needs `pip install mem0ai`") from e
        self.user_id = user_id
        self._mem = Memory()
        self._last_query = ""

    def system_prompt_block(self) -> str:
        try:
            results = self._mem.search(self._last_query or "recent context",
                                       user_id=self.user_id, limit=8)
            items = results.get("results", results) if isinstance(results, dict) else results
            mems = [r.get("memory", "") for r in (items or [])]
            return "# Long-term memory (mem0)\n" + "\n".join(f"- {m}" for m in mems if m) if mems else ""
        except Exception:  # noqa: BLE001
            return ""

    def sync_turn(self, messages) -> None:
        try:
            self._last_query = next((m.content for m in reversed(messages) if m.role == "user"), "")
            wire = [{"role": m.role, "content": m.content} for m in messages[-6:]
                    if m.role in ("user", "assistant") and m.content]
            if wire:
                self._mem.add(wire, user_id=self.user_id)
        except Exception:  # noqa: BLE001
            pass


class HonchoProvider(MemoryProvider):
    """Personal memory via Honcho (plastic-labs). Optional dep: `honcho-ai`.

    Set HONCHO_API_KEY (or HONCHO_ENVIRONMENT=demo for the public demo). Messages
    are added to a Honcho session; recall uses the dialectic `peer.chat()` endpoint.
    """

    def __init__(self, user_id: str = "user", session_id: str = "aegis"):
        try:
            from honcho import Honcho
        except ImportError as e:  # noqa: BLE001
            raise RuntimeError("honcho provider needs `pip install honcho-ai`") from e
        import os
        kwargs = {}
        if os.environ.get("HONCHO_ENVIRONMENT"):
            kwargs["environment"] = os.environ["HONCHO_ENVIRONMENT"]
        self._honcho = Honcho(**kwargs)
        self._user = self._honcho.peer(user_id)
        self._assistant = self._honcho.peer("assistant")
        self._session = self._honcho.session(session_id)
        self._last_query = ""

    def system_prompt_block(self) -> str:
        try:
            q = self._last_query or "What do you know about this user that's relevant right now?"
            resp = self._user.chat(q)
            text = resp if isinstance(resp, str) else getattr(resp, "content", "") or str(resp)
            return "# Personal memory (Honcho)\n" + text.strip() if text and text.strip() else ""
        except Exception:  # noqa: BLE001
            return ""

    def sync_turn(self, messages) -> None:
        try:
            self._last_query = next((m.content for m in reversed(messages) if m.role == "user"), "")
            batch = []
            for m in messages[-4:]:
                if m.role == "user" and m.content:
                    batch.append(self._user.message(m.content))
                elif m.role == "assistant" and m.content:
                    batch.append(self._assistant.message(m.content))
            if batch:
                self._session.add_messages(batch)
        except Exception:  # noqa: BLE001
            pass


class HTTPMemoryProvider(MemoryProvider):
    """Generic HTTP memory backend — wires any REST memory service via config.

    Used for providers without a bundled SDK (openviking, supermemory, byterover,
    hindsight, holographic, retaindb, …). Configure under ``memory.<name>``:
      add_url, search_url (POST JSON {messages}/{query}), headers, result_path.
    """

    def __init__(self, name: str, config):
        import os
        self.name = name
        node = config.get(f"memory.{name}", {}) or {}
        self.add_url = node.get("add_url")
        self.search_url = node.get("search_url")
        self.headers = node.get("headers", {}) or {}
        # allow an API key from env: <NAME>_API_KEY -> Authorization: Bearer
        key = os.environ.get(f"{name.upper()}_API_KEY")
        if key and "Authorization" not in self.headers:
            self.headers["Authorization"] = f"Bearer {key}"
        self.result_path = node.get("result_path", "results")
        self._last_query = ""

    def system_prompt_block(self) -> str:
        if not self.search_url:
            return ""
        try:
            import httpx
            r = httpx.post(self.search_url, json={"query": self._last_query or "recent context"},
                           headers=self.headers, timeout=20)
            data = r.json()
            items = data.get(self.result_path, data) if isinstance(data, dict) else data
            texts = [i.get("memory") or i.get("text") or str(i) for i in (items or [])][:8]
            return f"# Memory ({self.name})\n" + "\n".join(f"- {t}" for t in texts) if texts else ""
        except Exception:  # noqa: BLE001
            return ""

    def sync_turn(self, messages) -> None:
        if not self.add_url:
            return
        try:
            import httpx
            self._last_query = next((m.content for m in reversed(messages) if m.role == "user"), "")
            wire = [{"role": m.role, "content": m.content} for m in messages[-6:]
                    if m.role in ("user", "assistant") and m.content]
            httpx.post(self.add_url, json={"messages": wire}, headers=self.headers, timeout=20)
        except Exception:  # noqa: BLE001
            pass


# Niche providers wired via the generic HTTP backend (configure endpoints).
_HTTP_PROVIDERS = {"openviking", "supermemory", "byterover", "hindsight", "holographic", "retaindb"}


def build_memory_provider(name: str, config) -> MemoryProvider | None:
    name = (name or "").strip().lower()
    if name == "jsonl":
        return JSONLMemoryProvider()
    for key, cls in (("mem0", Mem0Provider), ("honcho", HonchoProvider)):
        if name == key:
            try:
                return cls()
            except RuntimeError as e:
                print(f"  ! {e}")
                return None
    if name in _HTTP_PROVIDERS or name == "http":
        return HTTPMemoryProvider(name, config)
    return None
