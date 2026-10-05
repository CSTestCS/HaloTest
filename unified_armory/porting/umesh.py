"""UMesh: the engine-neutral skinned mesh every game's model is converted through.

Units are Halo world units, Z up. Node bind poses are stored both parent-relative
(translation + quaternion, what tag and JMS formats use) and as world matrices.
Each triangle carries its region and permutation names so pieces can be cut out
of multi-permutation models such as the multiplayer Spartans.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import math3d


@dataclass
class Node:
    name: str
    parent: int  # -1 for root
    translation: tuple = (0.0, 0.0, 0.0)
    rotation: tuple = (0.0, 0.0, 0.0, 1.0)


@dataclass
class Material:
    name: str
    shader: str = ""  # source shader tag path, if known
    textures: dict = field(default_factory=dict)  # role -> image file path (base, normal, specular, change_color)
    source_game: str = ""


@dataclass
class Vertex:
    position: tuple
    normal: tuple = (0.0, 0.0, 1.0)
    uv: tuple = (0.0, 0.0)
    weights: list = field(default_factory=list)  # [(node_index, weight)], sums to 1


@dataclass
class Triangle:
    v: tuple  # three vertex indices
    material: int
    region: str = "default"
    permutation: str = "default"


@dataclass
class UMesh:
    nodes: list[Node] = field(default_factory=list)
    materials: list[Material] = field(default_factory=list)
    vertices: list[Vertex] = field(default_factory=list)
    triangles: list[Triangle] = field(default_factory=list)

    # --- skeleton ----------------------------------------------------------
    def world_matrices(self) -> list:
        mats = []
        for i, n in enumerate(self.nodes):
            local = math3d.trs(n.translation, n.rotation)
            if n.parent >= 0:
                if n.parent >= i:
                    raise ValueError(f"node {n.name!r} appears before its parent")
                local = math3d.mul(mats[n.parent], local)
            mats.append(local)
        return mats

    def node_index(self) -> dict[str, int]:
        return {n.name: i for i, n in enumerate(self.nodes)}

    def descendants(self, roots: set[int]) -> set[int]:
        out = set(roots)
        for i, n in enumerate(self.nodes):  # parents precede children
            if n.parent in out:
                out.add(i)
        return out

    def dominant_node(self, v: Vertex) -> int:
        return max(v.weights, key=lambda w: w[1])[0] if v.weights else 0

    # --- editing -----------------------------------------------------------
    def subset(self, tri_indices) -> "UMesh":
        """New mesh holding only the given triangles (and the vertices/materials they use)."""
        vmap, mmap = {}, {}
        out = UMesh(nodes=list(self.nodes))
        for ti in tri_indices:
            t = self.triangles[ti]
            if t.material not in mmap:
                mmap[t.material] = len(out.materials)
                out.materials.append(self.materials[t.material])
            vs = []
            for vi in t.v:
                if vi not in vmap:
                    vmap[vi] = len(out.vertices)
                    out.vertices.append(self.vertices[vi])
                vs.append(vmap[vi])
            out.triangles.append(Triangle(tuple(vs), mmap[t.material], t.region, t.permutation))
        return out

    def append(self, other: "UMesh", region: str | None = None) -> None:
        """Merge another mesh that is already bound to this mesh's skeleton."""
        if [n.name for n in other.nodes] != [n.name for n in self.nodes]:
            raise ValueError("cannot merge meshes bound to different skeletons")
        voff = len(self.vertices)
        mmap = {}
        for i, m in enumerate(other.materials):
            existing = next((j for j, x in enumerate(self.materials) if x.name == m.name), None)
            if existing is None:
                existing = len(self.materials)
                self.materials.append(m)
            mmap[i] = existing
        self.vertices.extend(other.vertices)
        for t in other.triangles:
            self.triangles.append(Triangle(tuple(v + voff for v in t.v), mmap[t.material],
                                           region or t.region, "default" if region else t.permutation))

    # --- io ----------------------------------------------------------------
    def to_json(self) -> dict:
        return {
            "nodes": [[n.name, n.parent, list(n.translation), list(n.rotation)] for n in self.nodes],
            "materials": [{"name": m.name, "shader": m.shader, "textures": m.textures, "source_game": m.source_game}
                          for m in self.materials],
            "vertices": [[list(v.position), list(v.normal), list(v.uv), [list(w) for w in v.weights]] for v in self.vertices],
            "triangles": [[list(t.v), t.material, t.region, t.permutation] for t in self.triangles],
        }

    @classmethod
    def from_json(cls, d: dict) -> "UMesh":
        return cls(
            nodes=[Node(n, p, tuple(t), tuple(r)) for n, p, t, r in d["nodes"]],
            materials=[Material(**m) for m in d["materials"]],
            vertices=[Vertex(tuple(p), tuple(n), tuple(uv), [tuple(w) for w in ws]) for p, n, uv, ws in d["vertices"]],
            triangles=[Triangle(tuple(v), m, r, p) for v, m, r, p in d["triangles"]],
        )

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_json()))

    @classmethod
    def load(cls, path: Path) -> "UMesh":
        return cls.from_json(json.loads(Path(path).read_text()))
