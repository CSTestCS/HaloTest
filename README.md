# Unified Armory

One customization editor for every game in **Halo: The Master Chief Collection**: Halo CE, Halo 2, Halo 3, ODST, Reach and Halo 4. It also carries your loadout into each game's **campaign** player model.

You pick your armor once. Each game gets its closest match: "CQB" becomes Reach's *CQC*, and a Military Police helmet falls back to *Security* in Halo 3. Colors snap to each game's palette using perceptual (CIELAB) matching. You can override any game individually. Then **Build mod** writes, for each game:

- **A tag-edit plan** that puts your colors and armor on the campaign player.
- **Scripts** that apply the plan through the official Editing Kit and rebuild the campaign maps.
- **A menu checklist** showing what to pick in MCC's own Customize screen for multiplayer.

![editor](docs/editor.png)

## Quick start

```sh
python -m unified_armory edit        # opens the editor at http://127.0.0.1:8642
python -m unified_armory resolve     # print what each game gets
python -m unified_armory build       # write ./armory_build/<game>/...
python -m unified_armory validate    # check the catalog data
```

You only need Python 3.10+, with no dependencies. Your profile is saved to `armory_profile.json`. See `examples/noble_six.json` for an example.

## How each campaign gets your look

| Game | Strategy | What changes in campaign |
|---|---|---|
| Halo CE | `change_colors` | The Chief's armor color (CE has no armor pieces) |
| Halo 2 | `change_colors` | Primary and secondary colors on the Chief and the Arbiter |
| Halo 3 | `variant_swap` | The campaign player becomes the multiplayer Spartan, using a generated model variant with your helmet, shoulders, body and visor, plus your colors |
| ODST | `change_colors` | Squad armor colors (MCC ODST has no armor pieces) |
| Reach | `native` | Reach's campaign already uses your MCC armor, so you only get a checklist. `--bake-reach` writes the loadout into the tags instead |
| Halo 4 | `variant_swap` | Same approach as Halo 3, using the Halo 4 multiplayer Spartan |

Items marked **MP only** in the editor (species, emblem) appear only in the menu checklist.

## Applying a build

Each game folder under `armory_build/` contains `plan.json`, `CHECKLIST.md`, and, when there are campaign edits, `apply.bat` and `build.bat`.

1. Install that game's MCC Editing Kit (Steam → Tools) and **back up its `tags` folder**.
2. Build the applier once:
   `dotnet build tools/ApplyPlan -c Release -p:EKPath="<path to any MCC Editing Kit>"`
3. Run `apply.bat "<Editing Kit path>" --dry-run` to preview, then run it again without `--dry-run`. Set `APPLYPLAN` to the applier's path if it isn't on your `PATH`.
4. Run `build.bat "<Editing Kit path>"` to compile the campaign maps with `tool.exe`.
5. Play the built maps through MCC's mod flow (launch with Easy Anti-Cheat off).

Halo CE's Editing Kit has no ManagedBlam. For CE, apply the single change-color edit in `plan.json` by hand in Guerilla, then run `build.bat`.

## Limits

- **MCC's online profile can't be written by a mod.** Multiplayer customization is set in MCC's menus, which is why every build includes a checklist.
- **Verify tag names in your kit.** Biped, model and globals paths, render-model region names and permutation names are data in `unified_armory/data/catalog/*.json`, not engine constants. Halo 3 paths follow the kit's standard layout. **Halo 4 paths are best guesses.** Plans flag what needs checking, and you can fix it in the catalog JSON without touching code.
- **ApplyPlan finds fields by name** (for example `change colors` or `player representation`) rather than by fixed struct paths, and writes values by reflection. If a kit names a field differently, override it under `campaign.fields` in that game's catalog.
- **ApplyPlan has not been run against a real Editing Kit.** It was written against ManagedBlam's API (`ManagedBlamSystem`, `TagFile`, `TagFieldBlock`). Start with `--dry-run` and a backed-up `tags` folder.
- **Palette hex values are approximations** of the in-game swatches. They're used for matching and preview only.

## Layout

```
unified_armory/
  data/families.json        cross-game armor families + fallback chains
  data/palette_classic.json shared MCC color palette
  data/catalog/<game>.json  slots, options, palette, campaign strategy, tag paths, scenarios
  catalog.py  color.py  profile.py  resolver.py  backends.py  export.py  server.py  __main__.py
  web/                      the editor (vanilla HTML/CSS/JS)
tools/ApplyPlan/            ManagedBlam applier (C#, .NET Framework 4.8 x64)
tests/                      python -m unittest discover -s tests
```

## Adding armor or a game

Add an option to a catalog slot with a `family` from `families.json`. Add new families there, with a `similar` fallback chain. If the render model's permutation name differs from the option `id`, set `"permutation"` on the option. Run `python -m unified_armory validate` and the tests afterwards.
