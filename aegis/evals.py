"""Provider-free replay and deterministic grading helpers.

This module is intentionally a foundation: it turns stored sessions/traces into a
stable replay shape, then runs local graders. It never calls a model unless a
caller supplies a custom grader that does so.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from . import config as cfg
from .session import Session, SessionStore
from .tracing import TraceStore
from .types import new_id
from .util import now_iso


@dataclass
class Replay:
    source: str
    id: str
    steps: list[dict[str, Any]]
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Grade:
    name: str
    passed: bool
    score: float = 1.0
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Grader = Callable[[Replay], Grade | Mapping[str, Any]]


def replay_session(session: Session | str, store: SessionStore | None = None) -> Replay:
    """Convert a stored/live session into replay steps without provider calls."""
    sess = _load_session(session, store)
    steps: list[dict[str, Any]] = []
    for index, message in enumerate(sess.messages):
        step: dict[str, Any] = {
            "index": index,
            "kind": "message",
            "role": message.role,
            "content": message.content,
        }
        if message.tool_calls:
            step["tool_calls"] = [tc.to_dict() for tc in message.tool_calls]
        if message.tool_call_id:
            step["tool_call_id"] = message.tool_call_id
        if message.name:
            step["tool_name"] = message.name
        if message.reasoning:
            step["reasoning"] = message.reasoning
        steps.append(step)
    return Replay(
        source="session",
        id=sess.id,
        steps=steps,
        meta={
            "title": sess.title,
            "created_at": sess.created_at,
            "updated_at": sess.updated_at,
            "summary": sess.meta.get("summary", ""),
            "parent_id": sess.parent_id,
            "system_prompt_hash": sess.meta.get("system_prompt_hash", ""),
            "system_prompt_tokens": sess.meta.get("system_prompt_tokens", 0),
            "prompt_parts": sess.meta.get("prompt_parts", []),
        },
    )


def replay_trace(trace_id: str, store: TraceStore | None = None) -> Replay:
    """Convert a stored trace into replay steps without provider calls."""
    trace = (store or TraceStore()).get_trace(trace_id)
    if trace is None:
        raise LookupError(f"trace not found: {trace_id}")
    meta = {k: v for k, v in trace.items() if k != "spans"}
    steps = [{"index": i, "kind": "span", **span} for i, span in enumerate(trace["spans"])]
    return Replay(source="trace", id=trace_id, steps=steps, meta=meta)


def grade_replay(replay: Replay | Mapping[str, Any],
                 graders: Iterable[Grader] | None = None) -> dict[str, Any]:
    """Run deterministic graders over a replay and return an aggregate result."""
    rep = _coerce_replay(replay)
    active = list(graders) if graders is not None else _default_graders(rep)
    grades = [_coerce_grade(grader(rep)).to_dict() for grader in active]
    passed = all(g["passed"] for g in grades)
    score = sum(float(g.get("score", 0.0)) for g in grades) / len(grades) if grades else 1.0
    return {
        "source": rep.source,
        "id": rep.id,
        "passed": passed,
        "score": round(score, 3),
        "grades": grades,
    }


def evaluate_session(session: Session | str, graders: Iterable[Grader] | None = None,
                     store: SessionStore | None = None) -> dict[str, Any]:
    return grade_replay(replay_session(session, store), graders)


def evaluate_trace(trace_id: str, graders: Iterable[Grader] | None = None,
                   store: TraceStore | None = None) -> dict[str, Any]:
    return grade_replay(replay_trace(trace_id, store), graders)


def evaluate_sessions(session_ids: Iterable[str], graders: Iterable[Grader] | None = None,
                      store: SessionStore | None = None) -> list[dict[str, Any]]:
    st = store or SessionStore()
    return [evaluate_session(session_id, graders, st) for session_id in session_ids]


def evaluate_traces(trace_ids: Iterable[str], graders: Iterable[Grader] | None = None,
                    store: TraceStore | None = None) -> list[dict[str, Any]]:
    st = store or TraceStore()
    return [evaluate_trace(trace_id, graders, st) for trace_id in trace_ids]


class EvalStore:
    def __init__(self, path: str | Path | None = None):
        self.db = Path(path) if path else cfg.sub("evals.db")
        self._init()

    @classmethod
    def from_config(cls, config) -> "EvalStore":
        raw = str(config.get("evals.path", "evals") or "evals")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = cfg.sub(raw)
        if path.suffix:
            return cls(path)
        return cls(path / "runs.db")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
        except sqlite3.OperationalError:
            pass
        return conn

    def _init(self) -> None:
        self.db.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS eval_runs (
                       id TEXT PRIMARY KEY,
                       suite TEXT,
                       created_at TEXT,
                       total INTEGER,
                       passed INTEGER,
                       score REAL,
                       data TEXT
                   )"""
            )

    def add_run(self, suite: str, results: list[dict[str, Any]]) -> dict[str, Any]:
        summary = summarize_results(results)
        row = {
            "id": new_id("eval"),
            "suite": suite,
            "created_at": now_iso(),
            "total": summary["total"],
            "passed": summary["passed"],
            "score": summary["score"],
            "data": json.dumps({"results": results, "summary": summary}),
        }
        with self._conn() as c:
            c.execute(
                """INSERT INTO eval_runs (id, suite, created_at, total, passed, score, data)
                   VALUES (:id, :suite, :created_at, :total, :passed, :score, :data)""",
                row,
            )
        return {k: v for k, v in row.items() if k != "data"} | {"results": results}

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT id, suite, created_at, total, passed, score
                   FROM eval_runs ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM eval_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        out = dict(row)
        try:
            out.update(json.loads(out.pop("data") or "{}"))
        except json.JSONDecodeError:
            out["results"] = []
        return out


