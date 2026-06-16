"""Scheduled tasks: interval shorthand + basic 5-field cron, JSON-backed."""

from __future__ import annotations

import json
import os
import re
import time
import threading
import hashlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import config as cfg
from ._locks import file_lock
from .types import new_id
from .util import atomic_write, read_text

_TICK_LOCAL = threading.local()
_JOBS_LOCK = threading.RLock()
_JOBS_LOCK_STATE = threading.local()
_CRON_BLOCKED_TOOLS = ("clarify", "send_message", "cronjob", "schedule_task")


def _cron_path():
    return cfg.sub("cron.json")


@contextmanager
def _jobs_file_lock():
    """Cross-process lock for cron.json load-modify-save transactions."""
    depth = int(getattr(_JOBS_LOCK_STATE, "depth", 0) or 0)
    if depth:
        _JOBS_LOCK_STATE.depth = depth + 1
        try:
            yield
        finally:
            _JOBS_LOCK_STATE.depth -= 1
        return
    with _JOBS_LOCK:
        _JOBS_LOCK_STATE.depth = 1
        try:
            path = _cron_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with file_lock(path):
                yield
        finally:
            _JOBS_LOCK_STATE.depth = 0


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write_cron(path: Path, text: str) -> None:
    atomic_write(path, text)
    _fsync_dir(path.parent)


def _backup_corrupt_jobs(raw: str) -> Path | None:
    if not raw:
        return None
    digest = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()[:12]
    pattern = f"{_cron_path().name}.corrupt.*.{digest}.bak"
    for existing in _cron_path().parent.glob(pattern):
        return existing
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    target = _cron_path().with_name(f"{_cron_path().name}.corrupt.{stamp}.{digest}.bak")
    try:
        _atomic_write_cron(target, raw)
        return target
    except OSError:
        return None


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
    context_from: list[str] = field(default_factory=list)  # prior cron job ids/names to prepend as context
    deliver: str = ""          # comma-sep "platform:chat_id" targets; supersedes ``channel`` when set
    no_agent: bool = False     # script-only: run the script and deliver its stdout, no LLM turn
    model: str = ""            # optional per-job model override
    enabled_toolsets: list[str] = field(default_factory=list)  # optional per-job toolsets
    workdir: str = ""          # optional cwd for script + agent execution
    state: str = "idle"        # idle | running | ok | error
    last_error: str = ""       # last failure message ("" when healthy)
    next_run: float = 0.0      # epoch of the next expected fire (advisory; computed on each run)
    runs: list = field(default_factory=list)          # recent run records (capped), newest last
    run_count: int = 0         # total times this job has fired
    max_runs: int = 0          # >0: retire (disable) the job after this many runs (AEGIS repeat.times)


_VALID_STATES = {"idle", "running", "ok", "error"}
_SAFE_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_SECRET_ENV_RE = re.compile(r"\$(?:[A-Z0-9_]*?(?:API[_-]?KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)[A-Z0-9_]*)\b")
_SECRET_PATH_RE = re.compile(
    r"(?:^|[\s'\"`])(?:[~\w./-]*/)?(?:\.env(?:\.[\w.-]+)?|\.netrc|\.aws/(?:credentials|config)|"
    r"\.ssh/(?:id_rsa|id_ed25519|id_dsa|id_ecdsa)|id_rsa|id_ed25519|\.git-credentials|"
    r"\.npmrc|\.pypirc|\.docker/config\.json|\.kube/config)\b",
    re.IGNORECASE,
)
_GITHUB_API_RE = re.compile(r"https?://(?:api\.)?github\.com(?:[/'\"\s]|$)", re.IGNORECASE)
_CRON_PROMPT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bdisregard\b[^\n]{0,50}\b(?:your|the|all)?\s*(?:rules|instructions|system|policy)\b",
                   re.IGNORECASE),
        "prompt-injection phrasing",
    ),
    (
        re.compile(r"\b(?:system\s+prompt\s+override|override\s+(?:the\s+)?system\s+prompt)\b",
                   re.IGNORECASE),
        "system prompt override directive",
    ),
    (
        re.compile(r"\bdo\s+not\s+(?:tell|inform|notify|mention\s+to)\s+(?:the\s+)?user\b",
                   re.IGNORECASE),
        "concealment directive",
    ),
    (
        re.compile(r"\b(?:write|append|add|install)\b[^\n]{0,80}\bauthorized_keys\b", re.IGNORECASE),
        "ssh backdoor persistence",
    ),
    (
        re.compile(r"\b(?:edit|write|append|modify)\b[^\n]{0,80}/etc/sudoers\b", re.IGNORECASE),
        "sudoers modification",
    ),
    (
        re.compile(r"\brm\s+-[^\n]*r[^\n]*f[^\n]*(?:/|~|\$HOME)\b|\brm\s+-rf\s+/", re.IGNORECASE),
        "destructive recursive delete",
    ),
)


