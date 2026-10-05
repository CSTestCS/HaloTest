"""Campaign backends: turn a resolved loadout into a tag-edit plan per game.

A plan is plain JSON that the ManagedBlam applier (tools/ApplyPlan) executes
against a game's Editing Kit tags. After that you build the campaign scenarios
with the kit's tool.exe. Every plan also carries a menu checklist, because MCC
keeps multiplayer customization in your online profile, which mods can't write.

Strategies (catalog "campaign.strategy"):
  change_colors  pin the player biped's change colors to your swatches
  variant_swap   change_colors, plus a generated model variant on the
                 multiplayer Spartan holding your armor permutations, and the
                 campaign player representation pointed at it
  native         the game already reads MCC armor in campaign (Reach); only a
                 checklist, unless bake=True, which behaves like variant_swap
                 on the game's own player biped
"""
from __future__ import annotations

from .catalog import Catalog
from .color import hex_to_unit_rgb
from .resolver import resolve_game

PLAN_FORMAT = "unified-armory-plan/1"

# Field names the applier searches for (case-insensitive, recursive), so it
# doesn't depend on exact struct nesting. A catalog can override any of these
# under campaign.fields if an Editing Kit names a field differently.
DEFAULT_FIELDS = {
    "change_colors": "change colors",
    "change_color_permutations": "permutations",
    "permutation_weight": "weight",
    "color_lower_bound": "color lower bound",
    "color_upper_bound": "color upper bound",
    "model_variants": "variants",
    "variant_name": "name",
    "variant_regions": "regions",
    "region_name": "region name",
    "region_permutations": "permutations",
    "permutation_name": "permutation name",
    "player_representation": "player representation",
    "third_person_unit": "third person unit",
    "third_person_variant": "third person variant",
}


def menu_checklist(resolved: dict, catalog: Catalog) -> list[dict]:
    items = []
    for channel, c in resolved["colors"].items():
        items.append({"slot": f"{channel.title()} Color", "choice": c["swatch"]["name"], "quality": c["source"]})
    for s in resolved["slots"].values():
        items.append({"slot": s["label"], "choice": s["option"]["name"], "quality": s["quality"]})
    return items


def _color_edits(game: dict, resolved: dict, bipeds: list[str]) -> list[dict]:
    edits = []
    for channel, index in game["campaign"]["channels"].items():
        sw = resolved["colors"][channel]["swatch"]
        for biped in bipeds:
            edits.append({
                "op": "set_change_color",
                "tag": biped,
                "index": index,
                "channel": channel,
                "swatch": sw["id"],
                "color": hex_to_unit_rgb(sw["hex"]),
            })
    return edits


def _variant_edits(game: dict, resolved: dict, model: str, unit: str) -> tuple[list[dict], list[str]]:
    camp = game["campaign"]
    regions, warnings = {}, []
    for slot_id, region in camp.get("region_for_slot", {}).items():
        slot = resolved["slots"].get(slot_id)
        if slot is None or not slot["campaign"]:
            continue
        regions[region] = slot["option"].get("permutation", slot["option"]["id"])
    if resolved["slots"].get("species", {}).get("option", {}).get("id") == "elite":
        warnings.append("Elite is multiplayer-only here; the campaign keeps a Spartan body.")
    edits = [
        {"op": "set_model_variant", "tag": model, "variant": camp["variant_name"], "regions": regions},
        {
            "op": "set_player_representation",
            "tag": camp["globals"],
            "third_person_unit": unit,
            "third_person_variant": camp["variant_name"],
        },
    ]
    return edits, warnings


def build_plan(catalog: Catalog, profile: dict, game_id: str) -> dict:
    game = catalog.game(game_id)
    camp = game["campaign"]
    resolved = resolve_game(catalog, profile, game_id)
    strategy = camp["strategy"]
    bake = strategy == "native" and profile["campaign"].get("bake_reach", False)

    edits: list[dict] = []
    warnings: list[str] = []
    if strategy in ("change_colors", "variant_swap") or bake:
        bipeds = [camp["player_biped"], *camp.get("extra_bipeds", [])]
        if strategy == "variant_swap":
            bipeds.append(camp["mp_biped"])
        edits += _color_edits(game, resolved, bipeds)
    if strategy == "variant_swap":
        e, w = _variant_edits(game, resolved, camp["mp_model"], camp["mp_biped"])
        edits += e
        warnings += w
    elif bake:
        e, w = _variant_edits(game, resolved, camp["mp_model"], camp["player_biped"])
        edits += e
        warnings += w
    elif strategy == "native":
        warnings.append(f"{game['name']} campaign reads your MCC armor directly: set it in-game with the checklist below.")

    for slot_id, s in resolved["slots"].items():
        if s["quality"] in ("similar", "default") and s["requested_family"] and s["requested_family"] != "none":
            fam = catalog.families[s["requested_family"]]["name"]
            if s["quality"] == "similar" and s["option"]["id"] != game["slots"][slot_id]["default"]:
                warnings.append(f"{s['label']}: no {fam} in {game['name']}, using {s['option']['name']}.")
            elif s["quality"] == "default":
                warnings.append(f"{s['label']}: no match for {fam} in {game['name']}, using the default {s['option']['name']}.")
    enabled = game_id in profile["campaign"]["games"]
    if not enabled:
        edits = []
    if "_verify" in camp and edits:
        warnings.append(camp["_verify"])

    return {
        "format": PLAN_FORMAT,
        "game": game_id,
        "name": game["name"],
        "toolkit": camp["toolkit"],
        "strategy": "bake" if bake else strategy,
        "applies_to_campaign": enabled,
        "fields": {**DEFAULT_FIELDS, **camp.get("fields", {})},
        "edits": edits,
        "build_commands": [camp["build_command"].format(scenario=s) for s in camp["scenarios"]] if edits else [],
        "menu_checklist": menu_checklist(resolved, catalog),
        "warnings": warnings,
        "notes": camp.get("notes", ""),
    }


def build_all(catalog: Catalog, profile: dict) -> dict[str, dict]:
    return {g["id"]: build_plan(catalog, profile, g["id"]) for g in catalog.ordered_games()}
