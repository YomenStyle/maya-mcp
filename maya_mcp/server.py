# -*- coding: utf-8 -*-
"""MCP 서버 조립. PC 파이썬 3.10+ 에서 실행됩니다 (Maya 내부가 아닙니다)."""

from __future__ import annotations

try:                                                # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as _Server, Image
except ImportError:                                 # mcp 1.x (FastMCP 시절)
    from mcp.server.fastmcp import FastMCP as _Server, Image  # type: ignore[no-redef]

from .connection import MayaConnection
from .tools import (
    register_inspection_tools,
    register_scene_tools,
    register_script_tools,
    register_unreal_tools,
    register_viewport_tools,
)


def build_server(conn: MayaConnection | None = None):
    """툴이 전부 등록된 MCP 서버를 만듭니다. 테스트에서도 이걸 씁니다."""
    conn = conn or MayaConnection()
    mcp = _Server("maya")

    register_script_tools(mcp, conn)
    register_scene_tools(mcp, conn)
    register_inspection_tools(mcp, conn)
    register_viewport_tools(mcp, conn, Image)
    register_unreal_tools(mcp, conn)
    return mcp


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
