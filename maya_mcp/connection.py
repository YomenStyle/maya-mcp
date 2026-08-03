# -*- coding: utf-8 -*-
"""Maya 브릿지와의 JSON-RPC 2.0 연결.

프레이밍만 unreal-mcp-bridge 와 다릅니다. 저쪽은 개행 구분 + 64KB 제한이고,
이쪽은 4바이트 빅엔디안 길이 프리픽스입니다. Maya 는 씬 덤프처럼 큰 응답을
자주 돌려주기 때문에(테스트에서 420KB 왕복을 검증합니다) 64KB 제한을 쓸 수
없습니다. 메시지 형식과 에러 코드는 동일합니다.
"""

from __future__ import annotations

import itertools
import json
import socket
import struct
from typing import Any

from . import config


class MayaBridgeError(RuntimeError):
    """브릿지가 JSON-RPC 에러를 돌려주거나 연결에 실패했을 때."""

    def __init__(self, message: str, code: int | None = None, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


class MayaConnection:
    """요청마다 새 연결을 씁니다. localhost 라 비용이 무시할 수준이고,
    상태가 없어 재연결 로직이 필요 없습니다."""

    def __init__(self, host: str | None = None, port: int | None = None,
                 timeout: float | None = None):
        self.host = host or config.HOST
        self.port = port if port is not None else config.PORT
        self.timeout = timeout if timeout is not None else config.TIMEOUT
        self._ids = itertools.count(1)

    # -------------------------------------------------- 프레이밍

    @staticmethod
    def _recv_exact(sock: socket.socket, count: int) -> bytes:
        buf = b""
        while len(buf) < count:
            chunk = sock.recv(count - len(buf))
            if not chunk:
                raise MayaBridgeError("브릿지가 연결을 끊었습니다.")
            buf += chunk
        return buf

    # -------------------------------------------------- 호출

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """메서드 하나를 호출하고 `result` 를 돌려줍니다. 에러면 예외를 냅니다."""
        request = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": method,
            "params": {k: v for k, v in (params or {}).items() if v is not None},
        }
        payload = json.dumps(request, ensure_ascii=False).encode("utf-8")

        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.settimeout(self.timeout)
                sock.sendall(struct.pack(">I", len(payload)) + payload)
                (length,) = struct.unpack(">I", self._recv_exact(sock, 4))
                raw = self._recv_exact(sock, length)
        except MayaBridgeError:
            raise
        except OSError as exc:
            raise MayaBridgeError(
                f"Maya 브릿지({self.host}:{self.port})에 연결할 수 없습니다. "
                "Maya 를 켜고 MCP 셸프 버튼을 누르거나, 스크립트 에디터에서 "
                f"`import maya_mcp_bridge; maya_mcp_bridge.start()` 를 실행하세요. (원인: {exc})"
            ) from exc

        response = json.loads(raw.decode("utf-8"))
        if "error" in response and response["error"]:
            err = response["error"]
            raise MayaBridgeError(err.get("message", "알 수 없는 오류"),
                                  code=err.get("code"), data=err.get("data"))
        return response.get("result")
