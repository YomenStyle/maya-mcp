# -*- coding: utf-8 -*-
"""
server.py — Maya MCP 서버 (PC 파이썬 3.10+ 에서 실행)

Maya 안에서 도는 maya_mcp_bridge.py 에 TCP(localhost)로 붙어서
MCP 툴로 노출합니다. 이 파일은 Maya 안에서 실행되지 않으므로
최신 라이브러리를 자유롭게 씁니다.

    python -m venv .venv
    .venv\\Scripts\\activate
    pip install -r requirements.txt
    python server.py
"""

from __future__ import annotations

import json
import os
import socket
import struct
from pathlib import Path
from typing import Any

try:                                                # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as _Server, Image
except ImportError:                                 # mcp 1.x (FastMCP 시절)
    from mcp.server.fastmcp import FastMCP as _Server, Image  # type: ignore[no-redef]


HOST = os.environ.get("MAYA_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MAYA_MCP_PORT", "20777"))
TIMEOUT = float(os.environ.get("MAYA_MCP_TIMEOUT", "180"))

mcp = _Server("maya")


# ---------------------------------------------------------------- 전송

class BridgeError(RuntimeError):
    pass


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise BridgeError("브릿지가 연결을 끊었습니다.")
        buf += chunk
    return buf


def _call(op: str, **params: Any) -> dict[str, Any]:
    """브릿지에 요청 하나를 보내고 응답을 받습니다. 요청마다 새 연결을 씁니다."""
    payload = json.dumps({"op": op, "params": params}, ensure_ascii=False).encode("utf-8")
    try:
        with socket.create_connection((HOST, PORT), timeout=TIMEOUT) as sock:
            sock.settimeout(TIMEOUT)
            sock.sendall(struct.pack(">I", len(payload)) + payload)
            (length,) = struct.unpack(">I", _recv_exact(sock, 4))
            raw = _recv_exact(sock, length)
    except (ConnectionRefusedError, OSError) as exc:
        raise BridgeError(
            f"Maya 브릿지({HOST}:{PORT})에 연결할 수 없습니다. "
            "Maya 스크립트 에디터에서 `import maya_mcp_bridge; maya_mcp_bridge.start()` "
            f"를 실행했는지 확인하세요. (원인: {exc})"
        ) from exc
    return json.loads(raw.decode("utf-8"))


def _fmt(resp: dict[str, Any]) -> str:
    """브릿지 응답을 모델이 읽기 좋은 텍스트로 정리합니다."""
    lines: list[str] = []
    if not resp.get("ok", True):
        lines.append("실패했습니다.")
        if resp.get("error"):
            lines.append(str(resp["error"]).rstrip())
    if resp.get("warning"):
        lines.append(f"경고: {resp['warning']}")
    if resp.get("stdout"):
        lines.append("--- 출력 ---")
        lines.append(str(resp["stdout"]).rstrip())
    if resp.get("value") is not None:
        lines.append("--- 결과 ---")
        value = resp["value"]
        if isinstance(value, str):
            lines.append(value)
        else:
            lines.append(json.dumps(value, ensure_ascii=False, indent=2))
    if not lines:
        lines.append("완료했습니다. (반환값 없음)")
    return "\n".join(lines)


# ---------------------------------------------------------------- L0: 코드 실행

@mcp.tool()
def maya_execute(code: str, undo_chunk: bool = True) -> str:
    """Maya 안에서 임의의 Python 코드를 실행합니다. Maya 로 되는 모든 작업의 통로입니다.

    다른 툴로 안 되는 작업(리깅, UV, 디포머, 커스텀 노드, Bifrost 등)은
    전부 이 툴로 하세요. 여러 작업을 한 번에 묶어 보내는 편이 툴을 여러 번
    호출하는 것보다 훨씬 빠르고 저렴합니다.

    사용 가능한 이름: `cmds` (maya.cmds), `mel` (maya.mel), `om` (maya.api.OpenMaya).

    반환 규약:
      - 코드가 단일 표현식이면 그 값이 결과가 됩니다.
        예) `cmds.ls(selection=True)`
      - 여러 줄이면 `result` 변수에 담긴 값이 결과가 됩니다.
        예) `objs = cmds.ls(type="mesh"); result = len(objs)`
      - print 출력과 예외 트레이스백도 함께 돌려줍니다. 실패하면 에러를 읽고 고치세요.

    Args:
        code: 실행할 Python 코드.
        undo_chunk: True 면 실행 전체를 하나의 undo 단위로 묶습니다.
            사용자가 Ctrl+Z 한 번으로 되돌릴 수 있으므로 기본값을 유지하세요.
    """
    try:
        return _fmt(_call("execute", code=code, undo_chunk=undo_chunk))
    except BridgeError as exc:
        return str(exc)


