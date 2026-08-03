# -*- coding: utf-8 -*-
"""
server.py 를 실제 MCP 클라이언트로 붙어 확인하는 엔드투엔드 스모크 테스트.

    .venv/Scripts/python.exe tests/test_e2e_stdio.py

Maya 가 없어도 됩니다. 브릿지가 안 떠 있으면 툴이 "연결할 수 없습니다" 안내를
돌려주는데, 그 자체가 정상 동작입니다. 여기서 보려는 것은 서버가 stdio 로 뜨고
MCP 핸드셰이크·툴 목록·툴 호출이 실제로 오간다는 사실입니다.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp import Client

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print("%s  %s%s" % ("PASS" if cond else "FAIL", name,
                        ("  <- " + str(detail)) if (detail and not cond) else ""))


def _text_of(result):
    parts = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


async def main():
    import server as S  # 인프로세스로 붙습니다 (별도 프로세스 스폰 불필요).

    async with Client(S.mcp) as client:
        tools = await client.list_tools()
        names = sorted(t.name for t in tools.tools)
        check("핸드셰이크 + 툴 목록", len(names) == 12, names)
        check("maya_execute 노출됨", "maya_execute" in names, names)
        check("언리얼 L2 툴 노출됨",
              all(n in names for n in ("maya_unreal_check", "maya_unreal_prepare",
                                       "maya_unreal_export_fbx")), names)

        # Maya 가 떠 있든 아니든 통과해야 합니다. 브릿지가 없으면 안내 문구가,
        # 있으면 실제 결과가 옵니다. 둘 다 정상 응답입니다.
        def sane(text):
            return ("연결할 수 없습니다" in text) or ("--- 결과 ---" in text)

        res = await client.call_tool("maya_ping", {})
        text = _text_of(res)
        check("툴 호출 왕복", bool(text), res)
        check("응답이 정상 형식 (연결 안내 또는 결과)", sane(text), text[:200])
        print("      브릿지 상태: %s"
              % ("미연결" if "연결할 수 없습니다" in text else "연결됨"))

        res = await client.call_tool("maya_execute", {"code": "cmds.ls()"})
        check("인자 전달 왕복", sane(_text_of(res)), _text_of(res)[:200])


if __name__ == "__main__":
    crashed = False
    try:
        asyncio.run(main())
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
