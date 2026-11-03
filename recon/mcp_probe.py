"""Exercise the MCP server over a real stdio session, exactly as a harness would."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mcp import types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def main() -> int:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "eclass_api", "mcp"],
        cwd=str(Path(__file__).resolve().parents[1]),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            info = init.server_info
            print(f"server   : {info.name} v{info.version}")
            print(f"protocol : {init.protocol_version}")
            print(f"instr    : {(init.instructions or '')[:90]}…")

            tools = await session.list_tools()
            print(f"\ntools ({len(tools.tools)}):")
            for tool in tools.tools:
                print(f"  • {tool.name:<22} {(tool.description or '')[:70]}")

            print("\ncalling whoami with no account configured:")
            result = await session.call_tool("whoami", {})
            for block in result.content:
                if isinstance(block, types.TextContent):
                    print(f"  -> {block.text[:200]}")
            print(f"  isError={result.is_error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
