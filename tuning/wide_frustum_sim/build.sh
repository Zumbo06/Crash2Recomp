#!/bin/sh
# Widescreen camera test model check: crash2_wide_slst.h's camera test driven
# with real level data laid out as the game holds it (sim.c), against the
# reference in frustum_ref.py (export.py writes the cases).
#
#   sh tuning/wide_frustum_sim/build.sh
#   python tuning/wide_frustum_sim/export.py cases.bin   (game-derived: keep it out of git)
#   tuning/wide_frustum_sim/sim.exe cases.bin path/to/SCUS_941.54
#
# Builds against the live framework tree, so run it after tuning/patches are
# applied.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
T=${RETCOMM_TOOLCHAIN:-$HOME/.local/share/retcomm/toolchains/cmake-clang-v1/latest}
R="$ROOT/_build/Crash2Recomp/psxrecomp/runtime"
"$T/bin/clang.exe" -O1 -g -Wall -Wno-unused-function -I"$R/src" \
    "$HERE/sim.c" -o "$HERE/sim.exe"
echo built "$HERE/sim.exe"
