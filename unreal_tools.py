# -*- coding: utf-8 -*-
"""
unreal_tools.py — 언리얼 익스포트 파이프라인용 L2 도구 (Maya 내부 / Python 3.7)

maya_mcp_bridge 와 같은 폴더에 두면 됩니다. 브릿지의 execute 로 임포트해서 씁니다.
Maya 밖에서는 임포트되지 않습니다.

설계 원칙
---------
- check() 는 아무것도 바꾸지 않습니다. 먼저 보고 나서 고칩니다.
- prepare() 는 되돌릴 수 있어야 합니다. 호출자가 undo 청크로 감쌉니다.
- 판단이 필요한 것(웨이트 품질 등)은 다루지 않습니다. 규칙이 명확한 것만.
"""

import math
import os

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.mel as mel


# 언리얼 에셋 네이밍 규칙 (Epic 표준)
PREFIX_STATIC_MESH = "SM_"
PREFIX_SKELETAL_MESH = "SK_"
PREFIX_MATERIAL = "M_"
PREFIX_TEXTURE = "T_"

PIVOT_MODES = ("center", "base", "origin", "keep")


# ------------------------------------------------------------ 유틸

def _resolve(objects):
    """대상 목록을 정규화합니다. None 이면 선택, 선택도 없으면 씬의 모든 메시."""
    if objects:
        if isinstance(objects, str):
            objects = [objects]
        found = []
        for o in objects:
            found.extend(cmds.ls(o, type="transform", long=True) or [])
        return sorted(set(found))

    sel = cmds.ls(selection=True, type="transform", long=True) or []
    if sel:
        return sel

    meshes = cmds.ls(type="mesh", long=True, noIntermediate=True) or []
    parents = []
    for m in meshes:
        p = cmds.listRelatives(m, parent=True, fullPath=True) or []
        parents.extend(p)
    return sorted(set(parents))


def _short(name):
    return name.rsplit("|", 1)[-1]


def _shapes(obj):
    return cmds.listRelatives(obj, shapes=True, fullPath=True,
                              noIntermediate=True, type="mesh") or []


def _history_nodes(obj):
    """셰이프에 남아있는 컨스트럭션 히스토리 노드."""
    out = []
    for shape in _shapes(obj):
        hist = cmds.listHistory(shape, pruneDagObjects=True) or []
        for node in hist:
            if node == shape:
                continue
            if cmds.nodeType(node) in ("shadingEngine", "groupId", "groupParts"):
                continue
            out.append(node)
    return sorted(set(out))


def _transform_state(obj):
    t = cmds.getAttr(obj + ".translate")[0]
    r = cmds.getAttr(obj + ".rotate")[0]
    s = cmds.getAttr(obj + ".scale")[0]
    return {
        "translate": [round(v, 5) for v in t],
        "rotate": [round(v, 5) for v in r],
        "scale": [round(v, 5) for v in s],
        "frozen": (all(abs(v) < 1e-5 for v in t)
                   and all(abs(v) < 1e-5 for v in r)
                   and all(abs(v - 1.0) < 1e-5 for v in s)),
        "negative_scale": any(v < 0 for v in s),
    }


def _pivot_offset(obj):
    """로컬 회전 피벗이 바운딩박스 중심에서 얼마나 떨어져 있는지."""
    try:
        bbox = cmds.exactWorldBoundingBox(obj)
    except Exception:
        return None
    center = [(bbox[0] + bbox[3]) / 2.0, (bbox[1] + bbox[4]) / 2.0, (bbox[2] + bbox[5]) / 2.0]
    rp = cmds.xform(obj, q=True, ws=True, rotatePivot=True)
    return round(math.sqrt(sum((a - b) ** 2 for a, b in zip(center, rp))), 4)


def _topology_issues(obj):
    """ngon / 논매니폴드 / 라미나 페이스. 선택 상태를 건드리므로 복원합니다."""
    issues = {"ngons": 0, "nonmanifold_edges": 0, "nonmanifold_verts": 0, "lamina": 0}
    shapes = _shapes(obj)
    if not shapes:
        return issues

    for shape in shapes:
        for key, flag in (("nonmanifold_edges", "nonManifoldEdges"),
                          ("nonmanifold_verts", "nonManifoldVertices"),
                          ("lamina", "laminaFaces")):
            try:
                found = cmds.polyInfo(shape, **{flag: True}) or []
                issues[key] += len(found)
            except Exception:
                pass

    saved = cmds.ls(selection=True, long=True) or []
    try:
        cmds.select(shapes, replace=True)
        # mode=3: 현재 선택 안에서, type=0x0008: 페이스, size=3: 5각형 이상
        cmds.polySelectConstraint(mode=3, type=0x0008, size=3)
        issues["ngons"] = len(cmds.ls(selection=True, flatten=True) or [])
    except Exception:
        pass
    finally:
        try:
            cmds.polySelectConstraint(disable=True)
        except Exception:
            pass
        if saved:
            cmds.select(saved, replace=True)
        else:
            cmds.select(clear=True)
    return issues


