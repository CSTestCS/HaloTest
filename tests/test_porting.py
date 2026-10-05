import math
import tempfile
import unittest
from pathlib import Path

from unified_armory.porting import jms, math3d, tagdump
from unified_armory.porting.images import read_tga, write_tiff
from unified_armory.porting.retarget import (base_permutations, canonical_bone, match_skeletons, rebind,
                                             select_triangles)
from unified_armory.porting.umesh import UMesh

from fixtures import BONES, body_model, mp_model, skeleton, to_dump, write_tga

HEAD = [b[0] for b in BONES].index("head")


def world_pos(mesh, node):
    m = mesh.world_matrices()[node]
    return (m[0][3], m[1][3], m[2][3])


def centroid(mesh, verts):
    vs = [mesh.vertices[i].position for i in verts]
    return tuple(sum(c) / len(vs) for c in zip(*vs))


class BoneNameTests(unittest.TestCase):
    def test_styles_agree(self):
        for name in ("frame l upperarm", "b_l_upperarm", "l_upperarm", "UpperArm_L", "Bip01 L UpperArm"):
            self.assertEqual(canonical_bone(name), "l_upperarm", name)
        self.assertEqual(canonical_bone("left_lower_arm"), "l_forearm")
        self.assertEqual(canonical_bone("frame spine1"), "spine1")

    def test_aliases_override(self):
        self.assertEqual(canonical_bone("Bone042", {"bone042": "head"}), "head")

    def test_match_across_naming_styles(self):
        a, b = UMesh(nodes=skeleton("frame", 1)), UMesh(nodes=skeleton("b", 1))
        mapping, notes = match_skeletons(a, b)
        self.assertEqual(mapping, list(range(len(BONES))))
        self.assertEqual(notes, [])

    def test_unmatched_bone_follows_parent(self):
        a = UMesh(nodes=skeleton("plain", 1))
        from unified_armory.porting.umesh import Node
        a.nodes.append(Node("antenna", HEAD))
        mapping, notes = match_skeletons(a, UMesh(nodes=skeleton("plain", 1)))
        self.assertEqual(mapping[-1], HEAD)
        self.assertTrue(notes)


class MathTests(unittest.TestCase):
    def test_invert(self):
        m = math3d.trs((1, 2, 3), math3d.quat_normalize((0.2, 0.1, 0.3, 0.9)), 1.3)
        i = math3d.mul(m, math3d.invert(m))
        for r in range(4):
            for c in range(4):
                self.assertAlmostEqual(i[r][c], 1.0 if r == c else 0.0, places=9)

    def test_quat_roundtrip(self):
        q = math3d.quat_normalize((0.3, -0.2, 0.5, 0.7))
        q2 = math3d.mat3_to_quat(math3d.quat_to_mat3(q))
        self.assertTrue(all(abs(a - b) < 1e-9 for a, b in zip(q, q2)) or all(abs(a + b) < 1e-9 for a, b in zip(q, q2)))


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.mp = mp_model("b", 1.0, "s\\armor.shader", {"helmet": ("head", ["base", "eod"])})

    def test_select_by_permutation(self):
        tris = select_triangles(self.mp, {"region": "helmet", "permutation": "eod"})
        self.assertEqual(len(tris), 12)
        self.assertTrue(all(self.mp.triangles[t].permutation == "eod" for t in tris))

    def test_select_by_bone(self):
        tris = select_triangles(self.mp, {"bones": ["head"], "descendants": True})
        self.assertEqual({self.mp.triangles[t].region for t in tris}, {"body", "helmet"})

    def test_base_permutation_prefers_default_names(self):
        self.assertEqual(base_permutations(self.mp)["helmet"], "base")
        self.assertEqual(base_permutations(self.mp, {"helmet": "eod"})["helmet"], "eod")


class RebindTests(unittest.TestCase):
    def test_piece_lands_on_target_bone_and_scales(self):
        src = mp_model("b", 1.0, "s\\armor.shader", {"helmet": ("head", ["eod"])})
        piece = src.subset(select_triangles(src, {"region": "helmet", "permutation": "eod"}))
        tgt = body_model("frame", 0.8, "t\\cyborg.shader")
        moved, notes = rebind(piece, tgt, max_weights=2)
        c = centroid(moved, range(len(moved.vertices)))
        h = world_pos(tgt, HEAD)
        self.assertTrue(all(abs(a - b) < 1e-6 for a, b in zip(c, h)), (c, h))
        xs = [v.position[0] for v in moved.vertices]
        self.assertAlmostEqual(max(xs) - min(xs), 0.12 * 0.8, places=6)  # box shrank with the smaller skeleton
        self.assertEqual([n.name for n in moved.nodes], [n.name for n in tgt.nodes])
        self.assertTrue(all(len(v.weights) <= 2 for v in moved.vertices))

    def test_rotated_bind_pose(self):
        src = body_model("plain", 1.0, "a\\a.shader")
        tgt = body_model("plain", 1.0, "b\\b.shader")
        q = math3d.quat_normalize((0, 0, math.sin(math.pi / 4), math.cos(math.pi / 4)))  # 90 deg about Z
        tgt.nodes[HEAD].rotation = q
        piece = src.subset(select_triangles(src, {"bones": ["head"]}))
        moved, _ = rebind(piece, tgt, scale=1.0)
        # A vertex offset +x from the source head should end up +y from the target head.
        hv = max(range(len(piece.vertices)), key=lambda i: piece.vertices[i].position[0] + piece.vertices[i].position[1])
        sx, sy, _ = (a - b for a, b in zip(piece.vertices[hv].position, world_pos(src, HEAD)))
        tx, ty, _ = (a - b for a, b in zip(moved.vertices[hv].position, world_pos(tgt, HEAD)))
        self.assertAlmostEqual(tx, -sy, places=6)
        self.assertAlmostEqual(ty, sx, places=6)

    def test_weights_merge_and_renormalize(self):
        src = body_model("plain", 1.0, "a\\a.shader")
        src.vertices[0].weights = [(0, 0.5), (1, 0.3), (2, 0.2)]
        tgt = body_model("plain", 1.0, "b\\b.shader")
        moved, _ = rebind(src.subset([0]), tgt, max_weights=2)
        self.assertAlmostEqual(sum(w for _, w in moved.vertices[0].weights), 1.0)
        self.assertEqual(len(moved.vertices[0].weights), 2)


