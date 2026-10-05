"""The unified profile: one loadout of exact pieces from any game, worn in every campaign.

armor[slot] is a piece id like "reach/helmet_gungnir" (that exact model, ported
into every game) or "default" (each game keeps its own). overrides[game][slot]
replaces the pick for one game. Colors are exact: campaign change colors take
any RGB value, so nothing is snapped to a palette.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .catalog import DEFAULT, SLOTS, Catalog
from .color import hex_to_rgb

PROFILE_VERSION = 2

DEFAULT_PROFILE = {
    "version": PROFILE_VERSION,
    "name": "Spartan",
    "colors": {"primary": "#556B2F", "secondary": "#556B2F"},
    "armor": {slot: DEFAULT for slot in SLOTS},
    "overrides": {},
    "campaign": {"games": ["halo1", "halo2", "halo3", "odst", "reach", "halo4"]},
}


class ProfileError(ValueError):
    pass


def new_profile() -> dict:
    return copy.deepcopy(DEFAULT_PROFILE)


def _check_piece(catalog: Catalog, slot: str, value: str, where: str) -> str:
    if value == DEFAULT:
        return value
    try:
        piece = catalog.piece(value)
    except KeyError as e:
        raise ProfileError(f"{where}: {e.args[0]}") from None
    if piece["slot"] != slot:
        raise ProfileError(f"{where}: {value!r} is a {piece['slot']} piece, not {slot}")
    return value


def validate_profile(profile: dict, catalog: Catalog) -> dict:
    if not isinstance(profile, dict):
        raise ProfileError("profile must be a JSON object")
    if profile.get("version", PROFILE_VERSION) != PROFILE_VERSION:
        raise ProfileError(f"unsupported profile version {profile.get('version')!r}; create a new profile in the editor")
    out = new_profile()
    out["name"] = str(profile.get("name", out["name"]))[:64]
    for channel, value in (profile.get("colors") or {}).items():
        if channel not in ("primary", "secondary"):
            raise ProfileError(f"unknown color channel {channel!r}")
        try:
            hex_to_rgb(value)
        except (ValueError, AttributeError) as e:
            raise ProfileError(f"colors.{channel}: {e}") from None
        out["colors"][channel] = "#" + value.lstrip("#").upper()
    for slot, value in (profile.get("armor") or {}).items():
        if slot not in SLOTS:
            raise ProfileError(f"armor.{slot}: unknown slot")
        out["armor"][slot] = _check_piece(catalog, slot, value, f"armor.{slot}")
    for game_id, ov in (profile.get("overrides") or {}).items():
        if game_id not in catalog.games:
            raise ProfileError(f"overrides: unknown game {game_id!r}")
        clean = {}
        for slot, value in (ov or {}).items():
            if slot not in SLOTS:
                raise ProfileError(f"overrides.{game_id}.{slot}: unknown slot")
            clean[slot] = _check_piece(catalog, slot, value, f"overrides.{game_id}.{slot}")
        if clean:
            out["overrides"][game_id] = clean
    camp = profile.get("campaign") or {}
    if "games" in camp:
        unknown = [g for g in camp["games"] if g not in catalog.games]
        if unknown:
            raise ProfileError(f"campaign.games: unknown games {unknown}")
        out["campaign"]["games"] = [g["id"] for g in catalog.ordered_games() if g["id"] in camp["games"]]
    return out


def loadout_for(profile: dict, game_id: str) -> dict[str, str]:
    """The pieces a given game's campaign will wear."""
    return {**profile["armor"], **profile.get("overrides", {}).get(game_id, {})}


def load_profile(path: Path, catalog: Catalog) -> dict:
    return validate_profile(json.loads(Path(path).read_text()), catalog)


def save_profile(profile: dict, path: Path) -> None:
    Path(path).write_text(json.dumps(profile, indent=2) + "\n")
