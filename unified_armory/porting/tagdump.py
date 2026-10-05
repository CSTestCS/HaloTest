"""Interpret the generic ManagedBlam tag dumps written by ArmoryTool.

ArmoryTool dumps a whole tag as a tree of {"n": name, "t": kind, "v": value,
"e": [[fields]...]} without knowing what any field means. This module finds the
fields it needs by name. Each lookup accepts several candidate names because
the kits label the same data differently, and a game's data file can add more
under "dump_fields".
"""
from __future__ import annotations

from .umesh import Material, Node, Triangle, UMesh, Vertex

DEFAULT_NAMES = {
    "nodes": ["nodes"], "node_name": ["name"], "node_parent": ["parent node", "parent", "parent index"],
    "node_translation": ["default translation", "translation"], "node_rotation": ["default rotation", "rotation"],
    "materials": ["materials"], "material_shader": ["render method", "shader", "material"],
    "regions": ["regions"], "region_name": ["name"], "permutations": ["permutations"], "permutation_name": ["name"],
    "mesh_index": ["mesh index", "section index"], "mesh_count": ["mesh count"],
    "meshes": ["meshes", "sections"], "parts": ["parts"],
    "part_material": ["render method index", "material index", "material"],
    "part_index_start": ["index start", "start index", "first index"], "part_index_count": ["index count"],
    "mesh_rigid_node": ["rigid node index"], "mesh_index_type": ["index buffer type", "primitive type"],
    "per_mesh_temporary": ["per mesh temporary"], "raw_vertices": ["raw vertices"],
    "v_position": ["position"], "v_texcoord": ["texcoord", "texture coordinate"], "v_normal": ["normal"],
    "v_node_indices": ["node indices", "node index"], "v_node_weights": ["node weights", "node weight"],
    "raw_indices": ["raw indices"], "index_value": ["word", "index", "value"],
    "node_maps": ["per mesh node map"], "node_map": ["node map"], "node_map_index": ["node index"],
}
STRIP_VALUES = {"triangle strip", "triangle_strip", "strip", 5}


class DumpError(ValueError):
    pass


def _matches(field, names):
    n = (field.get("n") or "").strip().lower()
    return n in names


def find(fields, names, kind=None, required=True):
    """Breadth-first search through fields and nested structs (not blocks) for a name."""
    names = [x.lower() for x in names]
    queue = list(fields)
    while queue:
        f = queue.pop(0)
        if _matches(f, names) and (kind is None or f.get("t") == kind):
            return f
        if f.get("t") in ("struct", "array") and f.get("e"):
            for el in f["e"]:
                queue.extend(el)
    if required:
        raise DumpError(f"field {names[0]!r} not found (tried {names}); add the kit's name under dump_fields")
    return None


def elements(fields, names, required=True):
    f = find(fields, names, "block", required)
    return f["e"] if f else []


def value(fields, names, default=None):
    f = find(fields, names, required=default is None)
    if f is None:
        return default
    if f.get("t") in ("struct", "array"):
        return scalars(f)
    return f.get("v", default)


def scalars(field) -> list:
    """Flatten a value, array or struct field into a list of numbers."""
    if field.get("t") not in ("struct", "array"):
        v = field.get("v")
        if isinstance(v, dict):
            return [v[k] for k in sorted(v)]
        return list(v) if isinstance(v, (list, tuple)) else [v]
    out = []
    for el in field.get("e", []):
        for sub in el:
            out.extend(scalars(sub))
    return out


def vec(v, n):
    if isinstance(v, dict):
        keys = [k for k in ("x", "y", "z", "i", "j", "k", "w") if k in v]
        v = [v[k] for k in keys]
    v = list(v or [])
    return tuple(float(x) for x in (v + [0.0] * n)[:n])


def _names(overrides):
    names = {k: list(v) for k, v in DEFAULT_NAMES.items()}
    for k, v in (overrides or {}).items():
        names[k] = list(v) + names.get(k, [])
    return names


def strip_to_list(indices):
    tris = []
    for i in range(len(indices) - 2):
        a, b, c = indices[i], indices[i + 1], indices[i + 2]
        if a == b or b == c or a == c:
            continue
        tris.append((a, b, c) if i % 2 == 0 else (b, a, c))
    return tris