def _uv_state(obj):
    shapes = _shapes(obj)
    if not shapes:
        return {"uv_sets": [], "has_uvs": False}
    sets_ = []
    has = False
    for shape in shapes:
        sets_.extend(cmds.polyUVSet(shape, q=True, allUVSets=True) or [])
        try:
            if cmds.polyEvaluate(shape, uvcoord=True) > 0:
                has = True
        except Exception:
            pass
    return {"uv_sets": sorted(set(sets_)), "has_uvs": has}


# ------------------------------------------------------------ 1. 감사

def check(objects=None, prefix=PREFIX_STATIC_MESH):
    """익스포트 전에 무엇이 문제인지 보고합니다. 씬을 바꾸지 않습니다."""
    targets = _resolve(objects)

    scene = {
        "linear_unit": cmds.currentUnit(q=True, linear=True),
        "up_axis": cmds.upAxis(q=True, axis=True),
    }
    scene_warnings = []
    if scene["linear_unit"] != "cm":
        scene_warnings.append(
            "씬 단위가 %s 입니다. 언리얼은 cm 기준이라 임포트 시 스케일이 틀어집니다."
            % scene["linear_unit"])
    if scene["up_axis"] != "y":
        scene_warnings.append(
            "업 축이 %s 입니다. Maya 기본은 y 이고, FBX 익스포터가 변환을 처리합니다."
            % scene["up_axis"])

    report = []
    for obj in targets:
        if not _shapes(obj):
            continue
        xform = _transform_state(obj)
        topo = _topology_issues(obj)
        uv = _uv_state(obj)
        hist = _history_nodes(obj)
        name = _short(obj)

        problems = []
        if not xform["frozen"]:
            problems.append("트랜스폼이 프리즈되지 않음")
        if xform["negative_scale"]:
            problems.append("음수 스케일 — 프리즈해도 노멀이 뒤집힌 채 남습니다. "
                            "Mesh > Conform 또는 Reverse 로 따로 고쳐야 합니다")
        if hist:
            problems.append("컨스트럭션 히스토리 %d개" % len(hist))
        if prefix and not name.startswith(prefix):
            problems.append("이름이 '%s' 로 시작하지 않음" % prefix)
        if not uv["has_uvs"]:
            problems.append("UV 없음 — 언리얼에서 라이트맵 생성이 실패합니다")
        if topo["ngons"]:
            problems.append("ngon %d개 (5각형 이상)" % topo["ngons"])
        if topo["nonmanifold_edges"] or topo["nonmanifold_verts"]:
            problems.append("논매니폴드 지오메트리")
        if topo["lamina"]:
            problems.append("라미나 페이스 %d개" % topo["lamina"])
        if len(_shapes(obj)) > 1:
            problems.append("트랜스폼 하나에 셰이프 %d개" % len(_shapes(obj)))

        report.append({
            "object": name,
            "clean": not problems,
            "problems": problems,
            "transform": xform,
            "history_count": len(hist),
            "pivot_offset_from_center": _pivot_offset(obj),
            "uv": uv,
            "topology": topo,
        })

    return {
        "scene": scene,
        "scene_warnings": scene_warnings,
        "checked": len(report),
        "clean": sum(1 for r in report if r["clean"]),
        "objects": report,
    }


# ------------------------------------------------------------ 2. 정리

