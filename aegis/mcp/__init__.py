"""Model Context Protocol (MCP) client: connect to servers, expose their tools."""

from .client import MCPClient, MCPManager, mcp_tools_from_config

__all__ = ["MCPClient", "MCPManager", "mcp_tools_from_config"]
