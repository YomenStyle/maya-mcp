# maya-mcp

Maya 2022 이상에서 쓰는 커스텀 MCP 서버. AI가 Maya를 직접 조작하게 해줍니다.

기존 오픈소스 서버들과 달리 **툴을 수백 개 나열하지 않습니다.** 코드 실행 + 자기탐색으로
전체 커버리지를 얻고, 구조화된 툴은 안전망에만 씁니다.

## 왜 이 구조인가

Maya 2022의 내장 Python은 **3.7**이고, 공식 `mcp` 파이썬 SDK는 **3.10 이상**을 요구합니다.
즉 MCP 서버 본체는 Maya 안에서 돌 수 없습니다. 그래서 두 조각으로 나뉩니다.

```
Claude ─(stdio)─> server.py           PC 파이썬 3.10+ / FastMCP
                       │
                       │ TCP 127.0.0.1:20777, 길이 프리픽스 + JSON
                       ▼
                  maya_mcp_bridge.py  Maya 2022 내부 / Python 3.7 / 표준 라이브러리만
                       │ executeInMainThreadWithResult
                       ▼
                    maya.cmds
```

이 경계 덕분에 Maya를 2024/2026으로 올려도 브릿지 파일 하나만 확인하면 됩니다.

## 툴 구성

| 계층 | 툴 | 역할 |
|---|---|---|
| L0 | `maya_execute` | 임의 Python 실행. **여기서 모든 기능이 나옵니다** |
| L1 | `maya_search_commands`, `maya_command_help` | AI가 Maya API를 런타임에 탐색 |
| L3 | `maya_undo`, `maya_viewport_capture` | 되돌리기 · 결과 눈으로 검증 |
| — | `maya_scene_info`, `maya_ping` | 상태 파악 |

기존 서버 비교표에서 "Maya 2022에서는 undo와 뷰포트 캡처가 불가능"이라고 나오는 건
Maya의 한계가 아니라 그 서버들이 구현을 안 한 것뿐입니다. 둘 다 2022에서 잘 됩니다.

- **undo** — `maya_execute` 호출 하나를 `undoInfo(openChunk/closeChunk)` 로 묶습니다.
  AI가 무슨 짓을 하든 Ctrl+Z 한 번에 통째로 되돌아갑니다.
- **뷰포트 캡처** — `playblast` 로 PNG를 떠서 MCP 이미지로 돌려줍니다.
  AI가 자기 결과물을 보고 스스로 고칠 수 있게 되는 것이 자동화 품질을 가장 크게 가릅니다.

## 설치

### 1. Maya 쪽 (한 번만)

스크립트 에디터(**Python 탭**)에서 아래를 한 번 실행하면 끝입니다.

```python
import sys; sys.path.append(r"C:\path\to\maya-mcp")
import install_maya; install_maya.install()
```

두 가지가 설치됩니다.

- **자동 시작** — `~/Documents/maya/scripts/userSetup.py` 에 등록됩니다.
  다음부터는 Maya 를 켜기만 하면 브릿지가 대기 상태가 됩니다.
- **셸프 버튼** — `MCP` 셸프에 원버튼 시작/중지 토글이 생깁니다.
  셸프 탭이 많으면 **맨 끝**에 추가되니 탭 바를 오른쪽으로 넘겨서 찾으세요.

성공하면 출력창에 이렇게 찍힙니다:

```
[maya-mcp] 대기 중: 127.0.0.1:20777 (Maya 2022 / Python 3.7.7)
```

제거: `import install_maya; install_maya.uninstall()`

<details>
<summary>설치 없이 수동으로 띄우기</summary>

```python
import sys; sys.path.append(r"C:\path\to\maya-mcp")
import maya_mcp_bridge; maya_mcp_bridge.start()   # 중지는 stop()
```

플러그인 매니저에서 `maya_mcp_bridge.py` 를 로드해도 됩니다
(`initializePlugin` 이 `start()` 를 호출합니다).
</details>

> **코드를 수정한 뒤에는 Maya 를 재시작하세요.** `importlib.reload` 는 모듈 전역
> (`_server`)을 초기화해 실행 중인 서버를 `stop()` 할 수 없게 만들고, 포트가 물린 채
> `start()` 가 실패합니다. 셸프 버튼이 리로드를 하지 않는 이유이기도 합니다.

### 2. PC 쪽 (MCP 서버)

