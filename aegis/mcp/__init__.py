"""Model Context Protocol (MCP) client: connect to servers, expose their tools."""

from .client import (
    MCPClient,
    MCPManager,
    catalog,
    install_from_catalog,
    mcp_tools_from_config,
    probe_server,
    save_tool_checklist,
    tool_checklist,
)

__all__ = [
    "MCPClient",
    "MCPManager",
    "catalog",
    "install_from_catalog",
    "mcp_tools_from_config",
    "probe_server",
    "save_tool_checklist",
    "tool_checklist",
]
