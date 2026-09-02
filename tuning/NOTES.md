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

## Enhancements round (2026-08-29)

### Overlay native compilation — FIXED, real speedup

Symptom: `tcc tier active but no bundled toolchain ... (overlay gaps ->
interpreter)`. Crash 2 streams level code as overlays, so uncovered code ran on
the MIPS interpreter.

Two things were needed, and the second is the non-obvious one:

1. A C compiler on PATH. `autocompile_toolchain_available()` (autocompile.c)
   just scans PATH for gcc/cc/clang; psxrecomp's own clang pack supplies it.
2. **`[runtime] overlay_autocompile_cmd`.** The gate is
   `deferred_has_overlay_ac && autocompile_toolchain_available()` — a compiler
   alone is NOT enough. Without the command the runtime picks its bundled-TCC
   tier, looks for an `overlay_toolchain/` directory we do not ship, and gives
   up to the interpreter. The launcher now writes this key and also sets
   `PSX_OVERLAY_AUTOCOMPILE_CMD`.

Then every overlay compile failed with a **clang-only** error:
`redeclaration of 'overlay_flush_cycles' cannot add 'dllexport' attribute`.
The DLL defines it with dllexport; `cpu_state.h` and `psx_cycles.h` declared it
bare under `PSX_OVERLAY_DLL_BUILD`. GCC only warns, clang errors. Fixed by
giving both declarations the same export attribute used by `overlay_init` in
`overlay_api.h` — see `patches/0002`. Result: **18 failures -> 0**, 2 DLLs
built, tier now reports `overlay autocompile enabled (gcc)`.

### Internal resolution — raised, but 8x is NOT usable

Cap raised 4->8 in three places (`patches/0003`). Measured on Crash 2:

| scale | avg fps | min fps | verdict |
|-------|---------|---------|---------|
| 5x    | 59.9    | 59.6    | clean |
| 6x    | 60.0    | 52.9    | occasional dips |
| 8x    | —       | —       | allocates, then produces NO frames |

Launcher caps at 6, recommends 5. The ceiling is the measurement, not the build.

### Widescreen — clamp removed, but does not engage yet

`ws_offered` / `ws_ultrawide_offered` were `constexpr false`, clamping any
aspect back to 4:3 (`patches/0004`). Flipping them removes the clamp — the
runtime now logs `widescreen 16:9 (native-wide, present 1:1; engages at game
entry)` — but the image stays 4:3 in practice. Unresolved.

Next step: rebuild with `-DPSX_DEBUG_TOOLS=ON`. The stock build sets
`PSX_NO_DEBUG_TOOLS=1`, which strips the TCP debug server, so `ws_aspect`,
`ws_census` and `gpu_state`'s `ws.{configured,active,game_mode}` — the exact
tools for this — are unavailable. (That build flag also flips player 1's
default device to "auto", which is the same gate behind the old controller bug.)

### Gotcha: sed -i destroys CRLF

`sed -i` on these sources rewrote CRLF -> LF file-wide, turning a 2-line change
into a 13,513-line diff. Harmless to the build, but it makes generated patches
worthless. `patches/0003` and `0004` were therefore hand-written. Prefer the
Edit tool over `sed -i` on the vendored tree.

### Widescreen — SOLVED: it was mode selection, not the aspect setting

Symptom: `[video] aspect_ratio = "16:9"` was accepted, the clamp was gone, the
startup line said `widescreen 16:9`, and yet nothing widened — in menus *or* 3D
gameplay.

`gpu_state` (needs a `-DPSX_DEBUG_TOOLS=ON` build) gave the answer:

    configured = 0   active = 0   squash = [1,1]   mode = 2   nw_extra = 0

There are **two** widescreen implementations, chosen in `refresh_widescreen_projection()`:

    const int mode = wide ? (native_wide ? 2 : 1) : 0;

* **mode 2, "native-wide"** — renders extra columns instead of squashing.
  Higher quality, but needs per-game viewport data. Crash 2 has none, so
  `nw_extra` stays 0 and nothing widens. **This is the framework default**
  (`ws_native_wide = true`, config_loader.cpp:1387).
* **mode 1, GTE X-squash + stretched present** — the classic DuckStation/Beetle
  widescreen hack. Squash the projection horizontally, present stretched, net
  result is genuinely wider FOV. Works on any title with no per-game data.

Fix is config-only, no patch: **`[widescreen] native_wide = false`** in
game.toml. The startup line then reads `GTE X-squash + stretched present` and
the image fills the canvas edge to edge.

