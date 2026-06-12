"""The kanban agent tool: the model can manage its own task board."""

from __future__ import annotations


def _ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.tools.base import ToolContext
    return ToolContext(cwd=tmp_path, config=Config.load())


def test_kanban_tool_full_lifecycle(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)
    from aegis.tools.kanban_tool import KanbanTool
    t = KanbanTool()

    # empty board
    assert "empty" in t.run({"action": "list"}, ctx).content

    # create
    r = t.run({"action": "create", "title": "ship the dashboard", "body": "react editors"}, ctx)
    assert not r.is_error and "created" in r.content
    cid = r.content.split()[1].rstrip(":")

    # list shows it
    assert "ship the dashboard" in t.run({"action": "list"}, ctx).content
    # show details
    assert "react editors" in t.run({"action": "show", "id": cid}, ctx).content
    # move to in_progress
    assert "in_progress" in t.run({"action": "move", "id": cid, "status": "in_progress"}, ctx).content
    # comment
    assert not t.run({"action": "comment", "id": cid, "text": "halfway"}, ctx).is_error
    # complete
    assert not t.run({"action": "complete", "id": cid}, ctx).is_error
    # filter to done
    assert "ship the dashboard" in t.run({"action": "list", "filter_status": "done"}, ctx).content


def test_kanban_tool_validation(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)
    from aegis.tools.kanban_tool import KanbanTool
    t = KanbanTool()
    assert t.run({"action": "create"}, ctx).is_error              # no title
    assert t.run({"action": "move", "id": "x", "status": "bogus"}, ctx).is_error
    assert t.run({"action": "show", "id": "nope"}, ctx).is_error
    assert t.run({"action": "wat"}, ctx).is_error


def test_kanban_tool_registered():
    from aegis.tools.registry import default_registry
    assert default_registry().get("kanban") is not None


def test_slash_title_renames(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.cli.repl import handle_slash
    from aegis.config import Config
    from aegis.session import Session, SessionStore

    class StubAgent:
        def __init__(self):
            self.session = Session.create()
            self.cwd = tmp_path
            self.config = Config.load()
    a = StubAgent()
    handle_slash("/title my big refactor", a, store=SessionStore())
    assert a.session.title == "my big refactor"
    assert a.session.meta.get("title_locked") is True
