#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -f "$ROOT/psxrecomp/runtime/runtime.cmake" ]; then
  echo "error: PSXRecomp runtime is missing: $ROOT/psxrecomp/runtime/runtime.cmake" >&2
  echo "This generated project needs the psxrecomp framework tree at $ROOT/psxrecomp." >&2
  echo "If this project came from git, run: git submodule update --init --recursive" >&2
  echo "If this project came from psxrecomp.exe, regenerate it from the full CLI zip and keep the generated psxrecomp folder." >&2
  exit 1
fi
cmake -S "$ROOT" -B "$ROOT/build" -G Ninja -DCMAKE_BUILD_TYPE=Release -DPSX_RECOMP_UI=OFF
cmake --build "$ROOT/build" --config Release --parallel
