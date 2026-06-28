"""Shared AEGIS setup/readiness payloads for dashboard, TUI, and desktop surfaces."""

from __future__ import annotations

from typing import Any

from .config import Config
from .providers import registry as provider_registry


def _as_mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _ready_from_totals(totals: dict[str, Any]) -> bool:
    raw = totals.get("ready")
    try:
        return int(raw if raw is not None else 0) > 0
    except (TypeError, ValueError):
        text = str(raw or "").strip().lower()
        return text not in {"", "0", "false", "none", "no"}


def setup_readiness_payload(config: Config, *, source: str = "api") -> dict[str, Any]:
    """Return a secret-free readiness summary shared by setup surfaces.

    The payload is deliberately passive: it reports configured provider/model state,
    local memory/gateway setup hints, and surface entry points without probing external
    providers or echoing credential material.
    """

    matrix = provider_registry.provider_capability_matrix(config)
    totals = _as_mapping(matrix.get("totals"))
    active = _as_mapping(matrix.get("active"))
    provider_ready = _ready_from_totals(totals)
    provider = str(active.get("provider") or active.get("name") or config.get("model.provider", "") or "")
    model = str(active.get("model") or config.get("model.default", "") or "")
    channels = _as_list(config.get("gateway.channels", []))
    memory_provider = str(config.get("memory.provider") or config.get("memory.backend") or "local")
    next_command = "aegis" if provider_ready else "aegis setup"

    return {
        "object": "aegis.setup.readiness",
        "ok": bool(provider_ready),
        "product": "AEGIS",
        "source": source,
        "provider_configured": bool(provider_ready),
        "provider": provider,
        "model": model,
        "next_command": next_command,
        "checks": [
            {
                "id": "provider",
                "label": "Provider auth",
                "ok": bool(provider_ready),
                "detail": f"{provider or 'provider'} / {model or 'model'}" if provider or model else "run aegis setup",
                "command": "aegis setup model" if not provider_ready else "aegis config show model",
            },
            {
                "id": "gateway",
                "label": "Gateway channels",
                "ok": bool(channels),
                "detail": ", ".join(channels) if channels else "no messaging channels enabled",
                "command": "aegis setup gateway",
            },
            {
                "id": "memory",
                "label": "Memory backend",
                "ok": bool(memory_provider),
                "detail": memory_provider,
                "command": "aegis memory status",
            },
        ],
        "sections": {
            "provider": {
                "ready": bool(provider_ready),
                "provider": provider,
                "model": model,
                "totals": totals,
            },
            "gateway": {
                "channels": channels,
                "configured": bool(channels),
            },
            "memory": {
                "provider": memory_provider,
                "configured": bool(memory_provider),
            },
        },
        "surfaces": {
            "dashboard": {"route": "/api/setup/status", "alias": "/api/readiness"},
            "tui": {"slash_command": "/setup status"},
            "terminal": {"command": "aegis setup"},
            "desktop": {"command": "aegis desktop --doctor"},
        },
    }


def setup_status_summary(config: Config) -> dict[str, object]:
    """Compact legacy TUI summary kept stable for existing callers/tests."""

    payload = setup_readiness_payload(config, source="tui")
    return {
        "provider_configured": bool(payload["provider_configured"]),
        "provider": str(payload.get("provider") or ""),
        "model": str(payload.get("model") or ""),
    }


__all__ = ["setup_readiness_payload", "setup_status_summary"]
