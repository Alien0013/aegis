"""Hermes-compatible action-style cron job management tool."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from ..cron import CronJob, CronStore, _interval_seconds, _scan_cron_prompt, is_due, run_job
from .base import Tool, ToolContext, ToolResult


class CronJobTool(Tool):
    name = "cronjob"
    description = (
        "Manage scheduled cron jobs. Use action=create/list/update/delete/run/status/service. "
        "Jobs run when `aegis cron install` service or `aegis gateway` is active."
    )
    groups = ["automation"]
    toolset = "core"
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "create", "list", "update", "delete", "remove",
                    "pause", "resume", "run", "status", "service",
                ],
            },
            "job_id": {"type": "string", "description": "Required for update/delete/run/job status"},
            "schedule": {
                "type": "string",
                "description": "Interval ('30m', 'every 2h'), one-shot ('in 30m', ISO timestamp), or 5-field cron.",
            },
            "prompt": {"type": "string", "description": "Self-contained task instructions"},
            "name": {"type": "string", "description": "Optional human-friendly label"},
            "deliver": {
                "type": "string",
                "description": (
                    "Optional comma-separated platform:chat_id targets. Omit or use 'origin' "
                    "to deliver back to the current chat when available; use 'local' to clear delivery."
                ),
            },
            "script": {"type": "string", "description": "Optional Python script path to prepend output as context"},
            "no_agent": {"type": "boolean", "description": "Run script-only and deliver stdout without an agent"},
            "skills": {"type": "array", "items": {"type": "string"}},
            "skill": {"type": "string", "description": "Compatibility alias for one skill"},
            "enabled": {"type": "boolean", "description": "Set enabled/paused state on update"},
            "max_runs": {"type": "integer", "description": "Retire (disable) a recurring job after this many runs (0 = unlimited)"},
            "include_disabled": {"type": "boolean", "description": "Include paused jobs in list (default true)"},
            "service_action": {
                "type": "string",
                "enum": ["status", "install", "start", "stop", "restart", "uninstall", "remove"],
                "description": "Used with action=service",
            },
            "enable_now": {"type": "boolean", "description": "Start the cron service after install (default true)"},
        },
        "required": ["action"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        action = str(args.get("action") or "").strip().lower()
        store = CronStore()

        if action == "create":
            return self._create(args, ctx, store)
        if action == "list":
            return self._list(args, store)
        if action == "update":
            return self._update(args, ctx, store)
        if action in {"delete", "remove"}:
            return self._delete(args, store)
        if action == "pause":
            return self._set_enabled(args, store, False)
        if action == "resume":
            return self._set_enabled(args, store, True)
        if action == "run":
            return self._run(args, ctx, store)
        if action == "status":
            return self._status(args, store)
        if action == "service":
            return self._service(args, ctx)
        return _error(f"unknown cronjob action: {action or '(missing)'}")

    def _create(self, args: dict[str, Any], ctx: ToolContext, store: CronStore) -> ToolResult:
        schedule = str(args.get("schedule") or "").strip()
        prompt = str(args.get("prompt") or "").strip()
        skills = _canonical_skills(args.get("skill"), args.get("skills"))
        if not schedule:
            return _error("schedule is required for create")
        if not prompt and not skills:
            return _error("create requires prompt or skills")
        prompt_error = _scan_cron_prompt(prompt)
        if prompt_error:
            return _error(prompt_error)
        deliver, deliver_error = _normalize_deliver(
            args.get("deliver") if "deliver" in args else None,
            ctx,
            default_origin=True,
        )
        if deliver_error:
            return _error(deliver_error)
        job = store.add(
            schedule=schedule,
            prompt=prompt,
            deliver=deliver or "",
            script=str(args.get("script") or "").strip(),
            skills=skills,
            name=str(args.get("name") or "").strip(),
            no_agent=bool(args.get("no_agent", False)),
            max_runs=int(args.get("max_runs", 0) or 0),
        )
        data = {
            "success": True,
            "job_id": job.id,
            "id": job.id,
            "name": _job_name(job),
            "schedule": job.schedule,
            "deliver": job.deliver or "local",
            "next_run_at": _next_run_epoch(job),
            "job": _format_job(job),
            "message": f"Cron job '{_job_name(job)}' created.",
        }
        return _json(data, f"created {job.id}")

    def _list(self, args: dict[str, Any], store: CronStore) -> ToolResult:
        include_disabled = bool(args.get("include_disabled", True))
        jobs = [_format_job(job) for job in store.list() if include_disabled or job.enabled]
        return _json({"success": True, "count": len(jobs), "jobs": jobs}, f"{len(jobs)} cron job(s)")

    def _update(self, args: dict[str, Any], ctx: ToolContext, store: CronStore) -> ToolResult:
        resolved = _resolve_job(store, str(args.get("job_id") or ""))
        if isinstance(resolved, ToolResult):
            return resolved
        updates: dict[str, Any] = {}
        for key in ("schedule", "prompt", "name", "script"):
            if key in args and args.get(key) is not None:
                updates[key] = str(args.get(key) or "").strip()
        if "prompt" in updates:
            prompt_error = _scan_cron_prompt(updates["prompt"])
            if prompt_error:
                return _error(prompt_error)
        if "enabled" in args and args.get("enabled") is not None:
            updates["enabled"] = bool(args.get("enabled"))
        if "no_agent" in args and args.get("no_agent") is not None:
            updates["no_agent"] = bool(args.get("no_agent"))
        if "deliver" in args and args.get("deliver") is not None:
            deliver, deliver_error = _normalize_deliver(args.get("deliver"), ctx, default_origin=False)
            if deliver_error:
                return _error(deliver_error)
            updates["deliver"] = deliver or ""
        if "skill" in args or "skills" in args:
            updates["skills"] = _canonical_skills(args.get("skill"), args.get("skills"))
        if not updates:
            return _error("No updates provided.")
        job = store.update(resolved.id, **updates)
        if job is None:
            return _error(f"Job with ID or name '{args.get('job_id')}' not found.")
        return _json({"success": True, "job": _format_job(job)}, f"updated {job.id}")

    def _delete(self, args: dict[str, Any], store: CronStore) -> ToolResult:
        resolved = _resolve_job(store, str(args.get("job_id") or ""))
        if isinstance(resolved, ToolResult):
            return resolved
        removed = store.remove(resolved.id)
        if not removed:
            return _error(f"Failed to remove job '{resolved.id}'")
        data = {
            "success": True,
            "message": f"Cron job '{_job_name(resolved)}' removed.",
            "removed_job": {
                "id": resolved.id,
                "job_id": resolved.id,
                "name": _job_name(resolved),
                "schedule": resolved.schedule,
            },
        }
        return _json(data, f"removed {resolved.id}")

    def _set_enabled(self, args: dict[str, Any], store: CronStore, enabled: bool) -> ToolResult:
        resolved = _resolve_job(store, str(args.get("job_id") or ""))
        if isinstance(resolved, ToolResult):
            return resolved
        store.set_enabled(resolved.id, enabled)
        job = store.get(resolved.id) or resolved
        return _json({"success": True, "job": _format_job(job)}, ("resumed" if enabled else "paused"))

    def _run(self, args: dict[str, Any], ctx: ToolContext, store: CronStore) -> ToolResult:
        resolved = _resolve_job(store, str(args.get("job_id") or ""))
        if isinstance(resolved, ToolResult):
            return resolved
        from ..config import Config

        result = run_job(ctx.config or Config.load(), resolved, store=store, sink=None, verbose=False)
        data = {"success": bool(result.get("ok")), "result": result}
        if result.get("ok"):
            return _json(data, f"ran {resolved.id}")
        return _json(data, f"run failed {resolved.id}", is_error=True)

    def _status(self, args: dict[str, Any], store: CronStore) -> ToolResult:
        job_ref = str(args.get("job_id") or "").strip()
        if job_ref:
            resolved = _resolve_job(store, job_ref)
            if isinstance(resolved, ToolResult):
                return resolved
            return _json({"success": True, "job": _format_job(resolved)}, f"cron {resolved.id}")

        jobs = store.list()
        now = time.time()
        service_status = ""
        try:
            from ..daemon import cron_service_status

            service_status = cron_service_status()
        except Exception as e:  # noqa: BLE001
            service_status = f"unavailable: {e}"
        data = {
            "success": True,
            "service": {"name": "aegis-cron.service", "status": service_status},
            "jobs": {
                "total": len(jobs),
                "enabled": sum(1 for job in jobs if job.enabled),
                "disabled": sum(1 for job in jobs if not job.enabled),
                "due": sum(1 for job in jobs if is_due(job, now)),
            },
        }
        return _json(data, "cron status")

    def _service(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        operation = str(
            args.get("service_action") or args.get("operation") or args.get("command") or "status"
        ).strip().lower()
        from ..config import Config
        from ..daemon import (
            control_cron_service,
            cron_service_status,
            install_cron_service,
            remove_cron_service,
        )

        if operation == "status":
            data = {
                "success": True,
                "service": "aegis-cron.service",
                "status": cron_service_status(),
            }
            return _json(data, "cron service status")
        if operation == "install":
            res = install_cron_service(
                ctx.config or Config.load(),
                enable_now=bool(args.get("enable_now", True)),
            )
            return _service_result(res.ok, res.message)
        if operation in {"start", "stop", "restart"}:
            res = control_cron_service(operation)
            return _service_result(res.ok, res.message)
        if operation in {"uninstall", "remove"}:
            res = remove_cron_service()
            return _service_result(res.ok, res.message)
        return _error(f"unknown cron service action: {operation}")


def _canonical_skills(skill: Any = None, skills: Any = None) -> list[str]:
    raw = []
    if skills is None:
        raw = [skill] if skill else []
    elif isinstance(skills, str):
        raw = [skills]
    else:
        raw = list(skills)
    out: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _origin_target(ctx: ToolContext) -> str:
    agent = getattr(ctx, "agent", None)
    platform = getattr(agent, "platform", None)
    chat_id = getattr(agent, "chat_id", None)
    return f"{platform}:{chat_id}" if platform and chat_id else ""


def _normalize_deliver(raw: Any, ctx: ToolContext, *, default_origin: bool) -> tuple[str | None, str]:
    origin = _origin_target(ctx)
    if raw is None:
        return (origin if default_origin else None), ""
    if isinstance(raw, (list, tuple)):
        parts = [str(part).strip() for part in raw if str(part).strip()]
    else:
        parts = [part.strip() for part in str(raw).split(",") if part.strip()]
    if not parts:
        return "", ""
    normalized: list[str] = []
    for part in parts:
        lower = part.lower()
        if lower == "local":
            continue
        if lower == "origin":
            if origin:
                normalized.append(origin)
            continue
        if lower == "all":
            return None, "deliver='all' is not supported by AEGIS cron; use explicit platform:chat_id targets"
        if ":" not in part:
            return None, f"delivery target must be platform:chat_id, got {part!r}"
        normalized.append(part)
    deduped: list[str] = []
    for target in normalized:
        if target not in deduped:
            deduped.append(target)
    return ",".join(deduped), ""


def _resolve_job(store: CronStore, ref: str) -> CronJob | ToolResult:
    ref = ref.strip()
    if not ref:
        return _error("job_id is required")
    jobs = store.list()
    exact = [job for job in jobs if job.id == ref or _job_name(job) == ref]
    if len(exact) == 1:
        return exact[0]
    matches = exact or [job for job in jobs if job.id.startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        data = {
            "success": False,
            "error": f"Ambiguous cron job reference '{ref}'",
            "matches": [_format_job(job) for job in matches],
        }
        return _json(data, f"ambiguous {ref}", is_error=True)
    return _error(f"Job with ID or name '{ref}' not found. Use cronjob(action='list') to inspect jobs.")


def _format_job(job: CronJob) -> dict[str, Any]:
    return {
        "job_id": job.id,
        "id": job.id,
        "name": _job_name(job),
        "schedule": job.schedule,
        "prompt_preview": _preview(job.prompt),
        "prompt": job.prompt,
        "deliver": job.deliver or job.channel or "local",
        "channel": job.channel,
        "script": job.script or None,
        "no_agent": bool(job.no_agent),
        "skills": list(job.skills or []),
        "enabled": bool(job.enabled),
        "state": _display_state(job),
        "last_error": job.last_error or "",
        "run_at": job.run_at or None,
        "next_run_at": _next_run_epoch(job),
        "last_run": job.last_run or None,
        "last_run_at": _iso(job.last_run),
        "runs": list(job.runs or []),
        "due": is_due(job, time.time()),
        "kind": _schedule_kind(job),
        "repeat": "once" if job.run_at else "forever",
    }


def _job_name(job: CronJob) -> str:
    return (job.name or job.prompt[:50] or job.id).strip()


def _display_state(job: CronJob) -> str:
    if not job.enabled:
        return "paused"
    return job.state if job.state in {"running", "ok", "error"} else "scheduled"


def _preview(text: str, limit: int = 100) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "..."


def _schedule_kind(job: CronJob) -> str:
    if job.run_at:
        return "once"
    if _interval_seconds(job.schedule) is not None:
        return "interval"
    if len(job.schedule.split()) == 5:
        return "cron"
    return "unknown"


def _next_run_epoch(job: CronJob) -> float | None:
    if not job.enabled:
        return None
    if job.run_at:
        return None if job.last_run else job.run_at
    if job.next_run:
        return job.next_run
    interval = _interval_seconds(job.schedule)
    if interval is not None:
        if not job.last_run:
            return time.time()
        return max(time.time(), job.last_run + interval)
    return time.time() if is_due(job, time.time()) else None


def _iso(epoch: float) -> str | None:
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _json(data: dict[str, Any], display: str, *, is_error: bool = False) -> ToolResult:
    return ToolResult(
        content=json.dumps(data, indent=2, sort_keys=True),
        is_error=is_error,
        display=display,
        data=data,
    )


def _error(message: str) -> ToolResult:
    return _json({"success": False, "error": message}, f"error: {message[:80]}", is_error=True)


def _service_result(ok: bool, message: str) -> ToolResult:
    return _json(
        {"success": bool(ok), "service": "aegis-cron.service", "message": message},
        message or "cron service",
        is_error=not ok,
    )


def cronjob_tools() -> list[Tool]:
    return [CronJobTool()]
