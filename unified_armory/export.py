"""Write a mod build folder: one directory per game with its plan, scripts and checklist."""
from __future__ import annotations

import json
from pathlib import Path

from .backends import build_all
from .catalog import Catalog

QUALITY_MARK = {"exact": "", "override": " (override)", "nearest": "", "similar": " (closest match)", "default": " (default, no match)"}


def _checklist_md(plan: dict) -> str:
    lines = [f"# {plan['name']}: Unified Armory", "", plan["notes"], ""]
    lines += ["## MCC Customize menu", "", "| Slot | Choice |", "|---|---|"]
    lines += [f"| {i['slot']} | {i['choice']}{QUALITY_MARK.get(i['quality'], '')} |" for i in plan["menu_checklist"]]
    if plan["warnings"]:
        lines += ["", "## Notes", ""] + [f"- {w}" for w in plan["warnings"]]
    if plan["edits"]:
        lines += ["", "## Campaign build", "",
                  f"Needs: {plan['toolkit']}.", "",
                  "1. Back up your Editing Kit's `tags` folder.",
                  "2. Run `apply.bat \"<path to Editing Kit>\"` to write the tag edits in `plan.json` (add `--dry-run` to preview first; set `APPLYPLAN` if ApplyPlan.exe isn't on PATH).",
                  "3. Run `build.bat \"<path to Editing Kit>\"` to compile the campaign maps.",
                  "4. Load the built maps through MCC's mod flow (EAC off). Keep your original maps backed up."]
    return "\n".join(lines) + "\n"


def _apply_bat() -> str:
    return (
        "@echo off\r\n"
        "if \"%~1\"==\"\" (echo usage: apply.bat ^<EditingKitPath^> & exit /b 1)\r\n"
        "set \"AP=%APPLYPLAN%\"\r\n"
        "if not defined AP set \"AP=ApplyPlan.exe\"\r\n"
        "\"%AP%\" \"%~1\" \"%~dp0plan.json\" %2\r\n"
    )


def _build_bat(plan: dict) -> str:
    out = ["@echo off", "if \"%~1\"==\"\" (echo usage: build.bat ^<EditingKitPath^> & exit /b 1)", "pushd \"%~1\""]
    for cmd in plan["build_commands"]:
        out += [cmd, "if errorlevel 1 (popd & exit /b 1)"]
    out.append("popd")
    return "\r\n".join(out) + "\r\n"


def export(catalog: Catalog, profile: dict, out_dir: Path) -> dict[str, dict]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plans = build_all(catalog, profile)
    (out_dir / "profile.json").write_text(json.dumps(profile, indent=2) + "\n")
    index = [f"# Unified Armory build: {profile['name']}", ""]
    for gid, plan in plans.items():
        gdir = out_dir / gid
        gdir.mkdir(exist_ok=True)
        (gdir / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
        (gdir / "CHECKLIST.md").write_text(_checklist_md(plan))
        for stale in ("apply.bat", "build.bat"):
            (gdir / stale).unlink(missing_ok=True)
        if plan["edits"]:
            (gdir / "apply.bat").write_bytes(_apply_bat().encode())
            (gdir / "build.bat").write_bytes(_build_bat(plan).encode())
        status = f"{len(plan['edits'])} campaign tag edits" if plan["edits"] else "menu checklist only"
        index.append(f"- [{plan['name']}]({gid}/CHECKLIST.md): {status}")
    (out_dir / "README.md").write_text("\n".join(index) + "\n")
    return plans