def prepare(objects=None, prefix=PREFIX_STATIC_MESH, freeze=True,
            delete_history=True, pivot="center", rename=True, dry_run=False):
    """익스포트 전 정리를 한 번에 수행합니다.

    pivot: "center" | "base" (바운딩박스 바닥) | "origin" (월드 원점) | "keep"
    dry_run=True 면 무엇을 할지만 돌려주고 아무것도 바꾸지 않습니다.
    """
    if pivot not in PIVOT_MODES:
        raise ValueError("pivot 은 %s 중 하나여야 합니다 (받은 값: %r)"
                         % (", ".join(PIVOT_MODES), pivot))

    targets = _resolve(objects)
    actions = []

    for obj in targets:
        if not _shapes(obj):
            continue
        done = []
        current = obj
        name = _short(current)

        if delete_history:
            hist = _history_nodes(current)
            if hist:
                done.append("히스토리 %d개 삭제" % len(hist))
                if not dry_run:
                    cmds.delete(current, constructionHistory=True)

        if freeze:
            state = _transform_state(current)
            if not state["frozen"]:
                done.append("트랜스폼 프리즈")
                if not dry_run:
                    # apply=True 가 핵심입니다. apply=False 는 "Reset Transformations"
                    # 로 동작해서 값을 0 으로 되돌려 버립니다.
                    cmds.makeIdentity(current, apply=True, translate=True,
                                      rotate=True, scale=True, normal=0,
                                      preserveNormals=True)
            if state["negative_scale"]:
                done.append("경고: 음수 스케일 — 노멀이 뒤집힌 채 남습니다 (수동 확인 필요)")

        if pivot != "keep" and not dry_run:
            if pivot == "center":
                cmds.xform(current, centerPivots=True)
            elif pivot == "origin":
                cmds.xform(current, ws=True, rotatePivot=(0, 0, 0),
                           scalePivot=(0, 0, 0))
            elif pivot == "base":
                cmds.xform(current, centerPivots=True)
                bbox = cmds.exactWorldBoundingBox(current)
                rp = cmds.xform(current, q=True, ws=True, rotatePivot=True)
                cmds.xform(current, ws=True,
                           rotatePivot=(rp[0], bbox[1], rp[2]),
                           scalePivot=(rp[0], bbox[1], rp[2]))
        if pivot != "keep":
            done.append("피벗: %s" % pivot)

        if rename and prefix and not name.startswith(prefix):
            new_name = prefix + name
            done.append("이름 변경: %s -> %s" % (name, new_name))
            if not dry_run:
                current = cmds.rename(current, new_name)
                name = _short(current)

        actions.append({"object": name, "actions": done})

    return {
        "dry_run": dry_run,
        "processed": len(actions),
        "prefix": prefix,
        "pivot_mode": pivot,
        "details": actions,
    }


# ------------------------------------------------------------ 3. 스켈레톤 감사

AXES = {
    "x": om.MVector(1, 0, 0), "y": om.MVector(0, 1, 0), "z": om.MVector(0, 0, 1),
    "-x": om.MVector(-1, 0, 0), "-y": om.MVector(0, -1, 0), "-z": om.MVector(0, 0, -1),
}

SIDE_SUFFIXES = ("_l", "_r")          # 언리얼 표준. _L/_R, _left, Lf_ 등은 비표준.
NONSTANDARD_SIDE = ("_L", "_R", "_left", "_right", "_Left", "_Right",
                    "_lf", "_rt", "Lf_", "Rt_")


def _joint_roots(root=None):
    if root:
        names = [root] if isinstance(root, str) else list(root)
        found = []
        for n in names:
            found.extend(cmds.ls(n, type="joint", long=True) or [])
        return sorted(set(found))
    all_joints = cmds.ls(type="joint", long=True) or []
    roots = []
    for j in all_joints:
        parent = cmds.listRelatives(j, parent=True, fullPath=True, type="joint")
        if not parent:
            roots.append(j)
    return roots


def _aim_deviation(joint, child, primary_axis):
    """자식 방향이 조인트 로컬의 주축에서 몇 도 벗어나 있는지.

    조인트 오리엔트가 어긋나면 리타겟과 IK 가 틀어지므로, 언리얼 파이프라인에서
    가장 먼저 확인해야 하는 값입니다.
    """
    jp = om.MVector(*cmds.xform(joint, q=True, ws=True, translation=True))
    cp = om.MVector(*cmds.xform(child, q=True, ws=True, translation=True))
    delta = cp - jp
    if delta.length() < 1e-6:
        return None, None            # 길이 0 본
    world_dir = delta.normal()

    matrix = om.MMatrix(cmds.xform(joint, q=True, ws=True, matrix=True))
    local_dir = (world_dir * matrix.inverse()).normal()

    target = AXES[primary_axis]
    dot = max(-1.0, min(1.0, local_dir * target))
    deviation = math.degrees(math.acos(dot))

    # 실제로는 어느 축에 가장 가까운지도 알려줍니다.
    best, best_dot = None, -2.0
    for name, vec in AXES.items():
        d = local_dir * vec
        if d > best_dot:
            best, best_dot = name, d
    return round(deviation, 2), best


