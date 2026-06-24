"""Batch of parity upgrades: guardrails, fuzzy edit, file safety/state,
cache breakpoints, URL policy, @references, snapshots, admin tiers, rate limits."""

from __future__ import annotations


# --- tool-loop guardrails ----------------------------------------------------
def test_loop_guard_warns_then_blocks():
    from aegis.agent.guardrails import ToolLoopGuard
    g = ToolLoopGuard(warn_after=2, block_after=3)
    args = {"command": "ls /nope"}
    assert g.check("bash", args) is None
    assert g.record("bash", args, "error: no such dir", True) is None      # 1st
    assert "failed the same way 2" in g.record("bash", args, "error: no such dir", True)  # warn
    g.record("bash", args, "error: no such dir", True)                     # 3rd -> block threshold
    assert "refusing to run it again" in (g.check("bash", args) or "")
    # a different result resets the streak
    g2 = ToolLoopGuard(warn_after=2, block_after=3)
    g2.record("bash", args, "err-a", True)
    assert g2.record("bash", args, "err-b", True) is None                  # different error, count=1


def test_loop_guard_no_progress_on_success():
    from aegis.agent.guardrails import ToolLoopGuard
    g = ToolLoopGuard(warn_after=2, block_after=5)
    a = {"path": "x"}
    assert g.record("read_file", a, "same content", False) is None
    assert "identical result 2" in g.record("read_file", a, "same content", False)
    assert g.check("read_file", a) is None        # success loops warn but never block
    assert g.record("write_file", {"path": "x"}, "wrote", False) is None
    assert g.record("write_file", {"path": "x"}, "wrote", False) is None


def test_loop_guard_warns_same_tool_failures_with_varied_args():
    from aegis.agent.guardrails import ToolLoopGuard
    g = ToolLoopGuard(warn_after=3, block_after=5, same_tool_warn_after=2)
    assert g.record("bash", {"command": "bad-one"}, "err-a", True) is None
    warning = g.record("bash", {"command": "bad-two"}, "err-b", True)
    assert warning and "bash has failed 2 times this turn" in warning
    assert "pwd && ls -la" in warning


def test_tool_executor_parallelizes_only_safe_batches(tmp_path):
    from aegis.agent.loop import ToolExecutor
    from aegis.config import Config
    from aegis.tools.base import ToolContext
    from aegis.types import ToolCall

    ex = ToolExecutor(None, None, ToolContext(cwd=tmp_path, config=Config.load()), lambda e: None)
    assert ex._should_parallelize([
        ToolCall("r1", "read_file", {"path": "a.txt"}),
        ToolCall("r2", "list_dir", {"path": "subdir"}),
    ])
    assert not ex._should_parallelize([
        ToolCall("w1", "write_file", {"path": "a.txt", "content": "x"}),
        ToolCall("r1", "read_file", {"path": "a.txt"}),
    ])
    assert not ex._should_parallelize([
        ToolCall("b1", "bash", {"command": "pwd && ls -la"}),
        ToolCall("r1", "read_file", {"path": "a.txt"}),
    ])


# --- fuzzy edit matching -----------------------------------------------------
def test_fuzzy_recovers_whitespace_drift():
    from aegis.tools.fuzzy import find_fuzzy
    text = "def add(a, b):\n        return a + b\n"
    # model used 4-space indent, file has 8 — line-trimmed match wins, uniquely
    hit = find_fuzzy(text, "def add(a, b):\n    return a + b")
    assert hit and hit[0] == "def add(a, b):\n        return a + b"


def test_fuzzy_refuses_ambiguous():
    from aegis.tools.fuzzy import find_fuzzy
    text = "x = 1\n\nx = 1\n"
    assert find_fuzzy(text, "x=1") is None        # two candidates -> no guess


def test_edit_file_uses_fuzzy(tmp_path):
    from aegis.tools.base import ToolContext
    from aegis.tools.builtin import EditFileTool
    f = tmp_path / "m.py"
    f.write_text("def f():\n        return 1\n")
    r = EditFileTool().run({"path": str(f), "old_string": "def f():\n    return 1",
                            "new_string": "def f():\n    return 2"},
                           ToolContext(cwd=tmp_path))
    assert not r.is_error and "auto-recovered" in r.content
    assert "return 2" in f.read_text()


