# -*- coding: utf-8 -*-
"""뷰포트 캡처 (L3). AI 가 자기 작업 결과를 눈으로 검증하게 해줍니다."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..connection import MayaConnection


def register_viewport_tools(mcp, conn: MayaConnection, image_cls) -> None:

    @mcp.tool()
    def maya_viewport_capture(width: int = 960, height: int = 540,
                              ornaments: bool = False) -> Any:
        """현재 뷰포트를 이미지로 캡처해서 돌려줍니다.

        모델링·배치·라이팅·LOD 처럼 결과가 눈에 보이는 작업을 한 뒤에는 이걸
        호출해서 의도대로 됐는지 직접 확인하세요. 수치 지표가 전부 정상이어도
        형태가 깨져 있을 수 있습니다. 확인 없이 다음 단계로 넘어가면 잘못된 상태
        위에 작업이 쌓입니다.

        Args:
            width: 캡처 가로 픽셀.
            height: 캡처 세로 픽셀.
            ornaments: True 면 HUD/기즈모 등 화면 장식을 포함합니다.
        """
        result = conn.call("viewport.capture",
                           {"width": width, "height": height, "ornaments": ornaments})

        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError(result["error"])

        path = Path((result or {}).get("path", ""))
        if not path.name or not path.exists():
            raise RuntimeError(f"캡처 파일을 찾을 수 없습니다: {path}")
        return image_cls(data=path.read_bytes(), format="png")
