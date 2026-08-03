# -*- coding: utf-8 -*-
"""언리얼 파이프라인 툴 (L2).

감사(읽기 전용) → 정리 → 생성 → 익스포트 순서를 전제로 설계했습니다.
보지 않고 고치면 되돌릴 수 없는 문제(뒤집힌 노멀 등)를 덮어쓰게 됩니다.
"""

from __future__ import annotations

from ..connection import MayaConnection


def register_unreal_tools(mcp, conn: MayaConnection) -> None:

    # ---------------------------------------------------------- 감사

    @mcp.tool()
    def maya_unreal_check(objects: list[str] | None = None, prefix: str = "SM_") -> dict:
        """언리얼 익스포트 전 지오메트리 문제를 감사합니다. 씬을 전혀 바꾸지 않습니다.

        정리나 익스포트를 하기 전에 항상 먼저 호출하세요.

        검사 항목: 프리즈 안 된 트랜스폼, 음수 스케일, 남은 컨스트럭션 히스토리,
        네이밍 규칙 위반, UV 누락, ngon, 논매니폴드/라미나 지오메트리, 씬 단위와 업 축.

        Args:
            objects: 대상 이름 목록(와일드카드 가능). 비우면 선택, 선택도 없으면 씬 전체 메시.
            prefix: 기대하는 네이밍 접두사. 스태틱 메시 "SM_", 스켈레탈 메시 "SK_".
        """
        return conn.call("unreal.check", {"objects": objects, "prefix": prefix})

    @mcp.tool()
    def maya_unreal_check_skeleton(root: str | None = None, primary_axis: str = "x",
                                   tolerance_deg: float = 1.0) -> dict:
        """조인트 오리엔트와 언리얼 스켈레톤 호환성을 감사합니다. 씬을 바꾸지 않습니다.

        **자동 수정을 하지 않습니다.** 조인트를 다시 오리엔트하면 이미 붙은 스킨
        웨이트가 깨지므로, 무엇을 어떻게 고칠지는 사람이 판단해야 합니다. 보고된
        문제를 사용자에게 전달하고, 고치라는 지시를 받기 전까지는 손대지 마세요.

        검사 항목: 자식 방향과 주축의 각도 편차, rotate/rotateAxis 잔여값, 조인트
        스케일, 길이 0 본, 루트 개수와 위치, rotateOrder 불일치,
        segmentScaleCompensate, 좌우 네이밍(_l/_r) 규칙과 짝 존재 여부.

        Args:
            root: 루트 조인트 이름. 비우면 씬의 모든 조인트 루트를 감사합니다.
            primary_axis: 자식을 향해야 하는 축. Maya/언리얼 관행은 "x".
            tolerance_deg: 이 각도를 넘는 편차만 문제로 봅니다.
        """
        return conn.call("unreal.check_skeleton", {
            "root": root, "primary_axis": primary_axis, "tolerance_deg": tolerance_deg})

    @mcp.tool()
    def maya_unreal_check_materials(objects: list[str] | None = None,
                                    prefix: str = "M_") -> dict:
        """머티리얼 슬롯을 감사합니다. 씬을 바꾸지 않습니다.

        언리얼에서 머티리얼 슬롯 하나는 드로우 콜 하나입니다. 메시당 슬롯 수가
        성능에 직접 영향을 주므로 익스포트 전에 확인할 가치가 큽니다.

        검사 항목: 메시당 슬롯 수, 기본 머티리얼(lambert1) 할당, 네이밍 규칙,
        페이스 단위 할당, 사용되지 않는 셰이딩 그룹.

        Args:
            objects: 대상. 비우면 선택, 선택도 없으면 씬 전체 메시.
            prefix: 기대하는 머티리얼 접두사. 언리얼 관행은 "M_".
        """
        return conn.call("unreal.check_materials", {"objects": objects, "prefix": prefix})

    # ---------------------------------------------------------- 정리

    @mcp.tool()
    def maya_unreal_prepare(objects: list[str] | None = None, prefix: str = "SM_",
                            freeze: bool = True, delete_history: bool = True,
                            pivot: str = "center", rename: bool = True,
                            dry_run: bool = False) -> dict:
        """익스포트 전 정리를 한 번에 수행합니다 (프리즈·히스토리·피벗·네이밍).

        먼저 maya_unreal_check 로 상태를 본 다음 호출하세요. 호출 전체가 하나의
        undo 단위로 묶이므로 Ctrl+Z 한 번에 되돌릴 수 있습니다.

        음수 스케일이 있으면 프리즈해도 노멀이 뒤집힌 채 남습니다. 이 툴은 경고만
        하고 고치지 않습니다 — 어느 면을 뒤집을지는 사람이 판단해야 합니다.

        Args:
            objects: 대상. 비우면 선택, 선택도 없으면 씬 전체 메시.
            prefix: 붙일 네이밍 접두사.
            freeze: 트랜스폼 프리즈 여부.
            delete_history: 컨스트럭션 히스토리 삭제 여부.
            pivot: "center"(바운딩박스 중심) | "base"(바닥) | "origin"(월드 원점) | "keep".
                바닥에 놓이는 프롭은 "base", 월드 기준 배치물은 "origin" 이 편합니다.
            rename: 접두사 리네임 수행 여부.
            dry_run: True 면 무엇을 할지만 보고하고 씬은 건드리지 않습니다.
        """
        return conn.call("unreal.prepare", {
            "objects": objects, "prefix": prefix, "freeze": freeze,
            "delete_history": delete_history, "pivot": pivot,
            "rename": rename, "dry_run": dry_run})

    @mcp.tool()
    def maya_unreal_cleanup_materials(prefix: str = "M_", rename: bool = True,
                                      delete_unused: bool = True,
                                      dry_run: bool = False) -> dict:
        """안전한 머티리얼 정리만 수행합니다 (미사용 셰이딩 그룹 삭제, 접두사 리네임).

        **서로 다른 머티리얼을 병합하지 않습니다.** 어느 것을 남기고 어느 것을
        버릴지는 룩뎁 판단이라 사람이 정해야 합니다. 슬롯을 줄여야 한다면 감사
        결과를 사용자에게 보여주고 지시를 받으세요.

        Args:
            prefix: 붙일 머티리얼 접두사.
            rename: 접두사 리네임 수행 여부. 레퍼런스된 머티리얼은 건너뜁니다.
            delete_unused: 멤버가 없는 셰이딩 그룹 삭제 여부.
            dry_run: True 면 무엇을 할지만 보고합니다.
        """
        return conn.call("unreal.cleanup_materials", {
            "prefix": prefix, "rename": rename,
            "delete_unused": delete_unused, "dry_run": dry_run})

    # ---------------------------------------------------------- 생성

    @mcp.tool()
    def maya_unreal_make_lods(objects: list[str] | None = None,
                              keep_percent: list[float] | None = None,
                              lod_group: bool = True, keep_borders: bool = True,
                              keep_hard_edges: bool = True, keep_uv_borders: bool = True,
                              dry_run: bool = False) -> dict:
        """원본을 복제해 LOD 메시를 만듭니다. 원본(LOD0)은 줄이지 않습니다.

        **만든 뒤 반드시 maya_viewport_capture 로 눈으로 확인하세요.** 삼각형 수와
        토폴로지 지표가 전부 정상이어도 실루엣이 깨져 있을 수 있습니다. 수치만
        보고 "완료" 라고 보고하지 마세요.

        감소율을 임의로 정하지 마세요. 실루엣이 무너지는 지점은 에셋마다 다릅니다.
        사용자가 값을 주지 않았다면 기본값으로 만든 뒤 결과를 보여주고 조정 여부를
        물어보세요. 캐릭터나 실루엣이 중요한 에셋은 첫 단계를 70~80 으로 올리는
        편이 안전하고, 배경 소품은 더 공격적으로 줄여도 됩니다.

        Args:
            objects: 대상. 비우면 선택, 선택도 없으면 씬 전체 메시.
            keep_percent: LOD1 부터 각 단계에서 **남길** 삼각형 비율(%). 원본 기준.
                생략하면 [50, 25, 12]. 예) [75, 50, 25] 는 더 보수적인 감소.
            lod_group: True 면 Maya LOD 그룹으로 묶습니다. 언리얼 FBX 임포터가
                이걸 인식해 LOD 를 자동 구성합니다.
            keep_borders: 메시 경계 보존. 열린 메시에서 끄면 형태가 무너집니다.
            keep_hard_edges: 하드 엣지와 크리스 보존. 각진 에셋에 중요합니다.
            keep_uv_borders: UV 경계 보존. 끄면 텍스처가 늘어납니다.
            dry_run: True 면 예상 삼각형 수만 계산하고 씬은 건드리지 않습니다.
        """
        return conn.call("unreal.make_lods", {
            "objects": objects, "keep_percent": keep_percent or [50, 25, 12],
            "lod_group": lod_group, "keep_borders": keep_borders,
            "keep_hard_edges": keep_hard_edges, "keep_uv_borders": keep_uv_borders,
            "dry_run": dry_run})

    @mcp.tool()
    def maya_unreal_make_collision(objects: list[str] | None = None, shape: str = "box",
                                   padding: float = 0.0, reduce_to: int = 200,
                                   dry_run: bool = False) -> dict:
        """언리얼 규칙에 맞는 콜리전 메시를 만듭니다 (UBX_/USP_/UCP_/UCX_).

        콜리전 메시는 원본과 **같은 FBX 에 함께** 익스포트해야 언리얼이 인식합니다.
        maya_unreal_export_fbx 호출 시 대상에 포함하세요.

        Args:
            objects: 대상. 비우면 선택, 선택도 없으면 씬 전체 메시.
            shape: 콜리전 형태.
                "box"     UBX_ — 바운딩박스. 정확하고 가장 가볍습니다. 대부분의 프롭에 충분.
                "sphere"  USP_ — 바운딩 스피어.
                "capsule" UCP_ — 캡슐. 캐릭터·기둥 형태에 적합.
                "convex"  UCX_ — **Maya 2022 에는 컨벡스 헐 명령이 없습니다.** 원본을
                          줄인 사본을 만들 뿐이라 볼록함이 보장되지 않습니다. 복잡한
                          형태라면 언리얼 스태틱 메시 에디터의 Auto Convex Collision 을
                          쓰라고 사용자에게 안내하세요.
            padding: 콜리전을 원본보다 이만큼 키웁니다(씬 단위). 관통 방지용.
            reduce_to: convex 일 때 목표 삼각형 수.
            dry_run: True 면 만들 이름과 크기만 보고합니다.
        """
        return conn.call("unreal.make_collision", {
            "objects": objects, "shape": shape, "padding": padding,
            "reduce_to": reduce_to, "dry_run": dry_run})

    # ---------------------------------------------------------- 출력

    @mcp.tool()
    def maya_unreal_export_fbx(path: str, objects: list[str] | None = None,
                               triangulate: bool = False, skins: bool = False,
                               blendshapes: bool = False, animation: bool = False,
                               up_axis: str = "y") -> dict:
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
        if not path:
            raise ValueError("path 가 필요합니다. 예: 'D:/export/SM_Prop.fbx'")
        return conn.call("unreal.export_fbx", {
            "path": path, "objects": objects, "triangulate": triangulate,
            "skins": skins, "blendshapes": blendshapes, "animation": animation,
            "up_axis": up_axis})
