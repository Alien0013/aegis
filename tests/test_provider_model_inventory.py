from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread


def _start_model_server(models: list[dict | str]):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.rstrip("/") != "/models":
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
