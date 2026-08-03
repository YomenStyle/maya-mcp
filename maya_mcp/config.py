# -*- coding: utf-8 -*-
"""설정. 모든 환경변수는 `MMCP_` 접두사를 씁니다 (unreal-mcp-bridge 의 `UMCP_` 와 동형)."""

from __future__ import annotations

import os


def _env(name: str, default: str) -> str:
    """MMCP_* 를 우선하고, 예전 MAYA_MCP_* 도 계속 받아줍니다."""
    return os.environ.get("MMCP_" + name) or os.environ.get("MAYA_MCP_" + name) or default


HOST: str = _env("HOST", "127.0.0.1")
PORT: int = int(_env("PORT", "20777"))
TIMEOUT: float = float(_env("TIMEOUT", "180"))
