# Unified Armory: advanced guide

Most players only need the [README](../README.md). This covers how the mod works, building from source, the manual Halo CE route, and editing the game data.

Unified Armory is a runtime mod for Halo: The Master Chief Collection. You pick armor pieces from any game in an in-game panel (F8, over the main menu), then play any campaign mission wearing them. The pieces are the real models, ported between engines.

## How it works

Two parts: an **armor pack** built once, and a **runtime** that MCC loads.

```
 Mod Tools ──extract──▶ cache ──port every piece──▶ player model with all pieces ──tool──▶ pack maps ─┐
 (ArmoryTool)                     (Python)           + mission script              (tool.exe)          │
                                                                                                      ▼
 MCC ── loads version.dll ── F8 panel (your picks) ── map opens routed to pack ── bridge writes picks into the mission
```

### The armor pack (`UnifiedArmory.exe` → Install)

1. **Extract.** `ArmoryTool extract` uses ManagedBlam to dump each source render model and its shaders, and exports their textures with the kit's `tool.exe`.
2. **Port** (`unified_armory/porting`). Each piece is cut out of its model by region and permutation (the multiplayer Spartans) or by bone (campaign characters).
   - **Bone names are matched across games:** `frame l upperarm`, `b_l_upperarm` and `UpperArm_L` are all the same bone.
   - **Each vertex is moved from the source pose into the target pose** and re-weighted to the target's bones (up to 4 weights per vertex).
   - **Sizes are adjusted** to each skeleton's proportions.
