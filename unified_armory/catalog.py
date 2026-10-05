"""Loads the per-game data: which armor pieces each game has, where their models
live, and how each game receives ported armor in its campaign."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

SLOTS = {
    "helmet": "Helmet",
    "chest": "Chest",
    "shoulder_left": "Left Shoulder",
    "shoulder_right": "Right Shoulder",
    "wrist": "Wrists",
    "utility": "Utility",
    "knees": "Knees",
}
DEFAULT = "default"  # keep the target game's own piece


@dataclass
class Catalog:
    games: dict[str, dict]

    def game(self, game_id: str) -> dict:
        try:
            return self.games[game_id]
        except KeyError:
            raise KeyError(f"unknown game {game_id!r}; known: {', '.join(self.games)}") from None

    def ordered_games(self) -> list[dict]:
        return sorted(self.games.values(), key=lambda g: g["order"])

    def piece(self, piece_id: str) -> dict:
        """Piece ids are '<game>/<piece>', e.g. 'reach/helmet_gungnir'."""
        game_id, _, local = piece_id.partition("/")
        game = self.game(game_id)
        for p in game["pieces"]:
            if p["id"] == local:
                return {**p, "game": game_id, "uid": piece_id}
        raise KeyError(f"unknown piece {piece_id!r}")

    def pieces_for_slot(self, slot: str) -> list[dict]:
        return [{**p, "game": g["id"], "uid": f"{g['id']}/{p['id']}"}
                for g in self.ordered_games() for p in g["pieces"] if p["slot"] == slot]

    def to_json(self) -> dict:
        return {
            "slots": SLOTS,
            "games": {g["id"]: {"id": g["id"], "name": g["name"], "toolkit": g["toolkit"], "tools_name": g.get("tools_name", g["toolkit"]),
                                "verify": g.get("_verify", ""),
                                "color_channels": list(g["target"]["change_colors"]["channels"])}
                      for g in self.ordered_games()},
            "pieces": {slot: [{"uid": p["uid"], "name": p["name"], "game": p["game"]} for p in self.pieces_for_slot(slot)]
                       for slot in SLOTS},
        }


def validate(cat: Catalog) -> list[str]:
    problems = []
    for gid, g in cat.games.items():
        ids = [p["id"] for p in g["pieces"]]
        if len(ids) != len(set(ids)):
            problems.append(f"{gid}: duplicate piece ids")
        for p in g["pieces"]:
            if p["slot"] not in SLOTS:
                problems.append(f"{gid}/{p['id']}: unknown slot {p['slot']!r}")
            if p["model"] not in g["models"]:
                problems.append(f"{gid}/{p['id']}: unknown model {p['model']!r}")
            if not p.get("select"):
                problems.append(f"{gid}/{p['id']}: no selector")
        t = g["target"]
        if t["base_model"] not in g["models"]:
            problems.append(f"{gid}: target base_model {t['base_model']!r} is not a model")
        for slot in t["slots"]:
            if slot not in SLOTS:
                problems.append(f"{gid}: target slot {slot!r} unknown")
        if t["format"] not in ("jms", "gltf"):
            problems.append(f"{gid}: unknown target format {t['format']!r}")
        for m in g["models"].values():
            if not ("tag" in m or "jms" in m):
                problems.append(f"{gid}: model needs 'tag' or 'jms'")
            if "tag" in m and not g.get("managedblam"):
                problems.append(f"{gid}: tag models need ManagedBlam")
    return problems


def load_catalog(data_dir: Path = DATA_DIR) -> Catalog:
    games = {}
    for path in sorted((data_dir / "games").glob("*.json")):
        g = json.loads(path.read_text())
        games[g["id"]] = g
    return Catalog(games=games)
