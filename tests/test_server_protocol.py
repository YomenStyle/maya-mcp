# -*- coding: utf-8 -*-
"""
server.py 의 전송 계층과 툴 등록 테스트. PC 파이썬(3.10+)으로 실행합니다.

    python tests/test_server_protocol.py

Maya 없이 동작합니다. 브릿지와 동일한 와이어 프로토콜(4바이트 빅엔디안 길이
프리픽스 + UTF-8 JSON)을 말하는 목 서버를 띄워서, server.py 가 프레이밍·에러
처리·응답 포매팅을 제대로 하는지 검증합니다.
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

# 1x1 투명 PNG
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
    "AAAABJRU5ErkJggg=="
)

_results = []


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
        raw = self._recv(length)
        req = json.loads(raw.decode("utf-8"))
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


def responder(req):
    op = req.get("op")
    params = req.get("params") or {}
    if op == "ping":
        return {"ok": True, "value": {"maya_version": "2022", "undo_enabled": True}}
    if op == "execute":
        code = params.get("code", "")
        if code == "BOOM":
            return {"ok": False, "error": "Traceback ...\nValueError: 터짐"}
        if code == "WARN":
            return {"ok": True, "value": None, "stdout": "",
                    "warning": "Maya 의 undo 가 꺼져 있습니다."}
        if code == "BIG":
            return {"ok": True, "value": BIG_TEXT, "stdout": ""}
        return {"ok": True, "value": [1, 2, 3], "stdout": "출력됨\n"}
    if op == "undo":
        return {"ok": True, "value": {"undone": params.get("steps", 1)}}
    if op == "search":
        return {"ok": True, "value": {"total": 2, "commands": ["polyBevel", "polyCube"]}}
    if op == "help":
        return {"ok": True, "value": {"name": params.get("name"), "help": "Flags: -offset"}}
    if op == "capture":
        return {"ok": True, "value": {"path": CAPTURE_PATH, "width": 960, "height": 540}}
    if op == "scene_info":
        return {"ok": True, "value": {"scene": "<untitled>", "counts": {"mesh": 3}}}
    return {"ok": False, "error": "알 수 없는 op"}


# ------------------------------------------------------------ 테스트

def run():
    srv, port = start_mock(responder)
    os.environ["MAYA_MCP_PORT"] = str(port)
    os.environ["MAYA_MCP_TIMEOUT"] = "15"

    import server as S
    check("import: MCP 서버 생성", S.mcp is not None)
    check("import: Image 헬퍼 존재", S.Image is not None)

    # --- 툴 등록 확인 (SDK 가 시그니처/독스트링을 읽었는지) -------------
    # mcp 2.x 는 동기, 1.x(FastMCP)는 코루틴을 돌려줍니다.
    tools = S.mcp.list_tools()
    if inspect.isawaitable(tools):
        tools = asyncio.run(tools)
    names = sorted(t.name for t in tools)
    expected = sorted(["maya_execute", "maya_search_commands", "maya_command_help",
                       "maya_viewport_capture", "maya_undo", "maya_scene_info",
                       "maya_ping",
                       "maya_unreal_check", "maya_unreal_prepare", "maya_unreal_check_skeleton",
                       "maya_unreal_export_fbx"])
    check("툴 등록: %d개 전부" % len(expected), names == expected,
          "누락/추가: %s" % sorted(set(names) ^ set(expected)))
    check("툴 등록: 설명 비어있지 않음",
          all((t.description or "").strip() for t in tools),
          [t.name for t in tools if not (t.description or "").strip()])
    ex = [t for t in tools if t.name == "maya_execute"][0]
    # mcp 2.x 는 input_schema, 1.x 는 inputSchema.
    schema = getattr(ex, "input_schema", None) or getattr(ex, "inputSchema", None) or {}
    props = schema.get("properties", {})
    check("툴 스키마: maya_execute 인자 2개", set(props) == {"code", "undo_chunk"}, props)

    # --- 정상 실행 -----------------------------------------------------
    out = S.maya_execute("cmds.ls()")
    check("execute: 결과 섹션", "--- 결과 ---" in out, out)
    check("execute: stdout 섹션", "출력됨" in out, out)

    # --- 에러 전달 -----------------------------------------------------
    out = S.maya_execute("BOOM")
    check("execute(실패): 실패 표시", "실패했습니다" in out, out)
    check("execute(실패): 트레이스백 전달", "ValueError" in out, out)

    # --- 경고 전달 -----------------------------------------------------
    out = S.maya_execute("WARN")
    check("execute(경고): 경고 전달", "경고:" in out, out)

    # --- 큰 페이로드 (프레이밍 검증) --------------------------------------
    # 한 번의 recv 로 안 들어오는 크기여야 _recv_exact 의 루프가 검증됩니다.
    wire_bytes = len(BIG_TEXT.encode("utf-8"))
    out = S.maya_execute("BIG")
    check("프레이밍: 다중 recv 필요한 크기", wire_bytes > 400000, "%d bytes" % wire_bytes)
    check("프레이밍: 대용량 응답 무손실 (전체 일치)", BIG_TEXT in out,
          "출력 %d자 / 기대 %d자" % (len(out), len(BIG_TEXT)))

    # --- 나머지 툴 -----------------------------------------------------
    check("undo", '"undone": 3' in S.maya_undo(3), S.maya_undo(3))
    check("search", "polyBevel" in S.maya_search_commands("bevel"), "")
    check("command_help", "-offset" in S.maya_command_help("polyBevel"), "")
    check("scene_info", '"mesh": 3' in S.maya_scene_info(), "")
    check("ping", "2022" in S.maya_ping(), "")

    # --- 캡처: 이미지 반환 ------------------------------------------------
    with open(CAPTURE_PATH, "wb") as fh:
        fh.write(_PNG)
    img = S.maya_viewport_capture()
    check("capture: Image 객체 반환", isinstance(img, S.Image), type(img).__name__)
    if isinstance(img, S.Image):
        check("capture: PNG 바이트 그대로", getattr(img, "data", None) == _PNG,
              len(getattr(img, "data", b"") or b""))

    # --- 캡처: 파일 없음 ---------------------------------------------------
    os.remove(CAPTURE_PATH)
    out = S.maya_viewport_capture()
    check("capture(파일없음): 문자열로 안내", isinstance(out, str) and "찾을 수 없" in out, out)

    # --- 연결 실패 안내 --------------------------------------------------
    dead = free_port()
    S.PORT = dead
    out = S.maya_execute("cmds.ls()")
    check("연결실패: 원인 안내", "연결할 수 없습니다" in out, out)
    check("연결실패: 해결 방법 안내", "maya_mcp_bridge.start()" in out, out)
    S.PORT = port

    # --- _fmt 엣지 케이스 -------------------------------------------------
    check("_fmt: 빈 응답", "완료했습니다" in S._fmt({"ok": True}), S._fmt({"ok": True}))
    check("_fmt: 값 0 도 출력", "--- 결과 ---" in S._fmt({"ok": True, "value": 0}),
          S._fmt({"ok": True, "value": 0}))

    srv.shutdown()


if __name__ == "__main__":
    crashed = False
    try:
        run()
    except Exception:
        # finally 에서 곧장 sys.exit 하면 트레이스백이 통째로 사라집니다.
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
