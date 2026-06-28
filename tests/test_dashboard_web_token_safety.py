from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_web_api_client_uses_injected_session_and_ticket_helpers_not_persistent_url_tokens():
    api_ts = _src("web/src/lib/api.ts")

    assert "export function getSessionToken" in api_ts
    assert "export async function buildWsUrl" in api_ts
    assert "export function authedFetch" in api_ts
    assert "export function downloadUrl" in api_ts
    assert "auth/ws-ticket" in api_ts

    assert "localStorage.setItem(\"aegis_token\"" not in api_ts
    assert "localStorage.getItem(\"aegis_token\"" not in api_ts
    assert "url.searchParams.get(\"token\")" not in api_ts
    assert "token=${encodeURIComponent(TOKEN)}" not in api_ts


def test_dashboard_web_surfaces_do_not_append_long_lived_tokens_to_urls():
    command_palette = _src("web/src/components/CommandPalette.tsx")
    files_page = _src("web/src/pages/Files.tsx")
    chat_page = _src("web/src/pages/Chat.tsx")
    plugin_host = _src("web/src/plugins/host.tsx")
    graph_chat = _src("web/src/pages/GraphicalChat.tsx")

    assert "?token=${TOKEN}" not in command_palette
    assert "params.set(\"token\"" not in files_page
    assert "X-Aegis-Token" not in files_page
    assert "buildWsUrl" in chat_page
    assert "authWsTicket" not in chat_page
    assert "searchParams.set(\"token\"" not in plugin_host
    assert "mintWsTicketSync" in plugin_host
    assert "localStorage.getItem(\"aegis_token\"" not in graph_chat
    assert "authedFetch" in graph_chat
