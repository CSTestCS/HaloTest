import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from unified_armory import autopilot, pack
from unified_armory.catalog import load_catalog

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
    mcc.joinpath(*autopilot.BINARIES).mkdir(parents=True)
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
                src = self.source / out.parent.name / out.name
                if src.is_dir():
                    shutil.copytree(src, out, dirs_exist_ok=True)
        elif "build-cache-file" in cmd:
            name = cmd.split()[-1].replace("\\", "/").rsplit("/", 1)[-1]
            (cwd / "maps").mkdir(exist_ok=True)
            (cwd / "maps" / f"{name}.map").write_text("pack:" + cwd.name)
        return 0


class PackBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.steam = make_steam(root)
        self.source = build_cache(root / "fixture_cache")
        self.cache, self.work = root / "cache", root / "work"
        self.env = autopilot.detect(CAT, roots=[self.steam])
        self.env.armorytool = root / "ArmoryTool.exe"
        self.env.blender = root / "blender.exe"
        self.env.runtime = root / "version.dll"
        self.env.runtime.write_text("dll")
        self.tools = FakeTools(self.source)
        self.lines = []

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, **kw):
        return autopilot.build_pack(CAT, self.env, self.cache, self.work, log=self.lines.append, runner=self.tools, **kw)

    def maps(self, gid):
        return self.env.mcc.joinpath(*CAT.game(gid)["mcc_maps"].split("\\"))

    def test_detects_mcc_and_kits_across_libraries(self):
        self.assertEqual(self.env.mcc.name, autopilot.MCC_FOLDER)
        self.assertEqual(set(self.env.kits), set(KITS))

    def test_kit_without_managedblam_is_not_detected(self):
        (self.env.kits["halo3"] / "bin" / "ManagedBlam.dll").unlink()
        self.assertNotIn("halo3", autopilot.detect(CAT, roots=[self.steam]).kits)

    def test_settings_pin_paths(self):
        env = autopilot.detect(CAT, {"mcc": str(self.env.mcc), "kits": {"halo3": str(self.env.kits["reach"])}}, roots=[])
        self.assertEqual(env.mcc, self.env.mcc)
        self.assertEqual(env.kits, {"halo3": self.env.kits["reach"]})

    def test_builds_and_installs_the_mod_without_touching_mcc_files(self):
        report = self.build()
        self.assertEqual(set(report.built), {"halo2", "halo3", "odst", "reach", "halo4"}, report.failed)
        self.assertIn("halo1", report.skipped)
        mod = autopilot.mod_dir(self.env.mcc)
        self.assertEqual(report.installed_to, str(mod))
        self.assertEqual((mod.parent / "version.dll").read_text(), "dll")
        manifest = json.loads((mod / "manifest.json").read_text())
        self.assertEqual(manifest["format"], pack.PACK_FORMAT)
        self.assertEqual(set(manifest["games"]), set(report.built))
        h3 = manifest["games"]["halo3"]
        self.assertEqual(h3["magic"], pack.MAGIC_BASE + 3)
        helmets = h3["slots"]["helmet"]
        self.assertEqual(helmets[0]["uid"], "own")
        self.assertIn("reach/helmet_gungnir", [e["uid"] for e in helmets])
        self.assertEqual([e["index"] for e in helmets], list(range(len(helmets))))
        for rel in h3["maps"]:
            self.assertTrue((mod / "maps" / rel).read_text().startswith("pack:"), rel)
        for gid in report.built:  # MCC's own maps are never modified
            self.assertTrue(all(m.read_text() == "original" for m in self.maps(gid).glob("*.map")), gid)
        self.assertTrue(any("F8" in line for line in self.lines))

    def test_mission_script_is_attached_to_every_level(self):
        self.build(games=["halo3"])
        plan = json.loads((self.work / "halo3" / "plan.json").read_text())
        attached = [op for op in plan["stages"]["post"] if op["op"] == "add_script"]
        self.assertEqual(len(attached), len(CAT.game("halo3")["target"]["scenarios"]))
        kit_script = self.env.kits["halo3"] / "data" / "levels" / "solo" / "010_jungle" / "scripts" / "unified_armory.hsc"
        text = kit_script.read_text()
        self.assertIn(f"(global long ua_magic {pack.MAGIC_BASE + 3})", text)
        self.assertNotIn("set_change_color", json.dumps(plan))  # colors aren't baked into the pack

    def test_missing_source_kits_leave_their_pieces_out(self):
        del self.env.kits["reach"]
        report = self.build(games=["halo3"])
        self.assertTrue(any("Halo: Reach" in d for d in report.left_out))
        manifest = json.loads((autopilot.mod_dir(self.env.mcc) / "manifest.json").read_text())
        uids = [e["uid"] for e in manifest["games"]["halo3"]["slots"]["helmet"]]
        self.assertNotIn("reach/helmet_gungnir", uids)
        self.assertIn("halo3/helmet_eod", uids)

    def test_rebuilding_one_game_keeps_the_others(self):
        self.build()
        self.build(games=["halo3"])
        manifest = json.loads((autopilot.mod_dir(self.env.mcc) / "manifest.json").read_text())
        self.assertEqual(set(manifest["games"]), {"halo2", "halo3", "odst", "reach", "halo4"})

    def test_halo4_needs_blender(self):
        self.env.blender = None
        report = self.build()
        self.assertIn("Blender", report.failed["halo4"])
        self.assertIn("halo3", report.built)

    def test_failed_level_build_is_reported(self):
        def runner(cmd, cwd):
            return 1 if "build-cache-file" in cmd and "H3EK" in str(cwd) else self.tools(cmd, cwd)
        report = autopilot.build_pack(CAT, self.env, self.cache, self.work, log=self.lines.append, runner=runner)
        self.assertIn("halo3", report.failed)
        self.assertIn("halo2", report.built)

    def test_uninstall(self):
        self.build(games=["halo3"])
        self.assertTrue(autopilot.uninstall(self.env.mcc, log=self.lines.append))
        self.assertFalse(autopilot.mod_dir(self.env.mcc).exists())
        self.assertFalse(self.env.mcc.joinpath(*autopilot.BINARIES, "version.dll").exists())


