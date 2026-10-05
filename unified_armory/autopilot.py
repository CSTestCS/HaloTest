"""One-click apply: find MCC and the Mod Tools, then extract, port, import, compile and install.

Everything the build scripts do by hand happens here in order, per game. Games
whose Mod Tools aren't installed are skipped with a clear reason; pieces whose
source game isn't available are left off rather than blocking the rest. Original
campaign maps are backed up once (never overwritten by a modded copy) and can
be restored with restore().
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .catalog import DEFAULT
from .export import TOOLS_DIR, export, needed_models
from .porting.cache import is_extracted, model_dir
from .profile import loadout_for

MCC_FOLDER = "Halo The Master Chief Collection"
BACKUP_FOLDER = "UnifiedArmoryBackup"

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

    def to_json(self) -> dict:
        return {"mcc": str(self.mcc) if self.mcc else None,
                "kits": {g: str(p) for g, p in self.kits.items()},
                "armorytool": str(self.armorytool) if self.armorytool else None,
                "blender": str(self.blender) if self.blender else None}


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
    env = Environment(armorytool=find_armorytool(), blender=find_blender())
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
    installed: dict = field(default_factory=dict)  # game -> list of maps installed
    skipped: dict = field(default_factory=dict)    # game -> reason
    failed: dict = field(default_factory=dict)     # game -> reason
    dropped: list = field(default_factory=list)    # pieces left off, with reasons

    def to_json(self) -> dict:
        return self.__dict__


class StepFailed(Exception):
    pass


def apply(catalog, profile: dict, env: Environment, cache: Path, out_dir: Path,
          log: Log = print, runner: Runner | None = None, install_maps: bool = True) -> Report:
    if sys.platform != "win32" and runner is None:
        raise RuntimeError("Applying to the game needs Windows (the Mod Tools are Windows programs).")
    runner = runner or default_runner(log)
    cache, out_dir = Path(cache), Path(out_dir)
    report = Report()
    profile = copy.deepcopy(profile)
    if not env.mcc and install_maps:
        raise RuntimeError("Couldn't find Halo: The Master Chief Collection. Set its folder in Settings.")

    # 1. Which campaigns can we do at all?
    targets = []
    for gid in profile["campaign"]["games"]:
        game = catalog.game(gid)
        if gid not in env.kits:
            report.skipped[gid] = f"{game['tools_name']} not installed (Steam > Library > Tools)"
        elif not game.get("managedblam"):
            report.skipped[gid] = "Halo CE's Mod Tools can't be automated; see docs/ADVANCED.md for the manual route"
        elif not env.armorytool:
            report.skipped[gid] = "ArmoryTool.exe not found next to UnifiedArmory.exe"
        else:
            targets.append(gid)
    profile["campaign"]["games"] = targets
    if not targets:
        log("Nothing to do: no selected game has its Mod Tools installed.")
        return report

    # 2. Extract every source model we can; anything we can't is left off the loadout.
    def can_extract(gid, model):
        g = catalog.game(gid)
        return "tag" in g["models"][model] and gid in env.kits and g.get("managedblam") and env.armorytool

    jobs: dict[str, list] = {}
    for gid, model in sorted(needed_models(catalog, profile)):
        if not is_extracted(cache, catalog.game(gid), model) and can_extract(gid, model):
            jobs.setdefault(gid, []).append({"tag": catalog.game(gid)["models"][model]["tag"],
                                             "out": str(model_dir(cache, gid, model).resolve())})
    for gid, job in jobs.items():
        game = catalog.game(gid)
        log(f"Extracting {len(job)} model(s) from {game['name']}…")
        jobfile = out_dir / "extract" / f"{gid}.json"
        jobfile.parent.mkdir(parents=True, exist_ok=True)
        jobfile.write_text(json.dumps({"bitmap_command": game["bitmap_export"], "jobs": job}, indent=2))
        runner(f"{_q(env.armorytool)} extract {_q(env.kits[gid])} {_q(jobfile)}", env.kits[gid])

    for gid in list(targets):
        if not is_extracted(cache, catalog.game(gid), catalog.game(gid)["target"]["base_model"]):
            report.failed[gid] = "couldn't read this game's player model from its Mod Tools (see the log)"
            targets.remove(gid)
        ov = profile["overrides"].setdefault(gid, {})
        for slot, uid in loadout_for(profile, gid).items():
            if uid == DEFAULT:
                continue
            piece = catalog.piece(uid)
            src = catalog.game(piece["game"])
            if not is_extracted(cache, src, piece["model"]):
                ov[slot] = DEFAULT
                why = (f"{src['tools_name']} not installed" if piece["game"] not in env.kits
                       else "Halo CE pieces need the manual export in docs/ADVANCED.md" if not src.get("managedblam")
                       else "its model couldn't be extracted")
                entry = f"{piece['name']} ({src['name']}): {why}"
                if entry not in report.dropped:
                    report.dropped.append(entry)
    profile["campaign"]["games"] = targets
    for d in report.dropped:
        log(f"Left off: {d}")

    # 3. Port.
    log("Porting armor…")
    result = export(catalog, profile, out_dir, cache)
    for gid, err in {**result["errors"], **{g: f"missing {m}" for g, m in result["waiting"].items()}}.items():
        report.failed[gid] = err

    # 4. Import, compile and install, one game at a time.
    for gid, plan in result["built"].items():
        game = catalog.game(gid)
        kit = env.kits[gid]
        try:
            log(f"── {game['name']} ──")
            shutil.copytree(out_dir / gid / "data", kit / "data", dirs_exist_ok=True)
            fmt = {"render_model_tag": game["target"]["render_model_tag"], "data_dir": game["target"]["data_dir"],
                   "bitmap_dir": game["target"]["bitmap_dir"], "data_root": str(kit / "data"), "tools": str(TOOLS_DIR)}
            cmds = [c.format(**fmt).replace("%BLENDER%", str(env.blender or "blender")) for c in plan["import_templates"]]
            plan_path = out_dir / gid / "plan.json"
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
            installed = install_built_maps(game, plan["maps"], kit, env.mcc, log) if install_maps else []
            report.installed[gid] = installed
        except StepFailed as e:
            report.failed[gid] = str(e)
            log(f"{game['name']} failed: {e}")
    return report


# ------------------------------------------------------------------ maps

def _mcc_maps(game: dict, mcc: Path) -> Path:
    return mcc.joinpath(*game["mcc_maps"].split("\\"))


def install_built_maps(game: dict, maps: list[str], kit: Path, mcc: Path, log: Log) -> list[str]:
    dest = _mcc_maps(game, mcc)
    backup = mcc / BACKUP_FOLDER / game["id"]
    backup.mkdir(parents=True, exist_ok=True)
    done = []
    for name in maps:
        built = kit / "maps" / name
        if not built.is_file():
            raise StepFailed(f"{name} wasn't produced by the level build")
        original = dest / name
        if original.is_file() and not (backup / name).is_file():
            shutil.copy2(original, backup / name)  # back up the real original only once
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(built, original)
        done.append(name)
    log(f"Installed {len(done)} {game['name']} maps (originals kept in {backup})")
    return done


def restore(catalog, mcc: Path, log: Log = print) -> dict[str, int]:
    """Put every backed-up original map back and remove the backup."""
    restored = {}
    root = Path(mcc) / BACKUP_FOLDER
    for game in catalog.ordered_games():
        backup = root / game["id"]
        if not backup.is_dir():
            continue
        dest = _mcc_maps(game, Path(mcc))
        n = 0
        for f in backup.glob("*.map"):
            shutil.copy2(f, dest / f.name)
            n += 1
        shutil.rmtree(backup)
        restored[game["id"]] = n
        log(f"Restored {n} original {game['name']} maps")
    if root.is_dir() and not any(root.iterdir()):
        root.rmdir()
    return restored
