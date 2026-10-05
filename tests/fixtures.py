"""Synthetic stand-ins for each game's models, written in the exact formats the
real pipeline consumes (ArmoryTool dumps, JMS, TGA), so the whole port runs in tests."""
from __future__ import annotations

import json
import struct
from pathlib import Path

from unified_armory.porting import jms
from unified_armory.porting.cache import model_dir, slug
from unified_armory.porting.umesh import Material, Node, Triangle, UMesh, Vertex

# canonical bone -> (parent, offset from parent at scale 1)
BONES = [
    ("pelvis", None, (0, 0, 1.0)), ("spine", "pelvis", (0, 0, 0.15)), ("spine1", "spine", (0, 0, 0.15)),
    ("neck", "spine1", (0, 0, 0.2)), ("head", "neck", (0, 0, 0.1)),
    ("l_clavicle", "spine1", (0, 0.08, 0.15)), ("l_upperarm", "l_clavicle", (0, 0.12, 0)), ("l_forearm", "l_upperarm", (0, 0.25, 0)),
    ("r_clavicle", "spine1", (0, -0.08, 0.15)), ("r_upperarm", "r_clavicle", (0, -0.12, 0)), ("r_forearm", "r_upperarm", (0, -0.25, 0)),
    ("l_thigh", "pelvis", (0, 0.1, 0)), ("l_calf", "l_thigh", (0, 0, -0.45)), ("r_thigh", "pelvis", (0, -0.1, 0)), ("r_calf", "r_thigh", (0, 0, -0.45)),
]
STYLES = {
    "frame": lambda c: "frame " + c.replace("_", " "),
    "plain": lambda c: c,
    "b": lambda c: "b_" + c,
}


def skeleton(style: str, scale: float) -> list[Node]:
    names = [b[0] for b in BONES]
    return [Node(STYLES[style](c), names.index(p) if p else -1, tuple(o * scale for o in off), (0, 0, 0, 1))
            for c, p, off in BONES]


def add_box(mesh: UMesh, node: int, size: float, material: int, region: str, perm: str):
    w = mesh.world_matrices()[node]
    cx, cy, cz = w[0][3], w[1][3], w[2][3]
    base = len(mesh.vertices)
    for dx in (-size, size):
        for dy in (-size, size):
            for dz in (-size, size):
                mesh.vertices.append(Vertex((cx + dx, cy + dy, cz + dz), (0, 0, 1), (0.5, 0.5), [(node, 1.0)]))
    faces = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    for f in faces:
        mesh.triangles.append(Triangle(tuple(base + i for i in f), material, region, perm))


def body_model(style, scale, shader) -> UMesh:
    """Campaign-style model: one region, every bone gets a box."""
    m = UMesh(nodes=skeleton(style, scale), materials=[Material(name=shader.rsplit("\\", 1)[-1].split(".")[0], shader=shader)])
    for i in range(len(m.nodes)):
        add_box(m, i, 0.04 * scale, 0, "body", "default")
    return m


def mp_model(style, scale, shader, regions: dict[str, tuple[str, list[str]]]) -> UMesh:
    """Multiplayer-style model: region -> (bone, permutations); plus a body region."""
    m = body_model(style, scale, shader)
    idx = {b[0]: i for i, b in enumerate(BONES)}
    for region, (bone, perms) in regions.items():
        for k, perm in enumerate(perms):
            add_box(m, idx[bone], (0.06 + 0.005 * k) * scale, 0, region, perm)
    return m


def to_dump(mesh: UMesh) -> dict:
    """UMesh -> ArmoryTool-style tag dump (one mesh per region/permutation)."""
    def v(name, value): return {"n": name, "t": "value", "v": value}
    def block(name, elems): return {"n": name, "t": "block", "e": elems}
    groups: dict[tuple, list] = {}
    for t in mesh.triangles:
        groups.setdefault((t.region, t.permutation), []).append(t)
    meshes, temps, regions = [], [], {}
    for mi, ((region, perm), tris) in enumerate(groups.items()):
        vmap, verts, idx = {}, [], []
        for t in tris:
            for vi in t.v:
                if vi not in vmap:
                    vmap[vi] = len(verts)
                    verts.append(mesh.vertices[vi])
                idx.append(vmap[vi])
        temps.append([
            block("raw vertices", [[v("position", list(x.position)), v("texcoord", list(x.uv)), v("normal", list(x.normal)),
                                    {"n": "node indices", "t": "array", "e": [[v("", n)] for n, _ in x.weights] + [[v("", 0)]] * (4 - len(x.weights))},
                                    {"n": "node weights", "t": "array", "e": [[v("", w)] for _, w in x.weights] + [[v("", 0.0)]] * (4 - len(x.weights))}]
                                   for x in verts]),
            block("raw indices", [[v("word", i)] for i in idx]),
        ])
        meshes.append([v("index buffer type", "triangle list"),
                       block("parts", [[v("render method index", tris[0].material), v("index start", 0), v("index count", len(idx))]])])
        regions.setdefault(region, []).append([v("name", perm), v("mesh index", mi), v("mesh count", 1)])
    return {"tag": "x.render_model", "fields": [
        block("nodes", [[v("name", n.name), v("parent node", n.parent), v("default translation", list(n.translation)),
                         v("default rotation", list(n.rotation))] for n in mesh.nodes]),
        block("materials", [[{"n": "render method", "t": "ref", "v": m.shader}] for m in mesh.materials]),
        block("regions", [[v("name", r), block("permutations", ps)] for r, ps in regions.items()]),
        {"n": "render geometry", "t": "struct", "e": [[block("meshes", meshes), block("per mesh temporary", temps)]]},
    ]}


