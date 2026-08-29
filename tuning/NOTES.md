# Crash 2 recomp - bring-up notes

Chronological record of what broke and why. Against an alpha framework the
change log is the debugging tool.

## Toolchain: MSVC is not a supported path - use the bundled clang

Building with MSVC (VS 2022) got all the way to a linked 11.4 MB exe, then
crashed at `0xC0000409` in static init, before `main`, with no output.

Two MSVC-only defects surfaced:

1. **`extern "C"` linkage.** 10 globals defined in C TUs were only redeclared
   block-scope inside functions in `main.cpp`, so MSVC name-mangled the
   references (`?g_psx_dispatch_depth@@3HA`) and they failed to link. Fixed by
   extending the file's existing "Cross-language globals" `extern "C"` block -
   the file's own comment documents this exact fix and notes it is a no-op on
   GCC/Clang. See `patches/0001-msvc-extern-c-globals.patch`.
2. **Static-init crash** (`0xC0000409`), never diagnosed - see below.

psxrecomp ships its own toolchain targeting **`x86_64-w64-windows-gnu`**
(MinGW-w64 + clang 22), fetched via `psxrecomp_cli.py ensure-toolchain`. That is
the ABI the framework is developed against. Rebuilt with it and both problems
vanished - the game booted first try. **Do not build this project with MSVC.**

The MSVC `extern "C"` patch is harmless under clang and is kept for the record.

## Vendoring: the CLI ships an incomplete framework tree

`psxrecomp.exe build` emits a project with a local `psxrecomp/` copy, but that
copy is missing pieces `runtime.cmake` includes. Filled in from the source
clone:

- `cmake/psx_dependency_archive.cmake` - configure failed outright without it
- `third_party/deps.manifest` + the pinned `libchdr` archive (avoids a network
  fetch during the build)
- `lib/retcomm-rbengine` - `PSX_REWIND=ON` is the default and hard-errors
  without it. Vendoring it (179 KB) keeps the Rewind feature rather than
  disabling it.

All are plain vendored copies with no `.git`, so nothing is a submodule.

## Input: a Release build never opens a gamepad

Symptom: keyboard works, controller does nothing. Not an SDL problem - SDL3 is
built with dinput/xinput/hidapi/rawinput/wgi/gameinput, and `padprobe.exe`
(in `_build/`, links the same SDL3) confirms it sees and opens the pad:

    >> GAMEPAD_ADDED which=2
       [0] id=2 name=DualSense Wireless Controller gamepad=YES
            SDL_OpenGamepad -> OK

Root cause is in `main.cpp` (~line 10660):

    #if defined(PSX_DEBUG_TOOLS)
        player_device[i] = (i == 0) ? "auto" : "none";
    #else
        player_device[i] = (i == 0) ? "keyboard" : "none";   // Release
    #endif

Release pins player 1 to `"keyboard"` -> `p.kind == 1` -> `refresh_player_devices()`
calls `close_player()` instead of `open_player()`, so no pad is ever opened. The
code comment explains the intent: *"the launcher assigns the selected physical
device"* - i.e. the built-in ImGui launcher. We build `PSX_RECOMP_UI=OFF` and run
`--no-launcher` because our own launcher replaces it, so nothing ever assigns one.

**Fix:** run with `PSX_DEV_INPUT=1`, which merges the keyboard *and* every
connected controller onto player 1 (`dev_all_controllers_buttons()` opens pads on
its own, bypassing the stuck `open_player` path). The launcher sets this via the
`merge_all_input` setting, on by default.

Dead end worth recording: `input.ini`'s `[controller] device = 0` does nothing.
`controller_device_index` is parsed at main.cpp:3849 and never read anywhere.

## Status

- Boots, hands off to the game, runs the intro and attract-mode demo
- Locked **59.9-60.0 fps**, 2600+ frames, no stutter
- Overlay captures are being written; the TCC overlay tier is inactive
  ("no bundled toolchain at .../overlay_toolchain"), so uncovered overlay code
  falls back to the interpreter. Worth revisiting for performance.
