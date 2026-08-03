# -*- coding: utf-8 -*-
"""
GUI 모드 스모크 테스트 — Maya 를 띄운 상태에서 실행합니다.

    .venv\\Scripts\\python.exe tests/smoke_gui.py

자동 테스트로 검증할 수 없는 두 경로를 여기서 확인합니다.
  1. 소켓 왕복 + executeInMainThreadWithResult (배치 모드에는 idle 루프가 없어 불가)
  2. playblast 뷰포트 캡처 (뷰포트가 있어야 함)

주의: undo 를 실제로 호출합니다. **새 씬이나 스크래치 씬에서 돌리세요.**
작업 중인 씬에서 돌리지 마세요.
"""

import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server as S

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PREFIX = "mcpSmoke_"


def png_byte_variety(data):
    """PNG 픽셀 데이터의 서로 다른 바이트값 개수. 참고용 지표일 뿐입니다.

    주의: 이 값으로 백지를 판정하려 했다가 실패했습니다. 거의 흰 640x360 프레임도
    28 정도가 나옵니다. 캡처가 실제로 씬을 반영하는지는 아래 run() 에서
    '빈 씬 캡처'와 '오브젝트 있는 캡처'가 서로 다른지로 검사합니다.
    """
    idat, i = b"", 8
    while i + 8 <= len(data):
        (length,) = struct.unpack(">I", data[i:i + 4])
        if data[i + 4:i + 8] == b"IDAT":
            idat += data[i + 8:i + 8 + length]
        i += 12 + length
    if not idat:
        return 0
    try:
        return len(set(zlib.decompress(idat)))
    except zlib.error:
        return 0

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    # 통과한 항목에 상세를 붙이면 실패처럼 읽힙니다. 실패했을 때만 출력합니다.
    print("%s  %s%s" % ("PASS" if cond else "FAIL", name,
                        ("\n      -> " + str(detail)) if (detail and not cond) else ""))


def run():
    # --- 1. 연결 -------------------------------------------------------
    try:
        ping = S._call("ping")
    except S.BridgeError as exc:
        print("브릿지에 연결할 수 없습니다.\n%s" % exc)
        print("\nMaya 스크립트 에디터(Python 탭)에서 먼저 실행하세요:")
        print('    import sys; sys.path.append(r"%s")'
              % os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        print("    import maya_mcp_bridge; maya_mcp_bridge.start()")
        return

    info = ping.get("value") or {}
    check("소켓 왕복 (핵심: 메인 스레드 마샬링)", ping.get("ok") and info.get("maya_version"), info)
    if info.get("batch"):
        print("\n배치 모드입니다. 이 스크립트는 GUI 모드에서 돌려야 의미가 있습니다.")
        return
    check("undo 활성화됨", info.get("undo_enabled") is True,
          "Maya 에서 undo 가 꺼져 있으면 롤백이 불가능합니다.")

    # --- 2. 실행 -------------------------------------------------------
    r = S._call("execute", code=(
        "cmds.polyCube(name='%sA')\n"
        "cmds.polySphere(name='%sB')\n"
        "result = cmds.ls('%s*', type='transform')" % (PREFIX, PREFIX, PREFIX)
    ))
    check("execute: 오브젝트 2개 생성", r.get("ok") and len(r.get("value") or []) >= 2,
          r.get("error") or r.get("value"))

    # --- 3. undo 청크: 한 호출 = 한 단계 ---------------------------------
    u = S._call("undo", steps=1)
    check("undo 호출", (u.get("value") or {}).get("undone") == 1, u)

    left = S._call("execute", code="result = cmds.ls('%s*', type='transform')" % PREFIX,
                   undo_chunk=False)
    remaining = left.get("value") or []
    check("undo 청크: 한 번에 둘 다 롤백", len(remaining) == 0,
          "남아있음: %s" % remaining)

    # --- 4. 뷰포트 캡처 --------------------------------------------------
    # 캡처가 '유효한 PNG' 인지만 보면 백지도 통과합니다. 씬 내용을 실제로 반영하는지
    # 확인하려면 빈 씬과 오브젝트가 있는 씬의 캡처가 서로 달라야 합니다.
    def grab(label):
        cap = S._call("capture", width=640, height=360)
        val = cap.get("value") or {}
        if val.get("error"):
            check("뷰포트 캡처 (%s)" % label, False, val["error"])
            return None
        path = val.get("path", "")
        if not path or not os.path.exists(path):
            check("뷰포트 캡처 (%s): 파일 생성" % label, False, path)
            return None
        return open(path, "rb").read()

    S._call("execute", undo_chunk=False,
            code="cmds.delete(cmds.ls('%s*', type='transform') or []); result='clean'" % PREFIX)
    empty = grab("빈 씬")

    S._call("execute", undo_chunk=False, code=(
        "cmds.polyCube(name='%sCap')\n"
        "cmds.viewFit(all=True)\n"
        "result = 'ok'" % PREFIX
    ))
    filled = grab("오브젝트 있음")

    if empty and filled:
        check("뷰포트 캡처: 유효한 PNG", filled[:8] == PNG_MAGIC, filled[:8])
        check("뷰포트 캡처: 씬 내용을 실제로 반영 (빈 씬과 다름)", empty != filled,
              "두 캡처가 동일합니다 — 뷰포트가 아니라 고정 프레임을 찍고 있습니다")
        print("      빈 씬 %d bytes (다양성 %d) / 오브젝트 있음 %d bytes (다양성 %d)"
              % (len(empty), png_byte_variety(empty),
                 len(filled), png_byte_variety(filled)))

    S._call("execute", undo_chunk=False,
            code="cmds.delete(cmds.ls('%s*', type='transform') or []); result='cleaned'" % PREFIX)

    # --- 5. MCP 툴 레이어로도 한 번 ---------------------------------------
    out = S.maya_scene_info()
    check("툴 레이어: scene_info", "결과" in out, out[:200])

    img = S.maya_viewport_capture(width=640, height=360)
    check("툴 레이어: 이미지 객체 반환", isinstance(img, S.Image), type(img).__name__)


if __name__ == "__main__":
    crashed = False
    try:
        run()
    except Exception:
        import traceback
        traceback.print_exc()
        crashed = True

    failed = [n for n, ok, _ in _results if not ok]
    if _results:
        print("\n%d개 중 %d개 통과" % (len(_results), len(_results) - len(failed)))
    if failed:
        print("실패: " + ", ".join(failed))
    if crashed:
        print("도중에 예외로 중단됐습니다 (위 트레이스백 참고).")
    sys.exit(1 if (failed or crashed) else 0)
