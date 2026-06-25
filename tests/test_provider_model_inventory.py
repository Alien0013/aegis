from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread


def _start_model_server(
    models: list[dict | str],
    *,
    path: str = "/models",
    seen_paths: list[str] | None = None,
):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if seen_paths is not None:
                seen_paths.append(self.path)
            if self.path.rstrip("/") != path.rstrip("/"):
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps({"data": models}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def test_custom_provider_dict_models_are_picker_visible(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.providers import registry

    registry._LIVE_MODEL_CACHE.clear()
    cfg = Config.load()
    cfg.data["custom_providers"] = [{
        "name": "local-ollama",
        "base_url": "http://127.0.0.1:1",
        "default_model": "minimax-m2.7:cloud",
        "models": {
            "minimax-m2.7:cloud": {"context_length": 196608},
            "kimi-k2.5:cloud": {"context_length": 200000},
            "glm-5.1:cloud": {"context_length": 202752},
        },
    }]

    rows = registry.picker_model_entries_for("local-ollama", cfg)
    assert [row["id"] for row in rows] == [
        "minimax-m2.7:cloud",
        "kimi-k2.5:cloud",
        "glm-5.1:cloud",
    ]


def test_custom_provider_live_models_append_after_configured_models(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.dashboard import _dashboard_models
    from aegis.providers import registry

    registry._LIVE_MODEL_CACHE.clear()
    server, port = _start_model_server([
        {"id": "old-configured-model"},
        {"id": "new-live-model"},
    ])
    try:
        cfg = Config.load()
        cfg.data["model"] = {"provider": "crs-henkee", "default": "old-configured-model"}
        cfg.data["custom_providers"] = [{
            "name": "crs-henkee",
            "base_url": f"http://127.0.0.1:{port}",
            "model": "old-configured-model",
            "models": {
                "old-configured-model": {"context_length": 200000},
            },
        }]

        data = _dashboard_models(cfg)
    finally:
        server.shutdown()

    assert data["presets"]["crs-henkee"] == ["old-configured-model", "new-live-model"]
    rows = data["preset_rows"]["crs-henkee"]
    assert [row["source"] for row in rows] == ["default", "live"]
    provider_row = next(row for row in data["provider_catalog"] if row["name"] == "crs-henkee")
    assert provider_row["models"] == ["old-configured-model"]


def test_custom_provider_discover_models_false_skips_live_probe(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.providers import registry

    seen_paths: list[str] = []
    registry._LIVE_MODEL_CACHE.clear()
    server, port = _start_model_server(
        [{"id": "should-not-be-seen"}],
        seen_paths=seen_paths,
    )
    try:
        cfg = Config.load()
        cfg.data["model"] = {"provider": "baidu-coding", "default": "kimi-k2.5"}
        cfg.data["custom_providers"] = [{
            "name": "baidu-coding",
            "base_url": f"http://127.0.0.1:{port}",
            "discover_models": False,
            "models": {"kimi-k2.5": {}, "glm-5": {}},
            "model": "kimi-k2.5",
        }]

        rows = registry.picker_model_entries_for("baidu-coding", cfg)
    finally:
        server.shutdown()

    assert seen_paths == []
    assert [row["id"] for row in rows] == ["kimi-k2.5", "glm-5"]
    assert [row["source"] for row in rows] == ["default", "catalog"]


def test_two_custom_providers_with_overlap_both_keep_models(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.providers import registry

    registry._LIVE_MODEL_CACHE.clear()
    cfg = Config.load()
    cfg.data["models"] = {"live_fetch": False}
    cfg.data["custom_providers"] = [
        {
            "name": "proxy-a",
            "base_url": "http://127.0.0.1:1",
            "default_model": "shared/model",
            "models": ["a/only"],
        },
        {
            "name": "proxy-b",
            "base_url": "http://127.0.0.1:2",
            "default_model": "shared/model",
            "models": ["b/only"],
        },
    ]

    a_rows = registry.picker_model_entries_for("proxy-a", cfg)
    b_rows = registry.picker_model_entries_for("proxy-b", cfg)

    assert [row["id"] for row in a_rows] == ["shared/model", "a/only"]
    assert [row["id"] for row in b_rows] == ["shared/model", "b/only"]


def test_active_provider_base_url_override_used_for_live_models(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from aegis.config import Config
    from aegis.providers import registry

    registry._LIVE_MODEL_CACHE.clear()
    server, port = _start_model_server([{"id": "proxy-only-model"}])
    try:
        cfg = Config.load()
        cfg.data["model"] = {
            "provider": "openai",
            "default": "gpt-5.5",
            "base_url": f"http://127.0.0.1:{port}",
        }

        rows = registry.picker_model_entries_for("openai", cfg)
    finally:
        server.shutdown()

    assert any(row["id"] == "proxy-only-model" and row["source"] == "live" for row in rows)


def test_no_key_custom_provider_probes_v1_models_from_root_base_url(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.providers import registry

    seen_paths: list[str] = []
    registry._LIVE_MODEL_CACHE.clear()
    server, port = _start_model_server(
        [{"id": "llamacpp-live-model"}],
        path="/v1/models",
        seen_paths=seen_paths,
    )
    try:
        cfg = Config.load()
        cfg.data["model"] = {"provider": "local-root", "default": "local-model"}
        cfg.data["custom_providers"] = [{
            "name": "local-root",
            "base_url": f"http://127.0.0.1:{port}",
            "default_model": "local-model",
        }]

        rows = registry.picker_model_entries_for("local-root", cfg)
    finally:
        server.shutdown()

    assert "/models" in seen_paths
    assert "/v1/models" in seen_paths
    assert any(row["id"] == "llamacpp-live-model" and row["source"] == "live" for row in rows)


def test_dashboard_provider_probe_passes_base_url(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    import aegis.doctor as doctor
    from aegis.dashboard_fastapi import _provider_probe

    seen: dict[str, str] = {}

    def fake_probe(config):
        seen["provider"] = config.get("model.provider")
        seen["model"] = config.get("model.default")
        seen["base_url"] = config.get("model.base_url")
        seen["timeout"] = str(config.get("providers.probe_timeout_seconds"))
        return True, "ok sk-1234567890abcdef"

    monkeypatch.setattr(doctor, "probe_provider", fake_probe)

    result = _provider_probe(
        Config.load(),
        {
            "provider": "litellm-proxy",
            "model": "proxy-model",
            "base_url": "http://proxy.local/v1",
            "timeout_seconds": 2,
        },
    )

    assert result["ok"] is True
    assert result["detail"] == "ok [REDACTED]"
    assert result["timeout_seconds"] == 2
    assert seen == {
        "provider": "litellm-proxy",
        "model": "proxy-model",
        "base_url": "http://proxy.local/v1",
        "timeout": "2.0",
    }
    cached = Config.load().get("providers.probe_cache.litellm-proxy")
    assert cached["detail"] == "ok [REDACTED]"


def test_provider_capability_matrix_exposes_limits_pricing_and_flags(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from aegis.config import Config
    from aegis.providers import registry

    cfg = Config.load()
    cfg.data["model"] = {"provider": "openai", "default": "gpt-5.5"}
    cfg.data.setdefault("providers", {})["probe_cache"] = {
        "openai": {
            "ok": True,
            "status": "ready",
            "provider": "openai",
            "model": "gpt-5.5",
            "detail": "openai probe ok",
            "latency_ms": 42,
            "tested_at": "2026-06-24T00:00:00+00:00",
            "timeout_seconds": 3,
        }
    }

    matrix = registry.provider_capability_matrix(cfg, ["openai"])

    assert matrix["ok"] is True
    assert matrix["totals"]["providers"] == 1
    row = matrix["providers"][0]
    assert row["provider"] == "openai"
    assert row["auth"]["available"] is True
    assert row["probe"]["status"] == "ready"
    assert row["probe"]["live"] is True
    assert row["probe"]["latency_ms"] == 42
    assert row["last_probe"]["detail"] == "openai probe ok"
    assert row["limits"]["context"] >= 400_000
    assert row["limits"]["max_output"] >= 8192
    model = next(item for item in row["models"] if item["id"] == "gpt-5.5")
    assert model["capabilities"]["tools"] is True
    assert model["capabilities"]["reasoning"] is True
    # OpenAI (chat/responses) now advertises structured output (response_format / text.format).
    assert model["capabilities"]["structured_output"] is True
    assert model["pricing"]["known"] is True
    assert matrix["totals"]["audio"] >= 0


def test_provider_capability_matrix_exposes_fallback_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.providers import registry

    cfg = Config.load()
    cfg.data["model"] = {"provider": "localtest", "default": "local-main"}
    cfg.data["custom_providers"] = [
        {
            "name": "localtest",
            "base_url": "http://local.test/v1",
            "api_mode": "chat_completions",
            "context_length": 70_000,
        }
    ]
    cfg.data["fallback_providers"] = [{"provider": "ollama", "model": "llama3.1"}]

    matrix = registry.provider_capability_matrix(cfg, ["localtest", "ollama"])

    assert [row["provider"] for row in matrix["fallback_chain"]] == ["localtest", "ollama"]
    local = next(row for row in matrix["providers"] if row["provider"] == "localtest")
    ollama = next(row for row in matrix["providers"] if row["provider"] == "ollama")
    assert local["fallback_enabled"] is True
    assert ollama["fallback_enabled"] is True
    assert [row["provider"] for row in local["fallback_chain"]] == ["localtest", "ollama"]


def test_provider_probe_timeout_is_bounded(monkeypatch):
    from aegis.config import Config
    import aegis.doctor as doctor

    seen: dict[str, float] = {}

    class Provider:
        name = "fake"
        model = "fake-model"

        def complete(self, messages, **kwargs):
            seen["timeout"] = kwargs["timeout"]
            from aegis.types import LLMResponse
            return LLMResponse(text="ok")

    monkeypatch.setattr("aegis.providers.fallback.build_with_fallbacks", lambda config: Provider())
    cfg = Config.load()
    cfg.data.setdefault("providers", {})["probe_timeout_seconds"] = 999

    ok, detail = doctor.probe_provider(cfg)

    assert ok is True
    assert "fake/fake-model responded" in detail
    assert seen["timeout"] == 30.0
