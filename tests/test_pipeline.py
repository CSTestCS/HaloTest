import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from unified_armory.backends import ospath
from unified_armory.catalog import SLOTS, load_catalog, validate
from unified_armory.export import export
from unified_armory.porting import jms
from unified_armory.porting.gltf import write_glb
from unified_armory.profile import ProfileError, new_profile, validate_profile

from fixtures import BONES, build_cache

CAT = load_catalog()
HEAD = [b[0] for b in BONES].index("head")
GAMES = ["halo1", "halo2", "halo3", "odst", "reach", "halo4"]


def profile(**armor):
    p = new_profile()
    p["armor"].update(armor)
    return validate_profile(p, CAT)


MIXED = dict(helmet="reach/helmet_gungnir", chest="halo3/chest_eod", shoulder_left="halo1/shoulder_left",
             shoulder_right="halo4/shoulder_right_warrior", utility="reach/utility_ua_hardcase")


class CatalogTests(unittest.TestCase):
    def test_data_is_valid(self):
        self.assertEqual(validate(CAT), [])

    def test_every_slot_offers_pieces_from_several_games(self):
        for slot in ("helmet", "chest", "shoulder_left", "shoulder_right"):
            games = {p["game"] for p in CAT.pieces_for_slot(slot)}
            self.assertGreaterEqual(len(games), 5, slot)


class ProfileTests(unittest.TestCase):
    def test_exact_piece_ids(self):
        p = profile(helmet="reach/helmet_gungnir")
        self.assertEqual(p["armor"]["helmet"], "reach/helmet_gungnir")

    def test_rejects_wrong_slot(self):
        with self.assertRaises(ProfileError):
            profile(chest="reach/helmet_gungnir")

    def test_rejects_unknown_piece(self):
        with self.assertRaises(ProfileError):
            profile(helmet="reach/helmet_master_chief_hat")

    def test_old_profile_version_is_rejected_clearly(self):
        with self.assertRaises(ProfileError) as ctx:
            validate_profile({"version": 1}, CAT)
        self.assertIn("new profile", str(ctx.exception))

    def test_colors_are_exact(self):
        p = new_profile()
        p["colors"]["primary"] = "#123abc"
        self.assertEqual(validate_profile(p, CAT)["colors"]["primary"], "#123ABC")


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.cache = build_cache(root / "cache")
        cls.out = root / "out"
        p = profile(**MIXED)
        p["colors"]["primary"] = "#FF8000"
        p["overrides"] = {"halo2": {"helmet": "halo2/elite_helmet"}}
        cls.profile = validate_profile(p, CAT)
        cls.result = export(CAT, cls.profile, cls.out, cls.cache)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_all_six_campaigns_build(self):
        self.assertEqual(self.result["errors"], {})
        self.assertEqual(self.result["waiting"], {})
        self.assertEqual(sorted(self.result["built"]), sorted(GAMES))
        self.assertFalse((self.out / "extract").exists())

    def test_ported_helmet_sits_on_each_targets_head(self):
        for gid in ("halo1", "halo3", "odst", "reach"):
            plan = self.result["built"][gid]
            t = CAT.game(gid)["target"]
            mesh = jms.read(self.out / gid / "data" / ospath(t["data_dir"]) / ospath(t["model_file"]))
            head = mesh.world_matrices()[HEAD]
            hx, hy, hz = head[0][3], head[1][3], head[2][3]
            if gid == "reach":  # same-game piece keeps Reach's own material; it lands in the helmet region
                tris = [tr for tr in mesh.triangles if tr.region == "helmet"]
            else:
                mat = next(i for i, m in enumerate(mesh.materials) if m.name.startswith("reach_"))
                tris = [tr for tr in mesh.triangles if tr.material == mat and tr.region != "utility"]
            verts = {v for tr in tris for v in tr.v}
            self.assertTrue(verts, gid)
            cz = sum(mesh.vertices[v].position[2] for v in verts) / len(verts)
            self.assertAlmostEqual(cz, hz, delta=1e-4, msg=gid)
            self.assertIn("Gungnir", plan["pieces"]["Helmet"])

    def test_replaced_slots_drop_the_targets_own_armor(self):
        t = CAT.game("halo3")["target"]
        mesh = jms.read(self.out / "halo3" / "data" / ospath(t["data_dir"]) / ospath(t["model_file"]))
        own = next(i for i, m in enumerate(mesh.materials) if m.name == "masterchief_armor")
        head_owned = [tr for tr in mesh.triangles if tr.material == own
                      and all(max(mesh.vertices[v].weights, key=lambda w: w[1])[0] == HEAD for v in tr.v)]
        self.assertEqual(head_owned, [])

    def test_legacy_weights_for_halo1(self):
        t = CAT.game("halo1")["target"]
        text = (self.out / "halo1" / "data" / ospath(t["data_dir"]) / ospath(t["model_file"])).read_text()
        self.assertTrue(text.startswith("8200\n"))

    def test_override_applies_to_one_game(self):
        self.assertIn("Elite", self.result["built"]["halo2"]["pieces"]["Helmet"])
        self.assertIn("Gungnir", self.result["built"]["halo3"]["pieces"]["Helmet"])

    def test_plan_stages(self):
        plan = self.result["built"]["halo3"]
        pre_ops = {op["op"] for op in plan["stages"]["pre"]}
        self.assertEqual(pre_ops, {"copy_tag", "clone_shader"})
        clones = [op for op in plan["stages"]["pre"] if op["op"] == "clone_shader"]
        self.assertTrue(all(op["bitmaps"] for op in clones))
        for op in clones:
            for bitmap in op["bitmaps"].values():
                tif = self.out / "halo3" / "data" / ospath(bitmap.rsplit(".", 1)[0] + ".tif")
                self.assertTrue(tif.is_file(), tif)
        post = plan["stages"]["post"]
        ref = next(op for op in post if op["op"] == "set_reference")
        self.assertEqual(ref["value"], CAT.game("halo3")["target"]["render_model_tag"])
        color = next(op for op in post if op["op"] == "set_change_color" and op["channel"] == "primary")
        self.assertEqual(color["color"], [1.0, 0.502, 0.0])  # exact, not snapped

    def test_shader_names_are_unique(self):
        for plan in self.result["built"].values():
            outs = [op["to"].lower() for op in plan["stages"]["pre"]]
            self.assertEqual(len(outs), len(set(outs)), plan["game"])

    def test_scripts_and_notes(self):
        for gid in GAMES:
            g = self.out / gid
            for f in ("plan.json", "install.bat", "build.bat", "NOTES.md", "preview.glb"):
                self.assertTrue((g / f).is_file(), f"{gid}/{f}")
        self.assertIn("ArmoryTool", (self.out / "halo3" / "install.bat").read_text())
        ce = (self.out / "halo1" / "install.bat").read_text()
        self.assertNotIn("ArmoryTool\" apply", ce.replace("%AT%", "ArmoryTool"))
        self.assertIn("Manual steps", (self.out / "halo1" / "NOTES.md").read_text())
        self.assertTrue((self.out / "tools" / "blender" / "gltf_to_fbx.py").is_file())

    def test_glb_is_valid(self):
        data = (self.out / "halo4" / "preview.glb").read_bytes()
        self.assertEqual(data[:4], b"glTF")
        n = int.from_bytes(data[12:16], "little")
        doc = json.loads(data[20:20 + n])
        self.assertEqual(len(doc["skins"][0]["joints"]), len(BONES))


