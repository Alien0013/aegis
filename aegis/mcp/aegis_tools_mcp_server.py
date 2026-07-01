"""Curated AEGIS tools MCP server for the Codex app-server runtime.

Codex already has native shell, file, and patch tools. This callback exposes a
stateless subset of AEGIS tools that Codex does not cover, mirroring Hermes'
``hermes-tools`` MCP server shape without handing Codex agent-loop tools that
require the live AEGIS loop context.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)


EXPOSED_TOOLS: tuple[str, ...] = (
    "web_search",
    "web_extract",
    "web_fetch",
    "browser_navigate",
    "browser_goto",
    "browser_open",
    "browser_click",
    "browser_type",
    "browser_fill",
    "browser_press",
    "browser_snapshot",
    "browser_scroll",
    "browser_back",
    "browser_go_back",
    "browser_get_images",
    "browser_console",
    "browser_vision",
    "vision_analyze",
    "image_generate",
    "generate_image",
    "skill_view",
    "skills_list",
    "text_to_speech",
    "speak",
    "audio_transcribe",
    "audio_analyze",
    "video_analyze",
    "video_generate",
    "kanban_complete",
    "kanban_block",
    "kanban_comment",
    "kanban_heartbeat",
    "kanban_show",
    "kanban_list",
    "kanban_create",
    "kanban_unblock",
    "kanban_link",
)


def _load_config() -> Any:
    from ..config import Config

    config = Config.load()
    # Make the callback server's model-visible inventory exactly the curated
    # set below while keeping each tool's normal permission/runtime behavior.
    config.data.setdefault("memory", {})["enabled"] = False
    config.data.setdefault("tools", {})["toolsets"] = ["all"]
    return config


def run_aegis_tools_mcp_server(config: Any | None = None) -> None:
    from .server import run_mcp_server

    os.environ.setdefault("AEGIS_QUIET", "1")
    os.environ.setdefault("AEGIS_REDACT_SECRETS", "true")
    run_mcp_server(
        config or _load_config(),
        visible_tool_names=set(EXPOSED_TOOLS),
        server_name="aegis-tools",
    )


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    verbose = "--verbose" in argv or "-v" in argv
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        stream=sys.stderr,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        run_aegis_tools_mcp_server()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.exception("aegis-tools MCP server crashed")
        sys.stderr.write(f"aegis-tools MCP server error: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
