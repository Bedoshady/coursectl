"""Dump the exact public surface: HTTP routes and MCP tools."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx

from coursectl.api import create_app
from coursectl.config import Settings


async def http_surface(tmp: Path) -> None:
    app = create_app(
        Settings(
            database_path=tmp / "inv.db",
            secret_key_file=tmp / "k.key",
            download_dir=tmp / "dl",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://x") as c:
        spec = (await c.get("/openapi.json")).json()

    print("=" * 78)
    print(f"HTTP API — {len(spec['paths'])} paths")
    print("=" * 78)
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            params = []
            for p in op.get("parameters", []):
                req = "" if p.get("required") else "?"
                params.append(f"{p['name']}{req}")
            body = op.get("requestBody")
            if body:
                params.append("<json body>")
            tags = ",".join(op.get("tags", []))
            print(f"{method.upper():<6} {path:<42} [{tags}]")
            if params:
                print(f"       params: {', '.join(params)}")


async def mcp_surface() -> None:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "coursectl", "mcp"],
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools

    print()
    print("=" * 78)
    print(f"MCP server — {len(tools)} tools")
    print("=" * 78)
    for t in tools:
        schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        args = ", ".join(
            f"{name}{'' if name in required else '?'}: {spec.get('type', 'any')}"
            for name, spec in props.items()
        )
        print(f"{t.name}({args})")
        print(f"       {t.description}")


async def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    tmp = Path(__file__).resolve().parents[1] / "recon" / "_inv"
    tmp.mkdir(exist_ok=True)
    await http_surface(tmp)
    await mcp_surface()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
