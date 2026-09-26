#!/bin/sh
# Renderer parity harness: gpu_gl_renderer.c's OpenGL and Direct3D 12
# compilations driven with the same PS1 command stream (parity.c), for
# comparing read-back VRAM and every presented frame (compare.py).
#
#   sh tuning/renderer_parity/build.sh
#   tuning/renderer_parity/parity.exe gl    2 out/gl2
#   tuning/renderer_parity/parity.exe d3d12 2 out/d3d2     (PSX_D3D12_DEBUG=1 for the debug layer)
#   python tuning/renderer_parity/compare.py out/gl2 out/d3d2
#
# PSX_POSTFX=... tests post-processing; PARITY_INTERP=1 frame blending;
# PARITY_BENCH=N times N synthetic game-like frames. Builds against the live
# framework tree, so run it after tuning/patches are applied.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
T=${RETCOMM_TOOLCHAIN:-$HOME/.local/share/retcomm/toolchains/cmake-clang-v1/latest}
R="$ROOT/_build/Crash2Recomp/psxrecomp/runtime"
CC="$T/bin/clang.exe"
CXX="$T/bin/clang++.exe"
FLAGS="-O1 -g -DPSX_SDL3 -DPSX_HAS_D3D12=1 -DPSX_NO_DEBUG_TOOLS=1 -I$R/include -I$R/src -I$T/deps/include"
OBJ="$HERE/obj"
mkdir -p "$OBJ"
cd "$OBJ"
$CC $FLAGS -DSDL_GL_SwapWindow=parity_gl_swap -c "$R/src/gpu_gl_renderer.c" -o gl_renderer.o
$CC $FLAGS -c "$R/src/gpu_gl_renderer_d3d12.c" -o gl_renderer_d3d12.o
$CC $FLAGS -c "$R/src/gpu_gl_postfx.c" -o gl_postfx.o
$CC $FLAGS -c "$R/src/gpu_gl_postfx_d3d12.c" -o gl_postfx_d3d12.o
$CC $FLAGS -c "$R/src/gpu_postfx.c" -o postfx.o
$CC $FLAGS -c "$R/src/gpu_hw_dispatch.c" -o dispatch.o
$CC $FLAGS -c "$R/src/gpu_sw_renderer.c" -o sw_renderer.o
$CC $FLAGS -c "$R/src/frame_interpolation.c" -o frame_interp.o
$CC $FLAGS -c "$R/src/gpu_vram_dirty.c" -o vram_dirty.o
$CXX $FLAGS -std=c++17 -c "$R/src/gpu_gl12.cpp" -o gl12.o
$CC $FLAGS -c "$HERE/parity.c" -o parity.o
$CC $FLAGS -c "$HERE/stubs.c" -o stubs.o
$CXX -o "$HERE/parity.exe" *.o "$T/deps/lib/libSDL3.a" "$T/deps/lib/libz.a" -static \
    -ld3d12 -ldxgi -lopengl32 -lkernel32 -luser32 -lgdi32 -lwinmm -limm32 -lole32 -loleaut32 \
    -lversion -luuid -ladvapi32 -lsetupapi -lshell32 -ldinput8
echo built "$HERE/parity.exe"
