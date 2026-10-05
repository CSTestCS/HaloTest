"""Write a mod build: extraction jobs for missing source models, and for each
campaign whose models are all extracted, the ported model, plan and scripts."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .backends import build_bat, install_bat, notes_md, write_target
from .catalog import DEFAULT
from .porting.assemble import assemble
from .porting.cache import Loader, MissingExtraction, is_extracted, model_dir
from .profile import loadout_for

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"


def needed_models(catalog, profile: dict) -> set[tuple[str, str]]:
    need = set()
    for gid in profile["campaign"]["games"]:
        need.add((gid, catalog.game(gid)["target"]["base_model"]))
        for uid in loadout_for(profile, gid).values():
            if uid != DEFAULT:
                p = catalog.piece(uid)
                need.add((p["game"], p["model"]))
    return need


def write_extract_jobs(catalog, models: set[tuple[str, str]], cache: Path, out_dir: Path) -> list[str]:
    edir = Path(out_dir) / "extract"
    shutil.rmtree(edir, ignore_errors=True)
    edir.mkdir(parents=True)
    lines = []
    by_game: dict[str, list[str]] = {}
    for gid, model in sorted(models):
        by_game.setdefault(gid, []).append(model)
    for gid, keys in by_game.items():
        game = catalog.game(gid)
        tag_jobs = [{"tag": game["models"][k]["tag"], "out": str(model_dir(cache, gid, k).resolve())}
                    for k in keys if "tag" in game["models"][k]]
        manual = [game["models"][k]["how"].replace("{cache}", str(Path(cache).resolve()))
                  for k in keys if "jms" in game["models"][k]]
        if tag_jobs:
            (edir / f"{gid}.json").write_text(json.dumps({"bitmap_command": game["bitmap_export"], "jobs": tag_jobs}, indent=2))
            (edir / f"{gid}.bat").write_bytes((
                "@echo off\r\n"
                "if \"%~1\"==\"\" (echo usage: " + gid + ".bat ^<" + game["toolkit"].split(" (")[0] + " path^> & exit /b 1)\r\n"
                "set \"AT=%ARMORYTOOL%\"\r\nif not defined AT set \"AT=ArmoryTool.exe\"\r\n"
                f"\"%AT%\" extract \"%~f1\" \"%~dp0{gid}.json\"\r\n").encode())
            lines.append(f"- **{game['name']}:** run `extract\\{gid}.bat \"<{game['toolkit']} path>\"`")
        for how in manual:
            (edir / f"{gid}.txt").write_text(how + "\n")
            lines.append(f"- **{game['name']}:** {how}")
    return lines


def export(catalog, profile: dict, out_dir: Path, cache: Path) -> dict:
    out_dir, cache = Path(out_dir), Path(cache)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "profile.json").write_text(json.dumps(profile, indent=2) + "\n")
    load = Loader(cache, catalog)
    built, waiting, errors = {}, {}, {}

    for gid in profile["campaign"]["games"]:
        gdir = out_dir / gid
        shutil.rmtree(gdir, ignore_errors=True)
        try:
            asm = assemble(catalog, gid, loadout_for(profile, gid), load)
        except MissingExtraction as e:
            waiting[gid] = f"{e.game}/{e.model}"
            continue
        except (ValueError, KeyError) as e:
            errors[gid] = str(e)
            continue
        gdir.mkdir(parents=True)
        plan = write_target(catalog, profile, gid, asm, out_dir)
        (gdir / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
        (gdir / "install.bat").write_bytes(install_bat(plan).encode())
        (gdir / "build.bat").write_bytes(build_bat(plan).encode())
        (gdir / "NOTES.md").write_text(notes_md(plan))
        built[gid] = plan

    if (TOOLS_DIR / "blender").is_dir() and "halo4" in built:
        shutil.copytree(TOOLS_DIR / "blender", out_dir / "tools" / "blender", dirs_exist_ok=True)

    missing = {(g, m) for g, m in needed_models(catalog, profile) if not is_extracted(cache, catalog.game(g), m)}
    extract_lines = write_extract_jobs(catalog, missing, cache, out_dir) if missing else []
    if not missing:
        shutil.rmtree(out_dir / "extract", ignore_errors=True)

    readme = [f"# Unified Armory build: {profile['name']}", ""]
    if extract_lines:
        readme += ["## 1. Extract source models", "",
                   f"These models aren't in the cache (`{cache.resolve()}`) yet. Run each, then build again:", ""] + extract_lines + [""]
    if built:
        readme += ["## Ready to install", ""] + [f"- [{p['name']}]({g}/NOTES.md)" for g, p in built.items()] + [""]
    if waiting:
        readme += ["## Waiting on extraction", ""] + [f"- {catalog.game(g)['name']} (needs {m})" for g, m in waiting.items()] + [""]
    if errors:
        readme += ["## Errors", ""] + [f"- {catalog.game(g)['name']}: {e}" for g, e in errors.items()] + [""]
    (out_dir / "README.md").write_text("\n".join(readme))
    return {"built": built, "waiting": waiting, "errors": errors, "extract": extract_lines}
