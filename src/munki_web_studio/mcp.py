"""The MCP server. Tools are added by later slices; see docs/mcp.md."""

from fastmcp import FastMCP

from munki_web_studio.studio import STUDIO


def create_mcp() -> FastMCP:
    return FastMCP(name=STUDIO.name)
