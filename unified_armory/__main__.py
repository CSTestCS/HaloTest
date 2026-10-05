"""Command line: python -m unified_armory {edit,resolve,build,validate}"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .catalog import load_catalog, validate
from .export import export
from .profile import ProfileError, load_profile, new_profile
from .resolver import resolve_all


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="unified_armory", description="One armor editor for every Halo MCC game, campaign included.")
    ap.add_argument("--profile", type=Path, default=Path("armory_profile.json"), help="unified profile (default: %(default)s)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("edit", help="open the web editor")
    e.add_argument("--port", type=int, default=8642)
    e.add_argument("--out", type=Path, default=Path("armory_build"))
    sub.add_parser("resolve", help="print what each game gets")
    b = sub.add_parser("build", help="write per-game campaign plans, scripts and checklists")
    b.add_argument("--out", type=Path, default=Path("armory_build"))
    b.add_argument("--bake-reach", action="store_true", help="write Reach armor into tags instead of relying on the MCC profile")
    sub.add_parser("validate", help="check catalog data")
    args = ap.parse_args(argv)

    catalog = load_catalog()
    if args.cmd == "validate":
        problems = validate(catalog)
        for p in problems:
            print("error:", p)
        print("catalog OK" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0

    try:
        profile = load_profile(args.profile, catalog) if args.profile.exists() else new_profile()
    except ProfileError as err:
        print(f"{args.profile}: {err}", file=sys.stderr)
        return 2

    if args.cmd == "edit":
        from .server import serve
        serve(catalog, args.profile, args.out, port=args.port)
    elif args.cmd == "resolve":
        for res in resolve_all(catalog, profile).values():
            print(f"== {res['name']}")
            for ch, c in res["colors"].items():
                print(f"  {ch:<18} {c['swatch']['name']} [{c['source']}]")
            for s in res["slots"].values():
                print(f"  {s['label']:<18} {s['option']['name']} [{s['quality']}]")
    elif args.cmd == "build":
        if args.bake_reach:
            profile["campaign"]["bake_reach"] = True
        plans = export(catalog, profile, args.out)
        for p in plans.values():
            print(f"{p['name']:<26} {len(p['edits']):>2} tag edits, {len(p['build_commands']):>2} maps")
        print(f"wrote {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
