"""JMS reader/writer: the text model format the Halo Editing Kits import.

Two dialects:
  legacy (8200)  Halo CE: sibling/child node links, up to 2 weights per vertex,
                 node rotations stored inverted
  modern (8210+) Halo 2 onward: parent indices, up to 4 weights, region and
                 permutation carried on each material
Positions in JMS are in JMS units (100 per world unit).
"""
from __future__ import annotations

from pathlib import Path

from . import math3d
from .umesh import Material, Node, Triangle, UMesh, Vertex

JMS_SCALE = 100.0


def _f(x: float) -> str:
    return f"{x:.6f}"


def _vec(v) -> str:
    return "\t".join(_f(c) for c in v)


def _legacy_links(nodes):
    children = {i: [] for i in range(len(nodes))}
    for i, n in enumerate(nodes):
        if n.parent >= 0:
            children[n.parent].append(i)
    first_child = {i: (c[0] if c else -1) for i, c in children.items()}
    sibling = {i: -1 for i in range(len(nodes))}
    for c in children.values():
        for a, b in zip(c, c[1:]):
            sibling[a] = b
    return first_child, sibling


def write(mesh: UMesh, path: Path, version: int = 8213, max_weights: int | None = None) -> None:
    legacy = version < 8210
    max_weights = max_weights or (2 if legacy else 4)
    out: list[str] = [str(version)]
    if legacy:
        out.append("3251")
    else:
        out.insert(0, ";### VERSION ###")
        out.append(";### NODES ###")
    out.append(str(len(mesh.nodes)))
    first_child, sibling = _legacy_links(mesh.nodes)
    for i, n in enumerate(mesh.nodes):
        rot = math3d.quat_conjugate(n.rotation) if legacy else n.rotation
        if not legacy:
            out.append(f";NODE {i}")
        out.append(n.name)
        out += [str(first_child[i]), str(sibling[i])] if legacy else [str(n.parent)]
        out.append(_vec(rot))
        out.append(_vec(c * JMS_SCALE for c in n.translation))

    # Modern JMS carries region/permutation on the material, so split materials per (material, region, perm).
    keys: list[tuple] = []
    tri_mat: list[int] = []
    for t in mesh.triangles:
        key = (t.material,) if legacy else (t.material, t.region, t.permutation)
        if key not in keys:
            keys.append(key)
        tri_mat.append(keys.index(key))
    if not legacy:
        out.append(";### MATERIALS ###")
    out.append(str(len(keys)))
    for i, key in enumerate(keys):
        m = mesh.materials[key[0]]
        if legacy:
            out += [m.name, m.textures.get("base", "<none>")]
        else:
            out += [f";MATERIAL {i}", m.name, f"({i + 1}) {key[2]} {key[1]}"]
    if legacy:
        regions = sorted({t.region for t in mesh.triangles}) or ["unnamed"]
        out += ["0", str(len(regions))] + regions
    else:
        out += [";### MARKERS ###", "0", ";### INSTANCE XREF PATHS ###", "0", ";### INSTANCE MARKERS ###", "0"]
        out.append(";### VERTICES ###")
    out.append(str(len(mesh.vertices)))
    for i, v in enumerate(mesh.vertices):
        ws = sorted(v.weights, key=lambda w: -w[1])[:max_weights] or [(0, 1.0)]
        total = sum(w for _, w in ws) or 1.0
        ws = [(n, w / total) for n, w in ws]
        pos = _vec(c * JMS_SCALE for c in v.position)
        if legacy:
            n1, w1 = ws[1] if len(ws) > 1 else (-1, 0.0)
            out += [str(ws[0][0]), pos, _vec(v.normal), str(n1), _f(w1), f"{_f(v.uv[0])}\t{_f(v.uv[1])}", "0"]
        else:
            out += [f";VERTEX {i}", pos, _vec(v.normal), str(len(ws))]
            for n, w in ws:
                out += [str(n), _f(w)]
            out += ["1", f"{_f(v.uv[0])}\t{_f(v.uv[1])}", "0.000000\t0.000000\t0.000000"]
    if not legacy:
        out.append(";### TRIANGLES ###")
    out.append(str(len(mesh.triangles)))
    for i, t in enumerate(mesh.triangles):
        if legacy:
            region_index = regions.index(t.region) if t.region in regions else 0
            out += [str(region_index), str(tri_mat[i]), "\t".join(map(str, t.v))]
        else:
            out += [f";TRIANGLE {i}", str(tri_mat[i]), "\t".join(map(str, t.v))]
    if not legacy:
        for section in ("SPHERES", "BOXES", "CAPSULES", "CONVEX SHAPES", "RAGDOLLS", "HINGES",
                        "CAR WHEELS", "POINT TO POINT", "PRISMATIC", "BOUNDING SPHERE"):
            out += [f";### {section} ###", "0"]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(out) + "\n")


