# JSON-RPC 2.0 프로토콜 명세

MCP 서버(PC 파이썬)와 Maya 내부 브릿지 사이의 규약입니다.
[unreal-mcp-bridge](https://github.com/YomenStyle/unreal-mcp-bridge) 와 메시지 형식·
에러 코드를 동일하게 맞췄습니다. **프레이밍만 다릅니다** — 아래 참고.

## 전송

- **프로토콜**: TCP 위의 JSON-RPC 2.0
- **주소**: `127.0.0.1:20777` (기본값, `MMCP_HOST` / `MMCP_PORT` 로 변경)
- **인코딩**: UTF-8
- **프레이밍**: **4바이트 빅엔디안 부호 없는 정수 길이 프리픽스** + JSON 본문
- **크기 제한**: 없음
- **연결**: 요청 1건당 1연결. 상태를 두지 않아 재연결 로직이 필요 없습니다.

### 프레이밍이 unreal-mcp-bridge 와 다른 이유

저쪽은 개행 구분(`\n`)에 64KB 제한입니다. Maya 는 씬 덤프나 노드 목록처럼 큰
응답을 일상적으로 돌려주고, 실제로 `cmds.ls()` 한 번에도 64KB를 넘길 수 있습니다.
테스트에서 **420KB 왕복 무손실**을 검증 항목으로 유지하고 있어 길이 프리픽스를
씁니다.

나중에 언리얼 플러그인 쪽에도 길이 프리픽스를 추가하면 완전히 같아집니다.
메시지 형식과 에러 코드는 이미 동일하므로 그 변경은 프레이밍 계층에만 국한됩니다.

## 메시지 형식

### 요청

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "scene.info",
  "params": {}
}
```

- `id`: 정수. 클라이언트가 호출마다 증가시킵니다.
- `params`: 객체. 값이 `null` 인 키는 클라이언트가 전송 전에 제거합니다.

### 성공 응답

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": { "scene": "<untitled>", "up_axis": "y" }
}
```

### 에러 응답

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": { "code": -32601, "message": "알 수 없는 메서드: 'nope'", "data": null }
}
```

`data` 에는 트레이스백이나 사용 가능한 메서드 목록이 들어갑니다.

## 에러 코드

| 코드 | 이름 | 의미 |
|---|---|---|
| -32700 | ParseError | JSON 파싱 실패 |
| -32600 | InvalidRequest | `method` 누락 등 구조 오류 |
| -32601 | MethodNotFound | 등록되지 않은 메서드 |
| -32602 | InvalidParams | 인자 이름/개수 불일치 |
| -32603 | InternalError | 핸들러 실행 중 예외 |

### 사용자 코드 실패는 에러가 아닙니다

`script.execute` 에 넘긴 코드가 예외를 내는 것은 **프로토콜 에러가 아니라 정상
결과**입니다. RPC 는 성공하고 `result` 안에 실패가 담깁니다:

```json
{ "result": { "ok": false, "error": "Traceback ...", "stdout": "" } }
```

브릿지 자체가 깨졌을 때만 `error` 를 씁니다. 이 구분 덕분에 AI 가 에러를 읽고
코드를 고쳐 다시 시도할 수 있습니다.

## 메서드 목록

`<도메인>.<동작>` 형식입니다.

| 메서드 | 씬 변경 | 설명 |
|---|---|---|
| `scene.ping` | | 버전·연결·undo 활성화 여부 |
| `scene.info` | | 단위·업축·프레임 범위·노드 개수 |
| `script.execute` | 자체 관리 | 임의 Python 실행 |
| `script.undo` | | 되돌리기 |
| `inspect.search_commands` | | 이름으로 `maya.cmds` 명령 검색 |
| `inspect.command_help` | | 명령의 플래그·시그니처 |
| `viewport.capture` | | playblast PNG, 경로 반환 |
| `unreal.check` | | 지오메트리 감사 |
| `unreal.check_skeleton` | | 조인트 오리엔트 감사 |
| `unreal.check_materials` | | 머티리얼 슬롯 감사 |
| `unreal.prepare` | ✔ | 프리즈·히스토리·피벗·네이밍 |
| `unreal.cleanup_materials` | ✔ | 미사용 SG 삭제, 리네임 |
| `unreal.make_lods` | ✔ | LOD 생성 |
| `unreal.make_collision` | ✔ | 콜리전 메시 생성 |
| `unreal.export_fbx` | | FBX 익스포트 |

**씬 변경** 표시가 있는 메서드는 브릿지가 자동으로 `undoInfo(openChunk/closeChunk)`
로 감쌉니다. 호출 하나가 Ctrl+Z 한 번에 되돌아갑니다. `script.execute` 는
`undo_chunk` 인자로 스스로 제어합니다.

## 스레드 규약

`maya.cmds` 는 스레드 세이프가 아닙니다. 소켓 수신은 워커 스레드에서 하고,
모든 핸들러는 `maya.utils.executeInMainThreadWithResult` 로 메인 스레드에
넘겨 실행합니다. unreal-mcp-bridge 의 GameThread 디스패치와 같은 개념입니다.

부작용: 무거운 작업 중에는 Maya UI 가 멈춥니다. 구조상 피할 수 없습니다.
