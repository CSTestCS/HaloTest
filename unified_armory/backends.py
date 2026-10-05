"""Turn an assembled model into a per-game build: model + texture files for the
Editing Kit's data folder, and a plan of tag edits for ArmoryTool.

Plan stages:
  pre   before import: shaders for every material (copied from the target's own,
        or cloned from its armor shader template and pointed at the ported textures)
  post  after import: point the campaign player's model at the new render model and
        pin the player's change colors to your exact colors
"""
from __future__ import annotations

from pathlib import Path

from .catalog import SLOTS
from .color import hex_to_unit_rgb
from .porting import gltf, jms
from .porting.assemble import Assembly
from .porting.images import convert_to_tiff

PLAN_FORMAT = "unified-armory-plan/2"

DEFAULT_FIELDS = {
    "change_colors": "change colors",
    "change_color_permutations": "permutations",
    "permutation_weight": "weight",
    "color_lower_bound": "color lower bound",
    "color_upper_bound": "color upper bound",
    "shader_parameters": "parameters",
    "shader_parameter_name": "parameter name",
    "shader_parameter_bitmap": "bitmap",
}


def ospath(tag_path: str) -> Path:
    """Tag/data paths are backslash-separated; make them real paths on any OS."""
    return Path(*tag_path.replace("\\", "/").split("/"))


def _ext(tag: str) -> str:
    return tag.rsplit(".", 1)[-1]


def _stem(tag: str) -> str:
    return tag.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]


def write_target(catalog, profile: dict, target_id: str, asm: Assembly, out_dir: Path) -> dict:
    game = catalog.game(target_id)
    t = game["target"]
    gdir = Path(out_dir) / target_id
    data = gdir / "data"
    warnings = list(asm.notes)

    # Every material gets a uniquely named shader next to the new model, so tool resolves them locally.
    pre, used_names = [], set()
    for m in asm.mesh.materials:
        name = m.name
        while name.lower() in used_names:
            name += "_"
        used_names.add(name.lower())
        m.name = name
    for m in asm.base:
        if m.shader:
            pre.append({"op": "copy_tag", "from": m.shader, "to": f"{t['shader_dir']}\\{m.name}.{_ext(m.shader)}"})
        else:
            warnings.append(f"material {m.name!r} has no source shader; tool will use a default")
    for m in asm.imported:
        bitmaps = {}
        for role, src in sorted(m.textures.items()):
            param = t["shader_bitmap_params"].get(role)
            if not param:
                continue
            stem = f"{m.name}_{role}{t.get('role_suffix', {}).get(role, '')}"
            try:
                convert_to_tiff(Path(src), data / ospath(t["bitmap_dir"]) / f"{stem}.tif")
            except (OSError, ValueError) as e:
                warnings.append(f"{m.name} {role} texture skipped: {e}")
                continue
            bitmaps[param] = f"{t['bitmap_dir']}\\{stem}.bitmap"
        if not bitmaps:
            warnings.append(f"{m.name}: no textures were extracted; it will show the template shader's textures")
        pre.append({"op": "clone_shader", "template": t["shader_template"],
                    "to": f"{t['shader_dir']}\\{m.name}.{t['shader_ext']}", "bitmaps": bitmaps})

    model_path = data / ospath(t["data_dir"]) / ospath(t["model_file"])
    if t["format"] == "jms":
        jms.write(asm.mesh, model_path, version=t["jms_version"], max_weights=t["max_weights"])
    else:
        gltf.write_glb(asm.mesh, model_path)
    gltf.write_glb(asm.mesh, gdir / "preview.glb")

    fmt = {"render_model_tag": t["render_model_tag"], "data_dir": t["data_dir"], "bitmap_dir": t["bitmap_dir"],
           "data_root": "%EK%\\data", "tools": "%~dp0..\\tools"}
    post = [{**h, "value": h["value"].format(**fmt)} for h in t["hookup"]]
    for channel, index in t["change_colors"]["channels"].items():
        for biped in t["change_colors"]["bipeds"]:
            post.append({"op": "set_change_color", "tag": biped, "index": index, "channel": channel,
                         "color": hex_to_unit_rgb(profile["colors"][channel])})
    if game.get("_verify"):
        warnings.append(game["_verify"])

    return {
        "format": PLAN_FORMAT,
        "game": target_id,
        "name": game["name"],
        "toolkit": game["toolkit"],
        "managedblam": game.get("managedblam", False),
        "fields": {**DEFAULT_FIELDS, **t.get("fields", {})},
        "stages": {"pre": pre, "post": post},
        "import_commands": [c.format(**fmt) for c in t["import_commands"]],
        "import_templates": list(t["import_commands"]),
        "maps": [s.replace("\\", "/").rsplit("/", 1)[-1] + ".map" for s in t["scenarios"]],
        "build_commands": [t["build_command"].format(scenario=s) for s in t["scenarios"]],
        "pieces": {SLOTS[s]: catalog.piece(uid)["name"] + f" ({catalog.game(catalog.piece(uid)['game'])['name']})"
                   for s, uid in asm.pieces.items()},
        "stats": {"vertices": len(asm.mesh.vertices), "triangles": len(asm.mesh.triangles),
                  "materials": len(asm.mesh.materials), "imported_materials": len(asm.imported)},
        "warnings": warnings,
    }


