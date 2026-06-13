"""Scheduled tasks: interval shorthand + basic 5-field cron, JSON-backed."""

from __future__ import annotations

import json
import re
import time
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import config as cfg
from .types import new_id
from .util import atomic_write, read_text

_TICK_LOCAL = threading.local()


def _cron_path():
    return cfg.sub("cron.json")


@contextmanager
def _tick_lock():
    """Best-effort, non-blocking cross-process cron tick lock.

    If a cron job recursively invokes the scheduler, or the gateway ticker and
    systemd scheduler overlap, the second tick skips instead of waiting.
    """
    try:
        import fcntl
        import os
    except ImportError:                       # pragma: no cover - non-Unix
        yield True
        return
    path = _cron_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path) + ".tick.lock", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


@dataclass
class CronJob:
    id: str
    schedule: str
    prompt: str
    name: str = ""             # optional human-friendly label
    channel: str = ""          # optional "telegram:<chat_id>" sink (single target; back-compat)
    last_run: float = 0.0
    enabled: bool = True
    run_at: float = 0.0        # >0 marks a one-shot job: fire once at this epoch, then disable
    script: str = ""           # path to a Python file run first; its stdout is prepended as context
    skills: list[str] = field(default_factory=list)   # skills to load before running
    deliver: str = ""          # comma-sep "platform:chat_id" targets; supersedes ``channel`` when set
    no_agent: bool = False     # script-only: run the script and deliver its stdout, no LLM turn
    state: str = "idle"        # idle | running | ok | error
    last_error: str = ""       # last failure message ("" when healthy)
    next_run: float = 0.0      # epoch of the next expected fire (advisory; computed on each run)
    runs: list = field(default_factory=list)          # recent run records (capped), newest last


_INTERVAL_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _interval_seconds(schedule: str) -> int | None:
    s = schedule.strip().lower()
    aliases = {"@hourly": "1h", "@daily": "1d", "hourly": "1h", "daily": "1d", "@minutely": "1m"}
    s = aliases.get(s, s)
    if s.startswith("every "):
        s = s[6:]
    if s and s[-1] in _INTERVAL_UNITS and s[:-1].isdigit():
        return int(s[:-1]) * _INTERVAL_UNITS[s[-1]]
    return None