```bash
python -m venv .venv
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

`mcp` 2.x(`MCPServer`)와 1.x(`FastMCP`) 양쪽을 지원합니다 — `server.py` 가 임포트
시점에 알아서 고릅니다. 2.0.0 에서 검증했습니다.

### 3. 클라이언트 등록

Claude Desktop / Claude Code 설정에 추가:

```json
{
  "mcpServers": {
    "maya": {
      "command": "C:\\path\\to\\maya-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\maya-mcp\\server.py"]
    }
  }
}
```

Claude Code CLI 라면:

```bash
claude mcp add maya -- C:\path\to\maya-mcp\.venv\Scripts\python.exe C:\path\to\maya-mcp\server.py
```

## 테스트

세 개의 스위트가 있고, 전부 통과 상태입니다 (Maya 2022.x + Python 3.12.10 + `mcp` 2.0.0 검증).

```bash
# 1. Maya 쪽 로직 (31개) — Maya 의 Python 3.7 에서 실제로 돌립니다
"C:\Program Files\Autodesk\Maya2022\bin\mayapy.exe" tests/test_bridge_mayapy.py

# 2. 서버 전송 계층 (24개) — 목 브릿지 상대. Maya 불필요
.venv\Scripts\python.exe tests/test_server_protocol.py

# 3. MCP 엔드투엔드 (5개) — 실제 MCP 클라이언트로 핸드셰이크·툴 호출. Maya 불필요
.venv\Scripts\python.exe tests/test_e2e_stdio.py

# 4. GUI 스모크 — Maya 를 띄우고 브릿지를 start() 한 상태에서 실행
.venv\Scripts\python.exe tests/smoke_gui.py
```

1번은 `maya.standalone` 배치 모드에서 `_op_*` 함수를 직접 호출합니다. 소켓 계층을
여기서 테스트하지 않는 이유는, 배치 모드에는 idle 이벤트 루프가 없어
`executeInMainThreadWithResult` 가 반환되지 않기 때문입니다. 프레이밍은 2번이
목 브릿지로 검증합니다. **GUI 모드에서의 소켓 왕복과 뷰포트 캡처는 자동 테스트
범위 밖이므로, Maya 를 띄워서 직접 확인하세요.**

## 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `MAYA_MCP_HOST` | `127.0.0.1` | 브릿지 호스트 |
| `MAYA_MCP_PORT` | `20777` | 브릿지 포트 |
| `MAYA_MCP_TIMEOUT` | `180` | 요청 타임아웃(초). 무거운 작업이 많으면 늘리세요 |

## `maya_execute` 반환 규약

- **단일 표현식**이면 그 값이 결과입니다 — `cmds.ls(selection=True)`
- **여러 줄**이면 `result` 변수에 담긴 값이 결과입니다

```python
objs = cmds.ls(type="mesh")
result = {"count": len(objs), "names": objs[:10]}
```

`print` 출력과 예외 트레이스백도 함께 돌아오므로, 실패하면 AI가 에러를 읽고 스스로 고칩니다.

## 보안 — 읽고 넘어가세요

**이건 정의상 원격 코드 실행 통로입니다.**

- `127.0.0.1` 에만 바인딩합니다. `HOST` 를 `0.0.0.0` 으로 바꾸면 같은 네트워크의
  누구나 여러분 Maya에서 임의 코드를 실행할 수 있습니다. 바꾸지 마세요.
- 렌더 노드, 공용 워크스테이션, 신뢰할 수 없는 네트워크에서는 켜지 마세요.
- **프로덕션 씬으로 먼저 시험하지 마세요.** 사본으로 며칠 돌려보고 판단하세요.
- undo 청크가 있어도 여러 단계 작업을 완벽히 되돌린다는 보장은 없습니다.
  **작업 전에 저장하는 습관이 여전히 가장 확실한 안전망입니다.**

## 알려진 제약

- 무거운 작업 중에는 Maya UI가 멈춥니다. `maya.cmds` 가 메인 스레드에서만
  동작하므로 구조적으로 피할 수 없습니다. 타임아웃을 넉넉히 잡으세요.
- 배치 모드(`maya -batch`)에서는 뷰포트 캡처가 동작하지 않습니다.
- Maya의 undo 가 꺼져 있으면(`undoInfo -state off`) 되돌리기가 불가능합니다.
  `maya_ping` 이 `undo_enabled` 로 알려줍니다.

## 확장 방향

`maya_execute` 로 같은 코드를 반복해서 짜고 있다면, 그때 구조화 툴(L2)로 승격시키세요.
처음부터 툴을 많이 만드는 것이 가장 흔한 실패 패턴입니다. 승격 기준은 셋뿐입니다.

1. 파괴적이라 확인 게이트가 필요하다 (`scene_new`, `delete_history`)
2. 너무 자주 써서 매번 코드 짜는 게 낭비다 (`create_primitive`, `set_attribute`)
3. AI가 자주 틀린다 (스킨 바인드 옵션, UV 투영 파라미터)

## 라이선스

MIT