class CronPromptInjectionBlocked(RuntimeError):
    """Raised when an assembled unattended cron prompt looks injected."""


class CronStoreCorruptError(RuntimeError):
    """Raised when the cron job store cannot be safely loaded."""


def _coerce_text(value, fallback: str = "") -> str:
    if value is None:
        return fallback
    return str(value)


def _coerce_bool(value, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "off", "disabled"}:
            return False
    return fallback


def _coerce_float(value, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _coerce_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def _coerce_refs(value) -> list[str]:
    raw = value
    if isinstance(value, str):
        raw = value.split(",") if "," in value else [value]
    refs: list[str] = []
    for item in _coerce_list(raw):
        text = _coerce_text(item).strip()
        if text and text not in refs:
            refs.append(text)
    return refs


def _safe_job_id(value) -> str:
    text = _coerce_text(value).strip()
    unsafe = (
        not text
        or text in {".", ".."}
        or "/" in text
        or "\\" in text
        or "\x00" in text
        or Path(text).is_absolute()
        or bool(Path(text).drive)
        or _SAFE_JOB_ID_RE.fullmatch(text) is None
    )
    if not unsafe:
        return text
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]+", "_", text).strip("._:-")
    if not cleaned or cleaned in {".", ".."}:
        cleaned = "unknown"
    return f"cron_legacy_{cleaned[:40]}"


def _schedule_text(value) -> str:
    if isinstance(value, dict):
        for key in ("display", "value", "expr", "run_at"):
            text = _coerce_text(value.get(key)).strip()
            if text:
                return text
        return ""
    return _coerce_text(value).strip()


def _normalize_job_record(raw: dict, *, index: int = 0, seen: set[str] | None = None) -> dict | None:
    if not isinstance(raw, dict):
        return None
    skills: list[str] = []
    for item in _coerce_list(raw.get("skills")):
        text = _coerce_text(item).strip()
        if text and text not in skills:
            skills.append(text)
    context_from = _coerce_refs(raw.get("context_from"))
    enabled_toolsets: list[str] = []
    raw_toolsets = raw.get("enabled_toolsets", raw.get("toolsets"))
    if isinstance(raw_toolsets, str):
        raw_toolsets = raw_toolsets.split(",")
    for item in _coerce_list(raw_toolsets):
        text = _coerce_text(item).strip()
        if text and text not in enabled_toolsets:
            enabled_toolsets.append(text)
    job_id = _safe_job_id(raw.get("id"))
    if seen is not None and job_id in seen:
        base = job_id
        suffix = max(1, index)
        while job_id in seen:
            job_id = f"{base}_{suffix}"
            suffix += 1
    if seen is not None:
        seen.add(job_id)
    prompt = _coerce_text(raw.get("prompt"))
    script = _coerce_text(raw.get("script")).strip()
    name = _coerce_text(raw.get("name")).strip()
    if not name:
        name = (prompt[:50] or (skills[0] if skills else "") or script or job_id).strip()
    state = _coerce_text(raw.get("state"), "idle").strip().lower()
    if state not in _VALID_STATES:
        state = "idle"
    runs = [item for item in _coerce_list(raw.get("runs")) if isinstance(item, dict)]
    return {
        "id": job_id,
        "schedule": _schedule_text(raw.get("schedule")),
        "prompt": prompt,
        "name": name,
        "channel": _coerce_text(raw.get("channel")).strip(),
        "last_run": _coerce_float(raw.get("last_run"), 0.0),
        "enabled": _coerce_bool(raw.get("enabled"), True),
        "run_at": _coerce_float(raw.get("run_at"), 0.0),
        "script": script,
        "skills": skills,
        "context_from": context_from,
        "deliver": _coerce_text(raw.get("deliver")).strip(),
        "no_agent": _coerce_bool(raw.get("no_agent"), False),
        "model": _coerce_text(raw.get("model")).strip(),
        "enabled_toolsets": enabled_toolsets,
        "workdir": _coerce_text(raw.get("workdir")).strip(),
        "state": state,
        "last_error": _coerce_text(raw.get("last_error")).strip()[:500],
        "next_run": _coerce_float(raw.get("next_run"), 0.0),
        "runs": runs[-10:],
        "run_count": max(0, int(_coerce_float(raw.get("run_count"), 0.0))),
        "max_runs": max(0, int(_coerce_float(raw.get("max_runs"), 0.0))),
    }


