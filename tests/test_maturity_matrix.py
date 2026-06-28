from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path

import httpx


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    cookies = kwargs.pop("cookies", None)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", cookies=cookies
    ) as client:
        return await client.request(method, path, **kwargs)


ROOT = Path(__file__).resolve().parents[1]


def test_maturity_matrix_covers_full_agent_architecture_without_live_overclaim():
    from aegis.maturity import (
        ARCHITECTURE_LAYER_IDS,
        LIVE_QA_TARGET_IDS,
        build_maturity_report,
    )

    report = build_maturity_report(ROOT)

    assert report["object"] == "aegis.maturity.report"
    assert report["ok"] is True
    assert report["summary"]["architecture_layers"] == 15
    assert report["summary"]["local_ready_layers"] == 15
    assert report["summary"]["live_targets"] >= 18
    assert report["summary"]["live_claimed_ready"] == 0
    assert report["summary"]["requires_credentials"] >= 10
    assert set(ARCHITECTURE_LAYER_IDS) == {row["id"] for row in report["architecture_layers"]}
    assert set(LIVE_QA_TARGET_IDS) == {row["id"] for row in report["live_qa_matrix"]}

    for row in report["architecture_layers"]:
        assert row["status"] == "local-ready", row
        assert row["source_paths"], row
        assert row["doc"].startswith("docs/"), row
        assert (ROOT / row["doc"]).is_file(), row
        assert row["local_proofs"], row
        assert row["local_proof_exists"], row
        assert all(row["local_proof_exists"].values()), row

    for target in report["live_qa_matrix"]:
        assert target["status"] in {"mocked-local", "requires-credentials", "manual-os-runner"}
        assert target["local_proof"], target
        assert target["live_proof_command"], target
        assert target["claims_live_ready"] is False
        serialized = json.dumps(target).lower()
        assert "secret_value" not in serialized
        assert "api_key_value" not in serialized


def _command_repo_paths(command: str) -> list[str]:
    paths: list[str] = []
    for token in shlex.split(command):
        if token.startswith(("tests/", "scripts/", "docs/", "desktop/", "web/")):
            paths.append(token)
    return paths


def test_maturity_live_qa_commands_are_runnable_or_manual_and_catalog_complete():
    from aegis.maturity import LIVE_QA_TARGETS

    targets = {target.id: target for target in LIVE_QA_TARGETS}
    for expected in {
        "api_server",
        "webhook",
        "homeassistant",
        "bluebubbles",
        "relay",
        "msgraph_webhook",
        "openrouter_provider",
        "groq_provider",
        "deepseek_provider",
        "xai_provider",
        "mistral_provider",
        "together_provider",
        "huggingface_provider",
        "qwen_provider",
        "nvidia_provider",
        "dashscope_provider",
        "cerebras_provider",
        "perplexity_provider",
        "fireworks_provider",
        "sambanova_provider",
    }:
        assert expected in targets

    for target in LIVE_QA_TARGETS:
        repo_paths = _command_repo_paths(target.live_proof_command)
        assert repo_paths, target
        for rel in repo_paths:
            assert (ROOT / rel).exists(), (target.id, rel)

    assert "EMAIL_PASSWORD" in targets["email"].credential_envs
    assert "MATTERMOST_BOT_TOKEN" in targets["mattermost"].credential_envs
    assert {"GEMINI_API_KEY", "GOOGLE_API_KEY"}.issubset(set(targets["google_provider"].credential_envs))


def test_maturity_local_proofs_resolve_under_plain_pytest():
    from aegis.maturity import build_maturity_report

    report = build_maturity_report(ROOT)
    unresolved = {
        row["id"]: [proof for proof, exists in row["local_proof_exists"].items() if not exists]
        for row in report["architecture_layers"]
    }
    unresolved = {key: value for key, value in unresolved.items() if value}
    assert unresolved == {}


def test_live_qa_matrix_is_not_healthy_when_command_paths_are_missing(tmp_path):
    from aegis.maturity import LiveQATarget, _live_row

    row = _live_row(
        LiveQATarget(
            "missing_live_stub",
            "gateway",
            "messaging",
            "tests/test_gateway_adapter_contract.py",
            "AEGIS_LIVE_MISSING=1 bash scripts/run_tests.sh tests/live/not-present.py",
            ("MISSING_TOKEN",),
        ),
        ROOT,
    )
    assert row["live_command_paths_exist"] == {"scripts/run_tests.sh": True, "tests/live/not-present.py": False}


def test_maturity_cli_outputs_json_and_check_summary(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.cli.main import main

    assert main(["maturity", "--json", "--check"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["summary"]["architecture_layers"] == 15
    assert payload["summary"]["live_claimed_ready"] == 0

    assert main(["maturity"]) == 0
    text = capsys.readouterr().out
    assert "AEGIS maturity report" in text
    assert "Architecture layers: 15/15 local-ready" in text
    assert "Live QA targets:" in text


def test_maturity_dashboard_route_is_explicit_and_secret_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_DASHBOARD_TOKEN", "secret-token-123")
    from aegis.config import Config
    from aegis.dashboard_fastapi import create_app

    app = create_app(Config.load())
    route_paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/maturity" in route_paths
    assert "/api/live-qa" in route_paths

    headers = {"X-Aegis-Token": "secret-token-123"}
    response = asyncio.run(_request(app, "GET", "/api/maturity", headers=headers))
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "aegis.maturity.report"
    assert body["ok"] is True
    assert body["summary"]["architecture_layers"] == 15
    assert "secret-token-123" not in json.dumps(body)

    live = asyncio.run(_request(app, "GET", "/api/live-qa", headers=headers))
    assert live.status_code == 200
    live_body = live.json()
    assert live_body["object"] == "aegis.live_qa.matrix"
    assert live_body["claimed_ready"] == 0


def test_maturity_docs_cover_remaining_gap_buckets_and_index_links():
    docs = {
        rel: (ROOT / rel).read_text(encoding="utf-8")
        for rel in [
            "docs/maturity.md",
            "docs/live-qa-matrix.md",
            "docs/operations-contracts.md",
            "docs/user-guide/configuration.md",
            "docs/user-guide/messaging.md",
            "docs/user-guide/cron.md",
            "docs/user-guide/sessions.md",
            "docs/user-guide/browser.md",
            "docs/user-guide/tts.md",
            "docs/user-guide/environment-variables.md",
            "docs/user-guide/docker.md",
            "docs/user-guide/hooks.md",
            "docs/user-guide/profile-distributions.md",
        ]
    }
    required_terms = [
        "runtime loop",
        "prompt context",
        "tool registry",
        "terminal processes",
        "memory layers",
        "session recovery",
        "skills lifecycle",
        "gateway adapter",
        "cron semantics",
        "delegation",
        "provider routing",
        "desktop dashboard",
        "security approvals",
        "extension ladder",
    ]
    maturity_text = docs["docs/maturity.md"].lower()
    for term in required_terms:
        assert term in maturity_text
    assert "does not claim live platform readiness" in docs["docs/live-qa-matrix.md"].lower()
    assert "credentialed smoke" in docs["docs/live-qa-matrix.md"].lower()
    assert "session lifecycle" in docs["docs/operations-contracts.md"].lower()

    index = (ROOT / "docs/index.md").read_text(encoding="utf-8")
    assert "maturity.md" in index
    assert "live-qa-matrix.md" in index
    assert "operations-contracts.md" in index