def check_skeleton(root=None, primary_axis="x", tolerance_deg=1.0,
                   require_ssc_off=True):
    """조인트 오리엔트와 언리얼 호환성을 감사합니다. 씬을 바꾸지 않습니다.

    자동 수정은 제공하지 않습니다. 오리엔트를 다시 잡으면 이미 붙은 스킨 웨이트가
    깨지므로, 무엇을 어떻게 고칠지는 사람이 판단해야 합니다.

    root: 루트 조인트 이름. 비우면 씬의 모든 조인트 루트를 찾습니다.
    primary_axis: 자식을 향해야 하는 축. Maya/언리얼 관행은 "x".
    tolerance_deg: 이 각도를 넘으면 오리엔트 어긋남으로 봅니다.
    """
    if primary_axis not in AXES:
        raise ValueError("primary_axis 는 %s 중 하나여야 합니다" % ", ".join(sorted(AXES)))

    roots = _joint_roots(root)
    scene_warnings = []
    if not roots:
        return {"error": "조인트를 찾지 못했습니다.", "roots": [], "joints": []}
    if len(roots) > 1:
        scene_warnings.append(
            "루트 조인트가 %d개입니다. 언리얼은 스켈레탈 메시당 루트 하나를 요구합니다: %s"
            % (len(roots), ", ".join(_short(r) for r in roots)))

    for r in roots:
        pos = cmds.xform(r, q=True, ws=True, translation=True)
        if any(abs(v) > 1e-4 for v in pos):
            scene_warnings.append(
                "루트 '%s' 가 월드 원점에 있지 않습니다 %s. 언리얼에서 위치 오프셋이 생깁니다."
                % (_short(r), [round(v, 3) for v in pos]))
        if _short(r) != "root":
            scene_warnings.append(
                "루트 이름이 '%s' 입니다. UE5 마네킹 호환을 원하면 'root' 를 권합니다."
                % _short(r))

    joints = []
    for r in roots:
        joints.extend(cmds.ls(r, dag=True, type="joint", long=True) or [])
    joints = sorted(set(joints))

    names_short = set(_short(j) for j in joints)
    report = []

    for j in joints:
        name = _short(j)
        problems = []

        rot = cmds.getAttr(j + ".rotate")[0]
        if any(abs(v) > 1e-4 for v in rot):
            problems.append("rotate 가 0이 아님 %s — 바인드 포즈가 더럽습니다. "
                            "jointOrient 로 옮겨야 합니다" % [round(v, 3) for v in rot])

        raxis = cmds.getAttr(j + ".rotateAxis")[0]
        if any(abs(v) > 1e-4 for v in raxis):
            problems.append("rotateAxis 가 0이 아님 %s — 숨은 회전이라 FBX 로 잘 안 넘어갑니다"
                            % [round(v, 3) for v in raxis])

        scl = cmds.getAttr(j + ".scale")[0]
        if any(abs(v - 1.0) > 1e-4 for v in scl):
            problems.append("scale 이 1이 아님 %s" % [round(v, 3) for v in scl])

        # segmentScaleCompensate 는 Maya 기본값이 켜짐이라 조인트마다 찍으면 노이즈가
        # 됩니다. 아래에서 씬 레벨로 한 번만 집계합니다.
        ssc = cmds.getAttr(j + ".segmentScaleCompensate")

        rorder = cmds.getAttr(j + ".rotateOrder")
        children = cmds.listRelatives(j, children=True, type="joint", fullPath=True) or []

        deviation, closest = (None, None)
        if len(children) == 1:
            deviation, closest = _aim_deviation(j, children[0], primary_axis)
            if deviation is None:
                problems.append("길이 0 본 — 자식이 같은 위치에 있습니다")
            elif deviation > tolerance_deg:
                problems.append(
                    "오리엔트 어긋남: 자식 방향이 %s 축에서 %.2f° 벗어남 (가장 가까운 축: %s)"
                    % (primary_axis, deviation, closest))
        elif len(children) > 1:
            # 분기 조인트는 어느 자식을 향해야 하는지 규칙이 없으므로 판정하지 않습니다.
            problems.append("자식 %d개인 분기 조인트 — 오리엔트는 수동 확인 필요" % len(children))

        for bad in NONSTANDARD_SIDE:
            if bad in name:
                problems.append("비표준 좌우 표기 '%s' — 언리얼 관행은 '_l' / '_r'" % bad)
                break

        for suffix, mirror in (("_l", "_r"), ("_r", "_l")):
            if name.endswith(suffix):
                partner = name[: -len(suffix)] + mirror
                if partner not in names_short:
                    problems.append("좌우 짝 없음: '%s' 를 찾을 수 없습니다" % partner)
                break

        report.append({
            "joint": name,
            "clean": not problems,
            "problems": problems,
            "children": len(children),
            "aim_deviation_deg": deviation,
            "closest_axis": closest,
            "rotate": [round(v, 4) for v in rot],
            "scale": [round(v, 4) for v in scl],
            "segment_scale_compensate": bool(ssc),
            "rotate_order": rorder,
        })

    orders = set(r["rotate_order"] for r in report)
    if len(orders) > 1:
        scene_warnings.append("rotateOrder 가 조인트마다 다릅니다 %s — 통일을 권합니다"
                              % sorted(orders))

    ssc_on = [r["joint"] for r in report if r["segment_scale_compensate"]]
    if require_ssc_off and ssc_on:
        scene_warnings.append(
            "segmentScaleCompensate 가 %d/%d 조인트에서 켜져 있습니다 (Maya 기본값). "
            "언리얼은 이 기능을 지원하지 않으므로, 리그에 스케일이 들어간다면 꺼야 "
            "결과가 일치합니다. 스케일을 쓰지 않는다면 무시해도 됩니다."
            % (len(ssc_on), len(report)))

    # 편차가 실제로 문제인 것만 추립니다. 전부 0이면 보고할 게 없습니다.
    worst = sorted((r for r in report
                    if r["aim_deviation_deg"] is not None
                    and r["aim_deviation_deg"] > tolerance_deg),
                   key=lambda r: -r["aim_deviation_deg"])[:5]

    return {
        "roots": [_short(r) for r in roots],
        "primary_axis": primary_axis,
        "tolerance_deg": tolerance_deg,
        "scene_warnings": scene_warnings,
        "joint_count": len(report),
        "clean": sum(1 for r in report if r["clean"]),
        "worst_deviations": [{"joint": w["joint"], "deg": w["aim_deviation_deg"],
                              "closest_axis": w["closest_axis"]} for w in worst],
        "joints": report,
    }


