# maya-mcp

Maya 2022 이상에서 쓰는 커스텀 MCP 서버. AI가 Maya를 직접 조작하게 해줍니다.

기존 오픈소스 서버들과 달리 **툴을 수백 개 나열하지 않습니다.** 코드 실행 + 자기탐색으로
전체 커버리지를 얻고, 구조화된 툴은 안전망에만 씁니다.

## 왜 이 구조인가

Maya 2022의 내장 Python은 **3.7**이고, 공식 `mcp` 파이썬 SDK는 **3.10 이상**을 요구합니다.
즉 MCP 서버 본체는 Maya 안에서 돌 수 없습니다. 그래서 두 조각으로 나뉩니다.

```
Claude ─(stdio)─> maya_mcp/           PC 파이썬 3.10+ / mcp SDK
                       │
                       │ TCP 127.0.0.1:20777
                       │ JSON-RPC 2.0 · 4바이트 길이 프리픽스
                       ▼
                  maya_mcp_bridge.py  Maya 2022 내부 / Python 3.7 / 표준 라이브러리만
                       │ executeInMainThreadWithResult
                       ▼
                    maya.cmds ← unreal_tools.py (L2 언리얼 파이프라인)
```

이 경계 덕분에 Maya를 2024/2026으로 올려도 브릿지 파일 하나만 확인하면 됩니다.

### 자매 프로젝트와의 정렬

