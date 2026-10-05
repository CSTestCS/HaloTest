import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from unified_armory import autopilot
from unified_armory.catalog import load_catalog
from unified_armory.profile import new_profile, validate_profile

from fixtures import build_cache

CAT = load_catalog()
KITS = {"halo1": "H1EK", "halo2": "H2EK", "halo3": "H3EK", "odst": "H3ODSTEK", "reach": "HREK", "halo4": "H4EK"}


def make_steam(root: Path, kits=KITS) -> Path:
    """Steam root whose MCC and Mod Tools live in a second library, like many real setups."""
    steam, lib = root / "Steam", root / "Games" / "SteamLibrary"
    (steam / "steamapps").mkdir(parents=True)
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n "0"\n {\n  "path" "%s"\n }\n "1"\n {\n  "path" "%s"\n }\n}\n'
        % (str(steam).replace("\\", "\\\\"), str(lib).replace("\\", "\\\\")))
    common = lib / "steamapps" / "common"
    mcc = common / autopilot.MCC_FOLDER
    for game in CAT.ordered_games():
        maps = mcc.joinpath(*game["mcc_maps"].split("\\"))
        maps.mkdir(parents=True)
        for s in game["target"]["scenarios"]:
            (maps / (s.replace("\\", "/").rsplit("/", 1)[-1] + ".map")).write_text("original")
    for gid, folder in kits.items():
        kit = common / folder
        (kit / "bin").mkdir(parents=True)
        (kit / "tool.exe").write_text("")
        if CAT.game(gid).get("managedblam"):
            (kit / "bin" / "ManagedBlam.dll").write_text("")
    return steam


class FakeTools:
    """Stands in for ArmoryTool and tool.exe: extraction copies from a prepared cache,
    level builds write a .map into the kit's maps folder."""

    def __init__(self, source_cache: Path):
        self.source, self.commands = source_cache, []

    def __call__(self, cmd: str, cwd: Path) -> int:
        self.commands.append(cmd)
        if " extract " in cmd:
            jobs = json.loads(Path(re.findall(r'"([^"]+)"', cmd)[-1]).read_text())["jobs"]
            for job in jobs:
                out = Path(job["out"])
                shutil.copytree(self.source / out.parent.name / out.name, out, dirs_exist_ok=True)
        elif "build-cache-file" in cmd:
            name = cmd.split()[-1].replace("\\", "/").rsplit("/", 1)[-1]
            (cwd / "maps").mkdir(exist_ok=True)
            (cwd / "maps" / f"{name}.map").write_text("modded")
        return 0


class AutopilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.steam = make_steam(root)
        self.source = build_cache(root / "fixture_cache")
        self.cache, self.out = root / "cache", root / "out"
        self.env = autopilot.detect(CAT, roots=[self.steam])
        self.env.armorytool = root / "ArmoryTool.exe"
        self.env.blender = root / "blender.exe"
        self.tools = FakeTools(self.source)
        self.lines = []

    def tearDown(self):
        self.tmp.cleanup()

    def profile(self, **armor):
        p = new_profile()
        p["armor"].update(armor)
        return validate_profile(p, CAT)

    def run_apply(self, profile):
        return autopilot.apply(CAT, profile, self.env, self.cache, self.out, log=self.lines.append, runner=self.tools)

    def maps(self, gid):
        g = CAT.game(gid)
        return self.env.mcc.joinpath(*g["mcc_maps"].split("\\"))

    def test_detects_mcc_and_kits_across_libraries(self):
        self.assertTrue(self.env.mcc.name == autopilot.MCC_FOLDER)
        self.assertEqual(set(self.env.kits), set(KITS))

    def test_kit_without_managedblam_is_not_detected(self):
        (self.env.kits["halo3"] / "bin" / "ManagedBlam.dll").unlink()
        self.assertNotIn("halo3", autopilot.detect(CAT, roots=[self.steam]).kits)

    def test_settings_pin_paths(self):
        env = autopilot.detect(CAT, {"mcc": str(self.env.mcc), "kits": {"halo3": str(self.env.kits["reach"])}}, roots=[])
        self.assertEqual(env.mcc, self.env.mcc)
        self.assertEqual(env.kits, {"halo3": self.env.kits["reach"]})

    def test_one_click_apply(self):
        report = self.run_apply(self.profile(helmet="reach/helmet_gungnir", chest="halo3/chest_eod",
                                             shoulder_left="halo1/shoulder_left"))
        self.assertEqual(set(report.installed), {"halo2", "halo3", "odst", "reach", "halo4"}, report.failed)
        self.assertIn("halo1", report.skipped)
        self.assertTrue(any("Mark V (CE)" in d for d in report.dropped))  # CE piece needs the manual export
        for gid in report.installed:
            maps = list(self.maps(gid).glob("*.map"))
            self.assertTrue(maps and all(m.read_text() == "modded" for m in maps), gid)
            backup = self.env.mcc / autopilot.BACKUP_FOLDER / gid
            self.assertTrue(all(b.read_text() == "original" for b in backup.glob("*.map")))
        self.assertTrue(all(m.read_text() == "original" for m in self.maps("halo1").glob("*.map")))
        # Steps ran in order for a game: bitmaps, pre edits, import, post edits, level builds.
        h3 = [c for c in self.tools.commands if str(self.env.kits["halo3"]) in c or "masterchief_armory" in c
              or "levels\\solo" in c]
        order = [next(i for i, c in enumerate(self.tools.commands) if key in c)
                 for key in ("tool.exe bitmaps \"objects\\characters\\masterchief_armory", "--stage pre",
                             "tool.exe render \"objects\\characters\\masterchief_armory", "--stage post", "005_intro")]
        self.assertEqual(order, sorted(order))
        self.assertTrue(h3)
        # Ported model and textures were copied into the kit.
        self.assertTrue((self.env.kits["halo3"] / "data" / "objects" / "characters" / "masterchief_armory" / "render"
                         / "masterchief_armory.jms").is_file())

    def test_second_apply_keeps_true_originals_and_restore_puts_them_back(self):
        self.run_apply(self.profile(helmet="reach/helmet_gungnir"))
        self.run_apply(self.profile(helmet="halo4/helmet_warrior"))
        backup = self.env.mcc / autopilot.BACKUP_FOLDER / "halo3"
        self.assertTrue(all(b.read_text() == "original" for b in backup.glob("*.map")))
        restored = autopilot.restore(CAT, self.env.mcc, log=self.lines.append)
        self.assertGreater(restored["halo3"], 0)
        for gid in ("halo2", "halo3", "odst", "reach", "halo4"):
            self.assertTrue(all(m.read_text() == "original" for m in self.maps(gid).glob("*.map")), gid)
        self.assertFalse((self.env.mcc / autopilot.BACKUP_FOLDER).exists())

    def test_missing_kits_skip_games_and_drop_their_pieces(self):
        for gid in ("reach", "halo4"):
            del self.env.kits[gid]
        report = self.run_apply(self.profile(helmet="reach/helmet_gungnir", chest="halo3/chest_eod"))
        self.assertIn("Reach Mod Tools", report.skipped["reach"])
        self.assertTrue(any("Gungnir" in d for d in report.dropped))
        self.assertEqual(set(report.installed), {"halo2", "halo3", "odst"})

    def test_halo4_needs_blender(self):
        self.env.blender = None
        report = self.run_apply(self.profile(helmet="reach/helmet_gungnir"))
        self.assertIn("Blender", report.failed["halo4"])
        self.assertIn("halo3", report.installed)

    def test_failed_level_build_leaves_maps_untouched(self):
        def runner(cmd, cwd):
            return 1 if "build-cache-file" in cmd and "H3EK" in str(cwd) else self.tools(cmd, cwd)
        report = autopilot.apply(CAT, self.profile(helmet="reach/helmet_gungnir"), self.env, self.cache, self.out,
                                 log=self.lines.append, runner=runner)
        self.assertIn("halo3", report.failed)
        self.assertTrue(all(m.read_text() == "original" for m in self.maps("halo3").glob("*.map")))


class LauncherTests(unittest.TestCase):
    def test_no_command_opens_the_app(self):
        from unittest import mock
        from unified_armory import __main__ as cli
        with mock.patch("unified_armory.server.serve") as serve, tempfile.TemporaryDirectory() as d:
            cli.main(["--profile", str(Path(d) / "p.json")])
            self.assertTrue(serve.called)
            self.assertTrue(serve.call_args.kwargs["open_browser"])
            cli.main(["--profile", str(Path(d) / "p.json"), "--port", "9999", "--no-browser"])
            self.assertEqual(serve.call_args.kwargs["port"], 9999)
            self.assertFalse(serve.call_args.kwargs["open_browser"])


if __name__ == "__main__":
    unittest.main()