# ------------------------------------------------------------ 4. LOD 생성

def _tri_count(obj):
    try:
        return int(cmds.polyEvaluate(obj, triangle=True))
    except Exception:
        return 0


def _strip_lod_suffix(name):
    import re as _re
    return _re.sub(r"_LOD\d+$", "", name)


def make_lods(objects=None, keep_percent=(50, 25, 12), lod_group=True,
              keep_borders=True, keep_hard_edges=True, keep_uv_borders=True,
              preserve_topology=True, dry_run=False):
    """원본을 복제해 LOD 메시를 만듭니다. 원본(LOD0)은 그대로 둡니다.

    keep_percent: LOD1 부터 각 단계에서 **남길** 삼각형 비율(%). 원본 기준입니다.
        (50, 25, 12) 이면 LOD1=50%, LOD2=25%, LOD3=12%.
        기본값은 흔한 출발점일 뿐입니다. 실루엣이 중요한 에셋은 첫 단계를
        70~80 으로 올리고, 배경 소품은 더 공격적으로 줄여도 됩니다.
    lod_group: True 면 Maya LOD 그룹으로 묶습니다. 언리얼 FBX 임포터가 이걸
        인식해 LOD 를 자동 구성합니다. False 면 이름만 붙은 별개 메시가 됩니다.

    감소율은 자동으로 정하지 않습니다. 실루엣이 깨지는 지점은 에셋마다 달라서
    사람이 결과를 보고 판단해야 합니다. 만든 뒤 뷰포트 캡처로 비교하세요.
    """
    levels = [float(p) for p in keep_percent]
    for p in levels:
        if not (0 < p < 100):
            raise ValueError("keep_percent 는 0 초과 100 미만이어야 합니다 (받은 값: %s)" % p)

    targets = _resolve(objects)
    results = []

    for obj in targets:
        if not _shapes(obj):
            continue
        base = _strip_lod_suffix(_short(obj))
        original_tris = _tri_count(obj)
        entry = {
            "source": _short(obj),
            "base_name": base,
            "lod0_tris": original_tris,
            "lods": [],
        }

        if dry_run:
            for i, keep in enumerate(levels, start=1):
                entry["lods"].append({
                    "name": "%s_LOD%d" % (base, i),
                    "target_keep_percent": keep,
                    "estimated_tris": int(round(original_tris * keep / 100.0)),
                })
            results.append(entry)
            continue

        # LOD0 = 원본. 이름만 맞춰줍니다.
        lod0 = obj
        if not _short(obj).endswith("_LOD0"):
            lod0 = cmds.rename(obj, base + "_LOD0")
        chain = [lod0]

        for i, keep in enumerate(levels, start=1):
            dup = cmds.duplicate(lod0, name="%s_LOD%d" % (base, i))[0]
            # polyReduce 의 percentage 는 '제거할' 비율입니다. 남길 비율의 보수.
            cmds.polyReduce(
                dup,
                version=1,                       # 신형 알고리즘. 구형보다 실루엣 보존이 낫습니다.
                percentage=100.0 - keep,
                preserveTopology=preserve_topology,
                keepBorder=keep_borders,
                keepMapBorder=keep_uv_borders,
                keepColorBorder=keep_borders,
                keepFaceGroupBorder=keep_borders,
                keepHardEdge=keep_hard_edges,
                keepCreaseEdge=keep_hard_edges,
                keepQuadsWeight=1.0,
                useVirtualSymmetry=0,
                replaceOriginal=True,
                constructionHistory=False,
            )
            actual = _tri_count(dup)
            topo = _topology_issues(dup)
            entry["lods"].append({
                "name": _short(dup),
                "target_keep_percent": keep,
                "actual_tris": actual,
                "actual_keep_percent": (round(actual * 100.0 / original_tris, 1)
                                        if original_tris else None),
                "ngons": topo["ngons"],
                "nonmanifold": topo["nonmanifold_edges"] + topo["nonmanifold_verts"],
            })
            chain.append(dup)

        if lod_group and len(chain) > 1:
            saved = cmds.ls(selection=True, long=True) or []
            try:
                cmds.select(chain, replace=True)   # 선택 순서가 LOD 순서가 됩니다
                mel.eval("LevelOfDetailGroup")
                grp = (cmds.ls(selection=True, long=True) or [None])[0]
                entry["lod_group"] = _short(grp) if grp else None
            except Exception as exc:
                entry["lod_group"] = None
                entry["lod_group_error"] = str(exc)
            finally:
                if saved:
                    cmds.select(saved, replace=True)
                else:
                    cmds.select(clear=True)

        results.append(entry)

    return {
        "dry_run": dry_run,
        "processed": len(results),
        "keep_percent": levels,
        "lod_group": lod_group,
        "objects": results,
        "note": ("LOD 는 수치만으로 판단할 수 없습니다. maya_viewport_capture 로 "
                 "각 단계를 눈으로 비교하세요."),
    }