class ExtractionTests(unittest.TestCase):
    def test_missing_models_produce_extract_jobs(self):
        with tempfile.TemporaryDirectory() as d:
            r = export(CAT, profile(helmet="reach/helmet_gungnir"), Path(d) / "out", Path(d) / "cache")
            self.assertEqual(r["built"], {})
            self.assertEqual(len(r["waiting"]), 6)
            job = json.loads((Path(d) / "out" / "extract" / "reach.json").read_text())
            self.assertEqual(job["jobs"][0]["tag"], CAT.game("reach")["models"]["spartans"]["tag"])
            self.assertTrue((Path(d) / "out" / "extract" / "reach.bat").is_file())
            self.assertTrue((Path(d) / "out" / "extract" / "halo1.txt").is_file())


class JmsFallbackTests(unittest.TestCase):
    def test_hand_exported_jms_replaces_a_tag_dump(self):
        from fixtures import body_model
        from unified_armory.porting.cache import model_dir
        with tempfile.TemporaryDirectory() as d:
            cache = build_cache(Path(d) / "cache")
            h2 = model_dir(cache, "halo2", "masterchief")
            (h2 / "model.json").unlink()
            jms.write(body_model("frame", 1.0, "objects\\characters\\masterchief\\shaders\\masterchief.shader"), h2 / "model.jms")
            p = profile(helmet="reach/helmet_gungnir")
            p["campaign"]["games"] = ["halo2"]
            r = export(CAT, p, Path(d) / "out", cache)
            self.assertEqual(list(r["built"]), ["halo2"])


class ServerTests(unittest.TestCase):
    def test_api(self):
        from unified_armory.server import make_handler
        with tempfile.TemporaryDirectory() as d:
            cache = build_cache(Path(d) / "cache")
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(CAT, Path(d) / "p.json", Path(d) / "out", cache))
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{httpd.server_address[1]}"

            def post(path, body):
                req = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
                return json.loads(urllib.request.urlopen(req).read())
            try:
                cat = json.loads(urllib.request.urlopen(base + "/api/catalog").read())
                self.assertEqual(set(cat["slots"]), set(SLOTS))
                self.assertIn("<title>", urllib.request.urlopen(base + "/").read().decode())
                st = post("/api/status", profile(helmet="reach/helmet_gungnir"))
                self.assertTrue(all(m["ready"] for m in st["models"]))
                r = post("/api/build", profile(helmet="reach/helmet_gungnir"))
                self.assertEqual(sorted(r["built"]), sorted(GAMES))
                with self.assertRaises(urllib.error.HTTPError):
                    post("/api/build", {"armor": {"helmet": "nope/nope"}})
            finally:
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()
