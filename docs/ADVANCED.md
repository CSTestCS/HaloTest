# Unified Armory: advanced guide

Most players only need the [README](../README.md). This covers how it works, the command line, the manual Halo CE route, and editing the game data.

Wear armor from **any** Halo: The Master Chief Collection game in **every** MCC campaign. For example: a Reach Gungnir helmet, a Halo 3 EOD chest, a Halo CE Mark V shoulder and a Halo 4 Warrior shoulder, all on the Chief in Halo 2.

These are the real models, not lookalikes. Unified Armory pulls each piece's 3D model and textures out of its own game's Editing Kit. It refits the piece to the target game's skeleton and combines it with that game's player body. Then it imports the result through the target game's Editing Kit and points the campaign player at it.

## How it works

```
 source game kit ──extract──▶ cache ──refit + merge──▶ target data folder ──install──▶ target kit ──build──▶ campaign .map
 (ArmoryTool)                (JSON/JMS/TGA)  (Python)    (JMS/glTF + TIFF)  (tool.exe + ArmoryTool)       (tool.exe)
```

1. **Extract.** `ArmoryTool extract` uses ManagedBlam to dump the source render model and the shaders it uses. It exports their textures with the kit's `tool.exe`. Halo CE's kit has no ManagedBlam, so for CE you export the Chief to JMS once with a community tool.
2. **Refit** (`unified_armory/porting`). Each piece is cut out of its model by region and permutation (the multiplayer Spartans), or by bone (the campaign characters).
   - **Bone names are matched across games:** `frame l upperarm`, `b_l_upperarm` and `UpperArm_L` are all the same bone.
   - **Each vertex is moved from the source pose into the target pose** and re-weighted to the target's bones. Weights are limited to 2 per vertex for CE and 4 for later games.
   - **Sizes are adjusted** to each skeleton's proportions.
   - **The target's own armor in that slot is removed** and the new piece takes its place.
3. **Write.** You get one model per game: JMS for CE (8200), H2 (8210), H3, ODST and Reach (8213), or glTF to FBX for Halo 4. Textures are converted to TIFF. A `plan.json` lists the tag edits:
   - **Before import:** shaders for every material. The game's own shaders are reused. Imported pieces get a copy of the target's armor shader pointed at the ported textures, so armor change-colors keep working.
   - **After import:** the campaign player's `.model` points at the new render model, and your **exact** primary and secondary colors are pinned on the player's change colors.
4. **Install and build.** `install.bat` runs the import and the tag edits. `build.bat` compiles that game's campaign levels.

## Running from source

```sh
python -m unified_armory                 # the app (same as UnifiedArmory.exe)
python -m unified_armory build           # write files only: extract jobs, ported models, plans, .bat scripts
python -m unified_armory pieces          # list every piece id
dotnet build tools/ArmoryTool -c Release # ArmoryTool.exe (no Mod Tools needed to compile)
```

Python 3.10+, no dependencies. Your profile, extracted models and builds live in `%LOCALAPPDATA%\UnifiedArmory`
(override with `--profile`, `--cache`, `--out`). `examples/mixed_loadout.json` shows the profile format.

What **Apply to my games** does (`unified_armory/autopilot.py`):

1. Finds Steam (registry), every library in `libraryfolders.vdf`, MCC, and each game's Mod Tools folder.
   Folders can be pinned under Settings.
2. Runs `ArmoryTool extract` in each source game's Mod Tools for any model not cached yet.
3. Ports the loadout. Pieces whose source isn't available are left off, and the report says why.
4. For each game: copies the ported files into the kit's `data`, runs `tool bitmaps`, `ArmoryTool apply --stage pre`,
   the model import and `ArmoryTool apply --stage post`, then compiles every campaign level.
5. Backs up each original map once to `<MCC>\UnifiedArmoryBackup\<game>` (a modded copy never replaces a backup),
   then copies the built maps in. **Restore original maps** puts the backups back.

`python -m unified_armory build` produces the same steps as `install.bat` / `build.bat` per game, for doing it by hand.

### Halo CE (manual)

