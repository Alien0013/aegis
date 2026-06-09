"""SSRF guard, fuzzy edit recovery, and nearest-AGENTS.md (monorepo) upgrades."""

from __future__ import annotations



# --- SSRF guard (offline: literal IPs / locally-resolvable hosts) ----------
def test_ssrf_blocks_metadata_and_private():
    from aegis.net_safety import is_safe_url
    assert is_safe_url("http://169.254.169.254/latest/meta-data/")[0] is False   # AWS creds
    assert is_safe_url("http://metadata.google.internal/")[0] is False           # GCP host
    assert is_safe_url("http://127.0.0.1/")[0] is False                          # loopback
    assert is_safe_url("http://10.0.0.5/x")[0] is False                          # private
    assert is_safe_url("http://192.168.1.1/")[0] is False                        # private
    assert is_safe_url("file:///etc/passwd")[0] is False                         # bad scheme
    assert is_safe_url("http://100.100.100.200/")[0] is False                    # Alibaba metadata


def test_ssrf_metadata_blocked_even_when_private_allowed():
    from aegis.net_safety import is_safe_url

    class Cfg:
        def get(self, k, d=None): return True       # allow_private_urls = True
    # private becomes allowed, but cloud metadata stays blocked
    assert is_safe_url("http://10.0.0.5/", Cfg())[0] is True
    assert is_safe_url("http://169.254.169.254/", Cfg())[0] is False


def test_web_fetch_tool_refuses_metadata():
    from aegis.tools.builtin import WebFetchTool
    from aegis.tools.base import ToolContext
    r = WebFetchTool().run({"url": "http://169.254.169.254/"}, ToolContext())
    assert r.is_error and "blocked for safety" in r.content


# --- fuzzy edit recovery ----------------------------------------------------
def test_edit_file_fuzzy_hint(tmp_path):
    from aegis.tools.builtin import EditFileTool
    from aegis.tools.base import ToolContext
    (tmp_path / "f.py").write_text("def hello():\n    return  42\n")   # two spaces
    r = EditFileTool().run(
        {"path": "f.py", "old_string": "return 42", "new_string": "return 43"},
        ToolContext(cwd=tmp_path))
    assert r.is_error
    assert "Closest match" in r.content and "return  42" in r.content   # surfaces the real text


# --- nearest AGENTS.md (monorepo) ------------------------------------------
def test_nearest_agents_md_walks_up(tmp_path):
    from aegis.config import Workspace
    (tmp_path / "AGENTS.md").write_text("ROOT RULES")
    sub = tmp_path / "packages" / "foo"
    sub.mkdir(parents=True)
    assert "ROOT RULES" in Workspace(cwd=sub).rules()
    # a closer rule file wins
    (sub / "AGENTS.md").write_text("SUBPKG RULES")
    assert "SUBPKG RULES" in Workspace(cwd=sub).rules()


# --- model metadata ---------------------------------------------------------
def test_model_metadata_resolves_current_models():
    from aegis.model_meta import context_window
    assert context_window("claude-sonnet-4-6") == 1_000_000
    assert context_window("gpt-5.5") == 400_000
    assert context_window("gpt-4o") == 128_000
    assert context_window("gemini-2.5-pro") == 1_048_576
    assert context_window("totally-unknown-model") is None     # falls back to preset


def test_provider_uses_model_metadata_for_context():
    from aegis.config import Config
    from aegis.providers import build_provider
    c = Config.load()
    c.set("model.provider", "openai")
    c.set("model.default", "gpt-4o")        # smaller than the openai preset default
    assert build_provider(c).context_length == 128_000   # the real gpt-4o window, not the preset


# --- schema sanitizer -------------------------------------------------------
def test_schema_sanitizer_strips_annotations_keeps_structure():
    from aegis.providers.schema import sanitize
    out = sanitize({"type": ["string", "null"], "$schema": "x", "examples": [1],
                    "properties": {"a": {"type": "integer", "readOnly": True}}, "required": ["a"]})
    assert out["type"] == "string"                    # union normalized
    assert "$schema" not in out and "examples" not in out
    assert "readOnly" not in out["properties"]["a"]   # nested annotation dropped
    assert out["required"] == ["a"]                   # structure preserved


# --- system-prompt: agentic guidance + per-channel hints --------------------
def test_prompt_has_agentic_and_capability_guidance():
    from aegis.agent.context import ContextBuilder
    from aegis.config import Config
    p = ContextBuilder(Config.load()).build()
    assert "tool-use enforcement" in p                 # act, don't describe
    assert "WORKING artifact" in p                      # finish the job, no fabrication
    assert "aegis gateway --channels telegram" in p     # knows its own gateway
    assert "NEVER echo it back" in p                    # secrets rule