def _find_unique_job_index(jobs: list[dict], job_id: str) -> int | None:
    ref = _coerce_text(job_id).strip()
    if not ref:
        return None
    exact = [i for i, job in enumerate(jobs) if job.get("id") == ref]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None
    matches = [i for i, job in enumerate(jobs) if _coerce_text(job.get("id")).startswith(ref)]
    return matches[0] if len(matches) == 1 else None


def _find_unique_job_ref_index(jobs: list[dict], ref: str) -> int | None:
    ref = _coerce_text(ref).strip()
    if not ref:
        return None
    exact_ids = [i for i, job in enumerate(jobs) if job.get("id") == ref]
    if len(exact_ids) == 1:
        return exact_ids[0]
    if len(exact_ids) > 1:
        return None
    exact_names = [i for i, job in enumerate(jobs) if _coerce_text(job.get("name")).strip() == ref]
    if len(exact_names) == 1:
        return exact_names[0]
    if len(exact_names) > 1:
        return None
    prefixes = [i for i, job in enumerate(jobs) if _coerce_text(job.get("id")).startswith(ref)]
    return prefixes[0] if len(prefixes) == 1 else None


def _cron_output_root() -> Path:
    return cfg.sub("cron", "output")


def _cron_output_dir(job_id: str) -> Path:
    return _cron_output_root() / _safe_job_id(job_id)


