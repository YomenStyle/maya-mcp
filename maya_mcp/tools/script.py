# -*- coding: utf-8 -*-
"""코드 실행과 되돌리기 (L0 · L3)."""

from __future__ import annotations

from ..connection import MayaConnection


def register_script_tools(mcp, conn: MayaConnection) -> None:

    @mcp.tool()
    def maya_execute(code: str, undo_chunk: bool = True) -> dict:
        """Maya 안에서 임의의 Python 코드를 실행합니다. Maya 로 되는 모든 작업의 통로입니다.

        전용 툴이 없는 작업(리깅, UV, 디포머, 커스텀 노드, Bifrost 등)은 전부 이
        툴로 하세요. 여러 작업을 한 번에 묶어 보내는 편이 툴을 여러 번 호출하는
        것보다 훨씬 빠르고 저렴합니다.

        사용 가능한 이름: `cmds` (maya.cmds), `mel` (maya.mel), `om` (maya.api.OpenMaya).

        반환 규약:
          - 코드가 단일 표현식이면 그 값이 `value` 가 됩니다. 예) `cmds.ls(selection=True)`
          - 여러 줄이면 `result` 변수에 담긴 값이 `value` 가 됩니다.
          - `stdout` 에 print 출력이, 실패하면 `error` 에 트레이스백이 담깁니다.
            실패 시 에러를 읽고 고쳐서 다시 시도하세요.

        Args:
            code: 실행할 Python 코드.
            undo_chunk: True 면 실행 전체를 하나의 undo 단위로 묶습니다. 사용자가
                Ctrl+Z 한 번으로 되돌릴 수 있으므로 기본값을 유지하세요.
        """
        if not code or not code.strip():
            raise ValueError("code 가 비어 있습니다.")
        return conn.call("script.execute", {"code": code, "undo_chunk": undo_chunk})

    @mcp.tool()
    def maya_undo(steps: int = 1) -> dict:
        """직전 작업을 되돌립니다.

        maya_execute 로 한 작업이 잘못됐을 때 호출하세요. maya_execute 는 기본적으로
        호출 하나를 undo 한 단계로 묶으므로, steps=1 이면 마지막 호출 전체가 취소됩니다.

        Args:
            steps: 되돌릴 단계 수 (1~50).
        """
        if steps < 1:
            raise ValueError("steps 는 1 이상이어야 합니다.")
        return conn.call("script.undo", {"steps": steps})