# ------------------------------------------------------------ 5. 머티리얼 슬롯

DEFAULT_SGS = ("initialShadingGroup", "initialParticleSE")
DEFAULT_MATERIALS = ("lambert1", "particleCloud1", "standardSurface1")


def _shading_groups(obj):
    sgs = []
    for shape in _shapes(obj):
        sgs.extend(cmds.listConnections(shape, type="shadingEngine") or [])
    return sorted(set(sgs))


def _material_of(sg):
    mats = cmds.listConnections(sg + ".surfaceShader") or []
    return mats[0] if mats else None


def _unused_shading_groups():
    out = []
    for sg in (cmds.ls(type="shadingEngine") or []):
        if sg in DEFAULT_SGS:
            continue
        if not (cmds.sets(sg, q=True) or []):
            out.append(sg)
    return sorted(out)


def check_materials(objects=None, prefix=PREFIX_MATERIAL):
    """머티리얼 슬롯을 감사합니다. 씬을 바꾸지 않습니다.

    언리얼에서 머티리얼 슬롯 하나는 드로우 콜 하나입니다. 메시당 슬롯 수를
    줄이는 것이 성능에 직접 영향을 줍니다.
    """
    targets = _resolve(objects)
    report = []

    for obj in targets:
        if not _shapes(obj):
            continue
        sgs = _shading_groups(obj)
        mats = [m for m in (_material_of(s) for s in sgs) if m]
        problems = []

        if not sgs:
            problems.append("머티리얼이 할당되지 않음")
        if len(sgs) > 1:
            problems.append("머티리얼 슬롯 %d개 — 언리얼에서 드로우 콜 %d회. "
                            "합칠 수 있는지 검토하세요" % (len(sgs), len(sgs)))
        for m in mats:
            if m in DEFAULT_MATERIALS:
                problems.append("기본 머티리얼 '%s' 이 할당됨 — 전용 머티리얼을 "
                                "만들어 붙이세요" % m)
            elif prefix and not m.startswith(prefix):
                problems.append("머티리얼 '%s' 이 '%s' 로 시작하지 않음" % (m, prefix))

        # 페이스 단위 할당 여부. 셰이딩 그룹 멤버에는 씬의 다른 오브젝트도 들어
        # 있으므로(특히 initialShadingGroup), 반드시 이 메시의 셰이프로 걸러야
        # 합니다. 안 그러면 lambert1 만 쓰는 오브젝트가 전부 오탐으로 잡힙니다.
        # 컴포넌트 멤버는 셰이프가 아니라 트랜스폼 이름으로 기록되는 경우가 있어
        # (예: "matTest_multi.f[0:20]") 둘 다 대조해야 합니다.
        own_names = set(_short(s) for s in _shapes(obj))
        own_names.add(_short(obj))
        face_assigned = False
        for sg in sgs:
            for member in (cmds.sets(sg, q=True) or []):
                text = str(member)
                if "." not in text:
                    continue
                node = text.split(".", 1)[0].rsplit("|", 1)[-1]
                if node in own_names:
                    face_assigned = True
                    break
            if face_assigned:
                break
        if face_assigned:
            problems.append("페이스 단위 머티리얼 할당 — 언리얼로 넘어가지만 "
                            "슬롯이 늘어납니다")

        report.append({
            "object": _short(obj),
            "clean": not problems,
            "problems": problems,
            "slot_count": len(sgs),
            "materials": mats,
            "face_level_assignment": face_assigned,
        })

    unused = _unused_shading_groups()
    scene_warnings = []
    if unused:
        scene_warnings.append("사용되지 않는 셰이딩 그룹 %d개: %s"
                              % (len(unused), ", ".join(unused[:10])))

    total_slots = sum(r["slot_count"] for r in report)
    return {
        "checked": len(report),
        "clean": sum(1 for r in report if r["clean"]),
        "total_slots": total_slots,
        "unused_shading_groups": unused,
        "scene_warnings": scene_warnings,
        "objects": report,
    }