def test_platform_hint_only_when_on_a_channel():
    from aegis.agent.context import ContextBuilder
    from aegis.config import Config
    b = ContextBuilder(Config.load())
    assert "You are on Telegram" not in b.build()                       # REPL: no hint
    tg = b.build(platform="telegram")
    assert "You are on Telegram" in tg and "NO table" in tg             # Telegram: formatting hint
    assert "You are on Discord" in b.build(platform="discord")


# --- gateway MEDIA: native attachments --------------------------------------
def test_media_split_and_deliver():
    from aegis.gateway.base import BasePlatformAdapter, split_media
    clean, media = split_media("chart attached:\nMEDIA:/tmp/c.png\nbye")
    assert media == ["/tmp/c.png"] and "MEDIA:" not in clean and clean.startswith("chart")

    sent = []

    class Fake(BasePlatformAdapter):
        def send(self, chat_id, text): sent.append(("text", text))
        def send_media(self, chat_id, path, caption=""): sent.append(("media", path))

    Fake().deliver("c", "see files\nMEDIA:/tmp/a.png\nMEDIA:/tmp/b.pdf")
    assert sent == [("text", "see files"), ("media", "/tmp/a.png"), ("media", "/tmp/b.pdf")]


def test_media_hint_only_on_supporting_channels():
    from aegis.agent.context import PLATFORM_HINTS
    assert "MEDIA:/absolute/path" in PLATFORM_HINTS["telegram"]
    assert "MEDIA:/absolute/path" in PLATFORM_HINTS["discord"]
    assert "MEDIA:" not in PLATFORM_HINTS["signal"]      # not wired there -> don't promise it


# --- reasoning scrub + table rewrite (Hermes parity) ------------------------
def test_strip_reasoning_blocks():
    from aegis.agent.governance import strip_reasoning
    assert strip_reasoning("<think>plan\nmore</think>\n\nFinal: 42.") == "Final: 42."
    assert strip_reasoning("<Thinking>x</Thinking>done") == "done"
    assert strip_reasoning("no tags here") == "no tags here"          # untouched
    assert strip_reasoning("a <think>b</think> c") == "a  c"          # inline removed


def test_tableify_rewrites_pipe_tables():
    from aegis.gateway.base import tableify
    out = tableify("| Name | Role |\n|---|---|\n| Ann | dev |\n| Bob | ops |")
    assert "|" not in out
    assert "• Name: Ann — Role: dev" in out and "• Name: Bob — Role: ops" in out
    assert tableify("just prose, no pipes") == "just prose, no pipes"  # untouched


def test_chat_adapters_dont_render_tables():
    from aegis.gateway.channels import TelegramAdapter
    from aegis.gateway.discord_channel import DiscordAdapter
    assert TelegramAdapter.renders_tables is False
    assert DiscordAdapter.renders_tables is False


# --- gateway secret redaction + aux-model compaction (deep Hermes audit) ----
def test_redact_secrets_covers_telegram_and_keys():
    from aegis.redact import redact_secrets
    out = redact_secrets("tok 8588602695:AAGH8RDjbO_mJ6_F4l7XDiHKh26TXqNonwg and sk-proj-ABCDEFGHIJ1234567890")
    assert "8588602695" not in out and "sk-proj" not in out
    assert out.count("[REDACTED]") == 2
    assert redact_secrets("plain text") == "plain text"


def test_learn_redact_reuses_shared_module():
    from aegis import learn
    from aegis.redact import redact_secrets
    assert learn._redact is redact_secrets        # single source of truth


def test_compaction_uses_aux_provider_helper():
    # _summarizer caches a provider on the agent and falls back to the main provider
    from aegis.agent.loop import _summarizer

    class FakeAgent:
        provider = object()
        config = None
    a = FakeAgent()
    # build_aux_provider needs a real config; on failure it must fall back to agent.provider
    s = _summarizer(a)
    assert s is a.provider and a._aux_provider is s


# --- send_message tool (proactive channel push) -----------------------------
def test_send_message_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.tools.extra_builtin import SendMessageTool
    from aegis.tools.base import ToolContext
    from aegis.tools.registry import default_registry
    assert default_registry().get("send_message")               # registered

    assert SendMessageTool().run({"text": "hi"}, ToolContext()).is_error   # no channel -> error
    assert SendMessageTool().run({"text": ""}, ToolContext()).is_error      # empty -> error

    r = SendMessageTool().run(
        {"text": "leak sk-proj-ABCDEFGHIJ1234567890", "platform": "telegram", "chat_id": "7"},
        ToolContext())
    assert not r.is_error and "telegram:7" in r.content
    from aegis.gateway.queue import DeliveryQueue
    row = DeliveryQueue().due()[0]
    assert row["platform"] == "telegram" and "[REDACTED]" in row["text"]   # redacted on the way out


