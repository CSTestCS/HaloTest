"""The map pack: campaign maps whose player model holds every ported piece, plus the
mission script and manifest the in-game runtime (tools/runtime) uses to switch them.

How a selection reaches the game, with no per-game memory addresses:
  * Each mission gets unified_armory.hsc. Its globals are declared in a fixed order:
      ua_magic (MAGIC_BASE + game index), ua_echo (0), one long per slot, ua_tick, ... ua_seed (SEED)
  * At mission start the script copies ua_seed into ua_echo. Only the live globals table
    then holds MAGIC next to SEED: the map's own data has MAGIC beside 0 and SEED far away.
  * The runtime scans memory for that pair, learns the spacing between globals from it,
    confirms ua_tick is toggling (the script flips it every loop), and writes the selected
    piece index into each slot global.
  * The script re-applies object_set_permutation for every slot each loop, so respawns and
    checkpoint loads pick the selection back up.
"""
from __future__ import annotations

from pathlib import Path

from .catalog import SLOTS

PACK_FORMAT = "unified-armory-pack/1"
MAGIC_BASE = 0x55410000  # "UA" + game index
SEED = 0x41524D59        # "ARMY"
LOOP_TICKS = 15


def game_index(catalog, game_id: str) -> int:
    return catalog.game(game_id)["order"]


def slot_global(slot: str) -> str:
    return f"ua_{slot}"


def generate_script(catalog, game_id: str, manifest: dict) -> str:
    game = catalog.game(game_id)
    player = game["target"]["script"]["player"]
    lines = [
        "; Unified Armory runtime bridge. Generated; do not edit.",
        "; The order of these globals is part of the protocol with the runtime DLL.",
        f"(global long ua_magic {MAGIC_BASE + game_index(catalog, game_id)})",
        "(global long ua_echo 0)",
    ]
    lines += [f"(global long {slot_global(s)} 0)" for s in SLOTS]
    lines += ["(global long ua_tick 0)", f"(global long ua_seed {SEED})", ""]
    lines.append("(script static void ua_apply")
    for slot in SLOTS:
        region = f"ua_{slot}"
        for e in manifest.get(slot, []):
            lines.append(f'    (if (= {slot_global(slot)} {e["index"]}) (object_set_permutation {player} "{region}" "{e["perm"]}"))')
    lines += [")", "",
              "(script startup ua_start",
              "    (set ua_echo ua_seed)",
              ")", "",
              "(script continuous ua_loop",
              "    (if (= ua_tick 0) (set ua_tick 1) (set ua_tick 0))",
              "    (ua_apply)",
              f"    (sleep {LOOP_TICKS})",
              ")", ""]
    return "\n".join(lines)


def scenario_tag(scenario: str) -> str:
    return scenario + ".scenario"


def script_source(scenario: str, filename: str) -> str:
    return scenario.rsplit("\\", 1)[0] + "\\scripts\\" + filename


def map_relpaths(game: dict) -> list[str]:
    """MCC-relative paths of this game's campaign maps, e.g. halo3/maps/010_jungle.map."""
    folder = game["mcc_maps"].replace("\\", "/")
    return [f"{folder}/{s.replace(chr(92), '/').rsplit('/', 1)[-1]}.map" for s in game["target"]["scenarios"]]


def manifest_entry(catalog, game_id: str, slots: dict, maps: list[str]) -> dict:
    g = catalog.game(game_id)
    return {"name": g["name"], "index": game_index(catalog, game_id), "magic": MAGIC_BASE + game_index(catalog, game_id),
            "slots": slots, "maps": maps}


def manifest(catalog, games: dict[str, dict]) -> dict:
    return {
        "format": PACK_FORMAT,
        "seed": SEED,
        "slot_order": list(SLOTS),
        "slots": SLOTS,
        "game_names": {g["id"]: g["name"] for g in catalog.ordered_games()},
        "games": games,
    }
