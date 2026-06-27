"""Compatibility helper for WhatsApp Cloud setup metadata."""

from __future__ import annotations

from aegis.platforms import BRIDGE_PLATFORM_DEFINITIONS


def setup_payload() -> dict:
    definition = BRIDGE_PLATFORM_DEFINITIONS.get("whatsapp_cloud", {})
    return {"name": "whatsapp_cloud", "known": bool(definition), "definition": dict(definition)}


__all__ = ["setup_payload"]
