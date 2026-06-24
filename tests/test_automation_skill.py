"""The automation (watchers) skill: it loads/validates, and its bundled watcher scripts
do correct watermark dedup (baseline-on-first-run, emit-only-new) and source parsing."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "aegis" / "builtin_skills" / "automation" / "scripts"
sys.path.insert(0, str(_SCRIPTS))


def test_automation_skill_loads_and_is_high_level():
    from aegis.config import Config
    from aegis.skills import SkillsLoader

    skills = SkillsLoader(Config.load()).available()
    auto = next((s for s in skills if s.name == "automation"), None)
    assert auto is not None, "automation skill should load"
    body = auto.full_body()
    # high-altitude playbook, not a bare script dump
    for marker in ("When to Use", "Mental model", "Procedure", "Verification",
                   "watch_rss.py", "watch_github.py", "cronjob"):
        assert marker in body, f"automation skill body missing '{marker}'"


def test_bundled_scripts_present():
    for name in ("_watermark.py", "watch_rss.py", "watch_http_json.py", "watch_github.py"):
        assert (_SCRIPTS / name).is_file(), f"missing watcher script {name}"


def test_watermark_baseline_then_emits_only_new(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_WATCHER_STATE_DIR", str(tmp_path))
    import _watermark
    key = lambda it: it["id"]  # noqa: E731

    # first run records a baseline and emits nothing
    assert _watermark.select_new("feed", [{"id": "a"}, {"id": "b"}], key) == []
    # a new item appears -> only it is emitted
    new = _watermark.select_new("feed", [{"id": "a"}, {"id": "b"}, {"id": "c"}], key)
    assert [it["id"] for it in new] == ["c"]
    # running again with no change emits nothing
    assert _watermark.select_new("feed", [{"id": "a"}, {"id": "b"}, {"id": "c"}], key) == []


def test_watermark_is_bounded(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_WATCHER_STATE_DIR", str(tmp_path))
    import _watermark

    _watermark.select_new("big", [{"id": str(i)} for i in range(800)], lambda it: it["id"])
    seen, first = _watermark.load_seen("big")
    assert first is False
    assert len(seen) == _watermark.MAX_IDS  # capped at 500 newest


def test_rss_parser_handles_rss_and_atom():
    import watch_rss
    rss = b"""<rss><channel>
      <item><title>Hello</title><link>http://x/1</link><guid>g1</guid><description>body</description></item>
    </channel></rss>"""
    items = watch_rss.parse_feed(rss)
    assert items and items[0]["id"] == "g1" and items[0]["url"] == "http://x/1" and items[0]["title"] == "Hello"

    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>Post</title><id>a1</id><link href="http://y/2" rel="alternate"/><summary>s</summary></entry>
    </feed>"""
    aitems = watch_rss.parse_feed(atom)
    assert aitems and aitems[0]["id"] == "a1" and aitems[0]["url"] == "http://y/2"


def test_github_normalize_commits_and_issues():
    import watch_github
    commits = watch_github.normalize("commits", [
        {"sha": "deadbeef", "commit": {"message": "fix: thing\n\ndetails"}, "html_url": "http://gh/c"},
    ])
    assert commits[0]["id"] == "deadbeef" and commits[0]["title"] == "fix: thing"

    issues = watch_github.normalize("issues", [
        {"id": 42, "number": 7, "title": "Bug", "html_url": "http://gh/i/7"},
    ])
    assert issues[0]["id"] == "42" and issues[0]["title"] == "#7 Bug"


def test_http_json_dig_path():
    import watch_http_json
    assert watch_http_json.dig({"data": {"items": [1, 2]}}, "data.items") == [1, 2]
    assert watch_http_json.dig([1, 2], "") == [1, 2]
    assert watch_http_json.dig({"a": 1}, "missing.x") is None