def _describe(op: dict) -> str:
    if op["op"] == "copy_tag":
        return f"Copy tag `{op['from']}` to `{op['to']}`."
    if op["op"] == "clone_shader":
        maps = ", ".join(f"{k} = `{v}`" for k, v in op["bitmaps"].items()) or "no maps"
        return f"Duplicate `{op['template']}` as `{op['to']}` and set {maps}."
    if op["op"] == "set_reference":
        return f"In `{op['tag']}`, set **{op['field']}** to `{op['value']}`."
    if op["op"] == "set_change_color":
        rgb = ", ".join(f"{c:.3f}" for c in op["color"])
        return f"In `{op['tag']}`, set change color {op['index']} ({op['channel']}) lower and upper bounds to ({rgb})."
    return str(op)


def install_bat(plan: dict) -> str:
    L = ["@echo off", "setlocal",
         "if \"%~1\"==\"\" (echo usage: install.bat ^<EditingKitPath^> & exit /b 1)",
         "set \"EK=%~f1\"",
         "set \"AT=%ARMORYTOOL%\"", "if not defined AT set \"AT=ArmoryTool.exe\"",
         "echo Copying ported model and textures into %EK%\\data",
         "xcopy /E /I /Y /Q \"%~dp0data\" \"%EK%\\data\" >nul || exit /b 1",
         "pushd \"%EK%\""]
    check = "if errorlevel 1 (popd & exit /b 1)"
    manual_pre = [c for c in plan["import_commands"] if not c.startswith("tool.exe bitmaps")]
    bitmaps = [c for c in plan["import_commands"] if c.startswith("tool.exe bitmaps")]
    for c in bitmaps:
        L += [c, check]
    if plan["managedblam"]:
        L += ["\"%AT%\" apply \"%EK%\" \"%~dp0plan.json\" --stage pre", check]
    elif plan["stages"]["pre"]:
        L += ["echo.", "echo Halo CE: make the shaders listed under 'Manual steps' in NOTES.md with Guerilla, then", "pause"]
    for c in manual_pre:
        L += [c, check]
    if plan["managedblam"]:
        L += ["\"%AT%\" apply \"%EK%\" \"%~dp0plan.json\" --stage post", check]
    else:
        L += ["echo.", "echo Halo CE: finish the biped and change-color edits under 'Manual steps' in NOTES.md.", "pause"]
    L += ["popd", "echo Done. Now run build.bat to compile the campaign levels."]
    return "\r\n".join(L) + "\r\n"


def build_bat(plan: dict) -> str:
    L = ["@echo off", "if \"%~1\"==\"\" (echo usage: build.bat ^<EditingKitPath^> & exit /b 1)", "pushd \"%~1\""]
    for c in plan["build_commands"]:
        L += [c, "if errorlevel 1 (popd & exit /b 1)"]
    L += ["popd", "echo Built maps are in the kit's maps folder."]
    return "\r\n".join(L) + "\r\n"


def notes_md(plan: dict) -> str:
    L = [f"# {plan['name']}: Unified Armory", "", f"Needs: {plan['toolkit']}.", ""]
    L += ["## Wearing", ""] + ([f"- **{slot}:** {name}" for slot, name in plan["pieces"].items()] or ["- (default armor, colors only)"])
    s = plan["stats"]
    L += ["", f"Model: {s['triangles']} triangles, {s['materials']} materials ({s['imported_materials']} ported). "
          "Open `preview.glb` in Blender or any glTF viewer to check the fit before importing.", ""]
    L += ["## Install", "",
          "1. Back up the kit's `tags` folder.",
          "2. `install.bat \"<kit path>\"`: copies the model and textures into the kit, makes the shaders, imports the model, "
          "and points the campaign player at it. Preview the tag edits first with `ArmoryTool apply \"<kit path>\" plan.json --stage pre --dry-run`.",
          "3. `build.bat \"<kit path>\"`: compiles the campaign levels.",
          "4. Back up MCC's original campaign `.map` files, copy the built ones over them and launch MCC with mods (EAC off).", ""]
    if not plan["managedblam"]:
        L += ["## Manual steps (no ManagedBlam in this kit)", "", "Before the model import:", ""]
        L += [f"- {_describe(op)}" for op in plan["stages"]["pre"]] or ["- (none)"]
        L += ["", "After the model import:", ""] + [f"- {_describe(op)}" for op in plan["stages"]["post"]] + [""]
    if plan["warnings"]:
        L += ["## Check", ""] + [f"- {w}" for w in plan["warnings"]] + [""]
    return "\n".join(L)
