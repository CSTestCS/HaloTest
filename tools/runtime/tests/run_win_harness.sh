#!/usr/bin/env bash
# Run the version.dll integration harness under Wine (or pass WINE= to run natively on Windows via bash).
#   tests/run_win_harness.sh <build dir containing version.dll and win_harness.exe>
set -euo pipefail
BUILD="$1"
WINE="${WINE-wine64}"
ROOT="$(mktemp -d)"
BIN="$ROOT/mcc/binaries/win64"
MOD="$BIN/UnifiedArmory"
mkdir -p "$ROOT/halo3/maps" "$MOD/maps/halo3/maps"
printf 'ORIGINAL' > "$ROOT/halo3/maps/010_jungle.map"
printf 'ORIGINAL' > "$ROOT/halo3/maps/shared.map"
printf 'PACK' > "$MOD/maps/halo3/maps/010_jungle.map"
cp "$BUILD/version.dll" "$BIN/"
cp "$BUILD/win_harness.exe" "$BIN/MCC-Win64-Shipping.exe"
slot() { printf '{"index": 0, "uid": "own", "name": "own", "from": "halo3"}'; }
cat > "$MOD/manifest.json" <<JSON
{"format": "unified-armory-pack/1", "seed": 1095912793,
 "slot_order": ["helmet", "chest", "shoulder_left", "shoulder_right", "wrist", "utility", "knees"],
 "slots": {"helmet": "Helmet", "chest": "Chest", "shoulder_left": "Left Shoulder", "shoulder_right": "Right Shoulder",
           "wrist": "Wrists", "utility": "Utility", "knees": "Knees"},
 "game_names": {"halo3": "Halo 3", "reach": "Halo: Reach"},
 "games": {"halo3": {"name": "Halo 3", "index": 3, "magic": 1430323203, "maps": ["halo3/maps/010_jungle.map"],
   "slots": {"helmet": [$(slot), {"index": 1, "uid": "halo3/helmet_eod", "name": "EOD", "from": "halo3"},
                         {"index": 2, "uid": "reach/helmet_gungnir", "name": "Gungnir", "from": "reach"}],
             "chest": [$(slot), {"index": 1, "uid": "halo3/chest_eod", "name": "EOD", "from": "halo3"}],
             "shoulder_left": [$(slot)], "shoulder_right": [$(slot)], "wrist": [$(slot)], "utility": [$(slot)],
             "knees": [$(slot)]}}}}
JSON
printf '{"armor": {"helmet": "reach/helmet_gungnir", "chest": "halo3/chest_eod"}, "overrides": {}}' > "$MOD/selection.json"
cd "$BIN"
status=0
if [ -n "$WINE" ]; then
  WINEDEBUG=-all WINEDLLOVERRIDES="version=n,b" "$WINE" MCC-Win64-Shipping.exe || status=$?
else
  ./MCC-Win64-Shipping.exe || status=$?
fi
echo "--- runtime.log"
cat "$MOD/runtime.log" || true
rm -rf "$ROOT"
exit $status