Dead ends ruled out along the way (all wrong):
- `fntrace_is_game_started()` never firing — it fires, `game_started: 1`
- the `ws_offered` clamp — real, and patch 0004 removes it, but it was not the
  reason nothing widened
- `ws_aspect 16 9` over TCP — returns ok but changes nothing in mode 2, because
  native-wide bypasses the GTE squash entirely

Exposed as a launcher setting (`widescreen_native_wide`, default False).

**Gotcha: duplicate dict key.** `apply_config_settings` briefly had two
`"widescreen"` keys in the same dict literal; Python silently keeps the last,
so a hardcoded `native_wide: True` shadowed the setting and game.toml kept
reverting. Merged into one entry.

### Output resolution decoupled from aspect (patch 0005)

`window_height` did not exist — the loader knew only `window_title` and
`window_width`, and height was always `width * den / num`, tying the canvas to
the content aspect. Added `[video] window_height` (game.toml 360..4320,
settings.toml 360..2160) plus `g_video_win_h`, honoured only when a width is
also set. Verified: a 1920x1080 canvas with 4:3 content pillarboxes correctly
instead of forcing a 1920x1440 window.

Note `settings.toml` caps `window_width` at 3840 (game.toml allows 7680).

### Presentation fit modes (patch 0006)

`letterbox_rect_aspect()` in gpu_gl_renderer.c always shrank the image to fit,
so a 4:3 game on a 16:9 canvas always pillarboxed with no way to opt out. Added
a presentation-fit mode read from `PSX_SCALING_MODE`:

| mode | behaviour |
|------|-----------|
| `letterbox` | preserve aspect, bars on the short axis (default, unchanged) |
| `stretch`   | fill the canvas exactly, ignoring aspect (distorts) |
| `fill`      | preserve aspect, scale until covered, crop the overflow |

Env-driven rather than a config key, so it needs no config_loader changes and
A/Bs without a rebuild. Applied at the final blit only - nothing upstream of the
present path is touched, which keeps it clear of the black-frame flicker class
of bug documented in ENHANCEMENTS.md R1.

### Framerate: what "59.9 fps" actually measures

`[FPS] game: 59.9 fps (1.00x)` comes from `s_frame_count`, incremented in the
**present/vblank** path, with `speed = fps / 59.94`. So it reports the vblank
rate and that the guest runs at exactly 1.00x real PS1 speed. It does NOT say
how often the game updates its animation - a title that renders new content
every other vblank still shows ~60 here.

## Audio dropouts — ROOT CAUSE FOUND (launcher, not the SPU)

Symptom: sound effects worked "but not always" - crate smash, HP up and fruit
pickups dropping, worst in dense levels (Un-Bearable, Crash Crush).

**Cause: the launcher was forcing `PSXRECOMP_AUDIO_LEGACY=1`.** That flag
disables the audio bridge at device-open time - no DRC callback, no rate
control, no fill target - leaving blind `SDL_QueueAudio` pushing. When the
queue ran dry the device silence-filled, i.e. an audible gap.

Measured before and after, same 20s window, via `audio_stats`:

| | legacy-push | bridge-pull |
|---|---|---|
| underruns | **146** | **0** |
| target_ms | 0.0 | 180.0 |
| fill_ms | 20.0 | 178.1 |
| overflow drops | 0 | 0 |

`audio_legacy` is a DIAGNOSTIC - the runtime's own comment says it exists so
"the underrun baseline can be measured against the bridge". It had been exposed
in the launcher next to Volume, indistinguishable from a quality option.

### Wrong turns, for the record

Three SPU theories were pursued and all died against captures:

1. *Silent key-ons* - 0 key-ons had both volumes zero.
2. *Samples ending instantly* - 0 KEYON->END_STOP within a frame.
3. *Release envelopes stuck* - `watch` proved decay runs; voices holding a high
   envelope had all been given `Rr=31`, the slowest rate, by the game itself.

Also wrongly claimed at one point: that `v->active` is never cleared on
END_STOP. It is - `spu.c:890`.

The SPU was never broken. The tell was "not consistent": a deterministic bug
cannot produce intermittent dropouts. That should have redirected the search to
the output pipeline several rounds earlier than it did.

### Still open

Whether voice STEALING is a separate, real problem. Captures showed
KEYON->KEYON with no end between (4 and 10 in two Un-Bearable captures), which
truncates a sounding voice. If dropouts persist now that underruns are zero,
that is the remaining lead - `spu_capture.py attach` is the capture for it.

### Guard

`launcher/test_settings_coverage.py` now asserts no diagnostic is reachable from
the ordinary settings page, none is enabled by default, and no preset can turn
one on. It caught a naming inconsistency on its first run.