class ScriptTests(unittest.TestCase):
    MANIFEST = {"helmet": [{"index": 0, "uid": "own", "perm": "own"},
                           {"index": 1, "uid": "reach/helmet_gungnir", "perm": "reach__helmet_gungnir"}]}

    def test_globals_follow_the_runtime_protocol(self):
        text = pack.generate_script(CAT, "halo3", self.MANIFEST)
        names = re.findall(r"\(global long (\w+) ", text)
        slots = [f"ua_{s}" for s in ("helmet", "chest", "shoulder_left", "shoulder_right", "wrist", "utility", "knees")]
        # The order is the protocol: magic, echo, slots..., tick, seed.
        self.assertEqual(names, ["ua_magic", "ua_echo", *slots, "ua_tick", "ua_seed"])
        self.assertIn(f"(global long ua_seed {pack.SEED})", text)
        self.assertIn("(global long ua_echo 0)", text)          # only the running script sets it
        self.assertIn("(set ua_echo ua_seed)", text)
        self.assertNotIn(f"ua_echo {pack.SEED}", text)
        self.assertIn('(object_set_permutation (player0) "ua_helmet" "reach__helmet_gungnir")', text)

    def test_parentheses_balance_and_dialects(self):
        for gid in ("halo2", "halo3", "odst", "reach", "halo4"):
            text = pack.generate_script(CAT, gid, self.MANIFEST)
            code = "\n".join(l.split(";", 1)[0] for l in text.splitlines())
            self.assertEqual(code.count("("), code.count(")"), gid)
        self.assertIn("(unit (list_get (players) 0))", pack.generate_script(CAT, "halo2", self.MANIFEST))


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
