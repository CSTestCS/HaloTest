"""glTF 2.0 (.glb) writer for a skinned UMesh.

Used for the Halo 4 route (Blender converts it to FBX for the H4 kit) and for
previewing a ported loadout in any 3D viewer.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

from . import math3d
from .umesh import UMesh


def _col_major(m):
    return [m[r][c] for c in range(4) for r in range(4)]


def write_glb(mesh: UMesh, path: Path) -> None:
    bin_chunks: list[bytes] = []
    views, accessors = [], []

    def add(data: bytes, comp: int, count: int, typ: str, target=None, minmax=None):
        while sum(len(c) for c in bin_chunks) % 4:
            bin_chunks.append(b"\x00")
        offset = sum(len(c) for c in bin_chunks)
        bin_chunks.append(data)
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target:
            view["target"] = target
        views.append(view)
        acc = {"bufferView": len(views) - 1, "componentType": comp, "count": count, "type": typ}
        if minmax:
            acc["min"], acc["max"] = minmax
        accessors.append(acc)
        return len(accessors) - 1

    verts = mesh.vertices
    pos = [c for v in verts for c in v.position]
    mins = [min(pos[i::3]) for i in range(3)] if verts else [0, 0, 0]
    maxs = [max(pos[i::3]) for i in range(3)] if verts else [0, 0, 0]
    a_pos = add(struct.pack(f"<{len(pos)}f", *pos), 5126, len(verts), "VEC3", 34962, (mins, maxs))
    nrm = [c for v in verts for c in v.normal]
    a_nrm = add(struct.pack(f"<{len(nrm)}f", *nrm), 5126, len(verts), "VEC3", 34962)
    uv = [c for v in verts for c in (v.uv[0], 1.0 - v.uv[1])]
    a_uv = add(struct.pack(f"<{len(uv)}f", *uv), 5126, len(verts), "VEC2", 34962)
    joints, weights = [], []
    for v in verts:
        ws = (sorted(v.weights, key=lambda w: -w[1])[:4] + [(0, 0.0)] * 4)[:4]
        joints += [n for n, _ in ws]
        weights += [w for _, w in ws]
    a_j = add(struct.pack(f"<{len(joints)}H", *joints), 5123, len(verts), "VEC4", 34962)
    a_w = add(struct.pack(f"<{len(weights)}f", *weights), 5126, len(verts), "VEC4", 34962)

    prims = []
    for mi, mat in enumerate(mesh.materials):
        idx = [i for t in mesh.triangles if t.material == mi for i in t.v]
        if idx:
            a_i = add(struct.pack(f"<{len(idx)}I", *idx), 5125, len(idx), "SCALAR", 34963)
            prims.append({"attributes": {"POSITION": a_pos, "NORMAL": a_nrm, "TEXCOORD_0": a_uv,
                                         "JOINTS_0": a_j, "WEIGHTS_0": a_w}, "indices": a_i, "material": mi})

    world = mesh.world_matrices()
    ibm = [c for m in world for c in _col_major(math3d.invert(m))]
    a_ibm = add(struct.pack(f"<{len(ibm)}f", *ibm), 5126, len(world), "MAT4")

    nodes = []
    for n in mesh.nodes:
        nodes.append({"name": n.name, "translation": list(n.translation), "rotation": list(math3d.quat_normalize(n.rotation))})
    for i, n in enumerate(mesh.nodes):
        if n.parent >= 0:
            nodes[n.parent].setdefault("children", []).append(i)
    roots = [i for i, n in enumerate(mesh.nodes) if n.parent < 0]
    mesh_node = len(nodes)
    nodes.append({"name": "armor", "mesh": 0, "skin": 0})

    doc = {
        "asset": {"version": "2.0", "generator": "unified-armory"},
        "scene": 0, "scenes": [{"nodes": roots + [mesh_node]}],
        "nodes": nodes, "meshes": [{"name": "armor", "primitives": prims}],
        "skins": [{"joints": list(range(len(mesh.nodes))), "inverseBindMatrices": a_ibm}],
        "materials": [{"name": m.name} for m in mesh.materials],
        "buffers": [{"byteLength": 0}], "bufferViews": views, "accessors": accessors,
    }
    blob = b"".join(bin_chunks)
    blob += b"\x00" * (-len(blob) % 4)
    doc["buffers"][0]["byteLength"] = len(blob)
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    out = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob))
    out += struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(blob), 0x004E4942) + blob
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(out)
