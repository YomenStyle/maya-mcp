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


# ---------------------------------------------------------------- L2: 언리얼 파이프라인

def _unreal(fn: str, **kwargs: Any) -> str:
    """unreal_tools 의 함수를 Maya 안에서 호출합니다.

    인자는 JSON 으로 실어보냅니다 — 소스 문자열을 조립하면 따옴표/역슬래시에서
    깨지기 때문입니다.
    """
    payload = json.dumps({k: v for k, v in kwargs.items() if v is not None})
    code = (
        "import importlib, json, unreal_tools\n"
        "importlib.reload(unreal_tools)\n"
        "result = unreal_tools.%s(**json.loads(%r))\n" % (fn, payload)
    )
    try:
        return _fmt(_call("execute", code=code, undo_chunk=(fn != "check")))
    except BridgeError as exc:
        return str(exc)


@mcp.tool()
def maya_unreal_check(objects: list[str] | None = None,
                      prefix: str = "SM_") -> str:
    """언리얼 익스포트 전 문제를 감사합니다. 씬을 전혀 바꾸지 않습니다.

    정리나 익스포트를 하기 전에 항상 먼저 호출하세요. 무엇이 잘못됐는지 모르고
    고치면 되돌릴 수 없는 문제(뒤집힌 노멀 등)를 덮어쓰게 됩니다.

    검사 항목: 프리즈 안 된 트랜스폼, 음수 스케일, 남은 컨스트럭션 히스토리,
    네이밍 규칙 위반, UV 누락, ngon, 논매니폴드/라미나 지오메트리, 씬 단위와 업 축.

    Args:
        objects: 대상 이름 목록(와일드카드 가능). 비우면 선택, 선택도 없으면 씬 전체 메시.
        prefix: 기대하는 네이밍 접두사. 스태틱 메시 "SM_", 스켈레탈 메시 "SK_".
    """
    return _unreal("check", objects=objects, prefix=prefix)


@mcp.tool()
def maya_unreal_prepare(objects: list[str] | None = None,
                        prefix: str = "SM_",
                        freeze: bool = True,
                        delete_history: bool = True,
                        pivot: str = "center",
                        rename: bool = True,
                        dry_run: bool = False) -> str:
    """언리얼 익스포트 전 정리를 한 번에 수행합니다 (프리즈·히스토리·피벗·네이밍).

    먼저 maya_unreal_check 로 상태를 본 다음 호출하세요. 호출 전체가 하나의 undo
    단위로 묶이므로 Ctrl+Z 한 번에 되돌릴 수 있습니다.

    음수 스케일이 있으면 프리즈해도 노멀이 뒤집힌 채 남습니다. 이 툴은 경고만
    하고 고치지 않습니다 — 어느 면을 뒤집을지는 사람이 판단해야 합니다.

    Args:
        objects: 대상 이름 목록. 비우면 선택, 선택도 없으면 씬 전체 메시.
        prefix: 붙일 네이밍 접두사.
        freeze: 트랜스폼 프리즈 여부.
        delete_history: 컨스트럭션 히스토리 삭제 여부.
        pivot: "center"(바운딩박스 중심) | "base"(바닥) | "origin"(월드 원점) | "keep".
            바닥에 놓이는 프롭은 "base", 월드 기준 배치물은 "origin" 이 편합니다.
        rename: 접두사 리네임 수행 여부.
        dry_run: True 면 무엇을 할지만 보고하고 씬은 건드리지 않습니다.
            파괴적 정리를 처음 돌릴 때 먼저 확인하는 용도로 쓰세요.
    """
    return _unreal("prepare", objects=objects, prefix=prefix, freeze=freeze,
                   delete_history=delete_history, pivot=pivot,
                   rename=rename, dry_run=dry_run)


@mcp.tool()
def maya_unreal_check_skeleton(root: str | None = None,
                               primary_axis: str = "x",
                               tolerance_deg: float = 1.0) -> str:
    """조인트 오리엔트와 언리얼 스켈레톤 호환성을 감사합니다. 씬을 바꾸지 않습니다.

    스켈레탈 메시를 내보내기 전에 호출하세요. 오리엔트가 어긋나면 언리얼에서
    리타겟과 IK 가 틀어지는데, 임포트 후에는 원인을 찾기 어렵습니다.

    **이 툴은 자동 수정을 하지 않습니다.** 조인트를 다시 오리엔트하면 이미 붙은
    스킨 웨이트가 깨지므로, 무엇을 어떻게 고칠지는 사람이 판단해야 합니다.
    보고된 문제를 사용자에게 전달하고, 고치라는 지시를 받기 전까지는 손대지 마세요.

    검사 항목: 자식 방향과 주축의 각도 편차, rotate/rotateAxis 잔여값, 조인트 스케일,
    길이 0 본, 루트 개수와 위치, rotateOrder 불일치, segmentScaleCompensate,
    좌우 네이밍(_l/_r) 규칙과 짝 존재 여부.

    Args:
        root: 루트 조인트 이름. 비우면 씬의 모든 조인트 루트를 감사합니다.
        primary_axis: 자식을 향해야 하는 축. Maya/언리얼 관행은 "x".
            "y", "z", "-x" 등도 지정할 수 있습니다.
        tolerance_deg: 이 각도를 넘는 편차만 문제로 봅니다. 기본 1도.
    """
    return _unreal("check_skeleton", root=root, primary_axis=primary_axis,
                   tolerance_deg=tolerance_deg)


