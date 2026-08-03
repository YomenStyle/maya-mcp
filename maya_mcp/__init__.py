# -*- coding: utf-8 -*-
"""maya-mcp — Maya 2022+ 용 MCP 서버 (PC 파이썬 쪽)."""

__version__ = "0.2.0"

from .connection import MayaBridgeError, MayaConnection

__all__ = ["MayaConnection", "MayaBridgeError", "__version__"]
