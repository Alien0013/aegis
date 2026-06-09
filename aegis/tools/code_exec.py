"""`execute_code` — run Python in a child process that can call AEGIS tools via RPC.

A "zero-context-cost turn": the model writes a short Python script that
orchestrates many tool calls; only the script's final stdout returns to the model,
not every intermediate tool result. The child reaches tools over a Unix-domain
socket (TCP fallback on Windows). Secrets are stripped from the child environment.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from ..util import truncate
from .base import Tool, ToolContext, ToolResult

MAX_STDOUT = 50_000
SECRET_HINTS = ("_API_KEY", "_TOKEN", "_SECRET", "ANTHROPIC", "OPENAI", "PASSWORD")

_BOOTSTRAP = r'''
import socket, json, os, sys
_P = os.environ["AEGIS_RPC_SOCKET"]
def _rpc(tool, args):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) if not _P.startswith("tcp:") \
        else socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(_P if not _P.startswith("tcp:") else (_P[4:].split(":")[0], int(_P.split(":")[-1])))
    s.sendall((json.dumps({"tool": tool, "args": args}) + "\n").encode())
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    resp = json.loads(buf.decode() or "{}")
    if resp.get("is_error"):
        raise RuntimeError(resp.get("content"))
    return resp.get("content", "")
def call_tool(name, **kw): return _rpc(name, kw)
def read_file(path, **kw): return _rpc("read_file", dict(path=path, **kw))
def write_file(path, content): return _rpc("write_file", dict(path=path, content=content))
def search_files(pattern, path="."): return _rpc("search", dict(pattern=pattern, path=path))
def web_search(query): return _rpc("web_search", dict(query=query))
def web_fetch(url): return _rpc("web_fetch", dict(url=url))
def bash(command): return _rpc("bash", dict(command=command))
__USER_CODE__
'''


class _RpcServer:
    def __init__(self, ctx: ToolContext, sock_path: str):
        self.ctx = ctx
        self.sock_path = sock_path
        self._stop = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.sock_path)
        self._srv.listen(8)
        self._srv.settimeout(0.3)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop:
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                self._handle(conn)

    def _handle(self, conn: socket.socket) -> None:
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                return
            buf += chunk
        try:
            req = json.loads(buf.decode())
        except json.JSONDecodeError:
            conn.sendall(b'{"is_error":true,"content":"bad request"}\n')
            return
        result = self._dispatch(req.get("tool"), req.get("args", {}))
        conn.sendall((json.dumps(result) + "\n").encode())

    def _dispatch(self, name: str, args: dict) -> dict:
        agent = self.ctx.agent
        tool = agent.registry.get(name) if agent else None
        if tool is None:
            return {"is_error": True, "content": f"unknown tool '{name}'"}
        allowed, reason = agent.permissions.authorize(tool, args, self.ctx)
        if not allowed:
            return {"is_error": True, "content": f"permission denied: {reason}"}
        try:
            res = tool.run(args, self.ctx)
            return {"is_error": res.is_error, "content": res.content}
        except Exception as e:  # noqa: BLE001
            return {"is_error": True, "content": f"{type(e).__name__}: {e}"}

    def stop(self) -> None:
        self._stop = True
        try:
            self._srv.close()
        except OSError:
            pass


class ExecuteCodeTool(Tool):
    name = "execute_code"
    description = (
        "Run a Python script in a child process to collapse many tool calls into one cheap "
        "turn. Helpers: call_tool(name, **args), read_file, write_file, search_files, "
        "web_search, web_fetch, bash. ONLY stdout is returned. NOTE: this is a context-saving "
        "device, not a security sandbox — the script can do anything the agent's tools can "
        "(set tools.terminal_backend=docker for isolation)."
    )
    groups = ["runtime"]
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python source to run."},
            "timeout": {"type": "integer", "description": "Seconds (default 120, max 300)."},
        },
        "required": ["code"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        if sys.platform == "win32":
            return ToolResult.error("execute_code requires a Unix socket (Linux/macOS only).")
        timeout = min(int(args.get("timeout", 120)), 300)
        tmpdir = tempfile.mkdtemp(prefix="aegis-exec-")
        sock_path = os.path.join(tmpdir, "rpc.sock")
        script_path = Path(tmpdir) / "script.py"
        script_path.write_text(_BOOTSTRAP.replace("__USER_CODE__", args["code"]), encoding="utf-8")

        server = _RpcServer(ctx, sock_path)
        server.start()

        env = {k: v for k, v in os.environ.items()
               if not any(h in k.upper() for h in SECRET_HINTS)}
        env["AEGIS_RPC_SOCKET"] = sock_path
        env["PATH"] = os.environ.get("PATH", "")
        try:
            proc = subprocess.run(
                [sys.executable, str(script_path)], cwd=str(ctx.cwd), env=env,
                capture_output=True, text=True, timeout=timeout,
            )
            out = proc.stdout.strip()
            if proc.stderr.strip():
                out += "\n[stderr]\n" + proc.stderr.strip()
            out = out or "(no stdout)"
            return ToolResult(content=truncate(out, MAX_STDOUT), is_error=proc.returncode != 0,
                              display=f"execute_code (exit {proc.returncode})")
        except subprocess.TimeoutExpired:
            return ToolResult.error(f"execute_code timed out after {timeout}s")
        finally:
            server.stop()


def code_tools() -> list[Tool]:
    return [ExecuteCodeTool()]
