#!/bin/sh
# The widescreen view sweep's rasteriser (raster.c -> raster.dll), which
# sweep.py loads with ctypes.
#
#   sh tuning/wide_view_sweep/build.sh
#   python tuning/wide_view_sweep/sweep.py --variants hook,0054 --png 10
#
# The sweep reads the level files from _build/nsf_cache; its JSON and pictures
# go to out/ (game data, gitignored).
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
T=${RETCOMM_TOOLCHAIN:-$HOME/.local/share/retcomm/toolchains/cmake-clang-v1/latest}
"$T/bin/clang.exe" -O2 -shared -Wall "$HERE/raster.c" -o "$HERE/raster.dll"
echo built "$HERE/raster.dll"
