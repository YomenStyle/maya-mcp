# -*- coding: utf-8 -*-
"""엔트리 포인트. 실제 구현은 `maya_mcp` 패키지에 있습니다.

기존에 등록해 둔 MCP 클라이언트 설정이 이 경로를 가리키고 있어 그대로 둡니다.

    python server.py

패키지를 설치했다면 `maya-mcp` 명령으로도 실행할 수 있습니다.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from maya_mcp.server import main

if __name__ == "__main__":
    main()