The Halo CE Mod Tools have no ManagedBlam, so the app skips CE. To do it by hand:
1. Export `characters\cyborg\cyborg.gbxmodel` to JMS with a community CE tool (e.g. Mozzarilla) and save it as
   `%LOCALAPPDATA%\UnifiedArmory\cache\halo1\cyborg\model.jms`, with textures beside it as `<material>_base.tga`.
   This also makes the CE pieces available to the other games.
2. Run `python -m unified_armory build`, then follow `build\halo1\NOTES.md`. It lists the exact Guerilla edits.

## What's verified and what isn't

**Tested here** (49 tests, synthetic models for all six games in each game's naming style and proportions):
- **Refitting:** bone matching across naming styles, refitting with rotated and scaled skeletons, and weight limits.
- **Cutting and merging:** selecting pieces by permutation and by bone, swapping slots, and merging materials.
- **Formats:** JMS read/write round trips for both dialects, tag dump parsing (triangle lists and strips), and TGA→TIFF and glTF output.
- **The full build:** one mixed loadout ported into all six campaigns, with each helmet landing exactly on that game's head.
- **One-click apply:** against a simulated Steam/MCC/Mod Tools install: detection across libraries, step order, map install, backups that are never overwritten, restore, and graceful skips.

**Not tested: nothing has run against a real Editing Kit or MCC install.** Expect to fix data, not code. Everything game-specific lives in `unified_armory/data/games/<game>.json`:
- **Tag paths and names:** model, biped and shader template paths, region and permutation names, and shader parameter names.
- **Kit field names:** if extraction or import reports a missing field, add the kit's name under `dump_fields` in that game's data file.
- **Kit commands:** the bitmap export and model import commands.

ArmoryTool is written against ManagedBlam (`TagFile`, `TagFieldBlock`, `TagFieldReference`). It deliberately finds fields by name and sets values by reflection so one build works across kits. Run `ArmoryTool dump <kit> <tag> out.json` to see a tag's real field names.

**Known hard spots:**
- **Halo CE:** no ManagedBlam in its kit. You export the Chief to JMS once with a community tool, and make its shader and biped edits by hand. NOTES.md lists them exactly.
- **Halo 2:** render models store geometry in sections, which may need extra `dump_fields` names. Exporting the model to JMS by hand also works: a `model.jms` in the cache folder replaces the tag dump for any game.
- **Reach:** the campaign normally applies your MCC profile's armor. The ported model has one permutation per region, so Reach falls back to it.
- **Halo 4:** its kit imports GR2 through `tool fbx-to-gr2`, which needs a JSON sidecar this project doesn't generate. The Foundry Blender add-on can produce it from the `.glb`.
- **Fit and seams:** pieces sized for one body can clip on another. Per-piece `scale` and `offset` in the data files are there for tuning.
- **Textures:** normal-map conventions differ between engines, and CE uses height-based bump maps, so CE gets diffuse and change-color maps only.
- **Multiplayer:** MCC's multiplayer customization comes from your online profile and its own menus, and ported pieces don't appear there. This mod targets the campaigns.

## Layout

```
unified_armory/
  data/games/<game>.json   pieces, source models, target settings, campaign levels
  porting/                 umesh (neutral mesh), retarget, assemble, tagdump, jms, gltf, images, cache
  autopilot.py             detection + one-click apply/restore
  catalog.py profile.py backends.py export.py server.py __main__.py
  web/                     the editor
tools/ArmoryTool/          ManagedBlam extract/dump/apply (C#, .NET Framework 4.8 x64)
packaging/                 launcher + README for the release zip (built by .github/workflows/build.yml)
tools/blender/             headless glTF→FBX for the Halo 4 route
tests/                     PYTHONPATH=. python -m unittest discover -s tests
```

## Adding a piece

Add an entry to a game's `pieces` list:

```json
{"id": "helmet_x", "slot": "helmet", "name": "X", "model": "spartans", "select": {"region": "helmet", "permutation": "x"}}
```

Or select by bone: `"select": {"bones": ["head"], "descendants": true}`. Then run `python -m unified_armory validate`.
