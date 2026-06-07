"""Gateway queue, trajectory tooling, process tool, tool-gateway status, backends, channels."""

from __future__ import annotations


def test_delivery_queue_backoff():
    from aegis.gateway.queue import DeliveryQueue
    q = DeliveryQueue()
    q.enqueue("telegram", "chat1", "hello")
    due = q.due()
    assert len(due) == 1 and q.pending_count() == 1
    q.mark_failed(due[0]["id"], attempts=0)        # schedules a retry, still pending
    assert q.pending_count() == 1
    q.mark_sent(due[0]["id"])
    assert q.pending_count() == 0


def test_delivery_queue_gives_up_after_max():
    from aegis.gateway.queue import DeliveryQueue
    q = DeliveryQueue()
    q.enqueue("x", "c", "t")
    rid = q.due()[0]["id"]
    q.mark_failed(rid, attempts=4, max_attempts=5)  # 5th attempt -> failed
    assert q.pending_count() == 0


def test_trajectory_record_export_stats(tmp_path):
    from aegis import trajectory
    from aegis.session import Session, SessionStore
    from aegis.types import Message
    s = Session.create()
    s.messages = [Message.user("hi"), Message.assistant("hello there")]
    SessionStore().save(s)
    traj = trajectory.record(s.id)
    assert traj["n_steps"] == 2 and traj["approx_tokens"] > 0
    out = tmp_path / "t.jsonl"
    assert trajectory.export(str(out)) >= 1 and out.exists()
    assert trajectory.stats()["trajectories"] >= 1


def test_trajectory_compress_truncates():
    from aegis import trajectory
    traj = {"messages": [{"role": "tool", "content": "x" * 5000}], "approx_tokens": 1}
    out = trajectory.compress(traj, None)
    assert len(out["messages"][0]["content"]) < 600 and "truncated" in out["messages"][0]["content"]


def test_process_tool_lifecycle(tmp_path):
    import time
    from aegis.tools.base import ToolContext
    from aegis.tools.process import ProcessTool
    ctx = ToolContext(cwd=tmp_path)
    t = ProcessTool()
    res = t.run({"action": "start", "command": "sleep 5"}, ctx)
    pid_id = res.display.split()[-1]
    assert not res.is_error
    assert pid_id in t.run({"action": "list"}, ctx).content
    time.sleep(0.2)
    t.run({"action": "stop", "id": pid_id}, ctx)
    assert pid_id not in t.run({"action": "list"}, ctx).content


def test_tools_status():
    from aegis.config import Config
    from aegis.tools.cloud import tools_status
    st = tools_status(Config.load())
    assert "web_search" in st and "terminal_backend" in st


def test_singularity_backend_fails_closed(tmp_path, monkeypatch):
    import aegis.tools.backends as b
    monkeypatch.setattr(b.shutil, "which", lambda *_: None)   # no apptainer/singularity
    from aegis.config import Config
    out, code = b.run_command("echo hi", str(tmp_path), 10, "singularity", Config.load())
    assert code == 126 and "efus" in out


def test_email_adapter_requires_env(monkeypatch):
    for k in ("EMAIL_ADDRESS", "EMAIL_PASSWORD", "EMAIL_IMAP_HOST", "EMAIL_SMTP_HOST"):
        monkeypatch.delenv(k, raising=False)
    from aegis.gateway.email_channel import EmailAdapter
    import pytest
    with pytest.raises(RuntimeError):
        EmailAdapter()


def test_mcp_server_exposes_tools():
    # the server lists the registry's tools
    from aegis.tools.registry import default_registry
    assert len(default_registry().all()) >= 25


def test_github_tool_needs_gh(tmp_path, monkeypatch):
    import aegis.tools.devtools as dt
    monkeypatch.setattr(dt.shutil, "which", lambda *_: None)
    from aegis.tools.base import ToolContext
    res = dt.GithubTool().run({"action": "issues"}, ToolContext(cwd=tmp_path))
    assert res.is_error and "gh CLI" in res.content
