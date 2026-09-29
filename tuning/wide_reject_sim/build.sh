#!/bin/sh
# Native-wide polygon test model check: crash2_wide_reject.h hooked into the
# real instructions of Crash 2's seven screen-reject sites (sim.c), read from
# the SCUS-94154 executable and run through a small MIPS interpreter.
#
#   sh tuning/wide_reject_sim/build.sh
#   tuning/wide_reject_sim/sim.exe [path/to/SCUS_941.54]
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
