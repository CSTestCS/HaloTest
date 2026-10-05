"""Build one target game's player model wearing pieces from any game."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace

from ..catalog import DEFAULT, SLOTS
from .retarget import base_permutations, rebind, select_triangles
from .umesh import UMesh


@dataclass
class Assembly:
    mesh: UMesh
    imported: list = field(default_factory=list)  # Materials that came from other models (need new shaders)
    base: list = field(default_factory=list)      # Materials from the target's own model (reuse its shaders)
    pieces: dict = field(default_factory=dict)    # slot -> piece uid actually applied
    notes: list = field(default_factory=list)


def assemble(catalog, target_id: str, loadout: dict[str, str], load) -> Assembly:
    game = catalog.game(target_id)
    target = game["target"]
    aliases = game.get("bone_aliases")
    full = load(target_id, target["base_model"])

    # Base body: one permutation per region (the target's default armor).
    chosen = base_permutations(full, target.get("base_permutations"))
    keep = [i for i, t in enumerate(full.triangles) if chosen.get(t.region) == t.permutation]
    base = full.subset(keep)
    removed: set[int] = set()
    regions_for_slot: dict[str, str] = {}
    notes: list[str] = []
    applied: dict[str, str] = {}

    picks = {slot: uid for slot, uid in loadout.items() if uid and uid != DEFAULT}
    for slot in SLOTS:
        if slot not in picks:
            continue
        sel = target["slots"].get(slot)
        if sel:
            cut = select_triangles(base, sel, aliases)
            removed.update(cut)
            regions = Counter(base.triangles[i].region for i in cut)
            if regions:
                regions_for_slot[slot] = regions.most_common(1)[0][0]
            elif "region" in sel:
                regions_for_slot[slot] = sel["region"]
            if not cut:
                notes.append(f"{SLOTS[slot]}: nothing of {game['name']}'s own armor matched this slot; the new piece is added on top")

    body = base.subset([i for i in range(len(base.triangles)) if i not in removed])
    out = UMesh(nodes=list(full.nodes))
    out.append(body)

    imported = {}
    for slot in SLOTS:
        uid = picks.get(slot)
        if not uid:
            continue
        piece = catalog.piece(uid)
        src_game = catalog.game(piece["game"])
        src = load(piece["game"], piece["model"])
        tris = select_triangles(src, piece["select"], src_game.get("bone_aliases"))
        if not tris:
            notes.append(f"{piece['name']}: no geometry matched its selector in {src_game['name']}'s model; check region/permutation names")
            continue
        part = src.subset(tris)
        moved, rb_notes = rebind(part, full, max_weights=target["max_weights"], scale=piece.get("scale", "auto"),
                                 src_aliases=src_game.get("bone_aliases"), tgt_aliases=aliases,
                                 offset=tuple(piece.get("offset", (0, 0, 0))))
        notes += [f"{piece['name']}: {n}" for n in dict.fromkeys(rb_notes)]
        # Pieces from another game need a shader made in this game; pieces from this game
        # (e.g. multiplayer armor on the campaign Chief) reuse their original shader as-is.
        if piece["game"] != target_id:
            for m in moved.materials:
                new = replace(m, name=f"{piece['game']}_{m.name}")
                imported[new.name] = new
            moved.materials = [imported[f"{piece['game']}_{m.name}"] for m in moved.materials]
        out.append(moved, region=regions_for_slot.get(slot, slot))
        applied[slot] = uid

    # Own copies: cached source models are shared across targets and build steps rename materials.
    out.materials = [replace(m, textures=dict(m.textures)) for m in out.materials]
    return Assembly(
        mesh=out,
        imported=[m for m in out.materials if m.name in imported],
        base=[m for m in out.materials if m.name not in imported],
        pieces=applied,
        notes=notes,
    )


# ---------------------------------------------------------------------------- map pack

OWN = "own"


def pack_region(slot: str) -> str:
    return f"ua_{slot}"


def pack_permutation(uid: str) -> str:
    """Permutation names are string ids: lowercase letters, digits and underscores."""
    return "".join(c if c.isalnum() else "_" for c in uid.lower().replace("/", "__"))


@dataclass
class PackAssembly(Assembly):
    manifest: dict = field(default_factory=dict)   # slot -> [{"index", "uid", "perm", "name", "from"}]
    skipped: list = field(default_factory=list)    # pieces left out, with reasons


def _marker(mesh: UMesh, node: int) -> UMesh:
    """A tiny triangle inside a bone: stands in for 'nothing' where a slot has no geometry of its own,
    because a permutation can't be empty."""
    from .umesh import Triangle as T, Vertex as V
    w = mesh.world_matrices()[node]
    c = (w[0][3], w[1][3], w[2][3])
    e = 0.001
    m = UMesh(nodes=list(mesh.nodes), materials=list(mesh.materials[:1]))
    m.vertices = [V((c[0], c[1], c[2]), weights=[(node, 1.0)]), V((c[0] + e, c[1], c[2]), weights=[(node, 1.0)]),
                  V((c[0], c[1] + e, c[2]), weights=[(node, 1.0)])]
    m.triangles = [T((0, 1, 2), 0)]
    return m


