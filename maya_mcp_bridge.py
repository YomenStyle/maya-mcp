# -*- coding: utf-8 -*-
"""
maya_mcp_bridge.py — Maya 안에서 도는 얇은 통로 (Maya 2022+ / Python 3.7 호환)

설계 원칙
---------
1. 표준 라이브러리만 사용합니다. `mcp` 패키지는 Python 3.10+ 를 요구하므로
   Maya 2022 의 내장 Python 3.7 에서는 절대 임포트하지 않습니다.
   MCP 서버 본체는 PC 의 별도 파이썬(3.10+)에서 돌고, 이 파일은 소켓으로만 통신합니다.
2. maya.cmds 는 스레드 세이프가 아닙니다. 소켓 수신은 워커 스레드에서 하되,
   실제 Maya 호출은 전부 executeInMainThreadWithResult 로 메인 스레드에 넘깁니다.
3. 모든 실행은 undo 청크로 감싸서 Ctrl+Z 한 번에 되돌아가게 합니다.

사용법 (스크립트 에디터)
-----------------------
    import sys; sys.path.append(r"<이 파일이 있는 폴더>")
    import maya_mcp_bridge
    maya_mcp_bridge.start()

또는 플러그인 매니저에서 이 파일을 로드하세요 (initializePlugin 이 start() 를 호출합니다).

중지:
    maya_mcp_bridge.stop()
"""

import json
import os
import socket
import struct
import sys
import tempfile
import threading
import traceback
import glob

try:                      # Python 3
    import socketserver
    from contextlib import redirect_stdout, redirect_stderr
    from io import StringIO
except ImportError:       # pragma: no cover - Maya 2020 이하 Python 2 대비
    raise RuntimeError("maya_mcp_bridge 는 Python 3 (Maya 2022 이상)이 필요합니다.")

import maya.cmds as cmds
import maya.mel as mel
import maya.utils
import maya.api.OpenMaya as om


# ---------------------------------------------------------------- 설정

HOST = "127.0.0.1"          # localhost 전용. 절대 0.0.0.0 으로 바꾸지 마세요.
PORT = 20777
PROTOCOL_VERSION = 1

MAX_STDOUT = 20000          # 응답에 담을 stdout 최대 길이 (문자)
MAX_VALUE_REPR = 20000      # 값 직렬화 최대 길이 (문자)
MAX_LIST_ITEMS = 500        # 리스트 반환 시 최대 항목 수

_server = None
_thread = None


# ---------------------------------------------------------------- 프레이밍
# TCP 에는 메시지 경계가 없습니다. 4바이트 빅엔디안 길이 프리픽스 + UTF-8 JSON.

def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _recv_msg(sock):
    header = _recv_exact(sock, 4)
    if header is None:
        return None
    (length,) = struct.unpack(">I", header)
    return _recv_exact(sock, length)


def _send_msg(sock, payload):
    sock.sendall(struct.pack(">I", len(payload)) + payload)


# ---------------------------------------------------------------- 직렬화

