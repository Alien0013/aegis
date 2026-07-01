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
from .oauth_manager import get_mcp_oauth_manager, reset_mcp_oauth_manager_for_tests
from .startup import (
    background_mcp_discovery_error,
    claim_background_mcp_discovery,
    join_mcp_discovery,
    mcp_discovery_in_flight,
    reset_background_mcp_discovery_for_tests,
    start_background_mcp_discovery,
    wait_for_mcp_discovery,
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
    "get_mcp_oauth_manager",
    "reset_mcp_oauth_manager_for_tests",
    "background_mcp_discovery_error",
    "claim_background_mcp_discovery",
    "join_mcp_discovery",
    "mcp_discovery_in_flight",
    "reset_background_mcp_discovery_for_tests",
    "start_background_mcp_discovery",
    "wait_for_mcp_discovery",
]
