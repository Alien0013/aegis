"""Additional built-in tools: patch, download,
generic HTTP, and agent-callable task scheduling.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..util import truncate
from .base import Tool, ToolContext, ToolResult


_PATCH_PREFIXES = ("a/", "b/")


def _strip_patch_path(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if raw == "/dev/null":
        return ""
    if raw.startswith('"'):
        try:
            parts = shlex.split(raw)
        except ValueError:
            parts = []
        if parts:
            raw = parts[0]
    elif "\t" in raw:
        raw = raw.split("\t", 1)[0].strip()
    if raw == "/dev/null":
        return ""
    for prefix in _PATCH_PREFIXES:
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    return raw.strip()


def extract_patch_paths(patch: str) -> list[str]:
    """Return cwd-relative paths touched by a unified/git patch."""
    paths: list[str] = []
    seen: set[str] = set()
    pending_old: str | None = None

    def add(raw: str) -> None:
        path = _strip_patch_path(raw)
        if path and path not in seen:
            seen.add(path)
            paths.append(path)

    for line in patch.splitlines():
        if line.startswith("diff --git "):
            try:
                parts = shlex.split(line)
            except ValueError:
                parts = line.split()
            if len(parts) >= 4:
                add(parts[-2])
                add(parts[-1])
            pending_old = None
            continue
        if line.startswith("rename from "):
            add(line[len("rename from "):])
            continue
        if line.startswith("rename to "):
            add(line[len("rename to "):])
            continue
        if line.startswith("--- "):
            pending_old = _strip_patch_path(line[4:])
            continue
        if line.startswith("+++ ") and pending_old is not None:
            add(pending_old)
            add(line[4:])
            pending_old = None
    return paths


def _resolve_patch_target(raw: str, cwd: Path) -> tuple[Path | None, str]:
    if not raw or "\x00" in raw:
        return None, "patch contains an empty or invalid file path"
    path = Path(raw)
    if path.is_absolute():
        return None, f"patch path must be relative to the working directory: {raw}"
    if any(part == ".." for part in path.parts):
        return None, f"patch path contains '..' traversal: {raw}"
    target = (cwd / path).resolve(strict=False)
    try:
        target.relative_to(cwd.resolve(strict=False))
    except ValueError:
        return None, f"patch path escapes the working directory: {raw}"
    return target, ""


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
        raw_paths = extract_patch_paths(patch)
        if not raw_paths:
            return ToolResult.error("patch does not name any target files")

        from . import file_safety, file_state

        targets: list[Path] = []
        seen_targets: set[str] = set()
        for raw in raw_paths:
            target, err = _resolve_patch_target(raw, ctx.cwd)
            if err:
                return ToolResult.error(err)
            assert target is not None
            key = os.path.realpath(str(target))
            if key in seen_targets:
                continue
            denied = file_safety.authorize_write(target, ctx)
            if denied:
                return ToolResult.error(denied)
            seen_targets.add(key)
            targets.append(target)

        with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8") as f:
            f.write(patch)
            tmp = f.name
        commands = (
            (["git", "apply", "--check", "--whitespace=nowarn", tmp],
             ["git", "apply", "--whitespace=nowarn", tmp],
             "git apply"),
            (["git", "apply", "-p0", "--check", "--whitespace=nowarn", tmp],
             ["git", "apply", "-p0", "--whitespace=nowarn", tmp],
             "git apply -p0"),
            (["patch", "--dry-run", "-p1", "-i", tmp],
             ["patch", "-p1", "-i", tmp],
             "patch -p1"),
        )
        try:
            with ExitStack() as stack:
                for target in sorted(targets, key=lambda p: os.path.realpath(str(p))):
                    stack.enter_context(file_state.lock_path(target))
                stale = "".join(file_state.stale_warning(target) for target in targets)
                from .builtin import _lsp_delta, _lsp_snapshot
                for target in targets:
                    _lsp_snapshot(ctx, target)

                for check_cmd, apply_cmd, label in commands:
                    try:
                        check = subprocess.run(check_cmd, cwd=str(ctx.cwd), capture_output=True,
                                               text=True, timeout=60)
                    except FileNotFoundError:
                        continue
                    if check.returncode != 0:
                        continue
                    applied = subprocess.run(apply_cmd, cwd=str(ctx.cwd), capture_output=True,
                                             text=True, timeout=60)
                    if applied.returncode == 0:
                        for target in targets:
                            file_state.note(target)
                        deltas = "".join(_lsp_delta(ctx, target) for target in targets)
                        count = len(targets)
                        return ToolResult.ok(
                            f"applied patch with `{label}` ({count} file{'s' if count != 1 else ''})"
                            + deltas + stale,
                            display="patch applied",
                            data={"files_modified": [str(p) for p in targets]},
                        )
        finally:
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
        from .. import net_safety
        blocked = net_safety.guard(url, getattr(ctx, "config", None))
        if blocked:
            return ToolResult.error(blocked)
        dest = Path(args["path"]) if args.get("path") else \
            ctx.cwd / (Path(urlparse(url).path).name or "download.bin")
        if not dest.is_absolute():
            dest = ctx.cwd / dest
        from . import file_safety
        denied = file_safety.authorize_write(dest, ctx)
        if denied:
            return ToolResult.error(denied)
        try:
            r = net_safety.request("GET", url, getattr(ctx, "config", None), timeout=120)
            r.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.content)
        except net_safety.BlockedURL as e:
            return ToolResult.error(str(e))
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
        from .. import net_safety
        blocked = net_safety.guard(args["url"], getattr(ctx, "config", None))
        if blocked:
            return ToolResult.error(blocked)
        try:
            r = net_safety.request(args.get("method", "GET"), args["url"],
                                   getattr(ctx, "config", None), timeout=60,
                                   headers=args.get("headers"), content=args.get("body"))
        except net_safety.BlockedURL as e:
            return ToolResult.error(str(e))
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
            "deliver": {"type": "string",
                        "description": "optional comma-separated platform:chat_id delivery targets"},
        },
        "required": ["schedule", "prompt"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        from ..cron import CronStore, _scan_cron_prompt
        agent = getattr(ctx, "agent", None)
        deliver = (args.get("deliver") or "").strip()
        if not deliver:
            platform = getattr(agent, "platform", None)
            chat_id = getattr(agent, "chat_id", None)
            if platform and chat_id:
                deliver = f"{platform}:{chat_id}"
        prompt_error = _scan_cron_prompt(str(args.get("prompt") or ""))
        if prompt_error:
            return ToolResult.error(prompt_error)
        job = CronStore().add(args["schedule"], args["prompt"], deliver=deliver)
        target = f" -> {deliver}" if deliver else ""
        return ToolResult.ok(f"scheduled {job.id} [{job.schedule}]{target} "
                             "(runs when `aegis cron install` service or `aegis gateway` is active)",
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
            "thread_id": {"type": "string", "description": "target thread/topic id (default: current thread)"},
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
        explicit_chat_id = args.get("chat_id")
        chat_id = explicit_chat_id or getattr(agent, "chat_id", None)
        thread_id = args.get("thread_id")
        if thread_id is None and not explicit_chat_id:
            thread_id = getattr(agent, "thread_id", None)
        if not platform or not chat_id:
            return ToolResult.error(
                "send_message needs an active channel + conversation. It works inside the "
                "gateway (`aegis gateway`); otherwise pass both platform and chat_id.")
        from ..gateway.queue import DeliveryQueue
        from ..redact import redact_secrets
        DeliveryQueue().enqueue(str(platform), str(chat_id), redact_secrets(text), thread_id=thread_id)
        target = f"{platform}:{chat_id}" + (f" thread:{thread_id}" if thread_id else "")
        return ToolResult.ok(f"queued message to {target}", display="message queued")


def extra_tools() -> list[Tool]:
    from .cronjob_tool import CronJobTool

    return [ApplyPatchTool(), DownloadTool(), HttpRequestTool(), ScheduleTaskTool(),
            CronJobTool(), DependencyAuditTool(), ClarifyTool(), SendMessageTool()]