def write_tga(path: Path, w=2, h=2, rgb=(200, 30, 30)):
    path.parent.mkdir(parents=True, exist_ok=True)
    hdr = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, w, h, 24, 0)
    path.write_bytes(hdr + bytes((rgb[2], rgb[1], rgb[0])) * (w * h))


def write_tag_model(cache: Path, game: str, key: str, mesh: UMesh, textured: bool = True):
    d = model_dir(cache, game, key)
    d.mkdir(parents=True, exist_ok=True)
    (d / "model.json").write_text(json.dumps(to_dump(mesh)))
    if textured:
        for m in mesh.materials:
            bitmap = m.shader.rsplit(".", 1)[0] + "_diffuse.bitmap"
            shader = {"tag": m.shader, "fields": [{"n": "parameters", "t": "block", "e": [
                [{"n": "parameter name", "t": "value", "v": "base_map"}, {"n": "bitmap", "t": "ref", "v": bitmap}]]}]}
            (d / "shaders").mkdir(exist_ok=True)
            (d / "shaders" / (slug(m.shader) + ".json")).write_text(json.dumps(shader))
            write_tga(d / "bitmaps" / (slug(bitmap) + ".tga"))


H3_PIECES = {"helmet": ("head", ["base", "eod", "recon"]), "chest": ("spine1", ["base", "eod"]),
             "left_shoulder": ("l_upperarm", ["base", "eod"]), "right_shoulder": ("r_upperarm", ["base", "eod"])}
REACH_PIECES = {"helmet": ("head", ["mark_v_b", "gungnir", "eod"]), "chest": ("spine1", ["default", "gungnir"]),
                "left_shoulder": ("l_upperarm", ["default", "gungnir"]), "right_shoulder": ("r_upperarm", ["default", "gungnir"]),
                "wrist": ("l_forearm", ["default", "ua_buckler"]), "utility": ("l_thigh", ["default", "ua_hardcase"]),
                "knees": ("l_calf", ["default", "gr_l"])}
H4_PIECES = {"helmet": ("head", ["recruit", "warrior"]), "chest": ("spine1", ["recruit", "warrior"])}


def build_cache(cache: Path) -> Path:
    """Populate a cache for every model in the game data, with distinct naming and proportions per game."""
    cyborg = body_model("frame", 0.9, "characters\\cyborg\\shaders\\cyborg.shader_model")
    d = model_dir(cache, "halo1", "cyborg")
    d.mkdir(parents=True, exist_ok=True)
    jms.write(cyborg, d / "model.jms", version=8200)
    write_tga(d / "cyborg_base.tga")
    write_tag_model(cache, "halo2", "masterchief", body_model("frame", 1.0, "objects\\characters\\masterchief\\shaders\\masterchief.shader"))
    write_tag_model(cache, "halo2", "elite", body_model("frame", 1.15, "objects\\characters\\elite\\shaders\\elite.shader"))
    write_tag_model(cache, "halo3", "masterchief", body_model("plain", 1.0, "objects\\characters\\masterchief\\shaders\\masterchief_armor.shader"))
    write_tag_model(cache, "halo3", "mp_masterchief", mp_model("plain", 1.0, "objects\\characters\\masterchief\\shaders\\mp_armor.shader", H3_PIECES))
    write_tag_model(cache, "odst", "odst_recon", body_model("plain", 0.95, "objects\\characters\\odst_recon\\shaders\\odst_recon_armor.shader"))
    write_tag_model(cache, "reach", "spartans", mp_model("b", 1.05, "objects\\characters\\spartans\\shaders\\spartan_armor.shader", REACH_PIECES))
    write_tag_model(cache, "halo4", "spartans", mp_model("b", 1.1, "objects\\characters\\spartans\\shaders\\spartan_h4.material", H4_PIECES))
    write_tag_model(cache, "halo4", "storm_masterchief", body_model("b", 1.1, "objects\\characters\\storm_masterchief\\shaders\\masterchief_armor.material"))
    return cache