@mcp.tool()
def maya_unreal_make_lods(objects: list[str] | None = None,
                          keep_percent: list[float] | None = None,
                          lod_group: bool = True,
                          keep_borders: bool = True,
                          keep_hard_edges: bool = True,
                          keep_uv_borders: bool = True,
                          dry_run: bool = False) -> str:
    """원본을 복제해 LOD 메시를 만듭니다. 원본(LOD0)은 줄이지 않습니다.

    **만든 뒤 반드시 maya_viewport_capture 로 눈으로 확인하세요.** 삼각형 수와
    토폴로지 지표가 전부 정상이어도 실루엣이 깨져 있을 수 있습니다. 수치만 보고
    "완료" 라고 보고하지 마세요.

    감소율을 임의로 정하지 마세요. 실루엣이 무너지는 지점은 에셋마다 다릅니다.
    사용자가 값을 주지 않았다면 기본값으로 만든 뒤 결과를 보여주고 조정 여부를
    물어보세요. 캐릭터나 실루엣이 중요한 에셋은 첫 단계를 70~80 으로 올리는 편이
    안전하고, 배경 소품은 더 공격적으로 줄여도 됩니다.

    Args:
        objects: 대상. 비우면 선택, 선택도 없으면 씬 전체 메시.
        keep_percent: LOD1 부터 각 단계에서 **남길** 삼각형 비율(%). 원본 기준.
            생략하면 [50, 25, 12]. 예) [75, 50, 25] 는 더 보수적인 감소.
        lod_group: True 면 Maya LOD 그룹으로 묶습니다. 언리얼 FBX 임포터가
            이걸 인식해 LOD 를 자동 구성합니다. 별개 파일로 관리하려면 False.
        keep_borders: 메시 경계 보존. 열린 메시에서 끄면 형태가 무너집니다.
        keep_hard_edges: 하드 엣지와 크리스 보존. 각진 에셋에 중요합니다.
        keep_uv_borders: UV 경계 보존. 끄면 텍스처가 늘어납니다.
        dry_run: True 면 예상 삼각형 수만 계산하고 씬은 건드리지 않습니다.
    """
    return _unreal("make_lods", objects=objects,
                   keep_percent=keep_percent or [50, 25, 12],
                   lod_group=lod_group, keep_borders=keep_borders,
                   keep_hard_edges=keep_hard_edges,
                   keep_uv_borders=keep_uv_borders, dry_run=dry_run)


@mcp.tool()
def maya_unreal_export_fbx(path: str,
                           objects: list[str] | None = None,
                           triangulate: bool = False,
                           skins: bool = False,
                           blendshapes: bool = False,
                           animation: bool = False,
                           up_axis: str = "y") -> str:
    """언리얼용 설정으로 FBX 를 익스포트합니다.

    스무딩 그룹과 탄젠트를 켜고, 서브디비전 결과·불필요한 업스트림 연결·카메라·
    라이트는 제외합니다. 익스포트 전에 maya_unreal_prepare 를 먼저 돌리세요.

    Args:
        path: 저장 경로. 예) "D:/export/SM_Prop.fbx"
        objects: 대상. 비우면 선택, 선택도 없으면 씬 전체 메시.
        triangulate: 보통 False 로 두세요. 언리얼이 임포트 시 삼각화하며,
            쿼드를 유지해야 나중에 수정이 쉽습니다.
        skins: 스켈레탈 메시면 True.
        blendshapes: 블렌드셰이프(모프 타깃)를 포함하려면 True.
        animation: 애니메이션 클립을 함께 내보내려면 True.
        up_axis: "y"(Maya 기본, 언리얼이 변환) 또는 "z".
    """
    return _unreal("export_fbx", path=path, objects=objects,
                   triangulate=triangulate, skins=skins,
                   blendshapes=blendshapes, animation=animation,
                   up_axis=up_axis)


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