def _parse_oneshot(schedule: str, now: float) -> float | None:
    """Resolve a one-shot schedule to a target epoch, else None. Forms:
    'in 2h' / 'in 30m', 'at 17:00' (next occurrence), or an ISO datetime '2026-06-10T17:00'."""
    s = schedule.strip().lower()
    if s.startswith("in "):                          # relative delay
        d = s[3:].strip()
        if d and d[-1] in _INTERVAL_UNITS and d[:-1].isdigit():
            return now + int(d[:-1]) * _INTERVAL_UNITS[d[-1]]
        return None
    m = re.match(r"^at (\d{1,2}):(\d{2})$", s)        # clock time -> next today/tomorrow
    if m:
        base = datetime.fromtimestamp(now)
        target = base.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        if target.timestamp() <= now:
            target += timedelta(days=1)
        return target.timestamp()
    if re.match(r"^\d{4}-\d{2}-\d{2}", schedule.strip()):  # ISO datetime
        try:
            return datetime.fromisoformat(schedule.strip().replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _cron_field_matches(field: str, value: int) -> bool:
    if field == "*":
        return True
    for part in field.split(","):
        if part.startswith("*/"):
            if value % int(part[2:]) == 0:
                return True
        elif "-" in part:
            lo, hi = part.split("-")
            if int(lo) <= value <= int(hi):
                return True
        elif part.isdigit() and int(part) == value:
            return True
    return False


def _cron_matches(schedule: str, when: float) -> bool:
    fields = schedule.split()
    if len(fields) != 5:
        return False
    dt = datetime.fromtimestamp(when)
    minute, hour, dom, mon, dow = fields
    return (_cron_field_matches(minute, dt.minute) and _cron_field_matches(hour, dt.hour)
            and _cron_field_matches(dom, dt.day) and _cron_field_matches(mon, dt.month)
            and _cron_field_matches(dow, dt.weekday()))


def _next_cron_time(schedule: str, after: float) -> float:
    """Return the next matching minute for a 5-field cron schedule, or 0 if invalid."""
    if len(schedule.split()) != 5:
        return 0.0
    start = int(after // 60) * 60 + 60
    for offset in range(366 * 24 * 60):
        candidate = start + offset * 60
        try:
            if _cron_matches(schedule, candidate):
                return float(candidate)
        except (ValueError, ZeroDivisionError):
            return 0.0
    return 0.0


def _compute_next_run(job: CronJob, now: float) -> float:
    if not job.enabled:
        return 0.0
    if job.run_at:
        return 0.0 if job.last_run else job.run_at
    interval = _interval_seconds(job.schedule)
    if interval is not None:
        return max(now, (job.last_run or now) + interval)
    return _next_cron_time(job.schedule, now)


def is_due(job: CronJob, now: float) -> bool:
    if not job.enabled:
        return False
    if job.run_at:                                   # one-shot: fire once when its time arrives
        return job.last_run == 0.0 and now >= job.run_at
    if job.next_run and now >= job.next_run:          # missed-run recovery
        return True
    interval = _interval_seconds(job.schedule)
    if interval is not None:
        return (now - job.last_run) >= interval
    # 5-field cron: minute hour day-of-month month day-of-week
    fields = job.schedule.split()
    if len(fields) != 5:
        return False
    if (now - job.last_run) < 60:   # avoid double firing within a minute
        return False
    return _cron_matches(job.schedule, now)


class CronStore:
    def _load(self) -> list[dict]:
        raw = read_text(_cron_path())
        return json.loads(raw) if raw.strip() else []

    def _save(self, jobs: list[dict]) -> None:
        atomic_write(_cron_path(), json.dumps(jobs, indent=2))

    def list(self) -> list[CronJob]:
        return [CronJob(**j) for j in self._load()]

    def get(self, job_id: str) -> CronJob | None:
        for job in self.list():
            if job.id == job_id or job.id.startswith(job_id):
                return job
        return None

    def add(self, schedule: str, prompt: str, channel: str = "", script: str = "",
            skills: list[str] | None = None, deliver: str = "", name: str = "",
            no_agent: bool = False) -> CronJob:
        run_at = _parse_oneshot(schedule, time.time()) or 0.0
        job = CronJob(id=new_id("cron"), schedule=schedule, prompt=prompt, name=name, channel=channel,
                      run_at=run_at, script=script, skills=skills or [], deliver=deliver,
                      no_agent=no_agent)
        jobs = self._load()
        jobs.append(job.__dict__)
        self._save(jobs)
        return job

    def remove(self, job_id: str) -> bool:
        jobs = self._load()
        kept = [j for j in jobs if not j["id"].startswith(job_id)]
        self._save(kept)
        return len(kept) != len(jobs)

    def set_enabled(self, job_id: str, enabled: bool) -> bool:
        jobs = self._load()
        hit = False
        for j in jobs:
            if j["id"] == job_id or j["id"].startswith(job_id):
                j["enabled"] = enabled
                hit = True
        self._save(jobs)
        return hit

    def update(self, job_id: str, **updates) -> CronJob | None:
        jobs = self._load()
        allowed = {"schedule", "prompt", "name", "channel", "enabled", "script", "skills",
                   "deliver", "no_agent"}
        found: dict | None = None
        now = time.time()
        for j in jobs:
            if j["id"] == job_id or j["id"].startswith(job_id):
                for key, value in updates.items():
                    if key not in allowed:
                        continue
                    if key == "skills" and value is None:
                        continue
                    j[key] = value
                if "schedule" in updates:
                    j["run_at"] = _parse_oneshot(str(j.get("schedule", "")), now) or 0.0
                    j["last_run"] = 0.0
                    j["next_run"] = 0.0
                found = j
                break
        if found is None:
            return None
        self._save(jobs)
        return CronJob(**found)

    def mark_run(self, job_id: str, when: float) -> None:
        jobs = self._load()
        for j in jobs:
            if j["id"] == job_id:
                j["last_run"] = when
                j["state"] = "ok"
                j["last_error"] = ""
                if j.get("run_at"):          # one-shot: done after it fires once
                    j["enabled"] = False
                    j["next_run"] = 0.0
                else:
                    j["next_run"] = _compute_next_run(CronJob(**j), when)
        self._save(jobs)

    def mark_running(self, job_id: str) -> None:
        jobs = self._load()
        for j in jobs:
            if j["id"] == job_id:
                j["state"] = "running"
                j["last_error"] = ""
                break
        self._save(jobs)

    def record_run(self, job_id: str, when: float, *, ok: bool, error: str = "",
                   reply: str = "", keep: int = 10) -> None:
        """Persist a typed run outcome: last_run, state, last_error, next_run, and a capped
        ``runs`` history (newest last)."""
        jobs = self._load()
        for j in jobs:
            if j["id"] != job_id:
                continue
            j["last_run"] = when
            j["state"] = "ok" if ok else "error"
            j["last_error"] = "" if ok else (error or "unknown error")[:500]
            if j.get("run_at"):              # one-shot is done after a single fire
                j["enabled"] = False
                j["next_run"] = 0.0
            else:
                j["next_run"] = _compute_next_run(CronJob(**j), when)
            runs = list(j.get("runs", []))
            runs.append({"at": when, "ok": ok, "error": error[:200] if error else "",
                         "chars": len(reply or "")})
            j["runs"] = runs[-keep:]
            break
        self._save(jobs)


def _run_script_only(script: str, timeout: int = 120) -> tuple[bool, str, str]:
    if not script:
        return False, "", "no script configured for no-agent cron job"
    try:
        import subprocess
        import sys
        r = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return False, "", str(e)
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        detail = f"script exited {r.returncode}"
        if err:
            detail += f": {err[:500]}"
        return False, out, detail
    return True, out, ""


def run_job(config, job: CronJob | str, *, sink=None, store: "CronStore | None" = None,
            runner=None, verbose: bool = True, mark: bool = True) -> dict:
    """Run one cron job immediately using the same path as the scheduler."""
    store = store or CronStore()
    if isinstance(job, str):
        found = store.get(job)
        if found is None:
            return {"ok": False, "error": f"cron job not found: {job}"}
        job = found
    now = time.time()
    if verbose:
        print(f"  ▸ running cron {job.id}: {job.prompt[:60]}")
    from .automation import build_prompt, delivery_targets, is_silent
    targets = delivery_targets(job.deliver) or ([job.channel] if job.channel else [])
    first_target = targets[0] if targets else ""
    platform, _, chat_id = first_target.partition(":")
    if mark:
        store.mark_running(job.id)
    try:
        if job.no_agent:
            ok, reply, error = _run_script_only(job.script)
            if not ok:
                raise RuntimeError(error)
            delivered = 0
            if sink and not is_silent(reply):
                for target in targets:
                    sink(target, reply)
                    delivered += 1
            out = {
                "ok": True,
                "job_id": job.id,
                "mode": "no_agent",
                "reply": reply,
                "delivered": delivered,
                "targets": targets,
            }
        else:
            from .surface import SurfaceRunner
            runner = runner or SurfaceRunner(config, include_mcp=True)
            session = runner.load_or_create_session(
                f"cron:{job.id}",
                title=f"cron {job.id}",
                surface="cron",
                meta={"cron_job_id": job.id, "cron_schedule": job.schedule},
            )
            agent = runner.make_agent(
                session=session,
                platform=platform if platform and chat_id else None,
                chat_id=chat_id if platform and chat_id else None,
                include_mcp=True,
            )
            # Headless approval policy for scheduled jobs (à la cron_mode): 'deny' (default, safe —
            # dangerous tools blocked since nobody can approve) or 'approve' (auto-run, for trusted jobs).
            if config and config.get("cron.approval", "deny") == "approve":
                agent.permissions._mode_override = "auto"
            prompt = build_prompt(job.prompt, skills=job.skills, script=job.script)
            result = runner.run_prompt(
                prompt,
                session=session,
                agent=agent,
                surface="cron",
                meta={"cron_job_id": job.id, "cron_schedule": job.schedule},
                platform=platform if platform and chat_id else None,
                chat_id=chat_id if platform and chat_id else None,
            )
            reply = result.text
            delivered = 0
            if sink and not is_silent(reply):
                for target in targets:
                    sink(target, reply)
                    delivered += 1
            out = {
                "ok": True,
                "job_id": job.id,
                "run_id": result.run_id,
                "session_id": result.session.id,
                "trace_id": result.trace_id,
                "turn_id": result.turn_id,
                "reply": reply,
                "delivered": delivered,
                "targets": targets,
            }
    except Exception as e:  # noqa: BLE001
        if verbose:
            print(f"    cron error: {e}")
        out = {"ok": False, "job_id": job.id, "error": str(e), "targets": targets}
    if mark:
        store.record_run(
            job.id,
            now,
            ok=bool(out.get("ok")),
            error=str(out.get("error") or ""),
            reply=str(out.get("reply") or ""),
        )
    return out


def tick(config, sink=None, store: "CronStore | None" = None, verbose: bool = True,
         runner=None) -> int:
    """Run every due job once. ``sink(channel, text)`` delivers output. Returns jobs run.

    Shared by the standalone scheduler and the in-gateway ticker so cron fires whether or not
    a separate daemon is running."""
    from .surface import SurfaceRunner

    if getattr(_TICK_LOCAL, "active", False):
        return 0
    with _tick_lock() as acquired:
        if not acquired:
            return 0
        _TICK_LOCAL.active = True
        try:
            store = store or CronStore()
            runner = runner or SurfaceRunner(config, include_mcp=True)
            now = time.time()
            ran = 0
            for job in store.list():
                if is_due(job, now):
                    run_job(config, job, sink=sink, store=store, runner=runner, verbose=verbose)
                    ran += 1
            return ran
        finally:
            _TICK_LOCAL.active = False


def build_delivery_sink(config, *, verbose: bool = True):
    """Return a sink that sends cron output to configured gateway channels.

    Cron can run as its own service without the inbound gateway loop. In that
    mode we still want scheduled ``deliver=platform:chat`` jobs to reach the
    user, so send directly when an adapter is configured and queue as a fallback.
    """
    from .automation import enqueue_delivery

    channels = []
    if config is not None:
        channels = list(config.get("gateway.channels", []) or [])
    adapters = {}
    for name in channels:
        try:
            from .gateway.channels import build_adapter
            adapter = build_adapter(str(name), config)
            adapters[adapter.name] = adapter
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"  ! cron delivery adapter {name!r} unavailable: {e}")

    def sink(target: str, text: str) -> None:
        platform, _, chat_id = (target or "").partition(":")
        if not platform or not chat_id:
            if verbose:
                print(f"  ! cron delivery target ignored: {target!r}")
            return
        adapter = adapters.get(platform)
        if adapter is not None:
            try:
                adapter.send(chat_id, text or "")
                return
            except Exception as e:  # noqa: BLE001
                if verbose:
                    print(f"  ! cron delivery to {target} failed, queued for retry: {e}")
        if enqueue_delivery(target, text or "") and verbose:
            print(f"  ▸ queued cron delivery for {target}")

    return sink


def run_scheduler(config, sink=None, poll: int = 30) -> None:
    """Blocking loop that runs due jobs. ``sink(channel, text)`` delivers output."""
    store = CronStore()
    from .surface import SurfaceRunner
    runner = SurfaceRunner(config, include_mcp=True)
    if sink is None:
        sink = build_delivery_sink(config)
    print(f"AEGIS cron scheduler running (poll {poll}s, {len(store.list())} jobs). Ctrl+C to stop.")
    try:
        while True:
            tick(config, sink=sink, store=store, runner=runner)
            time.sleep(poll)
    except KeyboardInterrupt:
        print("\nscheduler stopped.")
