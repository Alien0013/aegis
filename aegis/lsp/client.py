"""Persistent LSP client: one language-server process, JSON-RPC over stdio.

A background reader thread dispatches responses to waiting callers, captures
``textDocument/publishDiagnostics`` per document, and answers the handful of
server->client requests (configuration, capability registration) that servers
expect, so long-lived sessions don't wedge.
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path


def file_uri(path: str) -> str:
    return Path(path).resolve().as_uri()


class LSPClient:
    def __init__(self, cmd: list[str], root: str):
        self.root = str(root)
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, cwd=self.root)
        self._id = 0
        self._lock = threading.Lock()                 # writes
        self._pending: dict[int, dict] = {}           # id -> {event, result}
        self._versions: dict[str, int] = {}           # uri -> didChange version
        self.diagnostics: dict[str, list[dict]] = {}  # uri -> last published diagnostics
        self._diag_events: dict[str, threading.Event] = {}
        self.alive = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # -- wire ---------------------------------------------------------------
    def _send(self, payload: dict) -> None:
        data = json.dumps(payload).encode()
        with self._lock:
            try:
                self.proc.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
                self.proc.stdin.flush()
            except (BrokenPipeError, ValueError, OSError):
                self.alive = False

    def _read_loop(self) -> None:
        buf = b""
        stdout = self.proc.stdout
        while True:
            while b"\r\n\r\n" not in buf:
                chunk = stdout.read1(65536) if hasattr(stdout, "read1") else stdout.read(1)
                if not chunk:
                    self.alive = False
                    for p in self._pending.values():
                        p["event"].set()
                    return
                buf += chunk
            header, _, buf = buf.partition(b"\r\n\r\n")
            length = 0
            for line in header.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":")[1])
            while len(buf) < length:
                chunk = stdout.read(length - len(buf))
                if not chunk:
                    self.alive = False
                    return
                buf += chunk
            body, buf = buf[:length], buf[length:]
            try:
                self._dispatch(json.loads(body))
            except Exception:  # noqa: BLE001  (a malformed message must not kill the reader)
                continue

    def _dispatch(self, msg: dict) -> None:
        mid, method = msg.get("id"), msg.get("method")
        if method is None and mid is not None:                  # response to our request
            pending = self._pending.pop(mid, None)
            if pending is not None:
                pending["result"] = msg.get("result")
                pending["event"].set()
        elif method == "textDocument/publishDiagnostics":
            params = msg.get("params") or {}
            uri = params.get("uri", "")
            self.diagnostics[uri] = params.get("diagnostics", [])
            self._diag_events.setdefault(uri, threading.Event()).set()
        elif mid is not None:                                   # server -> client request
            result: object = None
            if method == "workspace/configuration":
                result = [{} for _ in (msg.get("params") or {}).get("items", [{}])]
            self._send({"jsonrpc": "2.0", "id": mid, "result": result})

    # -- protocol -----------------------------------------------------------
    def request(self, method: str, params: dict, timeout: float = 15.0):
        if not self.alive:
            return None
        with self._lock:
            self._id += 1
            rid = self._id
        slot = {"event": threading.Event(), "result": None}
        self._pending[rid] = slot
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        slot["event"].wait(timeout)
        self._pending.pop(rid, None)
        return slot["result"]

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def initialize(self) -> bool:
        result = self.request("initialize", {
            "processId": None,
            "rootUri": Path(self.root).as_uri(),
            "workspaceFolders": [{"uri": Path(self.root).as_uri(), "name": Path(self.root).name}],
            "capabilities": {
                "textDocument": {
                    "publishDiagnostics": {"versionSupport": True},
                    "hover": {"contentFormat": ["markdown", "plaintext"]},
                    "definition": {}, "references": {}, "rename": {},
                    "documentSymbol": {"hierarchicalDocumentSymbolSupport": False},
                    "synchronization": {"didSave": True},
                },
                "workspace": {"configuration": True, "workspaceFolders": True},
            },
        }, timeout=20.0)
        if result is None and not self.alive:
            return False
        self.notify("initialized", {})
        return True

    def sync_doc(self, path: str, text: str, language_id: str) -> str:
        """didOpen the first time, full-content didChange after. Returns the uri."""
        uri = file_uri(path)
        if uri not in self._versions:
            self._versions[uri] = 1
            self.notify("textDocument/didOpen", {"textDocument": {
                "uri": uri, "languageId": language_id, "version": 1, "text": text}})
        else:
            self._versions[uri] += 1
            self.notify("textDocument/didChange", {
                "textDocument": {"uri": uri, "version": self._versions[uri]},
                "contentChanges": [{"text": text}],
            })
        return uri

    def wait_diagnostics(self, uri: str, timeout: float = 5.0) -> list[dict]:
        """Block until the next publishDiagnostics for ``uri`` (call right after sync_doc,
        which clears the event), falling back to the last known set on timeout."""
        ev = self._diag_events.setdefault(uri, threading.Event())
        ev.wait(timeout)
        return list(self.diagnostics.get(uri, []))

    def clear_diag_event(self, uri: str) -> None:
        self._diag_events.setdefault(uri, threading.Event()).clear()

    def shutdown(self) -> None:
        try:
            self.request("shutdown", {}, timeout=3.0)
            self.notify("exit", {})
            self.proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.proc.kill()
        except Exception:  # noqa: BLE001
            pass
        self.alive = False
