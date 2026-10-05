"""Command line: python -m unified_armory {edit,build,validate,pieces}"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .catalog import SLOTS, load_catalog, validate
from .export import export
from .profile import ProfileError, load_profile, new_profile


def data_home() -> Path:
    """Where the profile, extracted models and builds live (%LOCALAPPDATA%\\UnifiedArmory on Windows)."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "UnifiedArmory"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not any(a in ("edit", "build", "pieces", "validate") for a in argv):
        # Double-click / no command: open the app. "edit" goes after the global options.
        i = 0
        while i < len(argv) and argv[i] in ("--profile", "--cache", "--out"):
            i += 2
        argv = [*argv[:i], "edit", *argv[i:]]
    home = data_home()
    ap = argparse.ArgumentParser(prog="unified_armory",
                                 description="Wear armor from any Halo MCC game in every MCC campaign.")
    ap.add_argument("--profile", type=Path, default=home / "profile.json", help="your loadout (default: %(default)s)")
    ap.add_argument("--cache", type=Path, default=home / "cache", help="extracted source models (default: %(default)s)")
    ap.add_argument("--out", type=Path, default=home / "build", help="build output (default: %(default)s)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("edit", help="open the app in your browser (the default)")
    e.add_argument("--port", type=int, default=8642)
    e.add_argument("--no-browser", action="store_true")
    sub.add_parser("build", help="port your loadout into every enabled campaign (or list what to extract first)")
    sub.add_parser("pieces", help="list every armor piece by slot")
    sub.add_parser("validate", help="check the game data files")
    args = ap.parse_args(argv)

    catalog = load_catalog()
    if args.cmd == "validate":
        problems = validate(catalog)
        for p in problems:
            print("error:", p)
        print("data OK" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    if args.cmd == "pieces":
        for slot, label in SLOTS.items():
            print(f"== {label}")
            for p in catalog.pieces_for_slot(slot):
                print(f"  {p['uid']:<36} {p['name']}")
        return 0

    try:
        profile = load_profile(args.profile, catalog) if args.profile.exists() else new_profile()
    except ProfileError as err:
        print(f"{args.profile}: {err}", file=sys.stderr)
        return 2

    if args.cmd == "edit":
        from .server import serve
        args.profile.parent.mkdir(parents=True, exist_ok=True)
        serve(catalog, args.profile, args.out, args.cache, port=args.port, open_browser=not args.no_browser)
        return 0

    result = export(catalog, profile, args.out, args.cache)
    for gid, plan in result["built"].items():
        s = plan["stats"]
        print(f"ready    {plan['name']:<24} {s['triangles']} tris, {s['imported_materials']} ported materials")
    for gid, model in result["waiting"].items():
        print(f"waiting  {catalog.game(gid)['name']:<24} needs {model} extracted")
    for gid, err in result["errors"].items():
        print(f"error    {catalog.game(gid)['name']:<24} {err}")
    if result["extract"]:
        print("\nExtract these first, then run build again:")
        for line in result["extract"]:
            print(" ", line.replace("**", ""))
    print(f"\nwrote {args.out.resolve()}  (see README.md there)")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