# --- ntfy push-notification channel -----------------------------------------
def test_ntfy_channel(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "aegis-alerts")
    from aegis.gateway.channels import build_adapter
    a = build_adapter("ntfy")
    assert a.name == "ntfy" and a.server == "https://ntfy.sh" and a.renders_tables is False

    import httpx
    sent = {}
    monkeypatch.setattr(httpx, "post",
                        lambda url, content=None, headers=None, timeout=None: sent.update(url=url, body=content))
    a.send("aegis-alerts", "done ✅")
    assert sent["url"] == "https://ntfy.sh/aegis-alerts" and sent["body"] == "done ✅".encode()


# --- cron one-shot scheduling (Hermes parity: agent/cron subsystem) ---------
def test_cron_oneshot_lifecycle(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import time
    from aegis.cron import CronStore, is_due, _parse_oneshot
    now = time.time()
    assert abs(_parse_oneshot("in 2h", now) - (now + 7200)) < 2
    assert _parse_oneshot("at 17:00", now) > now
    assert _parse_oneshot("every 5m", now) is None        # recurring, not one-shot

    s = CronStore()
    j = s.add("in 1h", "ping", "ntfy:alerts")
    assert j.run_at > now and not is_due(j, now)
    assert is_due(j, j.run_at + 1)                          # fires when its time arrives
    s.mark_run(j.id, j.run_at + 1)
    done = s.list()[0]
    assert done.enabled is False and not is_due(done, done.run_at + 999)   # one-shot, done

    r = CronStore().add("every 10m", "poll")
    assert r.run_at == 0.0 and is_due(r, now)               # recurring unaffected


# --- ACP tool-call streaming (Hermes parity: acp_adapter subsystem) ---------
def test_acp_streams_tool_calls():
    from aegis.acp import AcpServer
    srv = AcpServer.__new__(AcpServer)
    sent = []
    srv._notify = lambda method, params: sent.append(params["update"])
    srv._send_tool_call("s1", {"id": "c1", "name": "bash"}, status="in_progress")
    srv._send_tool_call("s1", {"id": "c1", "name": "bash", "summary": "ran ls"}, status="completed")
    srv._send_tool_call("s1", {"id": "c2", "name": "edit_file", "is_error": True}, status="completed")
    assert [u["sessionUpdate"] for u in sent] == ["tool_call"] * 3
    assert sent[0]["status"] == "in_progress"
    assert sent[1]["status"] == "completed" and sent[1]["title"] == "ran ls"
    assert sent[2]["status"] == "failed"          # tool error surfaces as failed


# --- gateway typing indicator (Hermes parity: gateway subsystem) ------------
def test_telegram_typing_indicator(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    from aegis.gateway.channels import TelegramAdapter
    a = TelegramAdapter(token="123:abc")
    calls = []
    a._api = lambda method, **kw: calls.append((method, kw))
    a._typing("42")
    assert calls == [("sendChatAction", {"chat_id": "42", "action": "typing"})]

    def boom(*a, **k):
        raise RuntimeError("net")
    a._api = boom
    a._typing("42")                  # best-effort: never blocks the reply


# --- run.py reverse-engineered: friendly errors + in-gateway cron -----------
def test_gateway_shape_reply():
    from aegis.gateway.replies import shape_reply, looks_like_provider_error
    assert looks_like_provider_error("[provider error] HTTP 404: not a chat model")
    assert "model provider" in shape_reply("[provider error] HTTP 500: upstream").lower()
    assert "rate-limit" in shape_reply("Error code: 429 too many requests").lower()
    assert "authentication" in shape_reply("HTTP 401 invalid api key").lower()
    prose = "HTTP 404 means not-found; here's how to debug: " + "x" * 400   # long prose passes through
    assert shape_reply(prose) == prose
    assert shape_reply("", api_calls=3).startswith("⚠️")     # worked but empty
    assert shape_reply("", api_calls=0) == "(no response)"


def test_cron_tick_and_gateway_sink(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.agent.agent as agentmod

    class _A:
        def run(self, p):
            class R:
                content = "done"
            return R()
    monkeypatch.setattr(agentmod.Agent, "create", staticmethod(lambda cfg, session=None: _A()))

    from aegis.cron import CronStore, tick
    s = CronStore()
    s.add("every 1s", "noop", "telegram:99")
    sent = []
    assert tick(None, sink=lambda ch, txt: sent.append((ch, txt)), store=s, verbose=False) == 1
    assert sent == [("telegram:99", "done")]

    from aegis.gateway.runner import GatewayRunner
    gr = GatewayRunner.__new__(GatewayRunner)
    cap = []
    gr.enqueue = lambda p, c, t: cap.append((p, c, t))
    gr._cron_sink("telegram:42", "hi")
    gr._cron_sink("no_colon", "ignored")            # malformed -> dropped
    assert cap == [("telegram", "42", "hi")]


# --- in-place status editing (run.py send_or_update_status) -----------------
def test_telegram_inplace_status(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    from aegis.gateway.channels import TelegramAdapter
    a = TelegramAdapter(token="123:abc")
    calls = []

    def fake_api(method, **kw):
        calls.append((method, kw))
        return {"result": {"message_id": 555}} if method == "sendMessage" else {}
    a._api = fake_api

    a._finish("42", 555, "short answer")                       # edited in place
    assert [m for m, _ in calls] == ["editMessageText"] and calls[0][1]["message_id"] == 555

    calls.clear(); a._finish("42", 555, "see\nMEDIA:/tmp/x.png")   # media -> drop bubble + deliver
    assert "deleteMessage" in [m for m, _ in calls] and "editMessageText" not in [m for m, _ in calls]

    calls.clear(); a._finish("42", 555, "L" * 5000)            # long -> drop + chunk
    ms = [m for m, _ in calls]
    assert ms[0] == "deleteMessage" and ms.count("sendMessage") >= 2

    calls.clear(); a._finish("42", 555, "")                    # empty -> just drop bubble
    assert [m for m, _ in calls] == ["deleteMessage"]

    calls.clear(); a._finish("42", 555, "| A | B |\n|---|---|\n| 1 | 2 |")   # table tableified on edit
    assert calls[0][0] == "editMessageText" and "•" in calls[0][1]["text"] and "|" not in calls[0][1]["text"]


# --- group-chat sender context (run.py observed-group context) --------------
def test_telegram_group_context():
    from aegis.gateway.channels import _with_group_context
    assert _with_group_context({"text": "hi", "chat": {"type": "private"}, "from": {"username": "tj"}}) == "hi"
    assert _with_group_context(
        {"text": "deploy", "chat": {"type": "supergroup"}, "from": {"username": "tj"}}) == "[tj]: deploy"
    assert _with_group_context(
        {"text": "yo", "chat": {"type": "group"}, "from": {"first_name": "Sam", "id": 9}}) == "[Sam]: yo"


# --- mid-run interruption + per-chat queue (run.py interruption handling) ----
def test_control_interrupt_detection():
    from aegis.gateway.base import is_control_interrupt
    for t in ["stop", "STOP", "/stop", "cancel", "abort!", " halt "]:
        assert is_control_interrupt(t)
    for t in ["stop the server", "cancel my subscription", "what is abort()", "hi"]:
        assert not is_control_interrupt(t)


def test_runner_interrupt_sets_cancel(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import threading
    from aegis.gateway.runner import GatewayRunner
    from aegis.gateway.base import MessageEvent
    gr = GatewayRunner.__new__(GatewayRunner)
    gr.session_mode = "per_channel"

    class FakeAgent:
        cancel_event = threading.Event()
    gr._agents = {"telegram:42": FakeAgent()}
    assert gr.interrupt(MessageEvent(platform="telegram", chat_id="42", text="stop")) is True
    assert gr._agents["telegram:42"].cancel_event.is_set()
    assert gr.interrupt(MessageEvent(platform="telegram", chat_id="99", text="stop")) is False


def test_telegram_per_chat_queue_orders_turns(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    import threading
    import time
    from aegis.gateway.channels import TelegramAdapter
    from aegis.gateway.base import MessageEvent
    a = TelegramAdapter(token="123:abc")
    seen = []
    a._typing = lambda c: None
    a._send_status = lambda c, t: None
    a._finish = lambda c, s, r: seen.append(r)
    a._dispatch = lambda ev: f"reply:{ev.text}"
    a._queues, a._workers, a._qlock = {}, {}, threading.Lock()
    for n in ["a", "b", "c"]:
        a._enqueue(MessageEvent(platform="telegram", chat_id="7", text=n))
    time.sleep(0.3)
    assert seen == ["reply:a", "reply:b", "reply:c"]      # single worker, ordered
