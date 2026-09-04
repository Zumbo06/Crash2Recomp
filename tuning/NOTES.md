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

## Overlay autocompile was silently off since patch 0002

`compile_overlays.py` refuses to emit shards unless `psxrecomp-game.exe`'s baked
codegen hash equals the one the runtime tree stamps into
`overlay_codegen_hash.h`. Patch 0002 edits `cpu_state.h` and `psx_cycles.h`,
and **both are listed in `runtime/codegen_hash_sources.cmake`** - so the moment
that patch landed, the vendored tree stopped matching the prebuilt binary that
ships in `_build/psxrecomp-cli/` (built from unpatched sources, Aug 27).

Every overlay had been falling back to the MIPS interpreter ever since, which
is also why months of SPU captures were taken with the *sound driver
interpreted*. Fixing it audibly improved the sound-effect dropouts that three
SPU theories had failed to explain.

Fix: `_build/build_recompiler.ps1` builds `psxrecomp-game` from the vendored
tree (`-DPSXRECOMP_ENABLE_CHD=OFF -DBUILD_TESTING=OFF`, and
`recompiler/tests/` had to be vendored because one test target sits outside the
`BUILD_TESTING` guard). `paths.py` prefers `_build/build-recompiler/` and falls
back to the prebuilt binary. Verify with:

    _build/build-recompiler/psxrecomp-game.exe --codegen-hash   # == 5cb10a8a
    grep PSX_OVERLAY_CODEGEN_HASH .../runtime/include/overlay_codegen_hash.h

## SPU: no defect found; the release rates are deliberate

Ruled out by captures, not by reasoning:

- **ENDX is never read.** 0 reads against ~280k CURVOL polls - the game picks
  voices purely from the live envelope level, so ENDX behaviour is irrelevant.
- **Parked voices carry release rate 30 or 31 exclusively.** In the divinco
  model those never decay (`divinco` saturates to 0 at Rr=31, and the
  `speed < zs` rescue gives 1 at Rr=30 - about 3 hours). Voices that *are*
  decaying always read Rr 12-15, which release in 1-5 s. The split is clean,
  so the game holds those voices on purpose and reclaims them by stealing.
- **The pool is not leaking.** Key-ons keep climbing on all 24 indices.

`PSX_VOICE_ALLOC_TRACE=1` (launcher: Advanced -> Trace SPU voice allocation)
prints per-voice key-ons, phase, envelope, Rr, and how many key-ons landed on a
still-audible voice.

## Line endings: main.cpp and gpu_gl_renderer.c are LF, the rest is CRLF

Old `sed -i` damage. `diff -u` against the pristine clone therefore reports the
whole file as changed. Generate patches with `--strip-trailing-cr`:

    diff -uN --strip-trailing-cr <pristine> <vendored>

## Home pause menu (patch 0009)

`psx_pause_menu.c` clones the `psx_savestate_menu.c` split: the module only
rasterizes an ARGB panel, while main.cpp owns state, input and the actions. It
reuses that menu's blocking pause loop (nested inside the vblank present body,
so the guest cannot advance a cycle) and `rewind_pause_present()`.

Rows: Resume, Quick save, Quick load, Game aspect, Image fit, FPS display,
Restart. Aspect and fit apply live - aspect repeats the sequence
`update_adaptive_widescreen()` uses, and clears `g_ws_adaptive_view` so the next
resize cannot overwrite an explicit pick. Restart re-execs the process
(`CreateProcessW` with our own command line) because there is no soft reset and
`psx_game_codegen_relaunch_or_exit` is compiled out of this build; it needs a
second press to confirm.

The FPS bar is no longer tied to `PSX_FPS_TELEMETRY`. It has its own flag
(`PSX_FPS_OSD`, default **off**) because `host_osd_set_status()` has no expiry -
so the launcher's "print telemetry to the log" checkbox was pinning a readout
over the game that nothing ever cleared.

## Pause-menu aspect change crashed the game

Changing Game aspect from the Home menu killed the process. Three plausible
causes were investigated and **ruled out** — worth recording so they are not
re-investigated:

- **A render-thread race.** There is no render thread. The log line
  "…presents/s on the render thread" is prose; `gpu_gl_renderer.c` says
  "(single context)" and "every blend and Swap remains on this context/thread",
  and grepping it for thread/mutex/atomic finds only comments. Interpolation
  sub-presents run synchronously on the calling thread.
- **`gl_renderer_set_display_aspect()`.** It is two stores (`s_aspect_num`,
  `s_aspect_den`) — no GL calls, no allocation.
- **The SDL logical-size call.** `sdl_renderer` is NULL on the OpenGL path, so
  that branch never executed.

The real cause was **applying a widescreen mode 0 → 2 transition inline from
the pause loop**, which is a blocking nested loop inside
`sdl_vblank_present_body()` with the guest frozen mid-frame. Two compounding
effects:

1. **Host.** With `native_wide` set, `refresh_widescreen_projection()` picks
   mode 2, whose first engage allocates the wide mirror surfaces. At
   supersampling 5 that is 3410x2560 RGBA8 + D24S8 per surface, up to
   `WIDE_MAX_SURF` (4) of them — roughly 280 MB of GL objects, built
   synchronously, with no `glGetError` check on `make_tex` /
   `glRenderbufferStorage`.
2. **Guest.** `psx_ws_x_margin()` jumps 0 → 85 the instant `ws_mode` becomes 2,
   which live-rewrites clip/cull constants the recompiler emitted into the
   game's own code — notably `psx_ws_xclip_bound()` returning `0x7FFFFFFF`
   instead of the per-primitive X-reject bound — on a frame already built at
   4:3.

**A false premise made this look safe:** the sequence was copied from
`update_adaptive_widescreen()`, but that function is **dead code here**. It
early-returns unless `g_ws_adaptive_view`, and the only assignment of `true` is
in `psx_mod_set_adaptive_display_aspect()`, a mod-plugin API nothing calls. The
pause menu was its first-ever caller.

Fixed two ways, both needed:

- **Staged, not inline.** `pause_menu_apply_aspect()` only records
  `g_pending_aspect`; `pause_menu_flush_pending()` does the work from the normal
  frame path at `main.cpp:7017`. The pause loop returns at 6688, earlier in the
  same present body, so the change still lands on the frame the menu closes.
  Same shape as `savestate_request_load` staging for a safe boundary.
- **Mode 1, not mode 2.** The flush forces `g_ws_native_wide = 0`. Mode 2 needs
  per-game viewport data Crash 2 does not have, so it allocates ~280 MB and
  widens nothing; mode 1 (GTE X-squash + stretched present) allocates no
  surfaces and is what the launcher already configures.

Window mode (windowed/borderless/exclusive) needs none of this and is applied
live: a single `SDL_SetWindowFullscreen()`, exactly as the Ctrl+F hotkey does.
The GL backend re-derives its viewport from `SDL_GL_GetDrawableSize()` on every
present. Read the row's value from `SDL_GetWindowFlags()`, never `g_fullscreen`
— the hotkey deliberately never writes that global, so the two desync.

## Launcher: Fusion + palette, then QSS

`apply_theme()` sets Fusion and a dark `QPalette` before the stylesheet. The
platform style drew light-theme combo arrows and checkmarks on dark surfaces.
Do **not** style `QCheckBox::indicator` or `QComboBox::down-arrow`: styling
either makes Qt stop drawing the native glyph and render only the rule, which
is how the old theme ended up with a tickless checkbox and a blank 20px
drop-down. Fusion draws both from the palette, using ACCENT as Highlight.

Card tones use `setProperty("tone", …)` + a QSS property selector, never
`setStyleSheet()` on the widget — a widget-level sheet resets style inheritance
for that whole subtree, so the two warning cards had been opting out of every
other Card rule. `common.set_tone()` does the required unpolish/polish pair.

The settings sections used to be a second 150px nav rail nested inside the
Settings page, sharing the `NavButton` object name with the real sidebar. They
are top-level sidebar entries now, grouped PLAY / SETTINGS / TOOLS, driven
through `SettingsPage.show_section()`. Display + Image merged into Video.
`SettingsPage` is still one class with every control attribute unchanged, which
is what keeps `test_settings_coverage.py` meaningful.