# ---------------------------------------------------------------- L1: 자기탐색

@mcp.tool()
def maya_search_commands(pattern: str = "", limit: int = 80) -> str:
    """이름에 특정 문자열이 들어간 maya.cmds 명령을 찾습니다.

    쓸 명령 이름이 확실하지 않을 때 추측하지 말고 먼저 이걸 호출하세요.
    예) pattern="bevel", "skin", "uv", "constraint"

    Args:
        pattern: 부분 일치 검색어(대소문자 무시). 비우면 전체 목록.
        limit: 반환할 최대 개수.
    """
    try:
        return _fmt(_call("search", pattern=pattern, limit=limit))
    except BridgeError as exc:
        return str(exc)


@mcp.tool()
def maya_command_help(name: str) -> str:
    """maya.cmds 명령 하나의 플래그 목록과 시그니처를 가져옵니다.

    플래그 이름이나 인자 형식이 불확실할 때 코드를 짜기 전에 호출하세요.
    Maya 버전마다 플래그가 달라서 기억에 의존하면 자주 틀립니다.

    Args:
        name: 명령 이름. 예) "polyBevel", "skinCluster", "polyProjection"
    """
    try:
        return _fmt(_call("help", name=name))
    except BridgeError as exc:
        return str(exc)


# ---------------------------------------------------------------- L3: 안전망

@mcp.tool()
def maya_viewport_capture(width: int = 960, height: int = 540,
                          ornaments: bool = False) -> Any:
    """현재 뷰포트를 이미지로 캡처해서 돌려줍니다.

    모델링·배치·라이팅처럼 결과가 눈에 보이는 작업을 한 뒤에는 이걸 호출해서
    의도대로 됐는지 직접 확인하세요. 확인 없이 다음 단계로 넘어가면
    잘못된 상태 위에 작업이 쌓입니다.

    Args:
        width: 캡처 가로 픽셀.
        height: 캡처 세로 픽셀.
        ornaments: True 면 HUD/기즈모 등 화면 장식을 포함합니다.
    """
    try:
        resp = _call("capture", width=width, height=height, ornaments=ornaments)
    except BridgeError as exc:
        return str(exc)

    value = resp.get("value") or {}
    if not resp.get("ok", True) or value.get("error"):
        return _fmt(resp) if not resp.get("ok", True) else str(value.get("error"))

    path = Path(value["path"])
    if not path.exists():
        return f"캡처 파일을 찾을 수 없습니다: {path}"

    return Image(data=path.read_bytes(), format="png")


@mcp.tool()
def maya_undo(steps: int = 1) -> str:
    """직전 작업을 되돌립니다.

    maya_execute 로 한 작업이 잘못됐을 때 호출하세요. maya_execute 는 기본적으로
    호출 하나를 undo 한 단계로 묶으므로, steps=1 이면 마지막 호출 전체가 취소됩니다.

    Args:
        steps: 되돌릴 단계 수 (1~50).
    """
    try:
        return _fmt(_call("undo", steps=steps))
    except BridgeError as exc:
        return str(exc)


# ---------------------------------------------------------------- 편의

@mcp.tool()
def maya_scene_info() -> str:
    """현재 씬의 요약 정보를 가져옵니다.

    작업을 시작하기 전에 한 번 호출해서 단위계, 업 축, 프레임 범위, 선택 상태,
    노드 종류별 개수를 파악하세요. 특히 단위와 업 축을 모르고 좌표를 지정하면
    엉뚱한 위치에 배치됩니다.
    """
    try:
        return _fmt(_call("scene_info"))
    except BridgeError as exc:
        return str(exc)


@mcp.tool()
def maya_ping() -> str:
    """Maya 브릿지가 살아있는지, 어떤 버전인지 확인합니다.

    다른 툴이 연결 오류를 낼 때 원인을 좁히는 용도로 쓰세요.
    """
    try:
        return _fmt(_call("ping"))
    except BridgeError as exc:
        return str(exc)


if __name__ == "__main__":
    mcp.run()
