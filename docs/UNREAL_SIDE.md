# 언리얼 쪽에서 해야 하는 작업

Maya ↔ 언리얼 에셋 왕복을 완성하려면 [unreal-mcp-bridge](https://github.com/YomenStyle/unreal-mcp-bridge)
플러그인에 C++ 커맨드 3개를 추가해야 합니다. 이 문서는 그 명세입니다.

> **이 문서를 쓴 시점의 한계**
>
> 작성자(Claude)는 이 코드를 **빌드하지도 실행하지도 못했습니다.** 언리얼이
> 설치되지 않은 PC 에서 작성했습니다. 아래 코드는 unreal-mcp-bridge 의 기존
> 커맨드(`AssetCommands.cpp`, `EditorCommands.cpp`)의 규약을 읽고 그대로 맞춘
> 것이지만, **컴파일 오류와 API 시그니처 불일치를 예상하고 보셔야 합니다.**
>
> 특히 **UE 5.8 의 정확한 API 표면은 검증하지 못했습니다.** `UAssetImportTask`,
> `UFbxExportOption`, `UEditorActorSubsystem` 같은 클래스는 버전마다 필드가
> 바뀝니다. 빌드하면서 잡아야 합니다.
>
> Maya 쪽(`unreal.import_fbx`)은 Maya 2022 실기에서 검증을 마쳤습니다 —
> 익스포트한 FBX 를 되가져와 좌표(반지름 6.0, y=1.0)와 삼각형 수(60)가
> 정확히 보존되는 것을 확인했습니다.

---

## 현재 상태

### 이미 있는 것 (그대로 씁니다)

| 메서드 | 용도 |
|---|---|
| `editor.list_actors` | 레벨 액터 목록 + 위치. **UE→Maya 의 첫 단계로 그대로 사용 가능** |
| `editor.get_status` | 현재 레벨, 선택 수, PIE 상태 |
| `asset.list` | 콘텐츠 경로 아래 에셋 목록 |
| `asset.get_metadata` | 에셋 클래스·태그 |
| `asset.save` | 에셋 저장 |

### 없어서 추가해야 하는 것

| 메서드 | 방향 | 용도 |
|---|---|---|
| `level.export_selected` | UE → Maya | 선택 액터를 FBX 로 뽑기 |
| `asset.import_fbx` | Maya → UE | FBX 를 콘텐츠 브라우저로 |
| `level.spawn_asset` | Maya → UE | 임포트한 에셋을 레벨에 배치 |

### Maya 쪽 (완료됨)

| 툴 | 상태 |
|---|---|
| `maya_unreal_export_fbx` | 검증 완료 |
| `maya_unreal_import_fbx` | 검증 완료 |

---

## 왕복 흐름

**언리얼 → Maya**

```
1. editor.list_actors            (이미 있음)  어떤 액터가 있는지 확인
2. level.export_selected         (추가 필요)  선택 액터 → FBX
3. maya_unreal_import_fbx        (완료)       Maya 로 가져오기
```

**Maya → 언리얼**

```
1. maya_unreal_check / prepare   (완료)       정리
2. maya_unreal_export_fbx        (완료)       FBX 로 뽑기
3. asset.import_fbx              (추가 필요)  콘텐츠 브라우저로
4. level.spawn_asset             (추가 필요)  레벨에 배치 (선택)
```

FBX 파일은 두 프로그램이 같은 PC 에 있다는 전제로 **공유 폴더 경로**를 주고받습니다.
프로토콜로 바이너리를 실어 나르지 않습니다.

---

## 명세

### `level.export_selected`

선택된 액터를 하나의 FBX 로 익스포트합니다.

**요청**

```json
{
  "jsonrpc": "2.0", "id": 1,
  "method": "level.export_selected",
  "params": {
    "path": "D:/exchange/level_props.fbx",
    "selected_only": true,
    "include_collision": false,
    "ascii": false
  }
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `path` | string | ✔ | 저장 경로. 부모 폴더가 없으면 만듭니다 |
| `selected_only` | bool | | 기본 `true`. `false` 면 레벨 전체 |
| `include_collision` | bool | | 기본 `false`. UCX_ 등 콜리전 메시 포함 여부 |
| `ascii` | bool | | 기본 `false` (바이너리) |

**응답**

```json
{
  "result": {
    "path": "D:/exchange/level_props.fbx",
    "exists": true,
    "size_bytes": 128400,
    "actor_count": 12,
    "actors": ["SM_Wall_01", "SM_Wall_02"]
  }
}
```

**에러**: 선택이 비었으면 `InvalidParams`, 익스포트 실패면 `InternalError`.

---

### `asset.import_fbx`

FBX 를 콘텐츠 브라우저로 임포트합니다.

**요청**

```json
{
  "jsonrpc": "2.0", "id": 2,
  "method": "asset.import_fbx",
  "params": {
    "path": "D:/exchange/SM_Prop.fbx",
    "destination_path": "/Game/Props",
    "replace_existing": true,
    "import_as_skeletal": false,
    "skeleton_path": null,
    "combine_meshes": false,
    "generate_lightmap_uvs": true,
    "auto_generate_collision": false,
    "save": true
  }
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `path` | string | ✔ | FBX 경로 |
| `destination_path` | string | ✔ | `/Game/...` 형식의 콘텐츠 경로 |
| `replace_existing` | bool | | 기본 `true` |
| `import_as_skeletal` | bool | | 기본 `false`. `true` 면 스켈레탈 메시 |
| `skeleton_path` | string | | 기존 스켈레톤에 붙일 때 그 오브젝트 경로 |
| `combine_meshes` | bool | | 기본 `false`. Maya 에서 오브젝트별로 뽑았으면 `false` 유지 |
| `generate_lightmap_uvs` | bool | | 기본 `true` |
| `auto_generate_collision` | bool | | 기본 `false`. **Maya 에서 UBX_ 를 이미 만들었다면 반드시 `false`** — 켜면 언리얼이 자체 콜리전을 덧붙여 중복됩니다 |
| `save` | bool | | 기본 `true`. 임포트 후 패키지 저장 |

**응답**

```json
{
  "result": {
    "imported": ["/Game/Props/SM_Prop.SM_Prop"],
    "count": 1,
    "saved": true
  }
}
```

**에러**: 파일이 없으면 `InvalidParams`, 임포트 결과가 0개면 `InternalError`.

---

### `level.spawn_asset`

임포트한 에셋을 현재 레벨에 배치합니다.

**요청**

```json
{
  "jsonrpc": "2.0", "id": 3,
  "method": "level.spawn_asset",
  "params": {
    "object_path": "/Game/Props/SM_Prop.SM_Prop",
    "location": [0, 0, 0],
    "rotation": [0, 0, 0],
    "scale": [1, 1, 1],
    "label": "SM_Prop_01"
  }
}
```

`location` / `rotation` / `scale` 은 각각 3요소 숫자 배열, 전부 선택. `rotation` 은
Pitch/Yaw/Roll 순서(언리얼 `FRotator` 관행)입니다.

**응답**

```json
{ "result": { "actor_name": "SM_Prop_01", "class": "StaticMeshActor" } }
```

---

## 구현 메모

### 파일 추가

```
Plugin/UnrealMCPBridge/Source/UnrealMCPBridge/
  Public/Commands/LevelIOCommands.h
  Private/Commands/LevelIOCommands.cpp
```

`asset.import_fbx` 는 `asset.*` 네임스페이스지만, 임포트/익스포트를 한 파일에 모으는
편이 관리하기 쉽습니다. `AssetCommands.cpp` 에 넣어도 무방합니다 — 등록만 되면 됩니다.

### 헤더 (기존 규약 그대로)

```cpp
#pragma once

#include "CoreMinimal.h"
#include "IMCPCommandHandler.h"

// Handles level.* FBX exchange and asset.import_fbx.
class FLevelIOCommandHandler : public IMCPCommandHandler
{
public:
    virtual void RegisterCommands(FMCPCommandRegistry& Registry) override;
};
```

### 등록 (`MCPBridgeSubsystem.cpp`)

인클루드 추가 후, 기존 핸들러 목록 끝에 한 줄:

```cpp
#include "Commands/LevelIOCommands.h"
// ...
Handlers.Add(MakeShared<FLevelIOCommandHandler>());
```

### `Build.cs` 의존성 추가

```csharp
"AssetTools",          // 이미 있음
"UnrealEd",            // 이미 있음
"EditorScriptingUtilities",  // 이미 있음
// 아래 두 개가 새로 필요합니다
"MeshDescription",
"StaticMeshDescription",
```

FBX 익스포트에 `UExporter` / `UFbxExportOption` 을 쓰면 `UnrealEd` 로 충분할 수
있습니다. 링크 오류가 나면 그때 추가하세요.

### 핵심 API (UE 5.x 계열 — 5.8 에서 확인 필요)

**익스포트**

```cpp
UAssetExportTask* Task = NewObject<UAssetExportTask>();
Task->Object   = World;                 // 레벨 익스포트
Task->Exporter = nullptr;               // 확장자로 자동 선택
Task->Filename = Path;
Task->bSelected = bSelectedOnly;        // 선택만
Task->bAutomated = true;                // 다이얼로그 억제 — 필수
Task->bReplaceIdentical = true;
Task->bPrompt = false;
UExporter::RunAssetExportTask(Task);
```

`bAutomated = true` 를 빠뜨리면 에디터가 모달 다이얼로그를 띄우고, GameThread
디스패치가 타임아웃될 때까지 멈춥니다. **반드시 넣으세요.**

**임포트**

```cpp
UAssetImportTask* Task = NewObject<UAssetImportTask>();
Task->Filename         = Path;
Task->DestinationPath  = DestinationPath;
Task->bAutomated       = true;          // 위와 같은 이유로 필수
Task->bReplaceExisting = bReplaceExisting;
Task->bSave            = bSave;

UFbxImportUI* Options = NewObject<UFbxImportUI>(Task);
Options->bImportMesh       = true;
Options->bImportAsSkeletal = bImportAsSkeletal;
Options->bImportMaterials  = false;     // 머티리얼은 왕복 대상이 아닙니다
Options->bImportTextures   = false;
Options->StaticMeshImportData->bCombineMeshes            = bCombineMeshes;
Options->StaticMeshImportData->bGenerateLightmapUVs      = bGenerateLightmapUVs;
Options->StaticMeshImportData->bAutoGenerateCollision    = bAutoGenerateCollision;
Task->Options = Options;

IAssetTools& AssetTools = FModuleManager::LoadModuleChecked<FAssetToolsModule>("AssetTools").Get();
AssetTools.ImportAssetTasks({ Task });
// 결과: Task->GetObjects() / Task->ImportedObjectPaths
```

**스폰**

```cpp
UEditorActorSubsystem* ActorSubsystem =
    GEditor->GetEditorSubsystem<UEditorActorSubsystem>();
AActor* Spawned = ActorSubsystem->SpawnActorFromObject(LoadedAsset, Location, Rotation);
if (Spawned) { Spawned->SetActorScale3D(Scale); Spawned->SetActorLabel(Label); }
```

UE 5.1 이전의 `UEditorLevelLibrary::SpawnActorFromObject` 는 서브시스템으로
대체됐습니다. 5.8 이면 서브시스템 쪽을 쓰세요.

### 기존 규약 따를 것

- 인자 검증은 `UnrealMCPBridge::Json::RequireString(Params, TEXT("path"), Path, OutError)` 사용
- 에러는 `MCPProtocol::FMCPError::InvalidParams` / `InternalError`
- 응답은 `MakeShared<FJsonObject>()` 에 `SetStringField` / `SetNumberField` / `SetArrayField`
- 핸들러는 전부 GameThread 에서 호출되므로 에디터 API 를 직접 불러도 안전합니다

---

## 축·단위 주의

| | 언리얼 | Maya |
|---|---|---|
| 업 축 | Z-up | Y-up |
| 단위 | cm | cm (기본) |

언리얼 FBX 익스포터는 보통 **Y-up 으로 내보냅니다**. Maya 쪽 `maya_unreal_import_fbx`
의 기본값도 `up_axis="y"` 입니다. 임포트 결과가 **90도 누워 있으면** `up_axis="z"` 로
재시도하세요.

단위는 둘 다 cm 라 `scale_factor=1.0` 이면 맞습니다. Maya 씬 단위가 cm 가 아니면
`maya_unreal_import_fbx` 가 경고를 돌려줍니다.

**머티리얼은 왕복하지 않습니다.** 언리얼 머티리얼과 Maya 셰이더는 다른 물건입니다.
지오메트리·이름·슬롯 구성만 넘어가고, 룩은 언리얼에서 다시 잡아야 합니다.
그래서 임포트 옵션에서 `bImportMaterials = false` 로 둡니다.

**블루프린트 액터는 단순 FBX 로 나오지 않습니다.** 스태틱/스켈레탈 메시만
대상입니다. `level.export_selected` 는 블루프린트가 섞여 있으면 그 액터를 건너뛰고
`skipped` 목록으로 보고하는 편이 좋습니다.

---

## 검증 순서 제안

빌드가 되면 이 순서로 확인하는 것이 원인 추적에 유리합니다.

1. `level.export_selected` 로 큐브 하나 뽑기 → 파일이 생기는지, 크기가 0 이 아닌지
2. 그 FBX 를 `maya_unreal_import_fbx` 로 Maya 에 가져오기 → **축이 누워 있지 않은지 뷰포트로 확인**
3. Maya 에서 `maya_unreal_export_fbx` 로 되뽑기
4. `asset.import_fbx` 로 콘텐츠 브라우저에 넣기 → 스태틱 메시가 생기는지
5. `level.spawn_asset` 으로 레벨에 배치 → 위치·스케일이 맞는지

2번에서 축이 틀어지면 `up_axis` 를 바꿔 재시도하고, 어느 쪽이 맞는지 확인되면
이 문서의 기본값을 고쳐두세요.
