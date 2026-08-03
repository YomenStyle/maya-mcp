# -*- coding: utf-8 -*-
"""JSON-RPC 전송 계층과 툴 등록 테스트. PC 파이썬(3.10+)으로 실행합니다.

    python tests/test_server_protocol.py

Maya 없이 동작합니다. 브릿지와 동일한 와이어 프로토콜(4바이트 빅엔디안 길이
프리픽스 + UTF-8 JSON-RPC 2.0)을 말하는 목 서버를 띄워서, 클라이언트가 요청을
제대로 만들고 응답·에러를 제대로 처리하는지 검증합니다.
"""

import asyncio
import base64
import inspect
import json
import os
import socket
import socketserver
import struct
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maya_mcp.connection import MayaBridgeError, MayaConnection
from maya_mcp.server import build_server

# 1x1 투명 PNG
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
    "AAAABJRU5ErkJggg=="
)

_results = []
_seen_requests = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print("%s  %s%s" % ("PASS" if cond else "FAIL", name,
                        ("  <- " + str(detail)) if (detail and not cond) else ""))


# ------------------------------------------------------------ 목 브릿지

class _MockHandler(socketserver.BaseRequestHandler):
    def handle(self):
        header = self._recv(4)
        if header is None:
            return
        (length,) = struct.unpack(">I", header)
        req = json.loads(self._recv(length).decode("utf-8"))
        _seen_requests.append(req)
        resp = self.server.responder(req)
        payload = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        self.request.sendall(struct.pack(">I", len(payload)) + payload)

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.request.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf


class _MockServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_mock(responder):
    srv = _MockServer(("127.0.0.1", 0), _MockHandler)
    srv.responder = responder
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ------------------------------------------------------------ 응답 정의

CAPTURE_PATH = os.path.join(tempfile.gettempdir(), "mcp_test_capture.png")
BIG_TEXT = "가나다라마바사" * 20000          # ≈ 420KB UTF-8 → 다중 recv 필요


def _result(req, value):
    return {"jsonrpc": "2.0", "id": req.get("id"), "result": value}


def _error(req, code, message, data=None):
    return {"jsonrpc": "2.0", "id": req.get("id"),
            "error": {"code": code, "message": message, "data": data}}


def responder(req):
    method = req.get("method")
    params = req.get("params") or {}

    if method == "scene.ping":
        return _result(req, {"maya_version": "2022", "undo_enabled": True})
    if method == "scene.info":
        return _result(req, {"scene": "<untitled>", "counts": {"mesh": 3}})
    if method == "script.execute":
        code = params.get("code", "")
        if code == "BOOM":
            return _error(req, -32603, "실행 중 예외", "Traceback ...\nValueError: 터짐")
        if code == "BIG":
            return _result(req, {"ok": True, "value": BIG_TEXT, "stdout": ""})
        return _result(req, {"ok": True, "value": [1, 2, 3], "stdout": "출력됨\n"})
    if method == "script.undo":
        return _result(req, {"undone": params.get("steps", 1)})
    if method == "inspect.search_commands":
        return _result(req, {"total": 2, "commands": ["polyBevel", "polyCube"]})
    if method == "inspect.command_help":
        return _result(req, {"name": params.get("name"), "help": "Flags: -offset"})
    if method == "viewport.capture":
        return _result(req, {"path": CAPTURE_PATH, "width": 960, "height": 540})
    if method.startswith("unreal."):
        return _result(req, {"method": method, "params": params})
    return _error(req, -32601, "알 수 없는 메서드: %r" % method)


def list_tools(mcp):
    tools = mcp.list_tools()
    return asyncio.run(tools) if inspect.isawaitable(tools) else tools


class _ToolError:
    """인프로세스 call_tool 은 툴 예외를 그대로 올립니다. 와이어 프로토콜에서는
    같은 상황이 isError 결과로 변환되므로, 테스트에서는 둘을 같게 취급합니다."""

    def __init__(self, exc):
        self.exc = exc

    def __repr__(self):
        return "ToolError(%s: %s)" % (type(self.exc).__name__, self.exc)


def call_tool(mcp, name, **arguments):
    """공개 API 로 툴을 호출합니다. 내부 속성에 의존하지 않습니다."""
    try:
        result = mcp.call_tool(name, arguments)
        return asyncio.run(result) if inspect.isawaitable(result) else result
    except Exception as exc:
        return _ToolError(exc)


def is_error(result):
    if isinstance(result, _ToolError):
        return True
    return bool(getattr(result, "isError", False) or getattr(result, "is_error", False))


def image_bytes(result):
    """CallToolResult 안의 이미지 콘텐츠를 원본 바이트로 되돌립니다."""
    for block in (getattr(result, "content", None) or []):
        data = getattr(block, "data", None)
        if data and getattr(block, "type", "") == "image":
            return base64.b64decode(data)
    return None


# ------------------------------------------------------------ 테스트

