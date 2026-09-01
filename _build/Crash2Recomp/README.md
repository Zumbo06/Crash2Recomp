# Crash Bandicoot 2

Generated locally by PSXRecomp from your own disc and BIOS.

## Build

Install CMake, Ninja, and a C/C++ compiler. SDL3 is fetched automatically.
Then run `sh build.sh` on macOS/Linux or `.\build.ps1` in PowerShell.

This project must keep the generated `psxrecomp/` framework folder beside
`CMakeLists.txt`. If it is missing, restore it from source control with
`git submodule update --init --recursive` or regenerate the project from
the full PSXRecomp CLI zip.

SDL3 is the default. To use SDL2 explicitly, configure once with
`cmake -S . -B build -DPSX_SDL_BACKEND=SDL2`; later build-script runs
preserve that cached selection.

The executable is written under `build/`. Keep your original disc image
at the path stored in `game.toml`, or update that path before running.

The `input/`, `generated/`, and `bios-generated/` folders contain data
derived from copyrighted files you supplied. Do not redistribute them.