[unreal-mcp-bridge](https://github.com/YomenStyle/unreal-mcp-bridge) 와 규약을 맞췄습니다.

| | 공통 |
|---|---|
| 메시지 | JSON-RPC 2.0 (`method` / `params` / `result` / `error`) |
| 에러 코드 | 표준 (-32700 ~ -32603) |
| 메서드명 | `<도메인>.<동작>` 점 네임스페이스 |
| 구조 | 패키지 + `tools/*.py` + `register_*_tools(mcp, conn)` |
| 스레드 | 워커 스레드 수신 → 메인/게임 스레드 디스패치 |

**의도적으로 다른 것 하나** — 프레이밍. 저쪽은 개행 구분 + 64KB 제한, 이쪽은
4바이트 길이 프리픽스에 제한 없음입니다. Maya 는 씬 덤프처럼 큰 응답이 흔해
(`cmds.ls()` 한 번에도 64KB를 넘길 수 있습니다) 64KB 제한을 쓸 수 없습니다.
자세한 내용은 [docs/PROTOCOL.md](docs/PROTOCOL.md).

### 파일 구조

```
maya_mcp/              PC 쪽 (Python 3.10+)
  config.py            MMCP_* 환경변수
  connection.py        JSON-RPC 클라이언트
  server.py            서버 조립 (build_server)
  tools/               script · scene · inspection · viewport · unreal
maya_mcp_bridge.py     Maya 내부 (Python 3.7, 표준 라이브러리만)
unreal_tools.py        Maya 내부, 언리얼 파이프라인 구현
install_maya.py        Maya 쪽 원버튼 설치
server.py              엔트리 포인트 (기존 MCP 등록 경로 유지용)
```

## 툴 목록 (16개)

| 계층 | 툴 | 역할 |
|---|---|---|
| L0 | `maya_execute` | 임의 Python 실행. **여기서 모든 기능이 나옵니다** |
| L1 | `maya_search_commands` | 이름으로 `maya.cmds` 명령 검색 |
| L1 | `maya_command_help` | 명령의 플래그·시그니처 조회 |
| L3 | `maya_undo` | 직전 작업 되돌리기 |
| L3 | `maya_viewport_capture` | 뷰포트를 이미지로 받아 결과 검증 |
| — | `maya_scene_info` | 단위·업축·프레임 범위·노드 개수 |
| — | `maya_ping` | 연결·버전 확인 |

### L2 — 언리얼 파이프라인 (`unreal_tools.py`)

| 툴 | 성격 | 역할 |
|---|---|---|
| `maya_unreal_check` | 읽기 전용 | 프리즈·음수 스케일·히스토리·네이밍·UV·ngon·논매니폴드 감사 |
| `maya_unreal_check_skeleton` | 읽기 전용 | 조인트 오리엔트 편차, rotate 잔여값, 루트·좌우 네이밍 감사 |
| `maya_unreal_check_materials` | 읽기 전용 | 슬롯 수(=드로우 콜), 기본 머티리얼, 페이스 단위 할당, 미사용 SG |
| `maya_unreal_prepare` | 변경 | 히스토리 삭제·프리즈·피벗·접두사 리네임 일괄 |
| `maya_unreal_cleanup_materials` | 변경 | 미사용 셰이딩 그룹 삭제, `M_` 리네임 |
| `maya_unreal_make_lods` | 생성 | LOD 메시 생성 + Maya LOD 그룹 |
| `maya_unreal_make_collision` | 생성 | `UBX_`/`USP_`/`UCP_`/`UCX_` 콜리전 |
| `maya_unreal_import_fbx` | 입력 | FBX 를 Maya 로 가져오기 (언리얼 레벨 에셋 수신) |
| `maya_unreal_export_fbx` | 출력 | 언리얼 프리셋 FBX |

**감사(읽기 전용)를 먼저 두는 구조입니다.** 보지 않고 고치면 되돌릴 수 없는 문제
(뒤집힌 노멀 등)를 덮어쓰게 됩니다.

자동화하지 않기로 한 것들 — 판단이 필요하기 때문입니다:

- **음수 스케일 수정** — 프리즈해도 노멀이 뒤집힌 채 남습니다. 어느 면을 뒤집을지는 사람이 정합니다.
- **조인트 재오리엔트** — 이미 붙은 스킨 웨이트가 깨집니다. 감사만 합니다.
- **머티리얼 병합** — 어느 것을 남길지는 룩뎁 판단입니다.
- **LOD 감소율 자동 결정** — 실루엣이 무너지는 지점은 에셋마다 다릅니다.

> ⚠️ **`UCX_` 컨벡스 콜리전 제약** — Maya 2022 에는 컨벡스 헐 명령이 없습니다
> (런타임 조사로 확인). `convex` 옵션은 원본을 줄인 사본일 뿐 볼록함이 보장되지
> 않습니다. 대부분의 프롭은 `box`(`UBX_`)로 충분하고, 복잡한 형태는 언리얼
> 스태틱 메시 에디터의 **Auto Convex Collision** 을 쓰는 편이 낫습니다.

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
pip install -e .              # 또는: pip install -r requirements.txt
```

`pip install -e .` 로 설치하면 `maya-mcp` 명령으로도 실행할 수 있습니다.

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

## 언리얼 익스포트 워크플로

```
1. maya_unreal_check              지오메트리 문제 확인
2. maya_unreal_check_materials    슬롯 수 확인
3. maya_unreal_prepare            프리즈·히스토리·피벗·네이밍
4. maya_unreal_cleanup_materials  미사용 셰이딩 그룹 정리
5. maya_unreal_make_collision     UBX_ 콜리전 생성
6. maya_unreal_make_lods          LOD 생성
7. maya_viewport_capture          눈으로 검증          ← 건너뛰지 마세요
8. maya_unreal_export_fbx         콜리전·LOD 포함 익스포트
```

7번이 핵심입니다. LOD 검증 중에 실제로 겪은 사례: 삼각형 수 10.3% 유지, ngon 0,
논매니폴드 0 — 지표는 전부 정상이었는데 캡처해보니 **실루엣이 눈에 띄게 깨져
있었습니다.** 수치만으로 품질을 판단할 수 없습니다.

콜리전 메시는 원본과 **같은 FBX 에 함께** 익스포트해야 언리얼이 인식합니다.

## 언리얼과의 에셋 왕복

Maya 쪽 절반은 완성돼 검증까지 마쳤습니다.

| 방향 | Maya 쪽 | 언리얼 쪽 |
|---|---|---|
| Maya → 언리얼 | `maya_unreal_export_fbx` ✅ | `asset.import_fbx` ❌ 미구현 |
| 언리얼 → Maya | `maya_unreal_import_fbx` ✅ | `level.export_selected` ❌ 미구현 |

FBX 왕복은 실기에서 좌표까지 보존되는 것을 확인했습니다(반지름 6.0 원형 배치를
내보냈다 되가져와 위치·삼각형 수 일치).

**언리얼 쪽 커맨드 3개는 아직 없습니다.** 자매 저장소
[unreal-mcp-bridge](https://github.com/YomenStyle/unreal-mcp-bridge) 에 C++ 로
추가해야 하며, JSON-RPC 계약과 구현 메모를 [docs/UNREAL_SIDE.md](docs/UNREAL_SIDE.md)
에 정리해 뒀습니다. 그 문서의 코드는 **빌드·검증되지 않았습니다.**

그전까지는 사용자가 언리얼에서 직접 뽑아둔 FBX 를 `maya_unreal_import_fbx` 로
받는 방식으로 쓸 수 있습니다.

## 테스트

네 개의 스위트가 있고, 전부 통과 상태입니다 (Maya 2022.x + Python 3.12.10 + `mcp` 2.0.0 검증).

```bash
# 1. Maya 쪽 로직 (31개) — Maya 의 Python 3.7 에서 실제로 돌립니다
"C:\Program Files\Autodesk\Maya2022\bin\mayapy.exe" tests/test_bridge_mayapy.py

# 2. 서버 전송 계층 (25개) — 목 브릿지 상대. Maya 불필요
.venv\Scripts\python.exe tests/test_server_protocol.py

# 3. MCP 엔드투엔드 (6개) — 실제 MCP 클라이언트로 핸드셰이크·툴 호출. Maya 불필요
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
| `MMCP_HOST` | `127.0.0.1` | 브릿지 호스트 |
| `MMCP_PORT` | `20777` | 브릿지 포트 |
| `MMCP_TIMEOUT` | `180` | 요청 타임아웃(초). 무거운 작업이 많으면 늘리세요 |

접두사는 unreal-mcp-bridge 의 `UMCP_` 와 맞춘 것입니다. 예전 `MAYA_MCP_*` 도
계속 인식하지만 `MMCP_*` 가 우선합니다.

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