def _jsonable(value, _depth=0):
    """maya.cmds 반환값을 JSON 으로 안전하게 바꿉니다. 못 바꾸면 repr 로 폴백."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if _depth > 6:
        return repr(value)[:MAX_VALUE_REPR]
    if isinstance(value, (list, tuple)):
        items = list(value)
        truncated = len(items) > MAX_LIST_ITEMS
        out = [_jsonable(v, _depth + 1) for v in items[:MAX_LIST_ITEMS]]
        if truncated:
            out.append("... (%d개 중 %d개만 표시)" % (len(items), MAX_LIST_ITEMS))
        return out
    if isinstance(value, dict):
        return dict((str(k), _jsonable(v, _depth + 1)) for k, v in list(value.items())[:MAX_LIST_ITEMS])
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)[:MAX_VALUE_REPR]


def _clip(text, limit):
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... (잘림: 총 %d자)" % len(text)


# ---------------------------------------------------------------- 오퍼레이션
# 아래 _op_* 함수는 전부 Maya 메인 스레드에서 실행됩니다.

def _op_ping(params):
    return {
        "protocol": PROTOCOL_VERSION,
        "maya_version": cmds.about(version=True),
        "python": sys.version.split()[0],
        "scene": cmds.file(q=True, sceneName=True) or "<untitled>",
        "undo_enabled": bool(cmds.undoInfo(q=True, state=True)),
        "batch": bool(cmds.about(q=True, batch=True)),
    }


def _op_execute(params):
    code = params.get("code") or ""
    use_chunk = params.get("undo_chunk", True)
    chunk_name = params.get("chunk_name") or "mcp"

    scope = {
        "cmds": cmds,
        "mel": mel,
        "om": om,
        "__name__": "__mcp__",
    }
    buf = StringIO()
    out = {"ok": True, "value": None, "stdout": "", "error": None}

    undo_enabled = bool(cmds.undoInfo(q=True, state=True))
    opened = False
    if use_chunk and undo_enabled:
        cmds.undoInfo(openChunk=True, chunkName=chunk_name)
        opened = True

    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            # 단일 표현식이면 그 값을 결과로 씁니다 (`cmds.ls(sl=True)` 같은 경우).
            try:
                compiled = compile(code, "<mcp>", "eval")
                value = eval(compiled, scope)
            except SyntaxError:
                exec(compile(code, "<mcp>", "exec"), scope)
                value = scope.get("result", None)
        out["value"] = _jsonable(value)
    except Exception:
        out["ok"] = False
        out["error"] = traceback.format_exc()
    finally:
        # 예외가 나도 반드시 닫습니다. 안 닫으면 이후 undo 스택이 망가집니다.
        if opened:
            try:
                cmds.undoInfo(closeChunk=True)
            except Exception:
                pass

    out["stdout"] = _clip(buf.getvalue(), MAX_STDOUT)
    out["undo_enabled"] = undo_enabled
    if not undo_enabled:
        out["warning"] = "Maya 의 undo 가 꺼져 있습니다. 되돌리기가 불가능합니다."
    return out


def _op_undo(params):
    steps = int(params.get("steps", 1))
    steps = max(1, min(steps, 50))
    done = 0
    for _ in range(steps):
        try:
            cmds.undo()
            done += 1
        except Exception:
            break
    return {"undone": done, "requested": steps}


def _op_search(params):
    pattern = (params.get("pattern") or "").lower()
    limit = int(params.get("limit", 80))
    names = [n for n in dir(cmds) if not n.startswith("_")]
    if pattern:
        names = [n for n in names if pattern in n.lower()]
    names.sort()
    return {"total": len(names), "commands": names[:limit]}


def _op_help(params):
    name = params.get("name") or ""
    if not name:
        return {"error": "name 이 필요합니다."}
    try:
        text = cmds.help(name)
    except Exception:
        text = None
    if not text:
        obj = getattr(cmds, name, None)
        text = (obj.__doc__ or "") if obj is not None else ""
    if not text:
        return {"name": name, "help": "", "error": "'%s' 명령을 찾을 수 없습니다." % name}
    return {"name": name, "help": _clip(text, MAX_STDOUT)}


def _pick_model_panel():
    """캡처에 쓸 모델 패널을 고릅니다. 보이는 패널을 우선합니다."""
    try:
        visible = cmds.getPanel(visiblePanels=True) or []
        for p in visible:
            if cmds.getPanel(typeOf=p) == "modelPanel":
                return p
        panels = cmds.getPanel(type="modelPanel") or []
        return panels[0] if panels else None
    except Exception:
        return None


def _op_capture(params):
    """현재 뷰포트를 PNG 로 저장하고 경로를 돌려줍니다."""
    if cmds.about(q=True, batch=True):
        return {"error": "배치 모드에서는 뷰포트 캡처를 할 수 없습니다."}

    width = int(params.get("width", 960))
    height = int(params.get("height", 540))
    ornaments = bool(params.get("ornaments", False))
    # offScreen=True 는 사용자의 뷰포트를 건드리지 않습니다. 이 환경(Maya 2022)에서는
    # True/False 결과가 바이트 단위로 동일했지만, 드라이버에 따라 오프스크린 렌더가
    # 빈 프레임을 내는 사례가 알려져 있어 끌 수 있게 남겨둡니다.
    off_screen = bool(params.get("off_screen", True))

    # 캡처할 모델 패널을 명시적으로 고릅니다. setFocus 에 의존하면
    # 스크립트 에디터가 포커스를 쥐고 있을 때 백지가 나옵니다.
    panel = _pick_model_panel()
    if panel is None:
        return {"error": "보이는 모델 패널이 없습니다. 뷰포트를 하나 열어두세요."}

    base = os.path.join(tempfile.gettempdir(), "mcp_capture")
    target = base + ".png"
    for stale in glob.glob(base + "*"):
        try:
            os.remove(stale)
        except OSError:
            pass

    frame = cmds.currentTime(q=True)
    try:
        cmds.refresh(force=True)          # 직전 편집을 화면에 반영시킨 뒤 캡처
        cmds.playblast(
            editorPanelName=panel,
            frame=frame,
            format="image",
            compression="png",
            completeFilename=target,
            widthHeight=[width, height],
            percent=100,
            quality=100,
            viewer=False,
            offScreen=off_screen,
            showOrnaments=ornaments,
            forceOverwrite=True,
        )
    except Exception:
        return {"error": traceback.format_exc()}

    if not os.path.exists(target):
        # 일부 환경에서 Maya 가 프레임 번호를 덧붙입니다.
        found = sorted(glob.glob(base + "*"))
        if not found:
            return {"error": "playblast 가 파일을 만들지 못했습니다."}
        target = found[0]

    return {"path": target, "width": width, "height": height, "frame": frame}


def _op_scene_info(params):
    def _count(node_type):
        try:
            return len(cmds.ls(type=node_type) or [])
        except Exception:
            return 0

    return {
        "scene": cmds.file(q=True, sceneName=True) or "<untitled>",
        "modified": bool(cmds.file(q=True, modified=True)),
        "maya_version": cmds.about(version=True),
        "up_axis": cmds.upAxis(q=True, axis=True),
        "linear_unit": cmds.currentUnit(q=True, linear=True),
        "time_unit": cmds.currentUnit(q=True, time=True),
        "frame_range": [cmds.playbackOptions(q=True, min=True),
                        cmds.playbackOptions(q=True, max=True)],
        "current_frame": cmds.currentTime(q=True),
        "selection": _jsonable(cmds.ls(selection=True, long=True) or []),
        "counts": {
            "transform": _count("transform"),
            "mesh": _count("mesh"),
            "joint": _count("joint"),
            "nurbsCurve": _count("nurbsCurve"),
            "camera": _count("camera"),
            "light": len(cmds.ls(lights=True) or []),
        },
    }


# ---------------------------------------------------------------- 메서드 등록
# unreal-mcp-bridge 와 맞춘 점 네임스페이스 규약입니다: "<도메인>.<동작>".

def _unreal_method(fn_name):
    """unreal_tools 의 함수를 메서드로 노출합니다.

    호출마다 reload 하므로 unreal_tools.py 를 고쳐도 Maya 재시작이 필요 없습니다.
    unreal_tools 는 모듈 전역 상태가 없어서 안전합니다.
    """
    def _invoke(params):
        import importlib
        import unreal_tools
        importlib.reload(unreal_tools)
        return getattr(unreal_tools, fn_name)(**params)
    return _invoke


# (핸들러, 씬을 변경하는가) — 변경하는 메서드는 undo 청크로 감쌉니다.
_METHODS = {
    "scene.ping":              (_op_ping, False),
    "scene.info":              (_op_scene_info, False),
    "script.execute":          (_op_execute, False),   # 자체적으로 청크를 관리합니다
    "script.undo":             (_op_undo, False),
    "inspect.search_commands": (_op_search, False),
    "inspect.command_help":    (_op_help, False),
    "viewport.capture":        (_op_capture, False),
    "unreal.check":            (_unreal_method("check"), False),
    "unreal.check_skeleton":   (_unreal_method("check_skeleton"), False),
    "unreal.check_materials":  (_unreal_method("check_materials"), False),
    "unreal.prepare":          (_unreal_method("prepare"), True),
    "unreal.cleanup_materials": (_unreal_method("cleanup_materials"), True),
    "unreal.make_lods":        (_unreal_method("make_lods"), True),
    "unreal.make_collision":   (_unreal_method("make_collision"), True),
    "unreal.export_fbx":       (_unreal_method("export_fbx"), False),
    "unreal.import_fbx":       (_unreal_method("import_fbx"), True),
}


# ---------------------------------------------------------------- JSON-RPC 2.0

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _ok(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id, code, message, data=None):
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": code, "message": message, "data": data}}


def _handle_in_main_thread(method, params):
    """메인 스레드에서 실행됩니다. (결과, 에러) 튜플을 돌려줍니다."""
    entry = _METHODS.get(method)
    if entry is None:
        return None, (METHOD_NOT_FOUND, "알 수 없는 메서드: %r" % method,
                      {"available": sorted(_METHODS)})
    fn, mutates = entry

    opened = False
    if mutates and cmds.undoInfo(q=True, state=True):
        cmds.undoInfo(openChunk=True, chunkName="mcp:%s" % method)
        opened = True
    try:
        return fn(params), None
    except TypeError as exc:
        # 인자 이름이 틀렸을 때 JSON-RPC 규약대로 InvalidParams 로 구분합니다.
        return None, (INVALID_PARAMS, str(exc), traceback.format_exc())
    except Exception as exc:
        return None, (INTERNAL_ERROR, str(exc), traceback.format_exc())
    finally:
        if opened:
            try:
                cmds.undoInfo(closeChunk=True)
            except Exception:
                pass


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        req_id = None
        try:
            raw = _recv_msg(self.request)
            if raw is None:
                return
            req = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            self._reply(_err(None, PARSE_ERROR, "JSON 파싱 실패", str(exc)))
            return
        except Exception:
            self._reply(_err(None, INTERNAL_ERROR, "요청 수신 실패",
                             traceback.format_exc()))
            return

        if not isinstance(req, dict) or not req.get("method"):
            self._reply(_err(None, INVALID_REQUEST, "method 가 없습니다", req))
            return

        req_id = req.get("id")
        method = req["method"]
        params = req.get("params") or {}
        if not isinstance(params, dict):
            self._reply(_err(req_id, INVALID_PARAMS, "params 는 객체여야 합니다"))
            return

        try:
            # 여기가 핵심: Maya 호출을 메인 스레드로 넘깁니다.
            outcome = maya.utils.executeInMainThreadWithResult(
                lambda: _handle_in_main_thread(method, params)
            )
        except Exception:
            self._reply(_err(req_id, INTERNAL_ERROR, "메인 스레드 실행 실패",
                             traceback.format_exc()))
            return

        if outcome is None:
            self._reply(_err(req_id, INTERNAL_ERROR,
                             "메인 스레드 실행이 결과를 반환하지 않았습니다"))
            return

        result, error = outcome
        if error is not None:
            code, message, data = error
            self._reply(_err(req_id, code, message, data))
        else:
            self._reply(_ok(req_id, _jsonable(result)))

    def _reply(self, obj):
        if obj.get("id") is None and "error" not in obj:
            return                      # 알림(notification)에는 응답하지 않습니다
        try:
            _send_msg(self.request, json.dumps(obj, ensure_ascii=False).encode("utf-8"))
        except Exception:
            pass


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


# ---------------------------------------------------------------- 수명 관리

def start(port=PORT):
    global _server, _thread
    if _server is not None:
        print("[maya-mcp] 이미 실행 중입니다 (%s:%d)" % (HOST, _server.server_address[1]))
        return _server.server_address[1]

    _server = _Server((HOST, port), _Handler)
    _thread = threading.Thread(target=_server.serve_forever, name="maya-mcp-bridge")
    _thread.daemon = True
    _thread.start()
    print("[maya-mcp] 대기 중: %s:%d (Maya %s / Python %s)"
          % (HOST, port, cmds.about(version=True), sys.version.split()[0]))
    return port


def stop():
    global _server, _thread
    if _server is None:
        print("[maya-mcp] 실행 중이 아닙니다.")
        return
    try:
        _server.shutdown()
        _server.server_close()
    finally:
        _server = None
        _thread = None
    print("[maya-mcp] 중지했습니다.")


def is_running():
    return _server is not None


def status():
    if _server is None:
        return "[maya-mcp] 중지됨"
    return "[maya-mcp] 대기 중: %s:%d" % (HOST, _server.server_address[1])


def toggle(port=PORT):
    """셸프 버튼용. 꺼져 있으면 켜고, 켜져 있으면 끕니다."""
    if is_running():
        stop()
        state = False
    else:
        start(port)
        state = True
    try:
        cmds.inViewMessage(
            amg=("maya-mcp <hl>대기 중</hl> (%s:%d)" % (HOST, port)) if state
                else "maya-mcp <hl>중지됨</hl>",
            pos="midCenter", fade=True, fadeStayTime=1500,
        )
    except Exception:
        pass
    return state


# ---------------------------------------------------------------- 플러그인 진입점

maya_useNewAPI = True


def initializePlugin(mobject):
    om.MFnPlugin(mobject, "maya-mcp", "0.1.0", "Any")
    start()


def uninitializePlugin(mobject):
    stop()