class _Lines:
    def __init__(self, text: str):
        self.lines = [ln.strip() for ln in text.splitlines()]
        self.lines = [ln for ln in self.lines if ln and not ln.startswith(";")]
        self.i = 0

    def next(self) -> str:
        if self.i >= len(self.lines):
            raise ValueError("unexpected end of JMS file")
        self.i += 1
        return self.lines[self.i - 1]

    def int(self) -> int:
        return int(self.next())

    def floats(self, n=None) -> list[float]:
        vals = [float(x) for x in self.next().replace(",", " ").split()]
        return vals if n is None else (vals + [0.0] * n)[:n]


def read(path: Path, source_game: str = "") -> UMesh:
    """Read a legacy (8200) or modern (8210-8213) JMS into a UMesh."""
    r = _Lines(Path(path).read_text(errors="replace"))
    version = r.int()
    legacy = version < 8210
    if legacy:
        r.next()  # checksum
    mesh = UMesh()
    count = r.int()
    links = []
    for _ in range(count):
        name = r.next()
        if legacy:
            links.append((r.int(), r.int()))
            parent = -1
        else:
            parent = r.int()
        rot = tuple(r.floats(4))
        trans = tuple(c / JMS_SCALE for c in r.floats(3))
        mesh.nodes.append(Node(name, parent, trans, math3d.quat_conjugate(rot) if legacy else rot))
    if legacy:  # rebuild parents from child/sibling links
        for i, (child, _) in enumerate(links):
            c = child
            while c != -1:
                mesh.nodes[c].parent = i
                c = links[c][1]

    mat_regions = []
    for _ in range(r.int()):
        name = r.next()
        extra = r.next()
        tex = {}
        if legacy:
            if extra and extra != "<none>":
                tex["base"] = extra
            mat_regions.append(None)
        else:
            parts = extra.split()
            perm, region = (parts[-2], parts[-1]) if len(parts) >= 3 else ("default", parts[-1] if parts else "default")
            mat_regions.append((region, perm))
        mesh.materials.append(Material(name=name, textures=tex, source_game=source_game))

    if legacy:
        for _ in range(r.int()):  # markers: name, region, parent node, rotation, translation, radius
            for _ in range(6):
                r.next()
        regions = [r.next() for _ in range(r.int())]
    else:
        for _ in range(r.int()):  # markers: name, parent, rotation, translation, radius
            for _ in range(5):
                r.next()
        for _ in range(r.int()):  # xref paths
            r.next(); r.next()
        for _ in range(r.int()):  # instance markers
            for _ in range(5):
                r.next()

    for _ in range(r.int()):
        if legacy:
            n0 = r.int()
            pos = r.floats(3)
            nrm = r.floats(3)
            n1 = r.int()
            w1 = float(r.next())
            uv = r.floats(2)
            r.next()  # unused
            weights = [(n0, 1.0 - w1)] + ([(n1, w1)] if n1 >= 0 and w1 > 0 else [])
        else:
            pos = r.floats(3)
            nrm = r.floats(3)
            weights = [(r.int(), float(r.next())) for _ in range(r.int())]
            uvs = [r.floats(2) for _ in range(r.int())]
            uv = uvs[0] if uvs else [0.0, 0.0]
            r.next()  # vertex color
        mesh.vertices.append(Vertex(tuple(c / JMS_SCALE for c in pos), tuple(nrm), tuple(uv[:2]),
                                    [w for w in weights if w[1] > 0] or [(0, 1.0)]))

    for _ in range(r.int()):
        if legacy:
            region = regions[r.int()] if regions else "default"
            mat = r.int()
            perm = "default"
        else:
            mat = r.int()
            region, perm = mat_regions[mat]
        v = tuple(int(x) for x in r.next().split())
        mesh.triangles.append(Triangle(v, mat, region, perm))
    return mesh