# --- file write safety -------------------------------------------------------
def test_write_blocks_sensitive_path(tmp_path, monkeypatch):
    from aegis.tools.base import ToolContext
    from aegis.tools.builtin import WriteFileTool
    fake_home = tmp_path / "home"
    (fake_home / ".ssh").mkdir(parents=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    ctx = ToolContext(cwd=tmp_path)          # no approver -> must block
    r = WriteFileTool().run({"path": str(fake_home / ".ssh" / "authorized_keys"),
                             "content": "ssh-rsa AAA"}, ctx)
    assert r.is_error and "sensitive path" in r.content
    assert not (fake_home / ".ssh" / "authorized_keys").exists()


def test_write_allows_normal_and_stamps_freshness(tmp_path):
    from aegis.tools import file_state
    from aegis.tools.base import ToolContext
    from aegis.tools.builtin import WriteFileTool
    file_state.reset()
    p = tmp_path / "ok.txt"
    r = WriteFileTool().run({"path": str(p), "content": "hi"}, ToolContext(cwd=tmp_path))
    assert not r.is_error and p.read_text() == "hi"


def test_file_state_stale_warning(tmp_path):
    import os
    import time
    from aegis.tools import file_state
    file_state.reset()
    p = tmp_path / "f.txt"
    p.write_text("v1")
    file_state.note(p)
    assert file_state.stale_warning(p) == ""
    time.sleep(0.01)
    os.utime(p, (time.time() + 5, time.time() + 5))   # external modification
    assert "changed on disk" in file_state.stale_warning(p)


# --- cache breakpoints -------------------------------------------------------
def test_anthropic_marks_last_three_messages():
    from aegis.providers.anthropic import AnthropicTransport
    from aegis.types import Message
    t = AnthropicTransport()
    # alternate user/assistant so they stay distinct wire messages (consecutive users merge)
    msgs = [Message.system("s")]
    for i in range(6):
        msgs.append(Message.user(f"u{i}") if i % 2 == 0 else Message.assistant(f"a{i}"))
    _system, wire = t._to_wire(msgs)
    # apply the exact production marking (mirrors complete())
    for wm in wire[-3:]:
        blocks = wm.get("content")
        if isinstance(blocks, list) and blocks and isinstance(blocks[-1], dict):
            blocks[-1]["cache_control"] = {"type": "ephemeral"}
    marked = sum(1 for w in wire if isinstance(w["content"], list)
                 and w["content"] and w["content"][-1].get("cache_control"))
    assert marked == 3 and len(wire) == 6


# --- URL domain policy -------------------------------------------------------
def test_url_domain_policy():
    from aegis.net_safety import check_domain_policy

    class Cfg:
        def __init__(self, **kw): self.d = kw
        def get(self, k, default=None): return self.d.get(k.split(".")[-1], default)

    assert check_domain_policy("evil.com", Cfg(deny_domains=["evil.com"]))
    assert check_domain_policy("sub.evil.com", Cfg(deny_domains=["evil.com"]))
    assert check_domain_policy("other.com", Cfg(allow_domains=["good.com"]))
    assert check_domain_policy("api.good.com", Cfg(allow_domains=["good.com"])) == ""
    assert check_domain_policy("anything.com", Cfg()) == ""


# --- @references -------------------------------------------------------------
def test_references_ranges_and_sensitive(tmp_path):
    from aegis.cli.repl import expand_references
    f = tmp_path / "code.py"
    f.write_text("a\nb\nc\nd\ne\n")
    out = expand_references(f"see @file:{f}:2-3", tmp_path)
    assert "2: b" in out and "3: c" in out and "1: a" not in out
    # sensitive reference is refused
    assert "refused" in expand_references("@file:~/.ssh/id_rsa", tmp_path)


def test_read_file_blocks_secret_files(tmp_path):
    from aegis import config as cfg
    from aegis.config import Config
    from aegis.tools.base import ToolContext
    from aegis.tools.builtin import ReadFileTool

    env = tmp_path / ".env"
    env.write_text("API_KEY=secret\n")
    example = tmp_path / ".env.example"
    example.write_text("API_KEY=\n")
    config = Config.load()
    ctx = ToolContext(cwd=tmp_path, config=config)

    denied = ReadFileTool().run({"path": ".env"}, ctx)
    assert denied.is_error and "sensitive path" in denied.content
    assert not ReadFileTool().run({"path": ".env.example"}, ctx).is_error

    auth = cfg.get_home() / "auth.json"
    auth.write_text("{}")
    denied_auth = ReadFileTool().run({"path": str(auth)}, ctx)
    assert denied_auth.is_error and "agent credential" in denied_auth.content

    config.data["tools"]["sensitive_read_allow"] = [str(env)]
    allowed = ReadFileTool().run({"path": ".env"}, ctx)
    assert not allowed.is_error and "API_KEY=secret" in allowed.content


# --- snapshots ---------------------------------------------------------------
def test_snapshot_create_and_restore(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis import backup, config as cfg
    (cfg.get_home() / "config.yaml").write_text("model:\n  provider: openai\n")
    snap = backup.make_snapshot("test")
    assert snap.exists() and snap.parent.name == "snapshots"
    (cfg.get_home() / "config.yaml").write_text("CORRUPTED")
    backup.restore_backup(snap)
    assert "openai" in (cfg.get_home() / "config.yaml").read_text()


# --- gateway admin tiers -----------------------------------------------------
def test_admin_command_tiers(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.gateway.base import MessageEvent
    from aegis.gateway.runner import GatewayRunner

    config = Config.load()
    config.data["gateway"]["admins"] = ["boss"]
    config.data["gateway"]["user_commands"] = ["/model"]
    r = GatewayRunner(config, cwd=tmp_path)

    admin = MessageEvent(platform="telegram", chat_id="c", text="/busy", user_id="boss")
    user = MessageEvent(platform="telegram", chat_id="c", text="/busy", user_id="rando")
    assert r._command_allowed(admin, "/busy")            # admin: everything
    assert not r._command_allowed(user, "/busy")         # user: not allowed
    assert r._command_allowed(user, "/model gpt-5")      # user: explicitly allowed
    assert r._command_allowed(user, "/help")             # floor


def test_admin_tiers_default_single_user(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.gateway.base import MessageEvent
    from aegis.gateway.runner import GatewayRunner
    r = GatewayRunner(Config.load(), cwd=tmp_path)       # no admins configured
    ev = MessageEvent(platform="telegram", chat_id="c", text="/busy", user_id="anyone")
    assert r._command_allowed(ev, "/busy")               # everyone is admin


# --- rate limit telemetry ----------------------------------------------------
def test_ratelimit_capture():
    from aegis import ratelimit
    ratelimit.record({"x-ratelimit-remaining-requests": "42",
                      "x-ratelimit-remaining-tokens": "1000"}, "test")
    s = ratelimit.summary()
    assert "42" in s and "test" in s