def cleanup_materials(prefix=PREFIX_MATERIAL, rename=True,
                      delete_unused=True, dry_run=False):
    """안전한 머티리얼 정리만 수행합니다.

    서로 다른 머티리얼을 자동으로 병합하지 않습니다. 어느 것을 남길지는
    룩뎁 판단이라 사람이 정해야 합니다. 여기서는 되돌려도 손해가 없는 것만 합니다.
    """
    actions = []

    if delete_unused:
        unused = _unused_shading_groups()
        if unused:
            actions.append("사용되지 않는 셰이딩 그룹 %d개 삭제: %s"
                           % (len(unused), ", ".join(unused[:10])))
            if not dry_run:
                cmds.delete(unused)

    renamed = []
    if rename and prefix:
        for mat in (cmds.ls(materials=True) or []):
            if mat in DEFAULT_MATERIALS or mat.startswith(prefix):
                continue
            if cmds.referenceQuery(mat, isNodeReferenced=True):
                continue                       # 레퍼런스 노드는 이름을 못 바꿉니다
            new = prefix + mat
            renamed.append("%s -> %s" % (mat, new))
            if not dry_run:
                cmds.rename(mat, new)
    if renamed:
        actions.append("머티리얼 리네임 %d개: %s"
                       % (len(renamed), ", ".join(renamed[:10])))

    return {"dry_run": dry_run, "actions": actions or ["정리할 것이 없습니다"]}


# ------------------------------------------------------------ 6. 콜리전

COLLISION_PREFIX = {"box": "UBX_", "sphere": "USP_", "capsule": "UCP_", "convex": "UCX_"}


def make_collision(objects=None, shape="box", padding=0.0, reduce_to=200,
                   dry_run=False):
    """언리얼 규칙(UBX_/USP_/UCP_/UCX_)에 맞는 콜리전 메시를 만듭니다.

    shape:
      "box"     UBX_ — 바운딩박스. 정확하고 가장 가볍습니다. 대부분의 프롭에 충분.
      "sphere"  USP_ — 바운딩 스피어.
      "capsule" UCP_ — 캡슐. 캐릭터/기둥 형태에 적합.
      "convex"  UCX_ — **Maya 2022 에는 컨벡스 헐 명령이 없습니다.** 원본을
                reduce_to 삼각형까지 줄인 사본을 만들 뿐이며, 볼록함이 보장되지
                않습니다. 언리얼이 임포트 시 헐을 다시 계산하므로 동작은 하지만
                의도와 다른 형태가 나올 수 있습니다. 복잡한 형태라면 언리얼
                스태틱 메시 에디터의 Auto Convex Collision 을 쓰는 편이 낫습니다.

    padding: 콜리전을 원본보다 이만큼 키웁니다(씬 단위). 관통 방지에 씁니다.
    """
    if shape not in COLLISION_PREFIX:
        raise ValueError("shape 은 %s 중 하나여야 합니다" % ", ".join(sorted(COLLISION_PREFIX)))

    targets = _resolve(objects)
    made = []

    for obj in targets:
        if not _shapes(obj):
            continue
        name = _short(obj)
        if name.startswith(tuple(COLLISION_PREFIX.values())):
            continue                            # 콜리전 메시 자신은 건너뜁니다

        bbox = cmds.exactWorldBoundingBox(obj)
        size = [bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]]
        center = [(bbox[0] + bbox[3]) / 2.0, (bbox[1] + bbox[4]) / 2.0,
                  (bbox[2] + bbox[5]) / 2.0]
        size = [s + padding * 2 for s in size]

        col_name = "%s%s_01" % (COLLISION_PREFIX[shape], name)
        info = {"source": name, "collision": col_name, "shape": shape,
                "size": [round(s, 3) for s in size]}

        if dry_run:
            made.append(info)
            continue

        if shape == "box":
            col = cmds.polyCube(name=col_name, w=size[0], h=size[1], d=size[2],
                                sx=1, sy=1, sz=1, ch=False)[0]
        elif shape == "sphere":
            r = max(size) / 2.0
            col = cmds.polySphere(name=col_name, r=r, sx=12, sy=8, ch=False)[0]
            info["radius"] = round(r, 3)
        elif shape == "capsule":
            r = max(size[0], size[2]) / 2.0
            col = cmds.polyCylinder(name=col_name, r=r, h=max(size[1] - r * 2, 0.01),
                                    sx=12, sy=1, sz=1, roundCap=True, ch=False)[0]
            info["radius"] = round(r, 3)
        else:                                   # convex
            col = cmds.duplicate(obj, name=col_name)[0]
            parents = cmds.listRelatives(col, parent=True)
            if parents:
                col = cmds.parent(col, world=True)[0]
            tris = _tri_count(col)
            if tris > reduce_to:
                cmds.polyReduce(col, version=1,
                                percentage=max(1.0, 100.0 - reduce_to * 100.0 / tris),
                                preserveTopology=False, keepBorder=False,
                                keepMapBorder=False, keepHardEdge=False,
                                replaceOriginal=True, constructionHistory=False)
            info["tris"] = _tri_count(col)
            info["warning"] = ("볼록함이 보장되지 않습니다. 언리얼이 임포트 시 "
                              "헐을 다시 계산합니다.")

        cmds.setAttr(col + ".translate", center[0], center[1], center[2])
        # 콜리전 메시는 렌더링되지 않아야 합니다.
        try:
            for s in _shapes(col):
                cmds.setAttr(s + ".castsShadows", 0)
                cmds.setAttr(s + ".receiveShadows", 0)
                cmds.setAttr(s + ".primaryVisibility", 0)
        except Exception:
            pass
        info["tris"] = info.get("tris", _tri_count(col))
        made.append(info)

    cmds.select(clear=True)
    return {
        "dry_run": dry_run,
        "created": len(made),
        "shape": shape,
        "collision": made,
        "note": ("콜리전 메시는 원본과 같은 FBX 에 함께 익스포트해야 언리얼이 "
                 "인식합니다. maya_unreal_export_fbx 호출 시 대상에 포함하세요."),
    }