def _write_job_output(job_id: str, when: float, reply: str, *, keep: int = 10) -> str:
    text = (reply or "").strip()
    if not text:
        return ""
    out_dir = _cron_output_dir(job_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(when).strftime("%Y%m%dT%H%M%S")
    path = out_dir / f"{stamp}-{int((when % 1) * 1000):03d}.md"
    atomic_write(path, text + ("\n" if not text.endswith("\n") else ""))
    files = sorted(out_dir.glob("*.md"), key=lambda p: (p.stat().st_mtime, p.name))
    for old in files[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass
    return str(path)


def _latest_job_output(job: CronJob, *, limit: int = 8000) -> str:
    files: list[Path] = []
    out_dir = _cron_output_dir(job.id)
    if out_dir.is_dir():
        try:
            files = sorted(out_dir.glob("*.md"), key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
        except OSError:
            files = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text:
            return text if len(text) <= limit else text[:limit] + "\n\n...[truncated]"
    for run in reversed(job.runs or []):
        if not isinstance(run, dict) or not run.get("ok"):
            continue
        text = _coerce_text(run.get("reply")).strip()
        if text:
            return text if len(text) <= limit else text[:limit] + "\n\n...[truncated]"
    return ""


def _context_from_block(store: "CronStore", job: CronJob) -> str:
    blocks: list[str] = []
    for ref in job.context_from or []:
        source = store.resolve(ref)
        if source is None or source.id == job.id:
            continue
        text = _latest_job_output(source)
        if not text:
            continue
        title = source.name or source.id
        blocks.append(f"## Output from job '{title}' ({source.id})\n{text}")
    if not blocks:
        return ""
    return "# Context from previous cron jobs\n" + "\n\n".join(blocks) + "\n\n"


def _scan_cron_prompt(prompt: str) -> str:
    from .security_scan import scan_text_findings

    text = prompt or ""
    findings = scan_text_findings(_strip_allowed_emoji_joiners(text))
    if not findings:
        for pattern, reason in _CRON_PROMPT_PATTERNS:
            if pattern.search(text):
                findings.append(reason)
                break
    if not findings and _SECRET_PATH_RE.search(text):
        findings.append("secret file read in unattended cron prompt")
    if not findings and re.search(r"\b(?:curl|wget)\b", text, re.IGNORECASE) and _SECRET_ENV_RE.search(text):
        if not _GITHUB_API_RE.search(text):
            findings.append("secret-bearing network request in unattended cron prompt")
    if not findings:
        return ""
    return f"cron prompt blocked by injection scanner: {findings[0]}"


def _scan_assembled_cron_prompt(prompt: str, job: CronJob) -> str:
    error = _scan_cron_prompt(prompt)
    if error:
        label = job.name or job.id
        raise CronPromptInjectionBlocked(f"{label}: {error}")
    return prompt


def _strip_allowed_emoji_joiners(text: str) -> str:
    chars: list[str] = []
    for index, char in enumerate(text):
        if char != "\u200d":
            chars.append(char)
            continue
        prev = text[index - 1] if index else ""
        nxt = text[index + 1] if index + 1 < len(text) else ""
        if _looks_emojiish(prev) or _looks_emojiish(nxt):
            continue
        chars.append(char)
    return "".join(chars)


def _looks_emojiish(char: str) -> bool:
    return char == "\ufe0f" or bool(char and ord(char) >= 0x1F000)


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
    @staticmethod
    def _load_unlocked() -> list[dict]:
        raw = read_text(_cron_path())
        if not raw.strip():
            return []
        repaired = False
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            try:
                loaded = json.loads(raw, strict=False)
                repaired = True
            except (json.JSONDecodeError, TypeError) as exc:
                backup = _backup_corrupt_jobs(raw)
                suffix = f"; backup saved to {backup}" if backup else ""
                raise CronStoreCorruptError(f"cron.json is corrupted and unrepairable{suffix}") from exc
        except TypeError as exc:
            backup = _backup_corrupt_jobs(raw)
            suffix = f"; backup saved to {backup}" if backup else ""
            raise CronStoreCorruptError(f"cron.json is unreadable{suffix}") from exc
        if isinstance(loaded, dict):
            loaded = loaded.get("jobs", [])
            repaired = True
        if not isinstance(loaded, list):
            backup = _backup_corrupt_jobs(raw)
            suffix = f"; backup saved to {backup}" if backup else ""
            raise CronStoreCorruptError(
                f"cron.json is corrupted: expected a list or jobs object, got {type(loaded).__name__}{suffix}"
            )
        seen: set[str] = set()
        jobs: list[dict] = []
        for index, item in enumerate(loaded):
            normalized = _normalize_job_record(item, index=index, seen=seen)
            if normalized is not None:
                jobs.append(normalized)
        if repaired:
            CronStore._save_unlocked(jobs)
        return jobs

    @staticmethod
    def _save_unlocked(jobs: list[dict]) -> None:
        seen: set[str] = set()
        normalized = [
            job for index, item in enumerate(jobs)
            if (job := _normalize_job_record(item, index=index, seen=seen)) is not None
        ]
        text = json.dumps(normalized, indent=2)
        _atomic_write_cron(_cron_path(), text + ("\n" if not text.endswith("\n") else ""))

    def _load(self) -> list[dict]:
        with _jobs_file_lock():
            return self._load_unlocked()

    def _save(self, jobs: list[dict]) -> None:
        with _jobs_file_lock():
            self._save_unlocked(jobs)

    def list(self) -> list[CronJob]:
        return [CronJob(**j) for j in self._load()]

    def get(self, job_id: str) -> CronJob | None:
        jobs = self._load()
        index = _find_unique_job_index(jobs, job_id)
        if index is not None:
            return CronJob(**jobs[index])
        return None

    def resolve(self, ref: str) -> CronJob | None:
        jobs = self._load()
        index = _find_unique_job_ref_index(jobs, ref)
        if index is not None:
            return CronJob(**jobs[index])
        return None

    def add(self, schedule: str, prompt: str, channel: str = "", script: str = "",
            skills: list[str] | None = None, deliver: str = "", name: str = "",
            no_agent: bool = False, max_runs: int = 0,
            context_from: list[str] | str | None = None,
            model: str = "", enabled_toolsets: list[str] | None = None,
            workdir: str = "") -> CronJob:
        run_at = _parse_oneshot(schedule, time.time()) or 0.0
        job = CronJob(id=new_id("cron"), schedule=schedule, prompt=prompt, name=name, channel=channel,
                      run_at=run_at, script=script, skills=skills or [], deliver=deliver,
                      context_from=_coerce_refs(context_from), no_agent=no_agent,
                      model=str(model or "").strip(),
                      enabled_toolsets=list(enabled_toolsets or []),
                      workdir=str(workdir or "").strip(),
                      max_runs=max(0, int(max_runs or 0)))
        with _jobs_file_lock():
            jobs = self._load_unlocked()
            jobs.append(job.__dict__)
            self._save_unlocked(jobs)
        return job

    def remove(self, job_id: str) -> bool:
        with _jobs_file_lock():
            jobs = self._load_unlocked()
            index = _find_unique_job_index(jobs, job_id)
            if index is None:
                return False
            del jobs[index]
            self._save_unlocked(jobs)
            return True

    def set_enabled(self, job_id: str, enabled: bool) -> bool:
        with _jobs_file_lock():
            jobs = self._load_unlocked()
            index = _find_unique_job_index(jobs, job_id)
            if index is None:
                return False
            jobs[index]["enabled"] = enabled
            self._save_unlocked(jobs)
            return True

    def prune_spent(self, *, dry_run: bool = True) -> list[str]:
        """Retire jobs that have run their course and can never fire again: fired
        one-shots (``run_at`` set, disabled, already run) and recurring jobs disabled
        after hitting ``max_runs``. Keeps the cron store from accumulating dead jobs —
        the cron-side of the lifecycle cleanup the curator runs for skills/sessions.
        Returns the ids pruned (or that would be, when ``dry_run``)."""
        with _jobs_file_lock():
            jobs = self._load_unlocked()
            spent: list[str] = []
            for j in jobs:
                if j.get("enabled", True):
                    continue                       # still active — never prune
                one_shot_done = bool(j.get("run_at")) and float(j.get("last_run", 0) or 0) > 0
                hit_limit = (int(j.get("max_runs", 0) or 0) > 0
                             and int(j.get("run_count", 0) or 0) >= int(j.get("max_runs", 0)))
                if one_shot_done or hit_limit:
                    spent.append(j.get("id", ""))
            if not dry_run and spent:
                self._save_unlocked([j for j in jobs if j.get("id") not in spent])
            return [s for s in spent if s]

    def update(self, job_id: str, **updates) -> CronJob | None:
        with _jobs_file_lock():
            jobs = self._load_unlocked()
            index = _find_unique_job_index(jobs, job_id)
            if index is None:
                return None
            allowed = {"schedule", "prompt", "name", "channel", "enabled", "script", "skills", "context_from",
                       "deliver", "no_agent", "max_runs", "model", "enabled_toolsets", "workdir"}
            now = time.time()
            found = jobs[index]
            for key, value in updates.items():
                if key not in allowed:
                    continue
                if key == "skills" and value is None:
                    continue
                if key == "context_from":
                    value = _coerce_refs(value)
                if key == "enabled_toolsets":
                    value = _coerce_refs(value)
                found[key] = value
            if "schedule" in updates:
                found["run_at"] = _parse_oneshot(str(found.get("schedule", "")), now) or 0.0
                found["last_run"] = 0.0
                found["next_run"] = 0.0
            self._save_unlocked(jobs)
            return CronJob(**_normalize_job_record(found))

    def mark_run(self, job_id: str, when: float) -> None:
        with _jobs_file_lock():
            jobs = self._load_unlocked()
            index = _find_unique_job_index(jobs, job_id)
            if index is not None:
                j = jobs[index]
                j["last_run"] = when
                j["state"] = "ok"
                j["last_error"] = ""
                if j.get("run_at"):          # one-shot: done after it fires once
                    j["enabled"] = False
                    j["next_run"] = 0.0
                else:
                    j["next_run"] = _compute_next_run(CronJob(**j), when)
            self._save_unlocked(jobs)

    def mark_running(self, job_id: str) -> None:
        with _jobs_file_lock():
            jobs = self._load_unlocked()
            index = _find_unique_job_index(jobs, job_id)
            if index is not None:
                jobs[index]["state"] = "running"
                jobs[index]["last_error"] = ""
            self._save_unlocked(jobs)

    def record_run(self, job_id: str, when: float, *, ok: bool, error: str = "",
                   reply: str = "", keep: int = 10) -> None:
        """Persist a typed run outcome: last_run, state, last_error, next_run, and a capped
        ``runs`` history (newest last)."""
        with _jobs_file_lock():
            jobs = self._load_unlocked()
            index = _find_unique_job_index(jobs, job_id)
            if index is not None:
                j = jobs[index]
                j["last_run"] = when
                j["state"] = "ok" if ok else "error"
                j["last_error"] = "" if ok else (error or "unknown error")[:500]
                j["run_count"] = int(j.get("run_count", 0)) + 1
                max_runs = int(j.get("max_runs", 0) or 0)
                if j.get("run_at"):              # one-shot is done after a single fire
                    j["enabled"] = False
                    j["next_run"] = 0.0
                elif max_runs > 0 and j["run_count"] >= max_runs:
                    j["enabled"] = False         # recurring job hit its run limit — retire it
                    j["next_run"] = 0.0
                else:
                    j["next_run"] = _compute_next_run(CronJob(**j), when)
                output_path = _write_job_output(job_id, when, reply, keep=keep) if ok else ""
                runs = list(j.get("runs", []))
                runs.append({"at": when, "ok": ok, "error": error[:200] if error else "",
                             "chars": len(reply or ""), "output": output_path,
                             "reply": (reply or "")[:8000]})
                j["runs"] = runs[-keep:]
            self._save_unlocked(jobs)


def _run_script_only(script: str, timeout: int = 120, cwd: Path | None = None) -> tuple[bool, str, str]:
    if not script:
        return False, "", "no script configured for no-agent cron job"
    try:
        import subprocess
        import sys
        r = subprocess.run([sys.executable, script], cwd=str(cwd) if cwd else None,
                           capture_output=True, text=True, timeout=timeout)
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


def _cron_run_config(config, job: CronJob | None = None):
    """Return the config used by the agent for a cron job.

    AEGIS cron jobs are deterministic unattended runs: the job prompt, explicit
    skills, and script context should carry the task. By default we disable built-in
    memory for the agent created here; users can opt back in with
    ``cron.skip_memory=false``.
    """
    from .config import Config

    base = config or Config.load()
    skip_memory = bool(base.get("cron.skip_memory", True))
    needs_copy = (
        skip_memory
        or bool(getattr(job, "model", ""))
        or bool(getattr(job, "enabled_toolsets", []))
        or bool(_CRON_BLOCKED_TOOLS)
    )
    if not needs_copy:
        return base
    import copy

    data = copy.deepcopy(getattr(base, "data", {}) or {})
    if skip_memory:
        memory = data.setdefault("memory", {})
        memory["enabled"] = False
        memory["user_profile_enabled"] = False
    if job is not None and job.model:
        data.setdefault("model", {})["default"] = job.model
    tools = data.setdefault("tools", {})
    disabled = tools.get("disabled", []) or []
    if isinstance(disabled, str):
        disabled = [disabled]
    merged_disabled = [str(item).strip() for item in disabled if str(item).strip()]
    seen_disabled = set(merged_disabled)
    for name in _CRON_BLOCKED_TOOLS:
        if name not in seen_disabled:
            merged_disabled.append(name)
            seen_disabled.add(name)
    tools["disabled"] = merged_disabled
    if job is not None and job.enabled_toolsets:
        tools["toolsets"] = list(job.enabled_toolsets)
    return Config(data)


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
    from .automation import delivery_targets, is_silent, script_context, skills_directive
    targets = delivery_targets(job.deliver) or ([job.channel] if job.channel else [])
    first_target = targets[0] if targets else ""
    platform, _, chat_id = first_target.partition(":")
    run_config = _cron_run_config(config, job)
    job_cwd = Path(job.workdir).expanduser() if job.workdir else None
    if job_cwd is not None and not job_cwd.exists():
        return {"ok": False, "job_id": job.id, "error": f"workdir not found: {job.workdir}", "targets": targets}
    if mark:
        store.mark_running(job.id)
    try:
        if job.no_agent:
            ok, reply, error = _run_script_only(job.script, cwd=job_cwd)
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
                "cron_skip_memory": bool(run_config.get("memory.enabled", True)) is False,
                "model": job.model or "",
                "enabled_toolsets": list(job.enabled_toolsets or []),
                "workdir": str(job_cwd or ""),
                "reply": reply,
                "delivered": delivered,
                "targets": targets,
            }
        else:
            from .surface import SurfaceRunner
            runner = runner or SurfaceRunner(run_config, include_mcp=True)
            cron_meta = {
                "cron_job_id": job.id,
                "cron_schedule": job.schedule,
                "cron_skip_memory": bool(run_config.get("memory.enabled", True)) is False,
                "cron_model": job.model or "",
                "cron_toolsets": list(job.enabled_toolsets or []),
                "cron_workdir": str(job_cwd or ""),
            }
            session = runner.load_or_create_session(
                f"cron:{job.id}",
                title=f"cron {job.id}",
                surface="cron",
                meta=cron_meta,
            )
            agent = runner.make_agent(
                session=session,
                platform=platform if platform and chat_id else None,
                chat_id=chat_id if platform and chat_id else None,
                include_mcp=True,
                config=run_config,
                cwd=job_cwd,
            )
            # Headless approval policy for scheduled jobs (à la cron_mode): 'deny' (default, safe —
            # dangerous tools blocked since nobody can approve) or 'approve' (auto-run, for trusted jobs).
            if run_config.get("cron.approval", "deny") == "approve":
                agent.permissions._mode_override = "auto"
            cwd = getattr(agent, "cwd", Path.cwd())
            skill_block = skills_directive(job.skills, config=run_config, cwd=cwd)
            script_block = script_context(job.script)
            context_block = _context_from_block(store, job)
            prompt_without_chained_context = skill_block + script_block + job.prompt
            # Chained cron output is prior runtime data; scan configured prompt/script first.
            _scan_assembled_cron_prompt(prompt_without_chained_context, job)
            prompt = skill_block + context_block + script_block + job.prompt
            result = runner.run_prompt(
                prompt,
                session=session,
                agent=agent,
                surface="cron",
                meta=cron_meta,
                platform=platform if platform and chat_id else None,
                chat_id=chat_id if platform and chat_id else None,
                cwd=job_cwd,
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
                "cron_skip_memory": cron_meta["cron_skip_memory"],
                "model": job.model or "",
                "enabled_toolsets": list(job.enabled_toolsets or []),
                "workdir": str(job_cwd or ""),
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
                deliver = getattr(adapter, "deliver", None)
                if callable(deliver):
                    deliver(chat_id, text or "")
                else:
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
