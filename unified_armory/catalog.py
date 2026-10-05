"""Loads the per-game customization catalogs and cross-game armor families."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


@dataclass
class Catalog:
    families: dict[str, dict]
    games: dict[str, dict]
    palettes: dict[str, list[dict]]

    def game(self, game_id: str) -> dict:
        try:
            return self.games[game_id]
        except KeyError:
            raise KeyError(f"unknown game {game_id!r}; known: {', '.join(self.games)}") from None

    def palette(self, game_id: str) -> list[dict]:
        pal = self.game(game_id)["palette"]
        return self.palettes[pal] if isinstance(pal, str) else pal

    def universal_slots(self) -> dict[str, str]:
        """Every slot any game offers, mapped to a display label."""
        slots: dict[str, str] = {}
        for game in self.ordered_games():
            for slot_id, slot in game["slots"].items():
                slots.setdefault(slot_id, slot["label"])
        return slots

    def ordered_games(self) -> list[dict]:
        return sorted(self.games.values(), key=lambda g: g["order"])

    def to_json(self) -> dict:
        return {
            "families": self.families,
            "games": {g["id"]: {**g, "palette": self.palette(g["id"])} for g in self.ordered_games()},
            "universal_slots": self.universal_slots(),
        }


def validate(cat: Catalog) -> list[str]:
    """Return a list of problems with the catalog data (empty when valid)."""
    problems = []
    for gid, game in cat.games.items():
        pal = game["palette"]
        if isinstance(pal, str) and pal not in cat.palettes:
            problems.append(f"{gid}: unknown palette {pal!r}")
        for slot_id, slot in game["slots"].items():
            ids = [o["id"] for o in slot["options"]]
            if len(ids) != len(set(ids)):
                problems.append(f"{gid}.{slot_id}: duplicate option ids")
            if slot.get("default") not in ids:
                problems.append(f"{gid}.{slot_id}: default {slot.get('default')!r} is not an option")
            for opt in slot["options"]:
                if opt["family"] not in cat.families:
                    problems.append(f"{gid}.{slot_id}.{opt['id']}: unknown family {opt['family']!r}")
        camp = game["campaign"]
        for slot_id in camp.get("region_for_slot", {}):
            if slot_id not in game["slots"]:
                problems.append(f"{gid}: region_for_slot names unknown slot {slot_id!r}")
        for channel in camp.get("channels", {}):
            if channel not in game["color_channels"]:
                problems.append(f"{gid}: campaign channel {channel!r} is not a color channel")
    for fid, fam in cat.families.items():
        for s in fam["similar"]:
            if s not in cat.families:
                problems.append(f"family {fid}: unknown similar family {s!r}")
    return problems


def load_catalog(data_dir: Path = DATA_DIR) -> Catalog:
    families = json.loads((data_dir / "families.json").read_text())["families"]
    palettes = {"classic": json.loads((data_dir / "palette_classic.json").read_text())["colors"]}
    games = {}
    for path in sorted((data_dir / "catalog").glob("*.json")):
        game = json.loads(path.read_text())
        games[game["id"]] = game
    return Catalog(families=families, games=games, palettes=palettes)
