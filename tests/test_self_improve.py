"""Verified self-improvement: keep a change only if the score holds/improves."""

import pytest

from aegis import self_improve as si


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))


def test_keeps_change_that_improves():
    state = {"applied": False, "reverted": False}
    score = {"v": 0.5}

    def apply():
        state["applied"] = True
        score["v"] = 0.8

    def revert():
        state["reverted"] = True
        score["v"] = 0.5

    exp = si.verify_change(apply, revert, scorer=lambda: score["v"], name="t")
    assert exp.kept is True
    assert state["applied"] and not state["reverted"]
    assert exp.baseline == 0.5 and exp.candidate == 0.8 and exp.delta == pytest.approx(0.3)


def test_keeps_change_that_holds_steady():
    score = {"v": 0.7}
    exp = si.verify_change(lambda: None, lambda: score.update(v=0.0),
                           scorer=lambda: score["v"])
    assert exp.kept is True            # no regression => kept even at delta 0


def test_reverts_change_that_regresses():
    state = {"reverted": False}
    score = {"v": 0.9}

    def apply():
        score["v"] = 0.4

    def revert():
        state["reverted"] = True
        score["v"] = 0.9

    exp = si.verify_change(apply, revert, scorer=lambda: score["v"], name="bad")
    assert exp.kept is False
    assert state["reverted"] is True
    assert score["v"] == 0.9           # actually rolled back


def test_min_delta_requires_real_improvement():
    score = {"v": 0.50}
    exp = si.verify_change(lambda: score.update(v=0.51), lambda: score.update(v=0.50),
                           scorer=lambda: score["v"], min_delta=0.05)
    assert exp.kept is False           # +0.01 < required +0.05 => reverted


def test_reverts_when_scorer_crashes_after_apply():
    calls = {"n": 0}
    reverted = {"v": False}

    def scorer():
        calls["n"] += 1
        if calls["n"] == 1:
            return 0.5
        raise RuntimeError("eval blew up")

    exp = si.verify_change(lambda: None, lambda: reverted.update(v=True), scorer=scorer)
    assert exp.kept is False and reverted["v"] is True
    assert "scorer failed" in exp.reason


def test_experiment_log_roundtrip():
    si.verify_change(lambda: None, lambda: None, scorer=lambda: 1.0, name="logged")
    rows = si.list_experiments()
    assert rows and rows[0]["name"] == "logged" and rows[0]["kept"] is True


def test_verified_review_falls_back_without_benchmarks(monkeypatch):
    from aegis.config import Config
    monkeypatch.setattr(si, "scorer_available", lambda *a, **k: False)
    monkeypatch.setattr("aegis.curator.llm_review", lambda c, **k: {"ran": True, "actions": []})
    out = si.verified_curator_review(Config.load())
    assert out["verified"] is False and out["ran"] is True


def test_verified_review_reverts_regressing_skills(monkeypatch, tmp_path):
    """End-to-end-ish: a review that lowers the score gets rolled back via curator backup."""
    from aegis import curator
    from aegis.config import Config

    skills = tmp_path / "home" / "skills"
    skills.mkdir(parents=True)
    (skills / "keep.txt").write_text("original")
    monkeypatch.setattr(curator, "backup", lambda **k: skills / "snap")
    captured = {}

    def fake_rollback(snap_id=None):
        (skills / "keep.txt").write_text("restored")
        captured["rolled_back"] = True

    monkeypatch.setattr(curator, "rollback", fake_rollback)

    score = {"v": 0.8}

    def review(config):
        (skills / "keep.txt").write_text("mangled")
        score["v"] = 0.3            # the review made things worse
        return {"ran": True, "actions": ["mangled keep"]}

    monkeypatch.setattr(curator, "llm_review", review)
    out = si.verified_curator_review(Config.load(), scorer=lambda: score["v"])
    assert out["kept"] is False
    assert captured.get("rolled_back") is True
    assert (skills / "keep.txt").read_text() == "restored"
