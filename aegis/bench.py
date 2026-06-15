"""End-to-end task benchmark — give AEGIS a task, score pass/fail.

Where :mod:`aegis.evals` *replays* stored sessions against deterministic graders,
this module runs the agent **live** against small fixture tasks and verifies the
result with a real command. A task is a directory holding a ``task.yaml``::

    name: fix-add
    prompt: "The add() function is wrong — make the test pass."
    files:                         # seeded into a fresh temp workspace
      calc.py: |
        def add(a, b):
            return a - b
      test_calc.py: |
        from calc import add
        def test_add():
            assert add(2, 3) == 5
    setup: ["python -m pip install -q pytest"]   # optional, run before the agent
    verify: ["python -m pytest -q"]              # all must exit 0 => pass
    timeout: 600

The runner copies the seed files into a throwaway directory, runs the agent
there (via the SDK), then runs ``verify``. ``pass`` iff every verify command
exits 0. The suite score is the pass rate — a single number to optimize, so
every harness change (prompt, model, compaction) becomes measurable instead of
vibes. Results are stored in the shared :class:`aegis.evals.EvalStore`.

The ``solver`` is injectable: the default drives :func:`aegis.sdk.run` (needs a
provider), but tests pass a deterministic stub, so the runner itself is testable
without a model.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# A solver takes (prompt, workdir, config) and edits the workspace to solve the task.
Solver = Callable[[str, Path, Any], str]

_DEFAULT_TIMEOUT = 600


@dataclass
class BenchTask:
    name: str
    prompt: str
    files: dict[str, str] = field(default_factory=dict)
    setup: list[str] = field(default_factory=list)
    verify: list[str] = field(default_factory=list)
    timeout: int = _DEFAULT_TIMEOUT
    source: str = ""


def _as_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def load_task(path: str | Path) -> BenchTask:
    """Load a single ``task.yaml`` (or a directory containing one)."""
    import yaml

    p = Path(path).expanduser()
    if p.is_dir():
        p = p / "task.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{p}: task file must be a YAML mapping")
    return BenchTask(
        name=str(data.get("name") or p.parent.name),
        prompt=str(data.get("prompt") or "").strip(),
        files={str(k): str(v) for k, v in (data.get("files") or {}).items()},
        setup=_as_list(data.get("setup")),
        verify=_as_list(data.get("verify")),
        timeout=int(data.get("timeout") or _DEFAULT_TIMEOUT),
        source=str(p),
    )


def discover_tasks(root: str | Path) -> list[BenchTask]:
    """Find every ``task.yaml`` under ``root`` (sorted by name)."""
    base = Path(root).expanduser()
    if base.is_file():
        return [load_task(base)]
    tasks = [load_task(f) for f in sorted(base.rglob("task.yaml"))]
    return sorted(tasks, key=lambda t: t.name)


def _default_root(config) -> Path:
    raw = str((config.get("bench.path", "") if config else "") or "benchmarks")
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (Path.cwd() / p)


def _seed_workspace(task: BenchTask, workdir: Path) -> None:
    for rel, content in task.files.items():
        dest = workdir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")


def _run_cmds(cmds: list[str], workdir: Path, timeout: int) -> tuple[bool, str]:
    """Run shell commands in order; stop at the first failure. Returns (ok, log)."""
    log: list[str] = []
    for cmd in cmds:
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=str(workdir), capture_output=True, text=True,
                timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            log.append(f"$ {cmd}\n[timeout after {timeout}s]")
            return False, "\n".join(log)
        out = (proc.stdout or "") + (proc.stderr or "")
        log.append(f"$ {cmd}\n{out.strip()}")
        if proc.returncode != 0:
            log.append(f"[exit {proc.returncode}]")
            return False, "\n".join(log)
    return True, "\n".join(log)


def _default_solver(prompt: str, workdir: Path, config) -> str:
    """Drive the real agent over the task (needs a configured provider)."""
    from .sdk import AegisClient

    client = AegisClient(config=config, cwd=workdir)
    try:
        result = client.run(prompt, cwd=workdir, auto=True, expand_refs=False)
        return result.message.content if result.message else ""
    finally:
        client.close()


def run_task(task: BenchTask, *, solver: Solver | None = None, config=None,
             keep_workdir: bool = False) -> dict[str, Any]:
    """Run one task end-to-end and grade it with its ``verify`` commands."""
    solve = solver or _default_solver
    workdir = Path(tempfile.mkdtemp(prefix=f"aegis-bench-{task.name}-"))
    try:
        _seed_workspace(task, workdir)
        setup_ok, setup_log = (True, "")
        if task.setup:
            setup_ok, setup_log = _run_cmds(task.setup, workdir, task.timeout)
        error = ""
        if setup_ok:
            try:
                solve(task.prompt, workdir, config)
            except Exception as exc:  # noqa: BLE001 — a solver crash is a task failure, not a runner crash
                error = f"{type(exc).__name__}: {exc}"
        if not setup_ok:
            passed, verify_log = False, setup_log
        elif error:
            passed, verify_log = False, error
        elif task.verify:
            passed, verify_log = _run_cmds(task.verify, workdir, task.timeout)
        else:
            passed, verify_log = True, "(no verify commands)"
        return {
            "source": "bench",
            "id": task.name,
            "case": task.name,
            "passed": bool(passed),
            "score": 1.0 if passed else 0.0,
            "error": error,
            "grades": [{
                "name": "verify",
                "passed": bool(passed),
                "score": 1.0 if passed else 0.0,
                "reason": "verify commands passed" if passed
                          else (error or "verify commands failed"),
                "details": {"log": verify_log[-4000:]},
            }],
        }
    finally:
        if not keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)


def run_suite(root: str | Path | None = None, *, solver: Solver | None = None,
              config=None, store: bool = True,
              only: list[str] | None = None) -> dict[str, Any]:
    """Run every discovered task and (optionally) persist a scored run."""
    base = Path(root).expanduser() if root else _default_root(config)
    tasks = discover_tasks(base)
    if only:
        wanted = set(only)
        tasks = [t for t in tasks if t.name in wanted]
    results = [run_task(t, solver=solver, config=config) for t in tasks]
    from .evals import summarize_results
    summary = summarize_results(results)
    out = {"suite": "bench", "root": str(base), **summary, "results": results}
    if store:
        from .evals import EvalStore
        eval_store = EvalStore.from_config(config) if config else EvalStore()
        rec = eval_store.add_run("bench", results)
        out["id"] = rec["id"]
    return out


def suite_score(root: str | Path | None = None, *, solver: Solver | None = None,
                config=None) -> float:
    """Pass-rate of the suite in [0,1] — the scorer the self-improve loop uses."""
    res = run_suite(root, solver=solver, config=config, store=False)
    return float(res.get("score", 0.0))


def cmd_bench(args, config) -> int:
    """`aegis bench [run|list|score] [--dir PATH] [--task NAME]`."""
    import json

    action = getattr(args, "action", "run") or "run"
    root = getattr(args, "dir", None)
    only = [getattr(args, "task")] if getattr(args, "task", None) else None

    if action == "list":
        tasks = discover_tasks(Path(root).expanduser() if root else _default_root(config))
        for t in tasks:
            print(f"  {t.name:<28} {t.prompt[:60]}")
        if not tasks:
            print("(no benchmark tasks found — add benchmarks/<name>/task.yaml)")
        return 0

    res = run_suite(root, config=config, store=(action == "run"), only=only)
    if getattr(args, "json", False):
        print(json.dumps(res, indent=2))
        return 0
    for r in res["results"]:
        mark = "✓" if r["passed"] else "✗"
        print(f"  {mark} {r['case']}")
        if not r["passed"]:
            reason = r["grades"][0].get("reason", "") if r.get("grades") else ""
            if reason:
                print(f"      {reason}")
    print(f"\n{res['passed']}/{res['total']} passed  score={res['score']}")
    return 0 if res["total"] and res["passed"] == res["total"] else 1