3. **One model holds everything** (`assemble_pack`). For every slot, the target's player model gets a region `ua_<slot>` whose permutations are `own` (that game's original armor, the default) plus one per ported piece. The rest of the body stays as is.
4. **Mission script** (`pack.py`). Every campaign level gets `unified_armory.hsc`, attached through the scenario's script source files. It re-applies `object_set_permutation` on the player for every slot twice a second, from one global per slot.
5. **Build and install.** The Mod Tools import the model and compile every campaign level. The maps go to `<MCC>\mcc\binaries\win64\UnifiedArmory\maps`, with `manifest.json` (what each game's pack contains). `version.dll` goes beside MCC's executable. MCC's own files are never changed.

### The runtime (`tools/runtime`, `version.dll`)

- **Loading.** MCC loads `version.dll` from its own folder. Every export forwards to the system copy. The runtime stays off unless the process is `MCC-Win64-Shipping.exe` and Easy Anti-Cheat isn't loaded.
- **Map redirect** (`file_hooks.cpp`, `core/redirect.cpp`). `CreateFileW/A`, `CreateFile2` and `GetFileAttributesExW` are hooked. When MCC opens a campaign map that's in the pack, it gets the pack's copy. Everything else is untouched.
- **Panel** (`overlay.cpp`). Dear ImGui drawn on MCC's DirectX 11 swap chain. F8 toggles it in the menus or in a mission. Picks are saved to `UnifiedArmory\selection.json`.
- **Bridge** (`core/bridge.cpp`). This connects your picks to the running mission without any per-game memory addresses:
  - The script declares, in order: `ua_magic` (per game), `ua_echo` (0), one long per slot, `ua_tick`, then `ua_seed`.
  - At mission start the script copies `ua_seed` into `ua_echo`. Only the live globals table then has the magic number directly followed by the seed. The map's own data has the magic number next to 0, and the seed far away.
  - The runtime scans the game's memory for that pair. The distance between them gives the spacing of globals. It then checks that `ua_seed` sits exactly where the declaration order puts it, and waits until `ua_tick` toggles, which proves a running script owns that memory.
  - Only then does it write your picks into the slot globals. It keeps rewriting them, so changes in the F8 panel apply live, and respawns and checkpoints pick them up. Reads and writes go through `ReadProcessMemory/WriteProcessMemory`, so a mission unloading can't crash the game.
  - Scans run only after a pack map opens, every 2 seconds until connected.
- **Log.** `UnifiedArmory\runtime.log` records start-up, map redirects and connection status.

## Running from source

```sh
python -m unified_armory                 # the installer (same as UnifiedArmory.exe)
python -m unified_armory build           # older single-loadout route: bake one loadout into the maps (files only)
python -m unified_armory pieces          # list every piece id
dotnet build tools/ArmoryTool -c Release # ArmoryTool.exe (no Mod Tools needed to compile)
cmake -S tools/runtime -B build -A x64 && cmake --build build --config Release   # version.dll (Windows)
cmake -S tools/runtime -B build && cmake --build build && ctest --test-dir build # runtime core tests (any OS)
tools/runtime/tests/run_win_harness.sh build    # the real version.dll against a stand-in MCC (Windows, or Wine)
```

Python 3.10+, no dependencies. Extracted models and build files live in `%LOCALAPPDATA%\UnifiedArmory`.

What **Install** does (`unified_armory/autopilot.py`):

1. Finds Steam (registry), every library in `libraryfolders.vdf`, MCC, and each game's Mod Tools folder. Folders can be pinned under Settings.
2. Runs `ArmoryTool extract` in each game's Mod Tools for every model not cached yet. Pieces whose source isn't available are left out of the pack, and the report says why.
3. For each campaign: builds the all-pieces player model, copies it and the mission script into the kit's `data`, then runs:
   - `tool bitmaps`
   - `ArmoryTool apply --stage pre` (shaders)
   - the model import
   - `ArmoryTool apply --stage post` (point the player at the new model, attach the mission script)
   - then compiles every campaign level.
4. Copies the built maps into the pack, merges `manifest.json` (rebuilding one game keeps the others), and installs `version.dll`.

### Halo CE (manual)

The Halo CE Mod Tools have no ManagedBlam, so the app skips CE. To do it by hand:
1. Export `characters\cyborg\cyborg.gbxmodel` to JMS with a community CE tool (e.g. Mozzarilla) and save it as
   `%LOCALAPPDATA%\UnifiedArmory\cache\halo1\cyborg\model.jms`, with textures beside it as `<material>_base.tga`.
   This also makes the CE pieces available to the other games.
2. That makes the CE armor pieces available to the other campaigns in the pack. A Halo CE campaign itself isn't in the pack yet.
   The older single-loadout route can still bake one loadout into CE by hand: run `python -m unified_armory build`
   and follow `build\halo1\NOTES.md`, which lists the exact Guerilla edits.

## What's verified and what isn't

**Tested here** (52 Python tests and the runtime suites below, synthetic models for all six games in each game's naming style and proportions):
- **Refitting:** bone matching across naming styles, refitting with rotated and scaled skeletons, and weight limits.
- **Cutting and merging:** selecting pieces by permutation and by bone, swapping slots, and merging materials.
- **Formats:** JMS read/write round trips for both dialects, tag dump parsing (triangle lists and strips), and TGA→TIFF and glTF output.
- **The full build:** one mixed loadout ported into all six campaigns, with each helmet landing exactly on that game's head.
- **Pack builder:** against a simulated Steam/MCC/Mod Tools install: detection across libraries, step order, mission scripts on every level, manifest contents, MCC's own files left alone, graceful skips, uninstall.
- **Mission script protocol:** global order, the echo/seed handshake, balanced syntax for every game's dialect.
- **Runtime core** (C++, `tools/runtime/tests`): the bridge against fake memory, covering decoys, a script that hasn't started, wrong game, garbage values, a nearby seed global faking a wider stride, pairs across chunk boundaries, its own scan buffer, and memory vanishing. Also the selection and manifest model, map redirect rules, and live selection changes.
- **The real `version.dll`**, cross-compiled and run under Wine as a stand-in `MCC-Win64-Shipping.exe`: export forwarding, map redirect, and finding a running mission's globals and writing the selection. CI runs the same harness natively on Windows.

**Not tested: nothing has run against a real Editing Kit or MCC install,** and the F8 panel hasn't been drawn on a real GPU. Expect to fix data, not code. Everything game-specific lives in `unified_armory/data/games/<game>.json`:
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
- **Runtime assumptions to confirm in game:**
  - each engine keeps HaloScript globals in declaration order at a fixed spacing;
  - MCC opens campaign maps through the hooked Windows file functions;
  - MCC renders its menus with DirectX 11.

  `runtime.log` shows which step stops if one of these doesn't hold.
- **Colors** aren't changeable in the panel yet; each campaign keeps its own armor colors.
- **Co-op:** the script dresses player 1 only.
- **Cutscenes** use separate cinematic objects, so they may still show the original armor.

## Layout

```
unified_armory/
  data/games/<game>.json   pieces, source models, target settings, campaign levels
  porting/                 umesh (neutral mesh), retarget, assemble, tagdump, jms, gltf, images, cache
  autopilot.py             detection, pack build/install, uninstall
  pack.py                  mission script generator + manifest
  catalog.py profile.py backends.py export.py server.py __main__.py
  web/                     the editor
tools/ArmoryTool/          ManagedBlam extract/dump/apply (C#, .NET Framework 4.8 x64)
tools/runtime/             version.dll: map redirect, F8 panel, script bridge (C++17; core is portable and tested)
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
