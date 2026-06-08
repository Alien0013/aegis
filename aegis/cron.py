"""Scheduled tasks: interval shorthand + basic 5-field cron, JSON-backed."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime

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
    channel: str = ""          # optional "telegram:<chat_id>" sink
    last_run: float = 0.0
    enabled: bool = True


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

    def add(self, schedule: str, prompt: str, channel: str = "") -> CronJob:
        job = CronJob(id=new_id("cron"), schedule=schedule, prompt=prompt, channel=channel)
        jobs = self._load()
        jobs.append(job.__dict__)
        self._save(jobs)
        return job

    def remove(self, job_id: str) -> bool:
        jobs = self._load()
        kept = [j for j in jobs if not j["id"].startswith(job_id)]
        self._save(kept)
        return len(kept) != len(jobs)

    def mark_run(self, job_id: str, when: float) -> None:
        jobs = self._load()
        for j in jobs:
            if j["id"] == job_id:
                j["last_run"] = when
        self._save(jobs)


def run_scheduler(config, sink=None, poll: int = 30) -> None:
    """Blocking loop that runs due jobs. ``sink(channel, text)`` delivers output."""
    from .agent.agent import Agent
    from .session import Session

    store = CronStore()
    print(f"AEGIS cron scheduler running (poll {poll}s, {len(store.list())} jobs). Ctrl+C to stop.")
    try:
        while True:
            now = time.time()
            for job in store.list():
                if is_due(job, now):
                    print(f"  ▸ running cron {job.id}: {job.prompt[:60]}")
                    agent = Agent.create(config, session=Session.create())
                    try:
                        result = agent.run(job.prompt)
                        if sink and job.channel:
                            sink(job.channel, result.content)
                    except Exception as e:  # noqa: BLE001
                        print(f"    cron error: {e}")
                    store.mark_run(job.id, now)
            time.sleep(poll)
    except KeyboardInterrupt:
        print("\nscheduler stopped.")
