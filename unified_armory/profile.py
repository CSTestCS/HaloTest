"""The unified profile: one loadout that every game is resolved from.

Universal armor picks name a cross-game *family* (e.g. "eod"), not a
game-specific option, so a single choice carries across all six games.
Per-game overrides pin an exact option or swatch when the automatic match
isn't what you want.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .catalog import Catalog
from .color import hex_to_rgb

PROFILE_VERSION = 1

DEFAULT_PROFILE = {
    "version": PROFILE_VERSION,
    "name": "Spartan",
    "colors": {"primary": "#556B2F", "secondary": "#556B2F"},
    "armor": {
        "species": "spartan",
        "helmet": "mark_vi",
        "shoulder_left": "mark_vi",
        "shoulder_right": "mark_vi",
        "chest": "mark_vi",
        "wrist": "mark_v",
        "utility": "mark_v",
        "knees": "mark_v",
        "visor": "visor_default",
        "armor_effect": "effect_none",
        "armor_skin": "none",
        "emblem_foreground": "emblem_seventh",
        "emblem_background": "emblem_bg_solid",
    },
    "overrides": {},
    "campaign": {"games": ["halo1", "halo2", "halo3", "odst", "reach", "halo4"], "bake_reach": False},
}


class ProfileError(ValueError):
    pass


def new_profile() -> dict:
    return copy.deepcopy(DEFAULT_PROFILE)


def validate_profile(profile: dict, catalog: Catalog) -> dict:
    """Check a profile against the catalog. Returns a normalized copy, raises ProfileError."""
    if not isinstance(profile, dict):
        raise ProfileError("profile must be a JSON object")
    if profile.get("version", PROFILE_VERSION) != PROFILE_VERSION:
        raise ProfileError(f"unsupported profile version {profile.get('version')!r}")

    out = new_profile()
    out["name"] = str(profile.get("name", out["name"]))[:64]

    for channel, value in (profile.get("colors") or {}).items():
        if channel not in ("primary", "secondary"):
            raise ProfileError(f"unknown color channel {channel!r}")
        try:
            hex_to_rgb(value)
        except (ValueError, AttributeError) as e:
            raise ProfileError(f"colors.{channel}: {e}") from None
        out["colors"][channel] = value.upper() if value.startswith("#") else "#" + value.upper()

    slots = catalog.universal_slots()
    for slot, family in (profile.get("armor") or {}).items():
        if slot not in slots:
            raise ProfileError(f"armor.{slot}: no game has this slot")
        if family not in catalog.families:
            raise ProfileError(f"armor.{slot}: unknown family {family!r}")
        out["armor"][slot] = family

    for game_id, ov in (profile.get("overrides") or {}).items():
        game = catalog.game(game_id) if game_id in catalog.games else None
        if game is None:
            raise ProfileError(f"overrides: unknown game {game_id!r}")
        clean = {"slots": {}, "colors": {}}
        for slot, option in (ov.get("slots") or {}).items():
            if slot not in game["slots"]:
                raise ProfileError(f"overrides.{game_id}.slots: {game['name']} has no slot {slot!r}")
            if option not in {o["id"] for o in game["slots"][slot]["options"]}:
                raise ProfileError(f"overrides.{game_id}.slots.{slot}: unknown option {option!r}")
            clean["slots"][slot] = option
        swatches = {s["id"] for s in catalog.palette(game_id)}
        for channel, swatch in (ov.get("colors") or {}).items():
            if channel not in game["color_channels"]:
                raise ProfileError(f"overrides.{game_id}.colors: {game['name']} has no {channel!r} color")
            if swatch not in swatches:
                raise ProfileError(f"overrides.{game_id}.colors.{channel}: unknown swatch {swatch!r}")
            clean["colors"][channel] = swatch
        if clean["slots"] or clean["colors"]:
            out["overrides"][game_id] = clean

    camp = profile.get("campaign") or {}
    if "games" in camp:
        unknown = [g for g in camp["games"] if g not in catalog.games]
        if unknown:
            raise ProfileError(f"campaign.games: unknown games {unknown}")
        out["campaign"]["games"] = list(camp["games"])
    out["campaign"]["bake_reach"] = bool(camp.get("bake_reach", False))
    return out


def load_profile(path: Path, catalog: Catalog) -> dict:
    return validate_profile(json.loads(Path(path).read_text()), catalog)


def save_profile(profile: dict, path: Path) -> None:
    Path(path).write_text(json.dumps(profile, indent=2) + "\n")
