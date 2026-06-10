"""LSPService: a pool of live language servers + the edit-diagnostics delta.

One client per (server id, project root), spawned on demand, kept for the life
of the process. The flow the edit tools use:

    service.snapshot(path)        # before the edit: text + current diagnostics
    ... file is written ...
    new = service.delta(path)     # only the problems the edit introduced

Stale baselines are remapped through a diff-based line-shift so unchanged
diagnostics that merely moved don't resurface as new.
"""

from __future__ import annotations

import threading
from pathlib import Path

from .client import LSPClient, file_uri
from .range_shift import build_line_shift, diag_key, shift_baseline
from .servers import ServerDef, find_server, resolve_binary
from .workspace import resolve_workspace

SEVERITY = {1: "error", 2: "warning", 3: "info", 4: "hint"}


class LSPService:
    def __init__(self, config=None):
        self.config = config
        self._clients: dict[tuple[str, str], LSPClient] = {}
        self._broken: set[str] = set()                 # server ids that failed to start
        self._baselines: dict[str, tuple[str, list[dict]]] = {}   # path -> (text, diags)
        self._lock = threading.Lock()

    # -- client pool ----------------------------------------------------------
    def _client_for(self, path: str, cwd: str | None = None,
                    block: bool = True) -> tuple[LSPClient, ServerDef] | None:
        sd = find_server(path, self.config)
        if sd is None or sd.id in self._broken:
            return None
        workspace = resolve_workspace(path, cwd)
        if workspace is None:                          # not a project -> LSP gated off
            return None
        root = sd.root(path, workspace)
        key = (sd.id, root)
        with self._lock:
            client = self._clients.get(key)
            if client is not None and client.alive:
                return client, sd
            binary = resolve_binary(sd, self.config, block=block)
            if binary is None:
                if block:                  # a background install may still land later
                    self._broken.add(sd.id)
                return None
            try:
                client = LSPClient([binary, *sd.command[1:]], root)
                if not client.initialize():
                    raise RuntimeError("initialize failed")
            except Exception:  # noqa: BLE001
                self._broken.add(sd.id)
                return None
            self._clients[key] = client
            return client, sd

    def _sync(self, path: str, cwd: str | None = None, timeout: float = 6.0,
              block: bool = True) -> tuple[LSPClient, str, list[dict]] | None:
        """Push the file's current content and wait for fresh diagnostics."""
        pair = self._client_for(path, cwd, block)
        if pair is None:
            return None
        client, sd = pair
        p = Path(path)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        uri = file_uri(path)
        client.clear_diag_event(uri)
        client.sync_doc(path, text, sd.language_for(p.suffix.lower()))
        diags = client.wait_diagnostics(uri, timeout)
        return client, uri, diags

    # -- public API -------------------------------------------------------------
    def diagnostics(self, path: str, cwd: str | None = None, timeout: float = 6.0) -> list[dict] | None:
        """Current diagnostics for the file (None = LSP not available for it)."""
        synced = self._sync(path, cwd, timeout)
        return None if synced is None else synced[2]

    def snapshot(self, path: str, cwd: str | None = None) -> None:
        """Capture pre-edit text + diagnostics; pair with :meth:`delta` after the edit."""
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        synced = self._sync(path, cwd, timeout=4.0, block=False)
        self._baselines[str(path)] = (text, synced[2] if synced else [])

    def delta(self, path: str, cwd: str | None = None, timeout: float = 6.0) -> list[dict] | None:
        """Diagnostics introduced since :meth:`snapshot` (line-shift adjusted)."""
        synced = self._sync(path, cwd, timeout, block=False)
        if synced is None:
            return None
        _, _, post = synced
        pre_text, pre_diags = self._baselines.pop(str(path), ("", []))
        try:
            post_text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            post_text = ""
        baseline = shift_baseline(pre_diags, build_line_shift(pre_text, post_text))
        seen = {diag_key(d) for d in baseline}
        return [d for d in post if diag_key(d) not in seen]

    def query(self, action: str, path: str, line: int, character: int,
              cwd: str | None = None, new_name: str | None = None):
        """hover | definition | references | rename | symbols at a position."""
        synced = self._sync(path, cwd, timeout=3.0)
        if synced is None:
            return None
        client, uri, _ = synced
        doc = {"textDocument": {"uri": uri}, "position": {"line": line, "character": character}}
        if action == "hover":
            return client.request("textDocument/hover", doc)
        if action == "definition":
            return client.request("textDocument/definition", doc)
        if action == "references":
            return client.request("textDocument/references",
                                  {**doc, "context": {"includeDeclaration": True}})
        if action == "rename":
            return client.request("textDocument/rename", {**doc, "newName": new_name or ""})
        if action == "symbols":
            return client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
        return None

    def status(self) -> dict:
        return {
            "servers": [{"id": sid, "root": root, "alive": c.alive,
                         "open_docs": len(c.diagnostics)}
                        for (sid, root), c in self._clients.items()],
            "broken": sorted(self._broken),
        }

    def restart(self) -> None:
        """Drop every client and forget failures — next query starts fresh."""
        self.shutdown()
        self._broken.clear()

    def shutdown(self) -> None:
        with self._lock:
            clients, self._clients = list(self._clients.values()), {}
        for c in clients:
            c.shutdown()


def format_diags(diags: list[dict], limit: int = 12) -> str:
    """Human/agent-readable one-liners, errors first."""
    diags = sorted(diags, key=lambda d: (d.get("severity", 9), d.get("range", {})
                                         .get("start", {}).get("line", 0)))
    lines = []
    for d in diags[:limit]:
        start = (d.get("range") or {}).get("start") or {}
        sev = SEVERITY.get(d.get("severity"), "info")
        src = f" [{d.get('source')}]" if d.get("source") else ""
        lines.append(f"L{int(start.get('line', 0)) + 1}:{int(start.get('character', 0)) + 1} "
                     f"{sev}: {d.get('message', '').strip()}{src}")
    if len(diags) > limit:
        lines.append(f"… and {len(diags) - limit} more")
    return "\n".join(lines)
