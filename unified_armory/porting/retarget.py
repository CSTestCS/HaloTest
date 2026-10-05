"""Move armor between games: match skeletons by bone name, cut pieces out, re-bind to the target.

Each game names its bones differently ("frame l upperarm", "b_l_upperarm",
"l_upperarm"...). Names are normalized to a canonical form so a piece skinned
to one game's Spartan can be re-skinned to another's. Vertices are moved from
the source bind pose into the target bind pose bone by bone (linear blend
skinning), so a helmet built on Reach's head bone sits on Halo CE's head bone.
"""
from __future__ import annotations

import math
import re

from . import math3d
from .umesh import Triangle, UMesh, Vertex

_PREFIXES = ("frame ", "frame_", "bip01 ", "bip01_", "bip ", "b_", "bone_", "bone ", "jnt_", "joint_")
_SYNONYMS = {
    "left": "l", "right": "r",
    "upper_arm": "upperarm", "uparm": "upperarm", "arm_upper": "upperarm",
    "lower_arm": "forearm", "lowerarm": "forearm", "arm_lower": "forearm",
    "upper_leg": "thigh", "upperleg": "thigh",
    "lower_leg": "calf", "lowerleg": "calf", "shin": "calf",
    "collarbone": "clavicle", "hips": "pelvis", "hip": "pelvis",
    "spine_1": "spine1", "spine_2": "spine2", "spine_3": "spine3",
}


def canonical_bone(name: str, aliases: dict[str, str] | None = None) -> str:
    raw = name.strip().lower()
    if aliases and raw in aliases:
        return aliases[raw]
    for p in _PREFIXES:
        if raw.startswith(p):
            raw = raw[len(p):]
            break
    s = re.sub(r"[\s\-\.]+", "_", raw).strip("_")
    for a, b in sorted(_SYNONYMS.items(), key=lambda kv: -len(kv[0])):
        s = re.sub(rf"(^|_){a}(_|$)", rf"\1{b}\2", s)
    tokens = s.split("_")
    side = [t for t in tokens if t in ("l", "r")]
    rest = [t for t in tokens if t not in ("l", "r")]
    s = "_".join(side[:1] + rest)
    return s.replace("_", "") if not side else side[0] + "_" + "".join(rest)


def match_skeletons(src: UMesh, tgt: UMesh, src_aliases=None, tgt_aliases=None) -> tuple[list[int], list[str]]:
    """Map every source node to a target node. Unmatched source nodes inherit their
    nearest matched ancestor. Returns (mapping, notes)."""
    tgt_by_canon: dict[str, int] = {}
    for i, n in enumerate(tgt.nodes):
        tgt_by_canon.setdefault(canonical_bone(n.name, tgt_aliases), i)
    mapping, notes = [], []
    for i, n in enumerate(src.nodes):
        c = canonical_bone(n.name, src_aliases)
        if c in tgt_by_canon:
            mapping.append(tgt_by_canon[c])
        elif n.parent >= 0:
            mapping.append(mapping[n.parent])
            notes.append(f"bone {n.name!r} has no match; follows {tgt.nodes[mapping[n.parent]].name!r}")
        else:
            mapping.append(0)
            notes.append(f"root bone {n.name!r} has no match; using {tgt.nodes[0].name!r}")
    return mapping, notes


def select_triangles(mesh: UMesh, selector: dict, aliases=None) -> list[int]:
    """Triangle indices matching a selector:
      {"region": "helmet", "permutation": "eod"}         by region/permutation (either optional)
      {"bones": ["head"], "descendants": true}           by dominant bone (canonical names)
    Both kinds can be combined; a triangle must satisfy every given condition."""
    region, perm = selector.get("region"), selector.get("permutation")
    node_set = None
    if selector.get("bones"):
        wanted = set(selector["bones"])
        roots = {i for i, n in enumerate(mesh.nodes) if canonical_bone(n.name, aliases) in wanted}
        node_set = mesh.descendants(roots) if selector.get("descendants", True) else roots
    out = []
    for ti, t in enumerate(mesh.triangles):
        if region is not None and t.region != region:
            continue
        if perm is not None and t.permutation != perm:
            continue
        if node_set is not None:
            hits = sum(mesh.dominant_node(mesh.vertices[v]) in node_set for v in t.v)
            if hits < 2:
                continue
        out.append(ti)
    return out


def base_permutations(mesh: UMesh, preferred: dict[str, str] | None = None) -> dict[str, str]:
    """Pick one permutation per region for the target's base body."""
    perms: dict[str, list[str]] = {}
    for t in mesh.triangles:
        perms.setdefault(t.region, [])
        if t.permutation not in perms[t.region]:
            perms[t.region].append(t.permutation)
    chosen = {}
    for region, names in perms.items():
        pick = (preferred or {}).get(region)
        if pick not in names:
            pick = next((n for n in ("default", "base", "standard") if n in names), names[0])
        chosen[region] = pick
    return chosen


def skeleton_height(mesh: UMesh, aliases=None) -> float | None:
    idx = {canonical_bone(n.name, aliases): i for i, n in enumerate(mesh.nodes)}
    if "pelvis" not in idx or "head" not in idx:
        return None
    w = mesh.world_matrices()
    a, b = w[idx["pelvis"]], w[idx["head"]]
    return math.dist((a[0][3], a[1][3], a[2][3]), (b[0][3], b[1][3], b[2][3])) or None


def rebind(piece: UMesh, target: UMesh, *, max_weights: int = 4, scale: float | str = "auto",
           src_aliases=None, tgt_aliases=None, offset=(0.0, 0.0, 0.0)) -> tuple[UMesh, list[str]]:
    """Re-skin `piece` (bound to its own game's skeleton) onto `target`'s skeleton."""
    mapping, notes = match_skeletons(piece, target, src_aliases, tgt_aliases)
    if scale == "auto":
        hs, ht = skeleton_height(piece, src_aliases), skeleton_height(target, tgt_aliases)
        scale = (ht / hs) if hs and ht else 1.0
        if not (hs and ht):
            notes.append("could not measure skeleton heights; no size adjustment")
    sw, tw = piece.world_matrices(), target.world_matrices()
    s = math3d.scale_matrix(float(scale))
    s[0][3], s[1][3], s[2][3] = offset
    # Per source bone: source bind space -> (scaled) bone-local -> target bind space.
    xf = [math3d.mul(tw[mapping[i]], math3d.mul(s, math3d.invert(sw[i]))) for i in range(len(piece.nodes))]

    out = UMesh(nodes=list(target.nodes), materials=list(piece.materials))
    for v in piece.vertices:
        weights = v.weights or [(0, 1.0)]
        m = math3d.blend((xf[n], w) for n, w in weights)
        merged: dict[int, float] = {}
        for n, w in weights:
            merged[mapping[n]] = merged.get(mapping[n], 0.0) + w
        top = sorted(merged.items(), key=lambda kv: -kv[1])[:max_weights]
        total = sum(w for _, w in top) or 1.0
        out.vertices.append(Vertex(
            position=math3d.transform_point(m, v.position),
            normal=math3d.normalize(math3d.transform_dir(m, v.normal)),
            uv=v.uv,
            weights=[(n, w / total) for n, w in top],
        ))
    out.triangles = [Triangle(t.v, t.material, t.region, t.permutation) for t in piece.triangles]
    return out, notes
