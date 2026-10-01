#!/bin/sh
# Widescreen scenery range model check: crash2_wide_slst.h against every SLST
# entry on the disc (export.py) and a model of the camera update and renderer
# calls (sim.c), with the real SCUS-94154 executable as guest RAM.
#
#   sh tuning/wide_slst_sim/build.sh
#   python tuning/wide_slst_sim/export.py slst.bin      (game-derived: keep it out of git)
#   tuning/wide_slst_sim/sim.exe slst.bin path/to/SCUS_941.54
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
