"""Blender (headless) conversion of a ported Halo 4 model from glTF to FBX.

  blender --background --python gltf_to_fbx.py -- <in.glb> <out.fbx>

The H4 Editing Kit then turns the FBX into GR2 with `tool fbx-to-gr2`, which
also needs a JSON sidecar describing the model. The Foundry Blender add-on
writes that pair; if you have Foundry installed, export from the opened scene
with it instead of using this FBX.
"""
import sys

import bpy  # noqa: provided by Blender

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if len(args) != 2:
    raise SystemExit("usage: blender --background --python gltf_to_fbx.py -- <in.glb> <out.fbx>")
src, dst = args
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
bpy.ops.export_scene.fbx(filepath=dst, use_selection=False, add_leaf_bones=False, bake_anim=False,
                         apply_scale_options="FBX_SCALE_ALL", axis_forward="Y", axis_up="Z")
print(f"wrote {dst}")
