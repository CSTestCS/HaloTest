import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from unified_armory.backends import build_all, build_plan
from unified_armory.catalog import load_catalog, validate
from unified_armory.color import hex_to_rgb, hex_to_unit_rgb, nearest_swatch
from unified_armory.export import export
from unified_armory.profile import ProfileError, new_profile, validate_profile
from unified_armory.resolver import resolve_all, resolve_game

CAT = load_catalog()


def profile(**armor):
    p = new_profile()
    p["armor"].update(armor)
    return validate_profile(p, CAT)


class CatalogTests(unittest.TestCase):
    def test_catalog_is_valid(self):
        self.assertEqual(validate(CAT), [])

    def test_all_six_games_present(self):
        self.assertEqual([g["id"] for g in CAT.ordered_games()],
                         ["halo1", "halo2", "halo3", "odst", "reach", "halo4"])

    def test_universal_slots_cover_every_game_slot(self):
        slots = CAT.universal_slots()
        for g in CAT.games.values():
            for s in g["slots"]:
                self.assertIn(s, slots)


class ColorTests(unittest.TestCase):
    def test_hex_parsing(self):
        self.assertEqual(hex_to_rgb("#fff"), (255, 255, 255))
        self.assertEqual(hex_to_unit_rgb("#FF0000"), [1.0, 0.0, 0.0])
        with self.assertRaises(ValueError):
            hex_to_rgb("#12345G")

    def test_exact_swatch_has_zero_delta(self):
        pal = CAT.palette("halo3")
        sw, de = nearest_swatch(pal[3]["hex"], pal)
        self.assertEqual(sw["id"], pal[3]["id"])
        self.assertAlmostEqual(de, 0.0, places=6)

    def test_nearest_uses_game_palette(self):
        sw, _ = nearest_swatch("#CC0000", CAT.palette("halo1"))
        self.assertEqual(sw["id"], "red")


class ResolverTests(unittest.TestCase):
    def test_exact_family_match(self):
        r = resolve_game(CAT, profile(helmet="eod"), "halo3")
        self.assertEqual(r["slots"]["helmet"]["option"]["id"], "eod")
        self.assertEqual(r["slots"]["helmet"]["quality"], "exact")

    def test_similar_fallback(self):
        # Halo 3 has no Military Police helmet; its closest relative is Security.
        r = resolve_game(CAT, profile(helmet="military_police"), "halo3")
        self.assertEqual(r["slots"]["helmet"]["option"]["id"], "security")
        self.assertEqual(r["slots"]["helmet"]["quality"], "similar")

    def test_cross_game_name_mapping(self):
        # One universal "CQB" pick becomes Reach's CQC helmet.
        r = resolve_game(CAT, profile(helmet="cqb"), "reach")
        self.assertEqual(r["slots"]["helmet"]["option"]["id"], "cqc")

    def test_override_wins(self):
        p = new_profile()
        p["overrides"] = {"reach": {"slots": {"helmet": "haunted"}, "colors": {"primary": "pink"}}}
        r = resolve_game(CAT, validate_profile(p, CAT), "reach")
        self.assertEqual(r["slots"]["helmet"]["option"]["id"], "haunted")
        self.assertEqual(r["slots"]["helmet"]["quality"], "override")
        self.assertEqual(r["colors"]["primary"]["swatch"]["id"], "pink")

    def test_every_game_resolves_every_family(self):
        for slot in CAT.universal_slots():
            for fam in CAT.families:
                p = new_profile()
                p["armor"][slot] = fam
                resolve_all(CAT, validate_profile(p, CAT))


class ProfileTests(unittest.TestCase):
    def test_rejects_unknown_family(self):
        p = new_profile()
        p["armor"]["helmet"] = "spartan_iv_mk9000"
        with self.assertRaises(ProfileError):
            validate_profile(p, CAT)

    def test_rejects_bad_override(self):
        p = new_profile()
        p["overrides"] = {"halo1": {"slots": {"helmet": "eod"}}}
        with self.assertRaises(ProfileError):
            validate_profile(p, CAT)

    def test_rejects_bad_color(self):
        p = new_profile()
        p["colors"]["primary"] = "green"
        with self.assertRaises(ProfileError):
            validate_profile(p, CAT)