def run_suite(path: str | Path, *, config=None, store: EvalStore | None = None) -> dict[str, Any]:
    """Run a provider-free JSONL eval suite.

    Rows may reference ``session_id`` or ``trace_id`` and optionally include
    ``expected_contains`` or ``expected_exact`` for a deterministic final-output
    grader. Without expectations, the default replay graders are used.
    """
    suite_path = Path(path).expanduser()
    trace_store = TraceStore.from_config(config) if config else TraceStore()
    session_store = SessionStore()
    results: list[dict[str, Any]] = []
    for line_no, line in enumerate(suite_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        label = f"{suite_path.name}:{line_no}"
        try:
            case = json.loads(line)
            if not isinstance(case, dict):
                raise ValueError("eval row must be a JSON object")
            label = str(case.get("name") or label)
            if case.get("trace_id"):
                replay = replay_trace(case["trace_id"], store=trace_store)
            elif case.get("session_id"):
                replay = replay_session(case["session_id"], store=session_store)
            else:
                replay = Replay(source="case", id=label, steps=list(case.get("steps", [])), meta=dict(case))
            graders = _case_graders(case)
            result = grade_replay(replay, graders)
            result["case"] = label
        except Exception as exc:  # noqa: BLE001
            result = _case_error_result(label, exc)
        results.append(result)
    target_store = store or (EvalStore.from_config(config) if config else EvalStore())
    return target_store.add_run(
        suite_path.stem,
        results,
    )


def _case_error_result(label: str, exc: Exception) -> dict[str, Any]:
    return {
        "source": "case",
        "id": label,
        "case": label,
        "passed": False,
        "score": 0.0,
        "error": f"{type(exc).__name__}: {exc}",
        "grades": [
            Grade(
                "case_error",
                False,
                0.0,
                f"{type(exc).__name__}: {exc}",
                {"error_type": type(exc).__name__},
            ).to_dict()
        ],
    }


def summarize_results(results: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(results)
    passed = sum(1 for row in rows if row.get("passed"))
    avg = sum(float(row.get("score", 0.0)) for row in rows) / len(rows) if rows else 0.0
    return {"total": len(rows), "passed": passed, "failed": len(rows) - passed,
            "score": round(avg, 3)}


def _load_session(session: Session | str, store: SessionStore | None) -> Session:
    if isinstance(session, Session):
        return session
    sess = (store or SessionStore()).load(session)
    if sess is None:
        raise LookupError(f"session not found: {session}")
    return sess


def _coerce_replay(replay: Replay | Mapping[str, Any]) -> Replay:
    if isinstance(replay, Replay):
        return replay
    return Replay(
        source=str(replay.get("source", "unknown")),
        id=str(replay.get("id", "")),
        steps=list(replay.get("steps", [])),
        meta=dict(replay.get("meta", {})),
    )


def _coerce_grade(grade: Grade | Mapping[str, Any]) -> Grade:
    if isinstance(grade, Grade):
        return grade
    return Grade(
        name=str(grade.get("name", "grader")),
        passed=bool(grade.get("passed")),
        score=float(grade.get("score", 1.0 if grade.get("passed") else 0.0)),
        reason=str(grade.get("reason", "")),
        details=dict(grade.get("details", {})),
    )


def _default_graders(replay: Replay) -> list[Grader]:
    graders: list[Grader] = [_has_steps]
    if replay.source == "trace":
        graders.append(_trace_no_error_spans)
    return graders


def _has_steps(replay: Replay) -> Grade:
    return Grade(
        name="has_steps",
        passed=bool(replay.steps),
        score=1.0 if replay.steps else 0.0,
        reason="replay contains steps" if replay.steps else "replay is empty",
    )


def _trace_no_error_spans(replay: Replay) -> Grade:
    bad = [step for step in replay.steps
           if str(step.get("status", "")).lower() in {"error", "failed", "failure"}]
    return Grade(
        name="trace_no_error_spans",
        passed=not bad,
        score=1.0 if not bad else 0.0,
        reason="no error spans" if not bad else f"{len(bad)} error span(s)",
        details={"span_ids": [step.get("span_id") for step in bad]},
    )


def _case_graders(case: Mapping[str, Any]) -> list[Grader] | None:
    graders: list[Grader] = []
    if any(k in case for k in ("expected_contains", "expected_exact")):
        graders.append(_expectation_grader(case))
    if "expected_status" in case:
        graders.append(_status_grader(str(case["expected_status"])))
    if "required_tool" in case:
        graders.append(_required_tool_grader(str(case["required_tool"])))
    if "max_error_spans" in case:
        graders.append(_max_error_spans_grader(int(case["max_error_spans"])))
    if "max_latency_ms" in case:
        graders.append(_max_latency_grader(float(case["max_latency_ms"])))
    return graders or None


def _expectation_grader(case: Mapping[str, Any]) -> Grader:
    def grade(replay: Replay) -> Grade:
        final = _final_text(replay)
        expected_exact = case.get("expected_exact")
        expected_contains = case.get("expected_contains")
        if expected_exact is not None:
            passed = final.strip() == str(expected_exact).strip()
            reason = "final output matched exactly" if passed else "final output differed"
            return Grade("expected_exact", passed, 1.0 if passed else 0.0, reason)
        needle = str(expected_contains or "")
        passed = needle.lower() in final.lower()
        reason = "final output contained expected text" if passed else "expected text missing"
        return Grade("expected_contains", passed, 1.0 if passed else 0.0, reason)
    return grade


def _final_text(replay: Replay) -> str:
    for step in reversed(replay.steps):
        if step.get("kind") == "message" and step.get("role") == "assistant":
            return str(step.get("content") or "")
        if step.get("kind") == "span" and step.get("data", {}).get("text"):
            return str(step["data"]["text"])
    return ""


def _status_grader(expected: str) -> Grader:
    def grade(replay: Replay) -> Grade:
        actual = str(replay.meta.get("status") or "")
        if not actual:
            for step in reversed(replay.steps):
                if step.get("status"):
                    actual = str(step.get("status"))
                    break
        passed = actual.lower() == expected.lower()
        return Grade(
            "expected_status",
            passed,
            1.0 if passed else 0.0,
            f"status {actual!r} matched" if passed else f"status {actual!r} != {expected!r}",
        )
    return grade


def _required_tool_grader(tool_name: str) -> Grader:
    def grade(replay: Replay) -> Grade:
        seen = []
        for step in replay.steps:
            if step.get("tool_name"):
                seen.append(step["tool_name"])
            for call in step.get("tool_calls", []) or []:
                if call.get("name"):
                    seen.append(call["name"])
        passed = tool_name in seen
        return Grade(
            "required_tool",
            passed,
            1.0 if passed else 0.0,
            f"used {tool_name}" if passed else f"did not use {tool_name}",
            {"tools": seen},
        )
    return grade


def _max_error_spans_grader(max_errors: int) -> Grader:
    def grade(replay: Replay) -> Grade:
        bad = [step for step in replay.steps
               if str(step.get("status", "")).lower() in {"error", "failed", "failure"}]
        passed = len(bad) <= max_errors
        return Grade(
            "max_error_spans",
            passed,
            1.0 if passed else 0.0,
            f"{len(bad)} error span(s) <= {max_errors}" if passed
            else f"{len(bad)} error span(s) > {max_errors}",
            {"span_ids": [step.get("span_id") for step in bad]},
        )
    return grade


def _max_latency_grader(max_ms: float) -> Grader:
    def grade(replay: Replay) -> Grade:
        duration = _duration_ms(replay)
        passed = duration is not None and duration <= max_ms
        return Grade(
            "max_latency_ms",
            passed,
            1.0 if passed else 0.0,
            f"{duration:.1f}ms <= {max_ms:.1f}ms" if duration is not None and passed
            else ("duration unavailable" if duration is None else f"{duration:.1f}ms > {max_ms:.1f}ms"),
            {"duration_ms": duration},
        )
    return grade


def _duration_ms(replay: Replay) -> float | None:
    from datetime import datetime
    starts, ends = [], []
    for step in replay.steps:
        try:
            if step.get("started_at"):
                starts.append(datetime.fromisoformat(str(step["started_at"])))
            if step.get("ended_at"):
                ends.append(datetime.fromisoformat(str(step["ended_at"])))
        except ValueError:
            continue
    if not starts or not ends:
        return None
    return max(0.0, (max(ends) - min(starts)).total_seconds() * 1000.0)
