import asyncio

from fastmcp import Client

from munki_web_studio import STUDIO
from munki_web_studio.mcp import create_mcp


def test_mcp_server_starts_with_no_tools() -> None:
    async def run() -> list[str]:
        async with Client(create_mcp()) as client:
            return [tool.name for tool in await client.list_tools()]

    server = create_mcp()
    assert server.name == STUDIO.name
    assert asyncio.run(run()) == []