def run():
    srv, port = start_mock(responder)
    conn = MayaConnection(port=port, timeout=15)
    mcp = build_server(conn)

    # --- 툴 등록 ------------------------------------------------------
    tools = mcp.list_tools()
    if inspect.isawaitable(tools):
        tools = asyncio.run(tools)
    names = sorted(t.name for t in tools)
    expected = sorted([
        "maya_execute", "maya_undo",
        "maya_scene_info", "maya_ping",
        "maya_search_commands", "maya_command_help",
        "maya_viewport_capture",
        "maya_unreal_check", "maya_unreal_check_skeleton", "maya_unreal_check_materials",
        "maya_unreal_prepare", "maya_unreal_cleanup_materials",
        "maya_unreal_make_lods", "maya_unreal_make_collision", "maya_unreal_export_fbx",
    ])
    check("툴 등록: %d개 전부" % len(expected), names == expected,
          "누락/추가: %s" % sorted(set(names) ^ set(expected)))
    check("툴 등록: 설명 비어있지 않음",
          all((t.description or "").strip() for t in tools),
          [t.name for t in tools if not (t.description or "").strip()])

    # --- JSON-RPC 요청 형식 ---------------------------------------------
    _seen_requests.clear()
    conn.call("scene.ping")
    req = _seen_requests[-1]
    check("JSON-RPC: jsonrpc 필드", req.get("jsonrpc") == "2.0", req)
    check("JSON-RPC: id 부여", req.get("id") is not None, req)
    check("JSON-RPC: 점 네임스페이스 메서드", req.get("method") == "scene.ping", req)
    check("JSON-RPC: id 는 호출마다 증가",
          conn.call("scene.ping") is not None and _seen_requests[-1]["id"] > req["id"],
          [r["id"] for r in _seen_requests])

    # --- None 인자는 params 에서 제거 -------------------------------------
    _seen_requests.clear()
    conn.call("unreal.check", {"objects": None, "prefix": "SM_"})
    check("None 인자 제거", _seen_requests[-1]["params"] == {"prefix": "SM_"},
          _seen_requests[-1]["params"])

    # --- result 반환 ---------------------------------------------------
    out = conn.call("script.execute", {"code": "cmds.ls()"})
    check("result 를 dict 로 반환", isinstance(out, dict) and out.get("ok") is True, out)
    check("stdout 전달", out.get("stdout") == "출력됨\n", out)

    # --- error 는 예외로 -------------------------------------------------
    try:
        conn.call("script.execute", {"code": "BOOM"})
        check("error 는 예외로 변환", False, "예외가 발생하지 않았습니다")
    except MayaBridgeError as exc:
        check("error 는 예외로 변환", True)
        check("에러 코드 보존", exc.code == -32603, exc.code)
        check("에러 data(트레이스백) 보존", "ValueError" in str(exc.data), exc.data)

    try:
        conn.call("nope.nothing")
        check("MethodNotFound 전달", False, "예외가 발생하지 않았습니다")
    except MayaBridgeError as exc:
        check("MethodNotFound 전달", exc.code == -32601, exc.code)

    # --- 프레이밍 ------------------------------------------------------
    wire_bytes = len(BIG_TEXT.encode("utf-8"))
    check("프레이밍: 다중 recv 필요한 크기", wire_bytes > 400000, "%d bytes" % wire_bytes)
    big = conn.call("script.execute", {"code": "BIG"})
    check("프레이밍: 대용량 응답 무손실 (전체 일치)", big.get("value") == BIG_TEXT,
          "길이 %d / 기대 %d" % (len(big.get("value") or ""), len(BIG_TEXT)))

    # --- 툴 계층 (공개 call_tool 경로) --------------------------------------
    _seen_requests.clear()
    res = call_tool(mcp, "maya_execute", code="cmds.ls()")
    check("maya_execute: 성공", not is_error(res), res)
    check("maya_execute: script.execute 호출",
          _seen_requests[-1]["method"] == "script.execute", _seen_requests[-1])

    res = call_tool(mcp, "maya_execute", code="   ")
    check("maya_execute: 빈 코드 거부", is_error(res), res)

    _seen_requests.clear()
    call_tool(mcp, "maya_unreal_check", objects=["a*"], prefix="SM_")
    check("언리얼 툴이 unreal.* 메서드 호출",
          _seen_requests[-1]["method"] == "unreal.check", _seen_requests[-1])

    _seen_requests.clear()
    call_tool(mcp, "maya_unreal_make_lods")
    sent = _seen_requests[-1]["params"]
    check("make_lods 기본 감소율 전달", sent.get("keep_percent") == [50, 25, 12], sent)

    # --- 캡처: MCP 이미지 콘텐츠로 왕복 -------------------------------------
    with open(CAPTURE_PATH, "wb") as fh:
        fh.write(_PNG)
    res = call_tool(mcp, "maya_viewport_capture")
    check("capture: 이미지 콘텐츠 반환", image_bytes(res) is not None, res)
    check("capture: PNG 바이트 그대로", image_bytes(res) == _PNG,
          len(image_bytes(res) or b""))

    os.remove(CAPTURE_PATH)
    res = call_tool(mcp, "maya_viewport_capture")
    check("capture(파일없음): 툴 에러로 보고", is_error(res), res)

    # --- 연결 실패 안내 --------------------------------------------------
    dead = MayaConnection(port=free_port(), timeout=3)
    try:
        dead.call("scene.ping")
        check("연결실패: 예외", False, "예외 없음")
    except MayaBridgeError as exc:
        check("연결실패: 원인 안내", "연결할 수 없습니다" in str(exc), str(exc))
        check("연결실패: 해결 방법 안내", "maya_mcp_bridge.start()" in str(exc), str(exc))

    srv.shutdown()


if __name__ == "__main__":
    crashed = False
    try:
        run()
    except Exception:
        import traceback
        traceback.print_exc()
        crashed = True

    failed = [n for n, ok, _ in _results if not ok]
    print("\n%d개 중 %d개 통과" % (len(_results), len(_results) - len(failed)))
    if failed:
        print("실패: " + ", ".join(failed))
    if crashed:
        print("테스트가 도중에 예외로 중단됐습니다 (위 트레이스백 참고).")
    sys.exit(1 if (failed or crashed) else 0)
