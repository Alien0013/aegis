"""Agent loop, governance, compaction, permissions, marketplace, checkpoints, cron."""

from __future__ import annotations

from conftest import FakeProvider


def _agent(tmp_path, script=None, exec_mode="full"):
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session
    cfg = Config.load()
    cfg.data["tools"]["exec_mode"] = exec_mode
    return Agent(config=cfg, provider=FakeProvider(script), session=Session.create(), cwd=tmp_path)


# --- agent loop -------------------------------------------------------------
def test_loop_multi_tool_then_final(tmp_path):
    from aegis.types import LLMResponse, ToolCall
    script = [
        LLMResponse(text="", tool_calls=[ToolCall("c1", "list_dir", {"path": "."}),
                                         ToolCall("c2", "list_dir", {"path": "."})]),
        LLMResponse(text="all done"),
    ]
    agent = _agent(tmp_path, script)
    events = []
    out = agent.run("look around", events.append)
    assert out.content == "all done"
    assert sum(1 for e in events if e["type"] == "tool_result") == 2


def test_loop_budget_exhaustion_grace(tmp_path):
    from aegis.types import LLMResponse, ToolCall
    # always returns a tool call -> never finishes -> grace call
    forever = [LLMResponse(text="", tool_calls=[ToolCall(f"c{i}", "list_dir", {"path": "."})])
               for i in range(60)]
    agent = _agent(tmp_path, forever)
    agent.budget.max_iterations = 3
    events = []
    agent.run("loop", events.append)
    assert any(e["type"] == "budget_exhausted" for e in events)


def test_governance_normalizes():
    from aegis.agent import governance
    from aegis.types import Message, ToolCall
    msgs = [
        Message.user("hi"),
        Message.assistant("", [ToolCall("c1", "t", {})]),  # missing result
        Message.tool("ghost", "t", "orphan"),               # orphan
    ]
    out = governance.normalize(msgs)
    ids = [(m.role, m.tool_call_id) for m in out]
    assert ("tool", "ghost") not in ids
    assert ("tool", "c1") in ids


def test_compaction_preserves_head_tail():
    from aegis.agent import compaction
    from aegis.types import Message
    msgs = [Message.system("s")] + [Message.user(f"u{i}") if i % 2 == 0 else Message.assistant(f"a{i}")
                                    for i in range(40)]

    class P:
        def complete(self, *a, **k):
            from aegis.types import LLMResponse
            return LLMResponse(text="SUMMARY")
    out = compaction.compress(msgs, P(), preserve_first=2, preserve_last=6)
    assert any("SUMMARY" in (m.content or "") for m in out)
    assert len(out) < len(msgs)


# --- permissions ------------------------------------------------------------
def _eng(mode, **extra):
    from aegis.config import Config
    from aegis.tools.permissions import PermissionEngine
    c = Config.load()
    c.data["tools"]["exec_mode"] = mode
    c.data["tools"].update(extra)
    return PermissionEngine(c)


def test_perms_safe_tool_always_allowed():
    from aegis.tools.builtin import ReadFileTool
    from aegis.tools.base import ToolContext
    from aegis.tools.permissions import Decision
    assert _eng("deny").check(ReadFileTool(), {"path": "x"}, ToolContext()) == Decision.ALLOW


def test_perms_modes():
    from aegis.tools.builtin import BashTool
    from aegis.tools.base import ToolContext
    from aegis.tools.permissions import Decision
    ctx = ToolContext()
    assert _eng("full").check(BashTool(), {"command": "ls"}, ctx) == Decision.ALLOW
    assert _eng("deny").check(BashTool(), {"command": "ls"}, ctx) == Decision.DENY
    assert _eng("ask").check(BashTool(), {"command": "ls"}, ctx) == Decision.PROMPT
    assert _eng("allowlist", allowlist=["ls"]).check(BashTool(), {"command": "ls -la"}, ctx) == Decision.ALLOW
    assert _eng("allowlist").check(BashTool(), {"command": "rm x"}, ctx) == Decision.DENY


def test_perms_deny_groups():
    from aegis.tools.builtin import BashTool
    from aegis.tools.base import ToolContext
    from aegis.tools.permissions import Decision
    assert _eng("full", deny_groups=["runtime"]).check(
        BashTool(), {"command": "ls"}, ToolContext()) == Decision.DENY


def test_perms_hardline_even_in_full():
    from aegis.tools.builtin import BashTool
    from aegis.tools.base import ToolContext
    ok, reason = _eng("full").authorize(BashTool(), {"command": "rm -rf /"}, ToolContext())
    assert not ok and "hardline" in reason.lower()


# --- marketplace / checkpoints / cron --------------------------------------
def test_marketplace_local_install_and_scan(tmp_path):
    from aegis import marketplace
    d = tmp_path / "pack" / "ok-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: ok-skill\ndescription: fine.\n---\nbody")
    assert "ok-skill" in marketplace.install(str(tmp_path / "pack"))
    assert "ok-skill" in marketplace.installed()
    assert marketplace.remove("ok-skill")


def test_checkpoint_snapshot_rollback(tmp_path):
    from aegis.checkpoints import CheckpointStore
    f = tmp_path / "x.txt"
    f.write_text("v1")
    cs = CheckpointStore(tmp_path)
    cs.snapshot([str(f)], "e")
    f.write_text("v2")
    assert str(f) in cs.rollback() and f.read_text() == "v1"


def test_cron_schedule_parsing():
    from aegis.cron import CronJob, is_due
    import time
    now = time.time()
    assert is_due(CronJob("1", "1m", "p", last_run=now - 120), now)
    assert not is_due(CronJob("2", "1h", "p", last_run=now - 60), now)
    assert is_due(CronJob("3", "* * * * *", "p", last_run=now - 120), now)


def test_kanban_claim_is_atomic():
    from aegis.kanban import KanbanStore
    k = KanbanStore()
    t = k.create("task")
    assert k.claim(t.id, "w1")
    assert not k.claim(t.id, "w2")
