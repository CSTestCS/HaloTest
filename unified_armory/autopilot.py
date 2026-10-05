"""Build the Unified Armory map pack and install the runtime mod.

Finds MCC and each game's Mod Tools, extracts every piece's source model, builds
each campaign's player model with all pieces as selectable permutations plus the
runtime mission script, compiles the campaign levels, and puts the result in
<MCC>/mcc/binaries/win64/UnifiedArmory with the runtime DLL beside it. MCC's own
files are never modified: the runtime redirects map loads to the pack, so deleting
version.dll (or the UnifiedArmory folder) returns the game to normal.

Games whose Mod Tools aren't installed are skipped with a reason; pieces whose
source game isn't available are left out of the pack rather than blocking the rest.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import pack
from .backends import write_target
from .export import TOOLS_DIR
from .porting.assemble import assemble_pack
from .porting.cache import Loader, is_extracted, model_dir

MCC_FOLDER = "Halo The Master Chief Collection"
BINARIES = ("mcc", "binaries", "win64")
MOD_FOLDER = "UnifiedArmory"
RUNTIME_DLL = "version.dll"

Log = Callable[[str], None]
Runner = Callable[[str, Path], int]


# ------------------------------------------------------------------ detection

def steam_roots() -> list[Path]:
    roots = []
    if sys.platform == "win32":
        try:
            import winreg
            for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                              (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        for name in ("SteamPath", "InstallPath"):
                            try:
                                roots.append(Path(winreg.QueryValueEx(k, name)[0]))
                            except OSError:
                                pass
                except OSError:
                    pass
        except ImportError:
            pass
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            if os.environ.get(env):
                roots.append(Path(os.environ[env]) / "Steam")
    roots += [Path.home() / ".steam" / "steam", Path.home() / ".local" / "share" / "Steam"]
    seen, out = set(), []
    for r in roots:
        key = str(r).lower()
        if key not in seen and r.is_dir():
            seen.add(key)
            out.append(r)
    return out


def steam_libraries(root: Path) -> list[Path]:
    """The Steam root plus every library listed in libraryfolders.vdf."""
    libs = [root]
    vdf = root / "steamapps" / "libraryfolders.vdf"
    if vdf.is_file():
        for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(errors="replace")):
            libs.append(Path(m.group(1).replace("\\\\", "\\")))
    return libs


@dataclass
class Environment:
    mcc: Path | None = None
    kits: dict[str, Path] = field(default_factory=dict)
    armorytool: Path | None = None
    blender: Path | None = None
    runtime: Path | None = None

    def to_json(self) -> dict:
        s = lambda p: str(p) if p else None
        return {"mcc": s(self.mcc), "kits": {g: str(p) for g, p in self.kits.items()},
                "armorytool": s(self.armorytool), "blender": s(self.blender), "runtime": s(self.runtime),
                "installed": bool(self.mcc and (mod_dir(self.mcc) / "manifest.json").is_file())}


def valid_kit(path: Path, game: dict) -> bool:
    if not (path / "tool.exe").is_file():
        return False
    return not game.get("managedblam") or (path / "bin" / "ManagedBlam.dll").is_file()


def find_armorytool() -> Path | None:
    candidates = []
    if os.environ.get("ARMORYTOOL"):
        candidates.append(Path(os.environ["ARMORYTOOL"]))
    candidates.append(Path(sys.executable).parent / "ArmoryTool.exe")  # bundled next to UnifiedArmory.exe
    candidates += sorted((TOOLS_DIR / "ArmoryTool" / "bin").glob("**/ArmoryTool.exe"))
    found = shutil.which("ArmoryTool.exe") or shutil.which("ArmoryTool")
    if found:
        candidates.append(Path(found))
    return next((c for c in candidates if c.is_file()), None)


def find_runtime() -> Path | None:
    """The runtime proxy DLL: bundled next to the builder, or a local CMake build."""
    candidates = [Path(sys.executable).parent / RUNTIME_DLL]
    candidates += sorted((TOOLS_DIR / "runtime").glob(f"**/{RUNTIME_DLL}"))
    return next((c for c in candidates if c.is_file()), None)


def mod_dir(mcc: Path) -> Path:
    return mcc.joinpath(*BINARIES, MOD_FOLDER)


def find_blender() -> Path | None:
    if os.environ.get("BLENDER") and Path(os.environ["BLENDER"]).is_file():
        return Path(os.environ["BLENDER"])
    for base in filter(None, (os.environ.get("ProgramFiles"),)):
        for exe in sorted(Path(base, "Blender Foundation").glob("Blender*/blender.exe"), reverse=True):
            return exe
    found = shutil.which("blender")
    return Path(found) if found else None


def detect(catalog, settings: dict | None = None, roots: list[Path] | None = None) -> Environment:
    """Find MCC and each game's Mod Tools in every Steam library. `settings` may pin paths:
    {"mcc": "...", "kits": {"halo3": "..."}}."""
    settings = settings or {}
    env = Environment(armorytool=find_armorytool(), blender=find_blender(), runtime=find_runtime())
    commons = [lib / "steamapps" / "common" for root in (roots if roots is not None else steam_roots())
               for lib in steam_libraries(root)]
    if settings.get("mcc") and Path(settings["mcc"]).is_dir():
        env.mcc = Path(settings["mcc"])
    else:
        env.mcc = next((c / MCC_FOLDER for c in commons if (c / MCC_FOLDER).is_dir()), None)
    for game in catalog.ordered_games():
        pinned = (settings.get("kits") or {}).get(game["id"])
        options = [Path(pinned)] if pinned else [c / name for c in commons for name in game.get("kit_folders", [])]
        hit = next((p for p in options if valid_kit(p, game)), None)
        if hit:
            env.kits[game["id"]] = hit
    return env


# ------------------------------------------------------------------ running

def default_runner(log: Log) -> Runner:
    def run(cmd: str, cwd: Path) -> int:
        log(f"> {cmd}")
        proc = subprocess.Popen(cmd, cwd=str(cwd), shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace")
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                log("  " + line)
        return proc.wait()
    return run


def _q(p) -> str:
    return f'"{p}"'


@dataclass
class Report:
    built: dict = field(default_factory=dict)    # game -> {"maps": n, "pieces": n}
    skipped: dict = field(default_factory=dict)  # game -> reason
    failed: dict = field(default_factory=dict)   # game -> reason
    left_out: list = field(default_factory=list) # pieces not in the pack, with reasons
    installed_to: str | None = None

    def to_json(self) -> dict:
        return self.__dict__


class StepFailed(Exception):
    pass


def _can_extract(catalog, env: Environment, gid: str, model: str) -> bool:
    g = catalog.game(gid)
    return bool("tag" in g["models"][model] and gid in env.kits and g.get("managedblam") and env.armorytool)


def extract_missing(catalog, models, env: Environment, cache: Path, work: Path, log: Log, runner: Runner) -> None:
    jobs: dict[str, list] = {}
    for gid, model in sorted(models):
        if not is_extracted(cache, catalog.game(gid), model) and _can_extract(catalog, env, gid, model):
            jobs.setdefault(gid, []).append({"tag": catalog.game(gid)["models"][model]["tag"],
                                             "out": str(model_dir(cache, gid, model).resolve())})
    for gid, job in jobs.items():
        game = catalog.game(gid)
        log(f"Reading {len(job)} model(s) from the {game['tools_name']}…")
        jobfile = work / "extract" / f"{gid}.json"
        jobfile.parent.mkdir(parents=True, exist_ok=True)
        jobfile.write_text(json.dumps({"bitmap_command": game["bitmap_export"], "jobs": job}, indent=2))
        runner(f"{_q(env.armorytool)} extract {_q(env.kits[gid])} {_q(jobfile)}", env.kits[gid])


def import_and_compile(game: dict, plan: dict, kit: Path, gdir: Path, env: Environment, log: Log, runner: Runner) -> None:
    shutil.copytree(gdir / "data", kit / "data", dirs_exist_ok=True)
    t = game["target"]
    fmt = {"render_model_tag": t["render_model_tag"], "data_dir": t["data_dir"], "bitmap_dir": t["bitmap_dir"],
           "data_root": str(kit / "data"), "tools": str(TOOLS_DIR)}
    cmds = [c.format(**fmt).replace("%BLENDER%", str(env.blender or "blender")) for c in plan["import_templates"]]
    plan_path = gdir / "plan.json"
    steps = [c for c in cmds if c.startswith("tool.exe bitmaps")]
    steps.append(f"{_q(env.armorytool)} apply {_q(kit)} {_q(plan_path)} --stage pre")
    steps += [c for c in cmds if not c.startswith("tool.exe bitmaps")]
    steps.append(f"{_q(env.armorytool)} apply {_q(kit)} {_q(plan_path)} --stage post")
    for step in steps:
        if "gltf_to_fbx" in step and not env.blender:
            raise StepFailed("Blender is needed for Halo 4 (blender.org) and wasn't found")
        if runner(step, kit) != 0:
            raise StepFailed(f"step failed: {step}")
    log(f"Compiling {len(plan['build_commands'])} campaign levels (this takes a while)…")
    for step in plan["build_commands"]:
        if runner(step, kit) != 0:
            raise StepFailed(f"level failed to compile: {step}")


def build_pack(catalog, env: Environment, cache: Path, work: Path, games: list[str] | None = None,
               include: set[str] | None = None, log: Log = print, runner: Runner | None = None) -> Report:
    """Build the map pack for `games` (default: all) with every piece in `include` (default: all)
    and install it, with the runtime DLL, into MCC's binaries folder."""
    if sys.platform != "win32" and runner is None:
        raise RuntimeError("Building the map pack needs Windows (the Mod Tools are Windows programs).")
    if not env.mcc:
        raise RuntimeError("Couldn't find Halo: The Master Chief Collection. Set its folder in Settings.")
    runner = runner or default_runner(log)
    cache, work = Path(cache), Path(work)
    report = Report()

    targets = []
    for gid in games or [g["id"] for g in catalog.ordered_games()]:
        game = catalog.game(gid)
        if not game["target"].get("script"):
            report.skipped[gid] = "Halo CE's Mod Tools can't be automated, so it isn't in the pack yet"
        elif gid not in env.kits:
            report.skipped[gid] = f"{game['tools_name']} not installed (Steam > Library > Tools)"
        elif not env.armorytool:
            report.skipped[gid] = "ArmoryTool.exe not found next to the builder"
        else:
            targets.append(gid)

    needed = {(gid, catalog.game(gid)["target"]["base_model"]) for gid in targets}
    for g in catalog.ordered_games():
        for p in g["pieces"]:
            if include is None or f"{g['id']}/{p['id']}" in include:
                needed.add((g["id"], p["model"]))
    extract_missing(catalog, needed, env, cache, work, log, runner)
    for gid, model in sorted(needed):
        if not is_extracted(cache, catalog.game(gid), model):
            game = catalog.game(gid)
            why = (f"{game['tools_name']} not installed" if gid not in env.kits
                   else "Halo CE pieces need the manual export in docs/ADVANCED.md" if not game.get("managedblam")
                   else "couldn't be read from the Mod Tools (see the log)")
            report.left_out.append(f"{game['name']} pieces from {model}: {why}")

    out_mod = mod_dir(env.mcc)
    load = Loader(cache, catalog)
    games_manifest = {}
    if (out_mod / "manifest.json").is_file():  # keep games built earlier when rebuilding only some
        games_manifest = json.loads((out_mod / "manifest.json").read_text()).get("games", {})
    for gid in targets:
        game = catalog.game(gid)
        kit = env.kits[gid]
        try:
            log(f"── {game['name']} ──")
            if not is_extracted(cache, game, game["target"]["base_model"]):
                raise StepFailed("couldn't read this game's player model from its Mod Tools (see the log)")
            asm = assemble_pack(catalog, gid, load, include)
            gdir = work / gid
            shutil.rmtree(gdir, ignore_errors=True)
            gdir.mkdir(parents=True)
            plan = write_target(catalog, None, gid, asm, work)
            (gdir / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
            import_and_compile(game, plan, kit, gdir, env, log, runner)
            rels = pack.map_relpaths(game)
            for rel, name in zip(rels, plan["maps"]):
                built = kit / "maps" / name
                if not built.is_file():
                    raise StepFailed(f"{name} wasn't produced by the level build")
                dst = out_mod / "maps" / Path(*rel.split("/"))
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(built, dst)
            games_manifest[gid] = pack.manifest_entry(catalog, gid, asm.manifest, rels)
            n = sum(len(v) - 1 for v in asm.manifest.values())
            report.built[gid] = {"maps": len(rels), "pieces": n}
            log(f"{game['name']}: {len(rels)} levels with {n} pieces added to the pack")
        except StepFailed as e:
            report.failed[gid] = str(e)
            log(f"{game['name']} failed: {e}")

    if games_manifest:
        out_mod.mkdir(parents=True, exist_ok=True)
        (out_mod / "manifest.json").write_text(json.dumps(pack.manifest(catalog, games_manifest), indent=2))
        if env.runtime:
            shutil.copy2(env.runtime, out_mod.parent / RUNTIME_DLL)
        else:
            log("Note: version.dll (the runtime) wasn't found next to the builder; copy it into "
                f"{out_mod.parent} yourself.")
        report.installed_to = str(out_mod)
        log(f"Installed to {out_mod}. Start MCC with mods (EAC off) and press F8 in the main menu.")
    return report


def uninstall(mcc: Path, log: Log = print) -> bool:
    """Remove the runtime and the pack. MCC's own files were never changed."""
    removed = False
    dll = mcc.joinpath(*BINARIES, RUNTIME_DLL)
    if dll.is_file():
        dll.unlink()
        removed = True
    if mod_dir(mcc).is_dir():
        shutil.rmtree(mod_dir(mcc))
        removed = True
    log("Unified Armory removed." if removed else "Unified Armory wasn't installed.")
    return removed
