"""Additional built-in tools: patch, download,
generic HTTP, and agent-callable task scheduling.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..util import truncate
from .base import Tool, ToolContext, ToolResult


class ApplyPatchTool(Tool):
    name = "apply_patch"
    description = "Apply a unified diff (git/patch format) to files in the working directory. Use for multi-file or multi-hunk edits."
    groups = ["fs"]
    parameters = {
        "type": "object",
        "properties": {"patch": {"type": "string", "description": "a unified diff"}},
        "required": ["patch"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        patch = args["patch"]
        if not patch.endswith("\n"):
            patch += "\n"
        with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8") as f:
            f.write(patch)
            tmp = f.name
        # try git apply at -p1 then -p0, then GNU patch
        for cmd in (["git", "apply", "--whitespace=nowarn", tmp],
                    ["git", "apply", "-p0", "--whitespace=nowarn", tmp],
                    ["patch", "-p1", "-i", tmp]):
            try:
                r = subprocess.run(cmd, cwd=str(ctx.cwd), capture_output=True, text=True, timeout=60)
            except FileNotFoundError:
                continue
            if r.returncode == 0:
                Path(tmp).unlink(missing_ok=True)
                return ToolResult.ok(f"applied patch with `{' '.join(cmd[:2])}`", display="patch applied")
        Path(tmp).unlink(missing_ok=True)
        return ToolResult.error("patch did not apply cleanly (check paths and context lines)")


class DownloadTool(Tool):
    name = "download"
    description = "Download a URL to a local file."
    groups = ["network", "fs"]
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "path": {"type": "string", "description": "destination (default: basename in cwd)"},
        },
        "required": ["url"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        url = args["url"]
        from ..net_safety import guard
        blocked = guard(url, getattr(ctx, "config", None))
        if blocked:
            return ToolResult.error(blocked)
        dest = Path(args["path"]) if args.get("path") else \
            ctx.cwd / (Path(urlparse(url).path).name or "download.bin")
        if not dest.is_absolute():
            dest = ctx.cwd / dest
        try:
            with httpx.Client(timeout=120, follow_redirects=True) as c:
                r = c.get(url)
                r.raise_for_status()
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(r.content)
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"download failed: {e}")
        return ToolResult.ok(f"downloaded {len(r.content)} bytes to {dest}", display=f"↓ {dest.name}")


class HttpRequestTool(Tool):
    name = "http_request"
    description = "Make an arbitrary HTTP request (GET/POST/PUT/DELETE) and return status + body. For APIs."
    groups = ["network"]
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
            "headers": {"type": "object"},
            "body": {"type": "string", "description": "raw request body (e.g. JSON string)"},
        },
        "required": ["url"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        from ..net_safety import guard
        blocked = guard(args["url"], getattr(ctx, "config", None))
        if blocked:
            return ToolResult.error(blocked)
        try:
            with httpx.Client(timeout=60, follow_redirects=True) as c:
                r = c.request(args.get("method", "GET"), args["url"],
                              headers=args.get("headers"), content=args.get("body"))
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"request failed: {e}")
        return ToolResult(
            content=f"HTTP {r.status_code}\n{truncate(r.text, 20_000)}",
            is_error=r.status_code >= 400,
            display=f"{args.get('method', 'GET')} {r.status_code}",
        )


class ScheduleTaskTool(Tool):
    name = "schedule_task"
    description = "Schedule a recurring task. schedule: interval ('30m','2h','@daily') or 5-field cron. Runs when the scheduler/gateway is active."
    groups = ["automation"]
    parameters = {
        "type": "object",
        "properties": {
            "schedule": {"type": "string"},
            "prompt": {"type": "string", "description": "instructions to run on schedule"},
        },
        "required": ["schedule", "prompt"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        from ..cron import CronStore
        job = CronStore().add(args["schedule"], args["prompt"])
        return ToolResult.ok(f"scheduled {job.id} [{job.schedule}] (run `aegis cron run` to activate)",
                             display=f"scheduled {job.id}")


def _collect_packages(path: str | None, cwd) -> list[tuple[str, str]]:
    """(name, version) pairs from a requirements file, or the installed environment."""
    if path:
        p = Path(path)
        if not p.is_absolute():
            p = Path(cwd) / path
        out: list[tuple[str, str]] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if "==" in line:
                name, ver = line.split("==", 1)
                out.append((name.strip(), ver.strip().split()[0]))
        return out
    import importlib.metadata as md
    seen: dict[str, str] = {}
    for dist in md.distributions():
        name = (dist.metadata["Name"] if dist.metadata else None) or ""
        if name and dist.version:
            seen[name] = dist.version
    return sorted(seen.items())


def _osv_querybatch(pkgs: list[tuple[str, str]]) -> list[dict]:
    """Query the OSV.dev batch API for PyPI advisories. Split into chunks of 250."""
    results: list[dict] = []
    for i in range(0, len(pkgs), 250):
        chunk = pkgs[i:i + 250]
        body = {"queries": [{"package": {"name": n, "ecosystem": "PyPI"}, "version": v}
                            for n, v in chunk]}
        r = httpx.post("https://api.osv.dev/v1/querybatch", json=body, timeout=30)
        r.raise_for_status()
        results.extend(r.json().get("results", []))
    return results


class DependencyAuditTool(Tool):
    name = "dependency_audit"
    description = ("Scan installed Python packages (or a requirements.txt) for known "
                   "vulnerabilities via the OSV.dev database. Reports each affected package "
                   "with its advisory IDs (CVE/GHSA). Use before shipping or when asked about "
                   "supply-chain risk.")
    groups = ["network"]
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string",
                     "description": "optional requirements.txt to scan instead of the installed env"},
            "max": {"type": "integer", "description": "cap on packages to query (default 300)"},
        },
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        try:
            pkgs = _collect_packages(args.get("path"), ctx.cwd)
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"could not read packages: {e}")
        if not pkgs:
            return ToolResult.error("no packages found to audit")
        pkgs = pkgs[: int(args.get("max", 300) or 300)]
        try:
            results = _osv_querybatch(pkgs)
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"OSV query failed: {e}")
        vulnerable = []
        for (name, ver), res in zip(pkgs, results, strict=False):
            ids = [v.get("id") for v in (res or {}).get("vulns", []) if v.get("id")]
            if ids:
                vulnerable.append((name, ver, ids))
        if not vulnerable:
            return ToolResult.ok(f"✓ no known vulnerabilities across {len(pkgs)} package(s)",
                                 display="0 vulnerabilities")
        lines = [f"⚠ {len(vulnerable)} vulnerable package(s) of {len(pkgs)} scanned:"]
        for name, ver, ids in vulnerable:
            lines.append(f"  {name} {ver} — {', '.join(ids[:6])}")
        return ToolResult(truncate("\n".join(lines), 4000), data={"vulnerable": vulnerable},
                          display=f"{len(vulnerable)} vulnerable package(s)")


class ClarifyTool(Tool):
    name = "clarify"
    description = ("Ask the user a clarifying question instead of guessing when the request is "
                   "ambiguous or you need a decision. Provide up to 4 concise choices, or omit "
                   "them for a free-text question. Prefer this over assuming on consequential "
                   "or irreversible actions.")
    groups = []   # safe: it only asks
    parameters = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "the question to ask"},
            "choices": {"type": "array", "items": {"type": "string"},
                        "description": "optional answer options (max 4)"},
        },
        "required": ["question"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        question = (args.get("question") or "").strip()
        if not question:
            return ToolResult.error("clarify needs a 'question'")
        choices = [str(c) for c in (args.get("choices") or [])][:4]
        asker = getattr(ctx, "asker", None)
        if callable(asker):
            try:
                answer = asker(question, choices)
                if answer:
                    return ToolResult.ok(f"User answered: {answer}", display="got answer")
            except Exception:  # noqa: BLE001
                pass
        # No interactive surface (gateway/API/headless): surface the question and wait.
        rendered = question + ("".join(f"\n  {i}. {c}" for i, c in enumerate(choices, 1)) if choices else "")
        return ToolResult.ok(
            f"Asked the user and am waiting for their reply:\n{rendered}\n"
            "Stop here and let the user respond; their next message is the answer.",
            display="awaiting user reply")


class SendMessageTool(Tool):
    name = "send_message"
    description = (
        "Proactively send a message to the user (or another conversation) on a messaging "
        "channel like Telegram or Discord. Use it to follow up, notify, or deliver a result "
        "out-of-band — e.g. from a scheduled/cron task ('remind me at 5pm'). Defaults to the "
        "current conversation; only works while the gateway is running."
    )
    groups = ["network"]
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "the message to send"},
            "chat_id": {"type": "string", "description": "target conversation id (default: current)"},
            "platform": {"type": "string",
                         "description": "channel name, e.g. telegram, discord (default: current)"},
        },
        "required": ["text"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        text = (args.get("text") or "").strip()
        if not text:
            return ToolResult.error("send_message: 'text' is required.")
        agent = ctx.agent
        platform = args.get("platform") or getattr(agent, "platform", None)
        chat_id = args.get("chat_id") or getattr(agent, "chat_id", None)
        if not platform or not chat_id:
            return ToolResult.error(
                "send_message needs an active channel + conversation. It works inside the "
                "gateway (`aegis gateway`); otherwise pass both platform and chat_id.")
        from ..gateway.queue import DeliveryQueue
        from ..redact import redact_secrets
        DeliveryQueue().enqueue(str(platform), str(chat_id), redact_secrets(text))
        return ToolResult.ok(f"queued message to {platform}:{chat_id}", display="message queued")


def extra_tools() -> list[Tool]:
    return [ApplyPatchTool(), DownloadTool(), HttpRequestTool(), ScheduleTaskTool(),
            DependencyAuditTool(), ClarifyTool(), SendMessageTool()]
