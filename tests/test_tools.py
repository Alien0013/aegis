"""Built-in and extended tools."""

from __future__ import annotations


def _ctx(tmp_path):
    from aegis.config import Config
    from aegis.tools.base import ToolContext
    return ToolContext(cwd=tmp_path, config=Config.load())


def test_read_write_edit(tmp_path):
    from aegis.tools.builtin import EditFileTool, ReadFileTool, WriteFileTool
    ctx = _ctx(tmp_path)
    assert not WriteFileTool().run({"path": "a.txt", "content": "one\ntwo"}, ctx).is_error
    r = ReadFileTool().run({"path": "a.txt"}, ctx)
    assert "one" in r.content and "two" in r.content
    EditFileTool().run({"path": "a.txt", "old_string": "two", "new_string": "2"}, ctx)
    assert (tmp_path / "a.txt").read_text().endswith("2")


def test_edit_ambiguous_requires_replace_all(tmp_path):
    from aegis.tools.builtin import EditFileTool, WriteFileTool
    ctx = _ctx(tmp_path)
    WriteFileTool().run({"path": "b.txt", "content": "x x x"}, ctx)
    assert EditFileTool().run({"path": "b.txt", "old_string": "x", "new_string": "y"}, ctx).is_error
    assert not EditFileTool().run(
        {"path": "b.txt", "old_string": "x", "new_string": "y", "replace_all": True}, ctx).is_error


def test_list_glob_search(tmp_path):
    from aegis.tools.builtin import GlobTool, ListDirTool, SearchTool, WriteFileTool
    ctx = _ctx(tmp_path)
    WriteFileTool().run({"path": "src/main.py", "content": "def hello(): pass"}, ctx)
    assert "src" in ListDirTool().run({"path": "."}, ctx).content
    assert "main.py" in GlobTool().run({"pattern": "**/*.py"}, ctx).content
    assert "hello" in SearchTool().run({"pattern": "def hello"}, ctx).content


def test_todo_tool(tmp_path):
    from aegis.session import Session
    from aegis.tools.base import ToolContext
    from aegis.tools.builtin import TodoWriteTool
    from aegis.config import Config
    s = Session.create()
    ctx = ToolContext(cwd=tmp_path, config=Config.load(), session=s)
    TodoWriteTool().run({"todos": [{"content": "a", "status": "completed"},
                                   {"content": "b", "status": "pending"}]}, ctx)
    assert len(s.todos) == 2


def test_apply_patch(tmp_path):
    import shutil
    if not shutil.which("git"):
        return
    from aegis.tools.extra_builtin import ApplyPatchTool
    f = tmp_path / "h.txt"
    f.write_text("a\nb\nc\n")
    patch = "--- a/h.txt\n+++ b/h.txt\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n"
    assert not ApplyPatchTool().run({"patch": patch}, _ctx(tmp_path)).is_error
    assert "B" in f.read_text()


def test_schedule_task_tool(tmp_path):
    from aegis.tools.extra_builtin import ScheduleTaskTool
    from aegis.cron import CronStore
    res = ScheduleTaskTool().run({"schedule": "30m", "prompt": "check"}, _ctx(tmp_path))
    assert not res.is_error
    assert len(CronStore().list()) == 1


def test_bash_tool_runs(tmp_path):
    from aegis.tools.builtin import BashTool
    res = BashTool().run({"command": "echo hello-bash"}, _ctx(tmp_path))
    assert "hello-bash" in res.content and not res.is_error


def test_registry_has_full_surface():
    from aegis.tools.registry import default_registry
    names = {t.name for t in default_registry().all()}
    for expected in ("read_file", "write_file", "edit_file", "bash", "web_search", "web_fetch",
                     "memory", "skill", "execute_code", "browser", "lsp", "session_search",
                     "apply_patch", "spawn_subagent", "generate_image"):
        assert expected in names, expected
