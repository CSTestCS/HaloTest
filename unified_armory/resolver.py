"""Resolve a unified profile into each game's concrete loadout."""
from __future__ import annotations

from .catalog import Catalog
from .color import nearest_swatch


def _family_chain(catalog: Catalog, family: str) -> list[str]:
    """family followed by its 'similar' fallbacks, breadth-first, no repeats."""
    chain, queue = [], [family]
    while queue:
        f = queue.pop(0)
        if f in chain or f not in catalog.families:
            continue
        chain.append(f)
        queue.extend(catalog.families[f]["similar"])
    return chain


def resolve_slot(catalog: Catalog, slot: dict, family: str | None) -> tuple[dict, str]:
    """Pick the option for one slot. Returns (option, quality) where quality is
    'exact', 'similar' or 'default'."""
    by_family: dict[str, dict] = {}
    for opt in slot["options"]:
        by_family.setdefault(opt["family"], opt)
    if family:
        for i, f in enumerate(_family_chain(catalog, family)):
            if f in by_family:
                return by_family[f], "exact" if i == 0 else "similar"
    default = next(o for o in slot["options"] if o["id"] == slot["default"])
    return default, "default"


def resolve_game(catalog: Catalog, profile: dict, game_id: str) -> dict:
    game = catalog.game(game_id)
    overrides = profile.get("overrides", {}).get(game_id, {})
    palette = catalog.palette(game_id)

    slots = {}
    for slot_id, slot in game["slots"].items():
        requested = profile["armor"].get(slot_id)
        if slot_id in overrides.get("slots", {}):
            opt_id = overrides["slots"][slot_id]
            option = next(o for o in slot["options"] if o["id"] == opt_id)
            quality = "override"
        else:
            option, quality = resolve_slot(catalog, slot, requested)
        slots[slot_id] = {
            "label": slot["label"],
            "option": option,
            "quality": quality,
            "requested_family": requested,
            "campaign": slot.get("campaign", True),
        }

    colors = {}
    for channel in game["color_channels"]:
        if channel in overrides.get("colors", {}):
            sw = next(s for s in palette if s["id"] == overrides["colors"][channel])
            colors[channel] = {"swatch": sw, "delta_e": None, "source": "override"}
        else:
            target = profile["colors"].get(channel) or profile["colors"]["primary"]
            sw, de = nearest_swatch(target, palette)
            colors[channel] = {"swatch": sw, "delta_e": round(de, 2), "source": "nearest", "requested": target}

    return {"game": game_id, "name": game["name"], "slots": slots, "colors": colors}


def resolve_all(catalog: Catalog, profile: dict) -> dict[str, dict]:
    return {g["id"]: resolve_game(catalog, profile, g["id"]) for g in catalog.ordered_games()}
