# -*- coding: utf-8 -*-
"""
maya_mcp_bridge 의 Maya 쪽 로직 테스트. Maya 의 mayapy(Python 3.7)로 실행합니다.

    "C:\\Program Files\\Autodesk\\Maya2022\\bin\\mayapy.exe" tests/test_bridge_mayapy.py

소켓 계층은 여기서 테스트하지 않습니다. maya.standalone(배치 모드)에는 idle
이벤트 루프가 없어서 executeInMainThreadWithResult 가 반환되지 않기 때문입니다.
소켓/프레이밍은 tests/test_server_protocol.py 가 목 브릿지로 검증합니다.
여기서는 _op_* 함수를 메인 스레드에서 직접 불러 Maya API 사용을 검증합니다.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import maya.standalone
maya.standalone.initialize(name="python")

import maya.cmds as cmds
import maya_mcp_bridge as B


_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print("%s  %s%s" % ("PASS" if cond else "FAIL", name,
                        ("  <- " + str(detail)) if (detail and not cond) else ""))


def run():
    # 배치 모드에서는 undo 가 기본으로 꺼져 있습니다. 켜야 청크 테스트가 의미 있습니다.
    cmds.undoInfo(state=True, infinity=True)
    cmds.file(new=True, force=True)

    # --- ping ---------------------------------------------------------
    p = B._op_ping({})
    check("ping: maya_version 반환", bool(p.get("maya_version")), p)
    check("ping: protocol 버전", p.get("protocol") == B.PROTOCOL_VERSION, p)
    check("ping: batch 감지", p.get("batch") is True, p)

    # --- execute: 단일 표현식 ------------------------------------------
    r = B._op_execute({"code": "cmds.polyCube(name='mcpExprCube')"})
    check("execute(표현식): ok", r["ok"], r.get("error"))
    check("execute(표현식): 값이 리스트", isinstance(r["value"], list), r["value"])
    check("execute(표현식): 큐브 생성됨", cmds.objExists("mcpExprCube"))

    # --- execute: 여러 줄 + result 규약 ---------------------------------
    r = B._op_execute({"code": "a = 2 + 3\nresult = {'sum': a, 'label': '합계'}"})
    check("execute(여러 줄): ok", r["ok"], r.get("error"))
    check("execute(여러 줄): result 규약", r["value"] == {"sum": 5, "label": "합계"}, r["value"])

    # --- execute: stdout 캡처 -------------------------------------------
    r = B._op_execute({"code": "print('헬로 브릿지')"})
    check("execute: stdout 캡처", "헬로 브릿지" in r["stdout"], repr(r["stdout"]))

    # --- execute: 런타임 예외 ------------------------------------------
    r = B._op_execute({"code": "raise ValueError('일부러 실패')"})
    check("execute(예외): ok=False", r["ok"] is False, r)
    check("execute(예외): 트레이스백 포함", "ValueError" in (r["error"] or ""), r.get("error"))
    check("execute(예외): 청크가 닫혔는지",
          cmds.undoInfo(q=True, state=True) is True)

    # --- execute: 구문 오류 --------------------------------------------
    r = B._op_execute({"code": "def broken(:\n  pass"})
    check("execute(구문오류): ok=False", r["ok"] is False, r)
    check("execute(구문오류): SyntaxError", "SyntaxError" in (r["error"] or ""), r.get("error"))

    # --- undo 청크: 한 호출 = 한 단계 ------------------------------------
    before = set(cmds.ls(type="transform") or [])
    r = B._op_execute({"code": (
        "cmds.polyCube(name='mcpChunkA')\n"
        "cmds.polySphere(name='mcpChunkB')\n"
        "result = 'ok'"
    )})
    check("undo청크: 실행 ok", r["ok"], r.get("error"))
    check("undo청크: 둘 다 생성됨",
          cmds.objExists("mcpChunkA") and cmds.objExists("mcpChunkB"))

    u = B._op_undo({"steps": 1})
    check("undo: 1단계 수행", u.get("undone") == 1, u)
    gone = (not cmds.objExists("mcpChunkA")) and (not cmds.objExists("mcpChunkB"))
    check("undo청크: 한 번에 둘 다 롤백 (핵심 기능)", gone,
          "남은 오브젝트: %s" % sorted(set(cmds.ls(type='transform') or []) - before))

    # --- search --------------------------------------------------------
    s = B._op_search({"pattern": "bevel", "limit": 20})
    check("search: polyBevel 발견", "polyBevel" in s["commands"], s["commands"][:5])
    check("search: limit 준수", len(s["commands"]) <= 20, len(s["commands"]))
    s_all = B._op_search({"pattern": "", "limit": 5})
    check("search: 전체 개수 보고", s_all["total"] > 1000, s_all["total"])

    # --- help ----------------------------------------------------------
    h = B._op_help({"name": "polyCube"})
    check("help: 텍스트 반환", len(h.get("help") or "") > 20, h)
    h_bad = B._op_help({"name": "존재하지않는명령xyz"})
    check("help: 없는 명령은 error", bool(h_bad.get("error")), h_bad)

    # --- scene_info ----------------------------------------------------
    cmds.polyCube(name="mcpInfoCube")
    info = B._op_scene_info({})
    check("scene_info: 메시 카운트", info["counts"]["mesh"] >= 1, info["counts"])
    check("scene_info: 프레임 범위 2개", len(info["frame_range"]) == 2, info["frame_range"])
    check("scene_info: 단위 보고", bool(info["linear_unit"]), info)

    # --- capture (배치 모드에서는 실패해야 정상) --------------------------
    c = B._op_capture({})
    check("capture: 배치 모드에서 에러 반환", bool(c.get("error")), c)

    # --- 직렬화 --------------------------------------------------------
    import maya.api.OpenMaya as om
    v = B._jsonable(om.MPoint(1, 2, 3))
    check("_jsonable: 비직렬화 객체를 repr 로 폴백", isinstance(v, str), v)
    big = B._jsonable(list(range(B.MAX_LIST_ITEMS + 50)))
    check("_jsonable: 긴 리스트 절단", len(big) == B.MAX_LIST_ITEMS + 1, len(big))
    check("_jsonable: 절단 안내 포함", "표시" in str(big[-1]), big[-1])

    # --- undo 꺼진 상태 경고 --------------------------------------------
    cmds.undoInfo(state=False)
    r = B._op_execute({"code": "result = 1"})
    check("undo 꺼짐: 경고 전달", bool(r.get("warning")), r)
    cmds.undoInfo(state=True, infinity=True)


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
    try:
        maya.standalone.uninitialize()
    except Exception:
        pass
    sys.exit(1 if (failed or crashed) else 0)