class BackendTests(unittest.TestCase):
    def test_h3_campaign_gets_armor_variant_and_colors(self):
        plan = build_plan(CAT, profile(helmet="eod", chest="recon"), "halo3")
        ops = [e["op"] for e in plan["edits"]]
        self.assertIn("set_model_variant", ops)
        self.assertIn("set_player_representation", ops)
        variant = next(e for e in plan["edits"] if e["op"] == "set_model_variant")
        self.assertEqual(variant["regions"]["helmet"], "eod")
        self.assertEqual(variant["regions"]["chest"], "recon")
        rep = next(e for e in plan["edits"] if e["op"] == "set_player_representation")
        self.assertEqual(rep["third_person_variant"], variant["variant"])
        colored = {e["tag"] for e in plan["edits"] if e["op"] == "set_change_color"}
        self.assertIn("objects\\characters\\masterchief\\masterchief.biped", colored)
        self.assertEqual(len(plan["build_commands"]), 10)

    def test_halo1_is_colors_only(self):
        plan = build_plan(CAT, profile(), "halo1")
        self.assertEqual({e["op"] for e in plan["edits"]}, {"set_change_color"})
        self.assertEqual(plan["edits"][0]["index"], 0)

    def test_reach_native_unless_baked(self):
        p = profile()
        self.assertEqual(build_plan(CAT, p, "reach")["edits"], [])
        p["campaign"]["bake_reach"] = True
        baked = build_plan(CAT, p, "reach")
        self.assertEqual(baked["strategy"], "bake")
        self.assertTrue(any(e["op"] == "set_model_variant" for e in baked["edits"]))

    def test_disabled_game_has_no_edits_or_builds(self):
        p = profile()
        p["campaign"]["games"] = ["halo3"]
        plans = build_all(CAT, p)
        self.assertEqual(plans["halo2"]["edits"], [])
        self.assertEqual(plans["halo2"]["build_commands"], [])
        self.assertTrue(plans["halo2"]["menu_checklist"])
        self.assertTrue(plans["halo3"]["edits"])

    def test_mp_only_slots_stay_out_of_campaign(self):
        variant = next(e for e in build_plan(CAT, profile(), "halo3")["edits"] if e["op"] == "set_model_variant")
        self.assertNotIn("species", variant["regions"])

    def test_export_writes_files(self):
        with tempfile.TemporaryDirectory() as d:
            export(CAT, profile(helmet="eva"), Path(d))
            plan = json.loads((Path(d) / "halo3" / "plan.json").read_text())
            self.assertEqual(plan["format"], "unified-armory-plan/1")
            self.assertTrue((Path(d) / "halo3" / "apply.bat").exists())
            self.assertFalse((Path(d) / "reach" / "apply.bat").exists())
            self.assertIn("EVA", (Path(d) / "halo3" / "CHECKLIST.md").read_text())


class ServerTests(unittest.TestCase):
    def test_api_round_trip(self):
        from unified_armory.server import make_handler
        with tempfile.TemporaryDirectory() as d:
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(CAT, Path(d) / "p.json", Path(d) / "out"))
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            try:
                def post(path, body):
                    req = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
                    return json.loads(urllib.request.urlopen(req).read())
                self.assertIn("halo4", json.loads(urllib.request.urlopen(base + "/api/catalog").read())["games"])
                self.assertIn("<title>Unified Armory</title>", urllib.request.urlopen(base + "/").read().decode())
                res = post("/api/resolve", profile(helmet="odst"))
                self.assertEqual(res["resolved"]["reach"]["slots"]["helmet"]["option"]["id"], "odst")
                post("/api/export", profile())
                self.assertTrue((Path(d) / "out" / "halo3" / "plan.json").exists())
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(base + "/../catalog.py")
                self.assertEqual(ctx.exception.code, 404)
            finally:
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()
