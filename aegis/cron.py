"""Scheduled tasks: interval shorthand + basic 5-field cron, JSON-backed."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import config as cfg
from .types import new_id
from .util import atomic_write, read_text


def _cron_path():
    return cfg.sub("cron.json")


@dataclass
class CronJob:
    id: str
    schedule: str
    prompt: str
    channel: str = ""          # optional "telegram:<chat_id>" sink (single target; back-compat)
    last_run: float = 0.0
    enabled: bool = True
    run_at: float = 0.0        # >0 marks a one-shot job: fire once at this epoch, then disable
    script: str = ""           # path to a Python file run first; its stdout is prepended as context
    skills: list[str] = field(default_factory=list)   # skills to load before running
    deliver: str = ""          # comma-sep "platform:chat_id" targets; supersedes ``channel`` when set


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


def is_due(job: CronJob, now: float) -> bool:
    if not job.enabled:
        return False
    if job.run_at:                                   # one-shot: fire once when its time arrives
        return job.last_run == 0.0 and now >= job.run_at
    interval = _interval_seconds(job.schedule)
    if interval is not None:
        return (now - job.last_run) >= interval
    # 5-field cron: minute hour day-of-month month day-of-week
    fields = job.schedule.split()
    if len(fields) != 5:
        return False
    dt = datetime.fromtimestamp(now)
    minute, hour, dom, mon, dow = fields
    if (now - job.last_run) < 60:   # avoid double firing within a minute
        return False
    return (_cron_field_matches(minute, dt.minute) and _cron_field_matches(hour, dt.hour)
            and _cron_field_matches(dom, dt.day) and _cron_field_matches(mon, dt.month)
            and _cron_field_matches(dow, dt.weekday()))


class CronStore:
    def _load(self) -> list[dict]:
        raw = read_text(_cron_path())
        return json.loads(raw) if raw.strip() else []

    def _save(self, jobs: list[dict]) -> None:
        atomic_write(_cron_path(), json.dumps(jobs, indent=2))

    def list(self) -> list[CronJob]:
        return [CronJob(**j) for j in self._load()]

    def add(self, schedule: str, prompt: str, channel: str = "", script: str = "",
            skills: list[str] | None = None, deliver: str = "") -> CronJob:
        run_at = _parse_oneshot(schedule, time.time()) or 0.0
        job = CronJob(id=new_id("cron"), schedule=schedule, prompt=prompt, channel=channel,
                      run_at=run_at, script=script, skills=skills or [], deliver=deliver)
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

    def mark_run(self, job_id: str, when: float) -> None:
        jobs = self._load()
        for j in jobs:
            if j["id"] == job_id:
                j["last_run"] = when
                if j.get("run_at"):          # one-shot: done after it fires once
                    j["enabled"] = False
        self._save(jobs)


def tick(config, sink=None, store: "CronStore | None" = None, verbose: bool = True) -> int:
    """Run every due job once. ``sink(channel, text)`` delivers output. Returns jobs run.

    Shared by the standalone scheduler and the in-gateway ticker so cron fires whether or not
    a separate daemon is running."""
    from .agent.agent import Agent
    from .session import Session

    store = store or CronStore()
    now = time.time()
    ran = 0
    for job in store.list():
        if is_due(job, now):
            if verbose:
                print(f"  ▸ running cron {job.id}: {job.prompt[:60]}")
            agent = Agent.create(config, session=Session.create())
            from .automation import build_prompt, delivery_targets, is_silent
            targets = delivery_targets(job.deliver) or ([job.channel] if job.channel else [])
            first_target = targets[0] if targets else ""
            platform, _, chat_id = first_target.partition(":")
            if platform and chat_id:
                agent.platform = platform
                agent.chat_id = chat_id
            # Headless approval policy for scheduled jobs (à la cron_mode): 'deny' (default, safe —
            # dangerous tools blocked since nobody can approve) or 'approve' (auto-run, for trusted jobs).
            if config and config.get("cron.approval", "deny") == "approve":
                agent.permissions._mode_override = "auto"
            prompt = build_prompt(job.prompt, skills=job.skills, script=job.script)
            try:
                reply = agent.run(prompt).content
                # [SILENT]/empty -> deliver nothing (monitors only notify on real change).
                if sink and not is_silent(reply):
                    for target in targets:
                        sink(target, reply)
            except Exception as e:  # noqa: BLE001
                print(f"    cron error: {e}")
            store.mark_run(job.id, now)
            ran += 1
    return ran


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
    if sink is None:
        sink = build_delivery_sink(config)
    print(f"AEGIS cron scheduler running (poll {poll}s, {len(store.list())} jobs). Ctrl+C to stop.")
    try:
        while True:
            tick(config, sink=sink, store=store)
            time.sleep(poll)
    except KeyboardInterrupt:
        print("\nscheduler stopped.")