# ------------------------------------------------------------ 7. 익스포트

def export_fbx(objects=None, path=None, smoothing_groups=True, tangents=True,
               triangulate=False, skins=False, blendshapes=False,
               animation=False, up_axis="y"):
    """선택 오브젝트를 언리얼용 설정으로 FBX 익스포트합니다."""
    if not path:
        raise ValueError("path 가 필요합니다. 예: 'D:/export/SM_Prop.fbx'")

    targets = _resolve(objects)
    if not targets:
        raise ValueError("익스포트할 오브젝트가 없습니다.")

    if not cmds.pluginInfo("fbxmaya", q=True, loaded=True):
        cmds.loadPlugin("fbxmaya", quiet=True)

    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)

    mel.eval("FBXResetExport")
    mel.eval("FBXExportSmoothingGroups -v %s" % ("true" if smoothing_groups else "false"))
    mel.eval("FBXExportTangents -v %s" % ("true" if tangents else "false"))
    mel.eval("FBXExportTriangulate -v %s" % ("true" if triangulate else "false"))
    mel.eval("FBXExportSkins -v %s" % ("true" if skins else "false"))
    mel.eval("FBXExportShapes -v %s" % ("true" if blendshapes else "false"))
    mel.eval("FBXExportAnimationOnly -v false")
    mel.eval("FBXExportBakeComplexAnimation -v %s" % ("true" if animation else "false"))
    mel.eval("FBXExportSmoothMesh -v false")        # 서브디비전 결과를 내보내지 않음
    mel.eval("FBXExportInputConnections -v false")  # 불필요한 업스트림 노드 제외
    mel.eval("FBXExportEmbeddedTextures -v false")
    mel.eval("FBXExportConstraints -v false")
    mel.eval("FBXExportCameras -v false")
    mel.eval("FBXExportLights -v false")
    mel.eval("FBXExportInAscii -v false")
    mel.eval("FBXExportUpAxis %s" % up_axis)

    saved = cmds.ls(selection=True, long=True) or []
    try:
        cmds.select(targets, replace=True)
        mel.eval('FBXExport -f "%s" -s' % path.replace("\\", "/"))
    finally:
        if saved:
            cmds.select(saved, replace=True)
        else:
            cmds.select(clear=True)

    return {
        "path": path,
        "exists": os.path.exists(path),
        "size_bytes": os.path.getsize(path) if os.path.exists(path) else 0,
        "exported": [_short(t) for t in targets],
        "settings": {"up_axis": up_axis, "triangulate": triangulate,
                     "skins": skins, "animation": animation},
    }