def render_model_to_umesh(dump: dict, overrides=None, source_game: str = "") -> UMesh:
    N = _names(overrides)
    root = dump["fields"]
    mesh = UMesh()

    for el in elements(root, N["nodes"]):
        parent = value(el, N["node_parent"], -1)
        mesh.nodes.append(Node(str(value(el, N["node_name"])), int(parent) if parent is not None else -1,
                               vec(value(el, N["node_translation"]), 3), vec(value(el, N["node_rotation"]), 4)))
    if not mesh.nodes:
        raise DumpError("render model has no nodes")

    for el in elements(root, N["materials"]):
        shader = value(el, N["material_shader"], "") or ""
        name = shader.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0] or f"material_{len(mesh.materials)}"
        mesh.materials.append(Material(name=name, shader=shader, source_game=source_game))

    owner: dict[int, tuple[str, str]] = {}
    for rel in elements(root, N["regions"]):
        rname = str(value(rel, N["region_name"]))
        for pel in elements(rel, N["permutations"], required=False):
            pname = str(value(pel, N["permutation_name"]))
            start = int(value(pel, N["mesh_index"], -1))
            count = int(value(pel, N["mesh_count"], 1))
            for m in range(start, start + max(count, 1)) if start >= 0 else []:
                owner[m] = (rname, pname)

    temps = elements(root, N["per_mesh_temporary"])
    node_maps = elements(root, N["node_maps"], required=False)
    for mi, mel in enumerate(elements(root, N["meshes"])):
        if mi >= len(temps):
            break
        region, perm = owner.get(mi, ("default", "default"))
        rigid = value(mel, N["mesh_rigid_node"], -1)
        node_map = None
        if mi < len(node_maps):
            node_map = [int(value(x, N["node_map_index"])) for x in elements(node_maps[mi], N["node_map"], required=False)]
        base = len(mesh.vertices)
        for vel in elements(temps[mi], N["raw_vertices"]):
            idx = [int(x) for x in scalars(find(vel, N["v_node_indices"]))] if find(vel, N["v_node_indices"], required=False) else []
            wts = [float(x) for x in scalars(find(vel, N["v_node_weights"]))] if find(vel, N["v_node_weights"], required=False) else []
            weights = [(node_map[i] if node_map and i < len(node_map) else i, w) for i, w in zip(idx, wts) if w > 0]
            if not weights:
                weights = [(int(rigid) if rigid is not None and int(rigid) >= 0 else 0, 1.0)]
            total = sum(w for _, w in weights)
            mesh.vertices.append(Vertex(
                position=vec(value(vel, N["v_position"]), 3),
                normal=vec(value(vel, N["v_normal"], [0, 0, 1]), 3),
                uv=vec(value(vel, N["v_texcoord"], [0, 0]), 2),
                weights=[(n, w / total) for n, w in weights],
            ))
        indices = [int(value(x, N["index_value"])) for x in elements(temps[mi], N["raw_indices"])]
        is_strip = value(mel, N["mesh_index_type"], "list")
        is_strip = (str(is_strip).lower() in {str(s) for s in STRIP_VALUES}) or is_strip in STRIP_VALUES
        for pel in elements(mel, N["parts"]):
            start = int(value(pel, N["part_index_start"]))
            count = int(value(pel, N["part_index_count"]))
            mat = int(value(pel, N["part_material"], 0))
            chunk = indices[start:start + count]
            tris = strip_to_list(chunk) if is_strip else [tuple(chunk[i:i + 3]) for i in range(0, len(chunk) - 2, 3)]
            for t in tris:
                mesh.triangles.append(Triangle(tuple(base + i for i in t), mat, region, perm))
    if not mesh.triangles:
        raise DumpError("render model produced no triangles (is 'per mesh temporary' populated in this kit?)")
    return mesh


ROLE_KEYWORDS = [
    ("change_color", ("change_color", "change color", "multipurpose")),
    ("normal", ("bump", "normal")),
    ("specular", ("specular", "spec_")),
    ("base", ("base_map", "base map", "diffuse", "albedo", "color_map")),
]


def classify_role(name: str) -> str | None:
    n = name.lower()
    for role, keys in ROLE_KEYWORDS:
        if any(k in n for k in keys):
            return role
    return None


def shader_bitmaps(dump: dict) -> dict[str, str]:
    """role -> bitmap tag path, from a shader dump. Looks at each element holding a
    bitmap reference and names it by a sibling '...name' string or the field's own name."""
    found: dict[str, str] = {}

    def walk(fields):
        label = next((str(f.get("v")) for f in fields if "name" in (f.get("n") or "").lower()
                      and isinstance(f.get("v"), str)), None)
        for f in fields:
            v = f.get("v")
            if f.get("t") == "ref" and isinstance(v, str) and v.lower().endswith(".bitmap"):
                role = classify_role(label or "") or classify_role(f.get("n") or "")
                if role and role not in found:
                    found[role] = v
            for el in f.get("e", []) or []:
                walk(el)

    walk(dump["fields"])
    return found
