#!/bin/sh
# Native 120 FPS model check: crash2_60fps.h driven by a model of the machine
# (sim.c), with the real SCUS-94154 executable as guest RAM.
#
#   sh tuning/native120_sim/build.sh
#   tuning/native120_sim/sim.exe [path/to/SCUS_941.54]
#
# Builds against the live framework tree, so run it after tuning/patches are
# applied.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
T=${RETCOMM_TOOLCHAIN:-$HOME/.local/share/retcomm/toolchains/cmake-clang-v1/latest}
R="$ROOT/_build/Crash2Recomp/psxrecomp/runtime"
"$T/bin/clang.exe" -O1 -g -Wall -Wno-unused-function -I"$R/include" -I"$R/src" \
    "$HERE/sim.c" -o "$HERE/sim.exe"
echo built "$HERE/sim.exe"
