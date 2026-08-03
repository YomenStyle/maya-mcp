# -*- coding: utf-8 -*-
"""Maya API 자기탐색 (L1). 전용 툴이 없어도 AI 가 명령을 찾아 쓸 수 있게 합니다."""

from __future__ import annotations

from ..connection import MayaConnection


def register_inspection_tools(mcp, conn: MayaConnection) -> None:

    @mcp.tool()
    def maya_search_commands(pattern: str = "", limit: int = 80) -> dict:
        """이름에 특정 문자열이 들어간 maya.cmds 명령을 찾습니다.

        쓸 명령 이름이 확실하지 않을 때 추측하지 말고 먼저 이걸 호출하세요.
        예) pattern="bevel", "skin", "uv", "constraint"

        Args:
            pattern: 부분 일치 검색어(대소문자 무시). 비우면 전체 목록.
            limit: 반환할 최대 개수.
        """
        return conn.call("inspect.search_commands", {"pattern": pattern, "limit": limit})

    @mcp.tool()
    def maya_command_help(name: str) -> dict:
        """maya.cmds 명령 하나의 플래그 목록과 시그니처를 가져옵니다.

        플래그 이름이나 인자 형식이 불확실할 때 코드를 짜기 전에 호출하세요.
        Maya 버전마다 플래그가 달라서 기억에 의존하면 자주 틀립니다.

        Args:
            name: 명령 이름. 예) "polyBevel", "skinCluster", "polyProjection"
        """
        if not name:
            raise ValueError("name 이 필요합니다.")
        return conn.call("inspect.command_help", {"name": name})
