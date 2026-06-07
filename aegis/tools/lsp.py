"""LSP tool: query a language server for hover, definition, references, diagnostics.

Speaks LSP JSON-RPC (Content-Length framing) over stdio to a server chosen by file
extension. Server commands are configurable under ``lsp.servers`` in config; sensible
defaults cover Python, TypeScript/JS, Go, and Rust. Degrades clearly if the server
binary isn't installed.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

from .base import Tool, ToolContext, ToolResult

DEFAULT_SERVERS = {
    ".py": "pyright-langserver --stdio",
    ".ts": "typescript-language-server --stdio",
    ".tsx": "typescript-language-server --stdio",
    ".js": "typescript-language-server --stdio",
    ".jsx": "typescript-language-server --stdio",
    ".go": "gopls",
    ".rs": "rust-analyzer",
}


class _LspClient:
    def __init__(self, cmd: list[str], root: Path):
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL)
        self.root = root
        self._id = 0
        self._diagnostics: list[dict] = []
        self._buf = b""

    def _send(self, payload: dict) -> None:
        data = json.dumps(payload).encode()
        header = f"Content-Length: {len(data)}\r\n\r\n".encode()
        self.proc.stdin.write(header + data)
        self.proc.stdin.flush()

    def _read_message(self, timeout: float = 10.0) -> dict | None:
        deadline = time.monotonic() + timeout
        while b"\r\n\r\n" not in self._buf:
            if time.monotonic() > deadline:
                return None
            chunk = self.proc.stdout.read1(65536) if hasattr(self.proc.stdout, "read1") \
                else self.proc.stdout.read(1)
            if not chunk:
                return None
            self._buf += chunk
        header, _, rest = self._buf.partition(b"\r\n\r\n")
        length = 0
        for line in header.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":")[1].strip())
        while len(rest) < length:
            chunk = self.proc.stdout.read(length - len(rest))
            if not chunk:
                break
            rest += chunk
        self._buf = rest[length:]
        try:
            return json.loads(rest[:length])
        except json.JSONDecodeError:
            return None

    def request(self, method: str, params: dict, timeout: float = 10.0) -> dict | None:
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self._read_message(timeout)
            if msg is None:
                return None
            if msg.get("method") == "textDocument/publishDiagnostics":
                self._diagnostics.append(msg["params"])
            elif msg.get("id") == rid:
                return msg.get("result")
        return None

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def initialize(self) -> None:
        self.request("initialize", {
            "processId": None, "rootUri": self.root.as_uri(),
            "capabilities": {"textDocument": {"hover": {}, "definition": {}, "references": {}}},
        })
        self.notify("initialized", {})

    def open(self, path: Path) -> str:
        uri = path.as_uri()
        text = path.read_text(encoding="utf-8", errors="replace")
        lang = {".py": "python", ".go": "go", ".rs": "rust"}.get(path.suffix, "typescript")
        self.notify("textDocument/didOpen", {"textDocument": {
            "uri": uri, "languageId": lang, "version": 1, "text": text}})
        time.sleep(1.0)  # let diagnostics arrive
        return uri

    def close(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            self.proc.kill()


class LspTool(Tool):
    name = "lsp"
    description = ("Query a language server. action: hover | definition | references | diagnostics. "
                   "Give path and (for non-diagnostics) line + character (0-based).")
    groups = ["runtime"]
    toolset = "lsp"
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["hover", "definition", "references", "diagnostics"]},
            "path": {"type": "string"},
            "line": {"type": "integer"},
            "character": {"type": "integer"},
        },
        "required": ["action", "path"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        import shutil

        path = Path(args["path"]).expanduser()
        if not path.is_absolute():
            path = ctx.cwd / path
        if not path.exists():
            return ToolResult.error(f"no such file: {path}")
        servers = {**DEFAULT_SERVERS, **(ctx.config.get("lsp.servers", {}) if ctx.config else {})}
        cmdline = servers.get(path.suffix)
        if not cmdline:
            return ToolResult.error(f"no LSP server configured for {path.suffix}")
        if not shutil.which(cmdline.split()[0]):
            return ToolResult.error(f"language server '{cmdline.split()[0]}' not installed")
        client = None
        try:
            client = _LspClient(cmdline.split(), ctx.cwd)
            client.initialize()
            uri = client.open(path)
            pos = {"line": int(args.get("line", 0)), "character": int(args.get("character", 0))}
            doc = {"textDocument": {"uri": uri}, "position": pos}
            if args["action"] == "hover":
                r = client.request("textDocument/hover", doc)
                content = (r or {}).get("contents")
                text = content.get("value") if isinstance(content, dict) else str(content)
                return ToolResult.ok(text or "(no hover info)", display="lsp hover")
            if args["action"] == "definition":
                r = client.request("textDocument/definition", doc)
                return ToolResult.ok(json.dumps(r, indent=2) if r else "(no definition)", display="lsp definition")
            if args["action"] == "references":
                doc["context"] = {"includeDeclaration": True}
                r = client.request("textDocument/references", doc)
                return ToolResult.ok(json.dumps(r, indent=2) if r else "(no references)", display="lsp references")
            # diagnostics
            diags = [d for params in client._diagnostics for d in params.get("diagnostics", [])]
            if not diags:
                return ToolResult.ok("(no diagnostics)", display="lsp diagnostics")
            out = [f"L{d['range']['start']['line']+1}: [{d.get('severity','?')}] {d.get('message','')}"
                   for d in diags]
            return ToolResult.ok("\n".join(out), display=f"lsp {len(diags)} diagnostic(s)")
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"lsp error: {e}")
        finally:
            if client:
                client.close()


def lsp_tools() -> list[Tool]:
    return [LspTool()]
