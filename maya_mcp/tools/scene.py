# -*- coding: utf-8 -*-
"""씬 상태 조회."""

from __future__ import annotations

from ..connection import MayaConnection


def register_scene_tools(mcp, conn: MayaConnection) -> None:

    @mcp.tool()
    def maya_scene_info() -> dict:
        """현재 씬의 요약 정보를 가져옵니다.

        작업을 시작하기 전에 한 번 호출해서 단위계, 업 축, 프레임 범위, 선택 상태,
        노드 종류별 개수를 파악하세요. 특히 단위와 업 축을 모르고 좌표를 지정하면
        엉뚱한 위치에 배치됩니다.
        """
        return conn.call("scene.info")

    @mcp.tool()
    def maya_ping() -> dict:
        """Maya 브릿지가 살아있는지, 어떤 버전인지 확인합니다.

        다른 툴이 연결 오류를 낼 때 원인을 좁히는 용도로 쓰세요.
        `undo_enabled` 가 false 면 되돌리기 안전망이 없는 상태입니다.
        """
        return conn.call("scene.ping")
