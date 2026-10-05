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
