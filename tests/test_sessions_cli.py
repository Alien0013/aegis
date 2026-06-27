from __future__ import annotations

import json


def _seed_session(session_id: str, title: str, *, messages: int = 2):
    from aegis.session import Session, SessionStore
    from aegis.types import Message

    session = Session(id=session_id, title=title)
    if messages:
        session.messages = [Message.user(f"hello from {title}"), Message.assistant(f"reply from {title}")]
    SessionStore().save(session)
    return session


def test_sessions_cli_browse_rename_and_delete(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    from aegis.cli.main import main
    from aegis.session import SessionStore

    _seed_session("sess-alpha", "Alpha Chat")
    _seed_session("sess-beta", "Beta Chat")

    assert main(["sessions", "browse", "--limit", "5"]) == 0
    out = capsys.readouterr().out
    assert "Alpha Chat" in out
    assert "Beta Chat" in out

    assert main(["sessions", "rename", "sess-alpha", "Renamed Alpha"]) == 0
    out = capsys.readouterr().out
    assert "renamed sess-alpha -> Renamed Alpha" in out
    renamed = SessionStore().load("sess-alpha")
    assert renamed is not None
    assert renamed.title == "Renamed Alpha"

    assert main(["sessions", "delete", "sess-beta"]) == 0
    out = capsys.readouterr().out
    assert "removed sess-beta" in out
    assert SessionStore().load("sess-beta") is None


def test_sessions_cli_export_and_stats_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    from aegis.cli.main import main

    _seed_session("sess-export-a", "Export A")
    _seed_session("sess-export-b", "Export B")
    out_path = tmp_path / "sessions.jsonl"

    assert main(["sessions", "export", str(out_path)]) == 0
    out = capsys.readouterr().out
    assert "exported 2 session(s)" in out
    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines()]
    assert {row["id"] for row in rows} == {"sess-export-a", "sess-export-b"}
    assert rows[0]["messages"]

    assert main(["sessions", "stats", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sessions"] == 2
    assert payload["messages"] == 4
    assert payload["user_facing_sessions"] == 2


def test_sessions_cli_prune_is_dry_run_until_confirmed(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    from aegis.cli.main import main
    from aegis.session import SessionStore

    _seed_session("sess-empty", "Empty Ghost", messages=0)
    _seed_session("sess-real", "Real Chat")

    assert main(["sessions", "prune"]) == 0
    out = capsys.readouterr().out
    assert "would prune 1 empty session(s)" in out
    assert SessionStore().load("sess-empty") is not None

    assert main(["sessions", "prune", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "pruned 1 empty session(s)" in out
    assert SessionStore().load("sess-empty") is None
    assert SessionStore().load("sess-real") is not None
