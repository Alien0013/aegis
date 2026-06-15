"""End-to-end benchmark runner: seed a workspace, run a (stubbed) solver, verify."""

import textwrap

import pytest

from aegis import bench
from aegis.config import Config


@pytest.fixture
def task(tmp_path):
    (tmp_path / "task.yaml").write_text(textwrap.dedent("""
        name: fix-add
        prompt: "make check.py pass"
        files:
          calc.py: |
            def add(a, b):
                return a - b
          check.py: |
            from calc import add
            assert add(2, 3) == 5
            print("OK")
        verify:
          - python check.py
    """))
    return bench.load_task(tmp_path)


def test_load_task(task):
    assert task.name == "fix-add"
    assert "calc.py" in task.files and "check.py" in task.files
    assert task.verify == ["python check.py"]


def test_run_task_passes_when_solver_fixes_it(task):
    def solver(prompt, workdir, config):
        (workdir / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        return "fixed"

    res = bench.run_task(task, solver=solver)
    assert res["passed"] is True and res["score"] == 1.0


def test_run_task_fails_when_solver_noop(task):
    res = bench.run_task(task, solver=lambda p, w, c: "did nothing")
    assert res["passed"] is False and res["score"] == 0.0
    assert "log" in res["grades"][0]["details"]


def test_run_task_fails_on_solver_crash(task):
    def boom(prompt, workdir, config):
        raise RuntimeError("solver exploded")

    res = bench.run_task(task, solver=boom)
    assert res["passed"] is False
    assert "solver exploded" in res["error"]


def test_run_suite_aggregates_and_scores(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    root = tmp_path / "benchmarks"
    for name, fixed in (("a", True), ("b", False)):
        d = root / name
        d.mkdir(parents=True)
        (d / "task.yaml").write_text(textwrap.dedent(f"""
            name: {name}
            prompt: x
            files:
              flag.txt: "no"
            verify:
              - "test \\"$(cat flag.txt)\\" = yes"
        """))

    def solver(prompt, workdir, config):
        # only solve task "a" by writing the expected flag
        if "bench-a-" in workdir.name:
            (workdir / "flag.txt").write_text("yes")
        return ""

    res = bench.run_suite(root, solver=solver, config=Config.load(), store=False)
    assert res["total"] == 2
    assert res["passed"] == 1
    assert res["score"] == 0.5


def test_suite_score_is_pass_rate(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    root = tmp_path / "benchmarks"
    d = root / "ok"
    d.mkdir(parents=True)
    (d / "task.yaml").write_text(textwrap.dedent("""
        name: ok
        prompt: x
        verify: []
    """))
    score = bench.suite_score(root, solver=lambda p, w, c: "", config=Config.load())
    assert score == 1.0