def assemble_pack(catalog, target_id: str, load, include: set[str] | None = None) -> PackAssembly:
    """One player model holding every piece as a permutation of a per-slot region:
    region ua_<slot>, permutation 'own' (this game's own armor, the default) plus one per piece.
    The mission script picks permutations at runtime from the player's selection."""
    from .cache import MissingExtraction

    game = catalog.game(target_id)
    target = game["target"]
    aliases = game.get("bone_aliases")
    full = load(target_id, target["base_model"])
    chosen = base_permutations(full, target.get("base_permutations"))
    base = full.subset([i for i, t in enumerate(full.triangles) if chosen.get(t.region) == t.permutation])

    taken: set[int] = set()
    own: dict[str, list[int]] = {}
    for slot in SLOTS:
        sel = target["slots"].get(slot)
        tris = [i for i in select_triangles(base, sel, aliases) if i not in taken] if sel else []
        own[slot] = tris
        taken.update(tris)

    out = UMesh(nodes=list(full.nodes))
    out.append(base.subset([i for i in range(len(base.triangles)) if i not in taken]))
    root_like = next((i for i, n in enumerate(full.nodes) if n.parent < 0), 0)
    manifest, notes, skipped, imported = {}, [], [], {}

    for slot in SLOTS:
        region = pack_region(slot)
        if own[slot]:
            out.append(base.subset(own[slot]), region=region, permutation=OWN)
        else:
            out.append(_marker(out, root_like), region=region, permutation=OWN)
        entries = [{"index": 0, "uid": OWN, "perm": OWN, "name": f"{game['name']}'s own", "from": target_id}]
        for piece in catalog.pieces_for_slot(slot):
            if include is not None and piece["uid"] not in include:
                continue
            src_game = catalog.game(piece["game"])
            try:
                src = load(piece["game"], piece["model"])
            except MissingExtraction:
                skipped.append(f"{piece['name']} ({src_game['name']}): source model not extracted")
                continue
            tris = select_triangles(src, piece["select"], src_game.get("bone_aliases"))
            if not tris:
                skipped.append(f"{piece['name']} ({src_game['name']}): no geometry matched its selector")
                continue
            moved, rb_notes = rebind(src.subset(tris), full, max_weights=target["max_weights"],
                                     scale=piece.get("scale", "auto"), src_aliases=src_game.get("bone_aliases"),
                                     tgt_aliases=aliases, offset=tuple(piece.get("offset", (0, 0, 0))))
            notes += [f"{piece['name']}: {n}" for n in dict.fromkeys(rb_notes)]
            if piece["game"] != target_id:
                renamed = []
                for m in moved.materials:
                    key = f"{piece['game']}_{m.name}"
                    imported.setdefault(key, replace(m, name=key))
                    renamed.append(imported[key])
                moved.materials = renamed
            perm = pack_permutation(piece["uid"])
            out.append(moved, region=region, permutation=perm)
            entries.append({"index": len(entries), "uid": piece["uid"], "perm": perm, "name": piece["name"],
                            "from": piece["game"]})
        manifest[slot] = entries

    out.materials = [replace(m, textures=dict(m.textures)) for m in out.materials]
    return PackAssembly(
        mesh=out,
        imported=[m for m in out.materials if m.name in imported],
        base=[m for m in out.materials if m.name not in imported],
        pieces={},
        notes=list(dict.fromkeys(notes)),
        manifest=manifest,
        skipped=skipped,
    )