class FormatTests(unittest.TestCase):
    def roundtrip(self, version):
        mesh = mp_model("frame", 1.0, "a\\armor.shader", {"helmet": ("head", ["base", "eod"])})
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.jms"
            jms.write(mesh, p, version=version)
            back = jms.read(p)
        self.assertEqual([n.name for n in back.nodes], [n.name for n in mesh.nodes])
        self.assertEqual([n.parent for n in back.nodes], [n.parent for n in mesh.nodes])
        self.assertEqual(len(back.triangles), len(mesh.triangles))
        for a, b in zip(back.vertices, mesh.vertices):
            self.assertTrue(all(abs(x - y) < 1e-5 for x, y in zip(a.position, b.position)))
        for a, b in zip(back.world_matrices(), mesh.world_matrices()):
            self.assertAlmostEqual(a[2][3], b[2][3], places=5)
        return back

    def test_jms_modern_keeps_regions_and_permutations(self):
        back = self.roundtrip(8213)
        self.assertEqual({(t.region, t.permutation) for t in back.triangles},
                         {("body", "default"), ("helmet", "base"), ("helmet", "eod")})

    def test_jms_legacy(self):
        back = self.roundtrip(8200)
        self.assertEqual({t.region for t in back.triangles}, {"body", "helmet"})

    def test_jms_legacy_rotations(self):
        mesh = body_model("frame", 1.0, "a\\a.shader")
        mesh.nodes[HEAD].rotation = math3d.quat_normalize((0.1, 0.2, 0.3, 0.9))
        with tempfile.TemporaryDirectory() as d:
            jms.write(mesh, Path(d) / "m.jms", version=8200)
            back = jms.read(Path(d) / "m.jms")
        self.assertTrue(all(abs(a - b) < 1e-5 for a, b in zip(back.nodes[HEAD].rotation, mesh.nodes[HEAD].rotation)))

    def test_dump_to_umesh(self):
        mesh = mp_model("b", 1.0, "s\\armor.shader", {"helmet": ("head", ["base", "eod"])})
        back = tagdump.render_model_to_umesh(to_dump(mesh))
        self.assertEqual(len(back.triangles), len(mesh.triangles))
        self.assertEqual(sorted({(t.region, t.permutation) for t in back.triangles}),
                         sorted({(t.region, t.permutation) for t in mesh.triangles}))
        self.assertEqual(back.materials[0].shader, "s\\armor.shader")

    def test_dump_missing_field_is_explained(self):
        with self.assertRaises(tagdump.DumpError) as ctx:
            tagdump.render_model_to_umesh({"fields": []})
        self.assertIn("dump_fields", str(ctx.exception))

    def test_strip(self):
        self.assertEqual(tagdump.strip_to_list([0, 1, 2, 3, 3, 4]), [(0, 1, 2), (2, 1, 3)])

    def test_shader_roles(self):
        dump = {"fields": [{"n": "parameters", "t": "block", "e": [
            [{"n": "parameter name", "v": "base_map"}, {"n": "bitmap", "t": "ref", "v": "a\\diffuse.bitmap"}],
            [{"n": "parameter name", "v": "bump_map"}, {"n": "bitmap", "t": "ref", "v": "a\\zbump.bitmap"}],
            [{"n": "parameter name", "v": "detail_map"}, {"n": "bitmap", "t": "ref", "v": "a\\detail.bitmap"}]]},
            {"n": "multipurpose map", "t": "ref", "v": "a\\multi.bitmap"}]}
        self.assertEqual(tagdump.shader_bitmaps(dump),
                         {"base": "a\\diffuse.bitmap", "normal": "a\\zbump.bitmap", "change_color": "a\\multi.bitmap"})

    def test_tga_to_tiff(self):
        with tempfile.TemporaryDirectory() as d:
            write_tga(Path(d) / "a.tga", 3, 2, (10, 20, 30))
            w, h, px = read_tga(Path(d) / "a.tga")
            self.assertEqual((w, h, px[:4]), (3, 2, bytes((10, 20, 30, 255))))
            write_tiff(Path(d) / "a.tif", w, h, px)
            data = (Path(d) / "a.tif").read_bytes()
            self.assertEqual(data[:4], b"II*\x00")
            self.assertEqual(data[-4:], bytes((10, 20, 30, 255)))


if __name__ == "__main__":
    unittest.main()
