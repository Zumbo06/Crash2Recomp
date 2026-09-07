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

## Reverb: documented 39-tap FIR replaces the linear stand-ins

`spu.c` carried a DOCUMENTED-GAP note: the reverb engine runs at 22.05 kHz and
the 44.1 <-> 22.05 boundary used a box average on input and linear
interpolation on output as placeholders. Those let everything above 11 kHz in
the reverb send alias into the engine, and rolled the wet signal's highs off -
the reverb sounded dull.

`reverb_frame()` now applies the 39-tap half-band FIR the SPU uses at that
boundary (psx-spx, "SPU Reverb Formula", reverb down/upsampling filter) on both
sides. Properties checked before it went in: symmetric, every odd tap but the
centre zero, taps sum to 0x7FFE (-0.0005 dB, inaudible). At frames aligned to
an engine step only the centre tap sees a non-zero stuffed sample, so those
frames reproduce the engine output exactly as before; only the in-between
frames change. Filter histories are not serialised - they restart from silence
on a savestate load and settle in under 1 ms, keeping the state format intact.

## Night Fight light: not the GTE control-register path

Suspected first: native overlay code emits CTC2 as a raw `cpu->gte_ctrl[rd]`
store for most registers, so a DQA/DQB write from level code might never reach
the depth-cue math. **Ruled out** with evidence: `gte_execute()` imports from
`cpu->gte_ctrl[]` on every call (the array is the source of truth), DQA (27)
is in the sign-extension helper set anyway, and the interpreter calls the same
`gte_write_ctrl()`. Native and interpreted paths agree on GTE control state.
`depth_cue_from_ir()` also matches the documented hardware formula.

What decides it is two in-game tests, both now reachable from the launcher:

1. **Video -> Renderer -> Software.** The software renderer reads VRAM directly
   with no texture/CLUT cache. If the light works there, it is an OpenGL
   renderer bug (a stale CLUT cache is the classic shape: darkness done by
   uploading darker palettes that the cache never sees).
2. **Advanced -> Run level code in the interpreter** (`PSX_OVERLAY_NATIVE_OFF`).
   If the light works there and not natively, the recompiler mis-compiles
   something in that level's overlay.

If neither changes it, the fault is in game logic the two paths share, and the
next step is a GTE/GPU trace from inside the level.

## Night Fight light: renderer and overlay codegen both cleared

User ran both decisive tests; **neither changed anything**:

- Software renderer shows the same broken light as OpenGL -> not a GL
  renderer bug (no texture/CLUT cache in the software path).
- `PSX_OVERLAY_NATIVE_OFF` (level code interpreted) shows the same -> the
  overlay recompiler is not mis-compiling the level.

So the fault is in something both paths share. Remaining suspects, each with a
test that isolates it:

1. **Main-executable codegen.** The overlay switch leaves the main exe
   compiled. `PSX_FORCE_INTERP=1` (memory.c) routes EVERY dispatch through
   the dirty-RAM interpreter, main exe included. Launcher: Advanced -> "Run
   ALL game code in the interpreter". Very slow; that is expected.
2. **Widescreen cull hooks.** At 16:9 `psx_ws_x_margin()` is ~53 px, so the
   recompiler-emitted hooks rescale depth bounds by 3*num/(4*den) = 1.33x and
   force "keep" at detected cull sites - live regardless of renderer or
   overlay tier. If the light is a draw-distance mechanic this would perturb
   it. Test: aspect 4:3, which zeroes the margin and makes every hook a no-op.
3. **Shared GPU decode (gpu.c) or GTE.** `_build/light_diag.py` diffs the
   primitive stream between a dark snapshot and a lit one: per-vertex
   brightness, primitive count, semi-transparency, drawing functions, and the
   GTE depth-cue registers (H/DQA/DQB/FC). Its verdict hints map each outcome
   to the subsystem. Needs Advanced -> Debug server port 4370.

Ruled out along the way: GTE `depth_cue_from_ir()` and `gte_gpf()` match the
documented formulas; `precise_nclip` is inert without PGXP (it reads
`pgxp_get_gte_sxy`); the CTC2 raw-store path is sound because `gte_execute()`
imports from `cpu->gte_ctrl[]` on every call.

## Night Fight capture: the game's lighting output never changes

`light_diag.py` A/B (dark vs firefly caught), frames 3043 / 3857:

    depth cue : H=288  DQA=-4194  DQB=0x1400000  FC=[0,0,0]   (identical A and B)
    vertex    : mean 19.1 -> 21.8, median 0.0 -> 0.0, <0x20: 76% -> 72%
    prims     : 932 -> 811 (camera moved; not a signal)

Two conclusions follow directly:

1. **The darkness is GTE depth cue toward a black far colour.** FC is black and
   DQA/DQB are set for a steep fade. The vertex colours the game hands the GPU
   are already dark (median exactly 0), so the renderer is drawing what it was
   given - the earlier renderer/overlay tests agreeing was not a coincidence.
2. **Nothing about the lighting reacts to the pickup.** Not the depth-cue
   registers, not the submitted colours. Whatever should widen the light -
   a new DQA/DQB, brighter colours - never happens. The fault is in the game
   logic that links the pickup to the lighting, not in GTE/GPU emulation.

That leaves: the main executable's compiled code (the overlay switch left it
compiled - test with "Run ALL game code in the interpreter"), or an emulated
event the pickup logic depends on (timer, IRQ, pad, CD). `light_diag.py
watch N` samples DQA/DQB/FC + brightness four times a second across the pickup
moment, so a write that lands and is immediately clobbered still shows up.

Tool lesson: the first verdict was silent because primitive COUNT vetoed it.
Prim count follows the camera; brightness and the depth-cue registers are the
signal. Fixed.

## Night Fight watch: the light DOES engage - for 2.4 s - and it is not depth cue

`light_diag.py watch 20` across a firefly catch:

    0-11.8 s   brightness ~18          DQA/DQB/FC constant       dark gameplay
    12.1 s     brightness 1.7 -> 1.3   frame f4132 held ~1 s     HOST STALL, near-black
    13.5 s     f4132 -> f4202 (70 frames in 0.3 s)               catch-up burst
    13.5-15.9  brightness 61-80        registers STILL constant  lit, ~145 frames
    16.2 s     brightness 0.0                                    one black frame
    16.5 s+    brightness ~18                                    dark again

Three facts, two of them overturning earlier assumptions:

1. **The game does brighten the scene.** Mean vertex brightness x4 for ~2.4 s.
   The mechanism is NOT the depth-cue registers (unchanged throughout) - the
   game recolours vertices some other way. Earlier "depth cue is the light"
   reasoning was wrong; depth cue is only the ambient darkness.
2. **It lasts ~145 guest frames.** The user remembers 10-15 s on hardware.
   Either the firefly's timer runs several times too fast, or an event ends it
   early.
3. **A ~1 s host stall sits exactly at the onset**, followed by a 70-frame
   catch-up burst. Something blocked the main thread when the light engaged
   (overlay load/compile, CD seek, or IRQ storm are the candidates). This is a
   bug in its own right and may be causally linked.

Open contradiction: the user reports NOT seeing the level light up, yet the
submitted colours were 4x brighter for 2.4 s. Either a short, modest
brightening straight after a freeze did not register, or those frames were a
different scene (black-bright-black is also a fade transition's shape).

`light_scan.py` diffs the full 2 MB RAM across dark/lit/dark snapshots to find
the game's own timer and flag words; `light_scan.py watch <addr>` then follows
them through a catch at 10 Hz. That distinguishes initialised-too-short from
decremented-too-often from cleared-by-an-event - three different fixes.

Aside from the Sep-3 heartbeat dump: vblank raised 2446, delivered 2090 (~15%
of VBlank IRQs never reached the game). If the firefly timer counts VBlank
callbacks, IRQ deferral is a suspect for duration distortion.

## Night Fight scan: the light flag is at 0x80060300

Whole-RAM diff across dark / lit / dark found two clean state words:

    0x80060300   0x00000000 -> 0x00000100   (0 dark, 256 lit, 0 dark again)
    0x800A01E8   0x00000002 -> 0x00000000

0x80060300 is just past the main executable's text end (0x60000), i.e. in the
game's static data - where a global light state belongs. It held 256 at both
lit snapshots, which were 366 frames (6.1 s) apart, so in that run the lit
STATE lasted at least 6 s - versus the 2.4 s of brighter colours in the earlier
watch. The duration is not consistent run to run; that is itself a clue.

The "timer" list was useless in that run: sorting by raw delta put display-list
scratch (millions per frame) on top and buried anything plausible. Fixed to
rank small values moving 0.2-32 units/frame first. Also, L2 was taken 6 s after
L1, so anything that reverted within 6 s was misclassified - the tool now says
so in its prompt.

Next: `light_scan.py region 0x80060000 1024 30` polls the flag's neighbourhood
at ~10 Hz through a catch and lists every word that moves. Globals for one
feature cluster, so the timer should be within a few hundred bytes of the flag.
Its first value after the catch is the duration the game intended; its rate
per frame says whether it is ticked too often.

## Night Fight region watch: 0x80060300 retracted; the light lives at 0x80060928

`light_scan.py region 0x80060000 1024 30` across a catch, death and respawn.

**Retraction.** 0x80060300 toggles 0<->256 every ~0.3 s from the first sample,
before any catch, as do 0x8006036C/3D8/444/4B0/51C - an array of 108-byte
structs with a per-frame parity bit. Render scratch aliasing against 10 Hz
sampling; the earlier scan's lit/dark snapshots just landed on opposite
phases. Lesson: a state word that is exactly 0/256 with no intermediate value
should be re-sampled before it is trusted.

**The real block is 0x80060900-0x80060958.** Timeline of that run:

    4.0 s   0x80060928  0 -> 4083 (12.12, 4096 = full)      light ON = catch
    4.7 s+  0x8006076C x5 (stride 0x20) and 0x800607F8 x3 (stride 0x24)
            start climbing, then ACCELERATE 5.7-6.3 s       something moving away
    6.1 s   0x80060928  4058 -> 58                           light OFF, 2.1 s later
    14.7 s  0x800608CC pointer -> null, (4096,4096,4096) at 0x8006080C..14,
            coords 0x80060884/888 jump                       death
    18.7 s  ~12 words revert to their 0.0 s values, 0x80060954 resets   respawn

0x80060928 is the light intensity. Its ~2.1 s life matches the 2.4 s of bright
vertices in the earlier watch - two independent measurements agree. The values
that move in lockstep with its collapse are position-like and accelerating
away, so the working hypothesis is: **the light is distance-driven and the
firefly stops following Crash** - it drifts off, distance grows, light fades.
On hardware it tracks for 10-15 s.

Next is not another watch. `light_writer.py` arms the debug server's write
trace on that block and reports every store to 0x80060928 with the exact
store PC, recompiled function, caller and frame, then resolves the PC through
SCUS_941.54_full.ranges to `func_XXXXXXXX` in generated/*.c. The formula can
then be READ in the recompiled C rather than inferred. If the writer is in
overlay space, the shard's C is in build-clang/cache/**/*_patched.c once it has
been compiled natively.

## Night Fight: the light is depth fog, and its parameters live in scratchpad

Read from the recompiled code rather than inferred. The world-mesh renderer's
per-vertex loop (tri `func_800439D4`, quad `func_80043A84`; a second family at
`func_80043DA0`/`func_80043EC0`) does, for every vertex:

    mfc2  SZn                    screen-space depth from RTPT
    subu  (SZn - ctx[16])        minus fog START
    sllv  << ctx[8]              times 2^fog SHIFT
    mtc2  IR0                    that is the depth-cue factor
    mtc2  RGBC, gte DPCS(sf=1)   baked colour -> far colour by IR0
    mfc2  RGB2

There is no distance-to-firefly term anywhere; the whole game contains zero
NCDS/NCCS/NCDT/NCCT ops - only DPCS x52 and DPCT x6. The "light" is a linear
depth fog toward a black far colour, and the firefly can only work by changing
the fog START (push darkness out) and/or SHIFT (flatten the ramp).

`ctx` is `$v1 = 0x1F800000` - the PS1 SCRATCHPAD. `func_8003DB94` (the world
render entry, args a0/a1/a2) copies `a2[160]` -> scratch[8] (shift) and
`a2[164]` -> scratch[16] (start); `func_8004399C` reads the far colour from
`a2[156]` into RFC/GFC/BFC and falls into the triangle loop. So `$a2` is a
render-parameters struct: +156 far colour, +160 fog shift, +164 fog start.

Consequences:
- Every RAM scan so far (light_scan.py) covered 0x80000000+ only. The two
  words that decide the darkness are in scratchpad and were never sampled.
- 0x80060928 was a red herring twice over: a camera-path keyframe, not light.
- The bug is in whatever writes params+160 / params+164 when the firefly is
  caught (CPU logic, main exe), or a codegen fault in the fragments above.
  The whole-program interpreter test (PSX_FORCE_INTERP) separates those and
  has still not been run.

## Fog-parameter writers: the static leads were false, so go dynamic

The only `sw` to offsets +160/+164 outside the renderer are:
- `func_8003BC5C` - an ordering-table initialiser (`at += 4; sw at, N(a0)` for
  64 words; the classic ClearOTag). +160/+164 are two links in the chain.
- `func_800528F8` - writes one value to BOTH +156 and +160 of an object indexed
  from a table at 0x8007xxxx. Far colour and fog shift never share a value; it
  is a different struct.
- the renderer's own `sw $t9, 160($v1)` - a scratchpad VERTEX slot (the value
  was just staged into VXY2/VZ2), nothing to do with the fog copy at
  scratch[8]/[16].

So params+160/+164 are written through a computed address - Crash's GOOL
script VM or a block copy from zone data - and no offset grep will find it.

Dynamic route: `func_8003DB94` stores the params pointer at scratch[96]
(`sw $a2, 96($v1)`). `light_scan.py fog` reads scratch[8]/[16]/[96], derefs the
struct, watches its fog fields next to brightness through a catch, and arms the
write trace on them so the writer comes back with its PC.

## Do not sample the scratchpad asynchronously

`light_scan.py fog` read scratch[8]/[16]/[96] from the debug server, which pumps
between frames. The scratchpad is shared scratch for many code paths with their
own layouts; the renderer's fog copies exist there only WHILE func_8003DB94
runs. What the sampler saw was other code's locals - pointers, small ints, zeros
- and the "params struct" it dereferenced from stale scratch[96] was unrelated
(three zero fields, zero writes). The mode's fog columns from that run are void.

The brightness column was real and confirmed the catch signature a THIRD time:

    15.3 s  near-black frame (1.6)      screen goes black at the catch
    15.7-16.6 s  frame held ~1.2 s      host stall
    16.9 s  +78 frames in 0.3 s         catch-up burst, brightness 84
    17.5-20.4 s  brightness 73-83       LIT, ~170 frames
    20.7 s  brightness 22               dark again

Two defects: the light dies after ~3 s (should be 10-15), and a ~1 s stall with
a black frame at the onset. The stall alone would make the light read as broken.

Synchronous replacement: `light_fog.py`. The renderer stores its fog copies at
fixed PCs (0x8003DBDC params ptr -> scratch[96]; 0x8003DBF8 shift -> scratch[8];
0x8003DBFC start -> scratch[16]) and memory.c:1696 traces scratchpad word stores,
so the write trace armed on exactly those three words - filtered by PC - yields
shift/start/params per frame as used. The params pointer learned in the first
2.5 s is then traced itself, so the writer that changes the fog at the catch is
named by store PC in the same run. wtrace_dump caps at 2048 entries per call;
the tool pages over frame windows (frame_lo/frame_hi) and halves any full one.

## The render-params struct is 0x80062A18, and its fog fields sit at zero

`light_fog.py` (synchronous, PC-filtered write trace) over 30 s of dark play,
982 world-render calls (one per 2 frames - the game runs at 30 Hz):

    params struct : 0x80062A18  (fixed global, just past text end 0x80060000)
    far colour    : 0x000000
    fog shift     : 0
    fog start     : 0          -> darkness from the camera, black beyond depth 4096
    writes to the struct's fog fields in 30 s: none

No catch happened in that window (no lit period, no stall), so this is the
steady dark state. Zero is also what uninitialised memory looks like; whether
these are the designed dark values or an initialiser that never ran is open.
Either way the catch must change THIS struct or switch the renderer to another.

Search note: a fixed global is addressed as lui 0x8006 + full offset, so its
fog fields appear as sw x, 0x2AB8/0x2ABC(base) - not as +160/+164. The earlier
+160/+164 grep could not have found them.

## func_8002131C: the level render-mode setup, where the fog comes from

Reads the level flags word at 0x80062AD8 and, per bit, installs a 5-entry
renderer function-pointer table at 0x80062A28..0x2A3C and loads that mode's
parameters:

    default   0x80044980 0x80042420 0x800426A0 0x800449B0 0x80044A04
    bit 3     0x8004299C 0x80042AB8 0x80042B7C 0x80042B44 0x80042C2C  (+ item 0x1B8 -> 0x2A48/4C)
    bit 5     0x8004399C 0x80042420 0x800426A0 0x800439D4 0x80043A84  FOG, per-vertex
    bit 21    0x8004599C 0x80042420 0x800426A0 0x800459A8 0x80045AB4  fog, flat per batch
    bit 6     0x80043D54 0x80042420 0x800426A0 0x80043D84 0x80043EA0  fog family B
    (0x8006CB94 bit 24)  0x80045C18 0x80045D88 0x80045F40 0x80042548 0x80042818

Fog parameters (bits 5 and 21): lookup(*0x800608DC, type 0x1DE, *0x800608E0, 0)
returns a record; far colour = rec[0], fog shift = rec[4], fog start = rec[8],
stored at 0x80062AB4/B8/BC (= params 0x80062A18 + 156/160/164). So the fog is
LEVEL DATA - a type-0x1DE item in the current zone entry - and the zone-entry
pointers live at 0x800608D8..E0, the same cluster whose camera-table pointer
flipped at the catch in the region watch.

Consequence: for the firefly to light the level, either the zone pointers must
switch to an entry whose 0x1DE record is "lit", or the record must be modified,
AND func_8002131C must re-run to reload. It did not run once in 30 s of dark
play. The ~3 s lit window would then be: catch -> setup re-runs with a lit
record -> next zone boundary -> setup re-runs with a dark record. Whether that
is the bug (the firefly should override the fog while attached) or the design
is what a catch run of light_fog.py decides: it now traces the flags word, the
zone pointers and the fn-ptr table, so every setup re-run is timestamped with
the family it installed and the writer PC.

func_8002131C has no static caller (reached through a pointer). func_80011800
passes 0x80062A18 as $a2 to the world renderer - it is the render dispatcher.

## Night Fight light: the mechanism, fully read

Corrections first. The fog fields (params+156/160/164) are IRRELEVANT to this
level - they are zero because the fog render mode (flags bit 5) is off, and
func_8002131C (which loads them) is not the per-frame setup. 0x80060928 was a
camera keyframe. Both retracted.

What actually happens, per frame, in func_80020A24 (the per-frame render setup,
reached through a pointer; writes the flags word itself):

1. flags = word[0] of the current zone's item type 0x185  (Night Fight: 0x4)
2. flags & 0x00800004 (bit 2 or 23) -> install family F4:
       renderer[0..4] = 0x80044A70 0x80042420 0x80042698 0x80044E48 0x80044EC0
   F4[0] does ctc2 zero -> RFC/GFC/BFC (far colour BLACK) and copies
   params+64..76 into scratch[124..140].
3. Light A: a1 = *0x8006CD50 (an OBJECT). If non-null,
       params+72 = ((a1.y - cam.y)>>8)<<16 | (a1.x - cam.x)>>8    (packed xy)
       params+76 =  (a1.z - cam.z)>>8
   else params+72 = 0x80008000, params+76 = -32768  ("no light" sentinel).
   Light B: same from a1 = *0x8006CC48 into params+64/68.
   Object coords are at +96/+100/+104; camera at 0x800607F4/F8/FC.
4. F4[3]/[4] (tri/quad) call func_80044BB4 per vertex with the vertex's 3D
   position (VXY/VZ input regs) and baked colour. It computes, per light,
       i = max(0, 5600 - |dx| - |dy| - |dz| - 800)      (Manhattan falloff)
   sums both, and func_80044C88 converts the sum to IR0 for DPCS toward black
   (table at 0x80044B00).

So the darkness is not a fog setting: it is the ABSENCE of light sources. With
both object pointers null every vertex gets intensity 0 and renders black. On
hardware at least one light is always present (the glow around Crash) and the
firefly supplies/widens the other. Here the tool showed the world at
brightness ~19 with median 0 and the catch changed nothing in render state.

The defect reduces to one question: why is nothing writing 0x8006CD50 /
0x8006CC48? Neither has a single direct writer in the recompiled main exe or
the cached overlays, so they are assigned through a computed store - Crash's
GOOL script VM writing a global is the natural suspect. That VM is main-exe
code, and PSX_FORCE_INTERP (whole-program interpreter) has still never been
run; it is the one test that separates a codegen fault in that path from an
emulation fault in whatever the script waits on.

light_fog.py now traces both pointers and params+64..76 and prints their live
values in its header; "A NONE / B NONE" there is the bug made visible.

## Night Fight light: the real mechanism (2026-09-07, from the disc)

Corrections to everything above about this bug:

* Night Fight is `S000000C.NSF` (Totally Fly is `S0000027.NSF`), NOT the
  `LighC` levels (0F/1E). Its render-flags records (camera record type 0x185,
  read by func_80031AE8, a keyed binary search over 8-byte records at item+16)
  are 0x00000004 on disc. The runtime's 0x4 is CORRECT. Fog is not involved.
* flags bit 2 installs render family F4 (0x80044A70...), which lights every
  vertex from two LIGHT OBJECTS: script globals GLOBAL[120] (0x8006CD50, light
  A -> params+72/76) and GLOBAL[54] (0x8006CC48, light B -> params+64/68).
  Null -> sentinel 0x80008000/-32768 -> vertex infinitely far from light ->
  black. That is the dark we see. Both pointers are null in our build.
* Script globals: base 0x8006CB70, planted in scratchpad 0x1F800058 by
  func_80019F08. GOOL opcode 0x1F pushes GLOBAL[B>>8], 0x20 stores A into
  GLOBAL[B>>8]. The VM main loop is func_8003A014/0x8003A06C; handler table in
  .data at 0x8005C514 (79 entries, copied to scratchpad 0x1F800060, pointer at
  0x1F80005C). Operand refs: <0x400 own pool (*(obj+0x10)+24), 0x400-0x7FF
  external pool (*(obj+0x14)+24), 0x800-0x9FF imm*256, 0xA00-0xAFF imm*16,
  0xB00-0xB7F frame local, 0xBE0 null, 0xBF0 true, 0xC00-0xDFF linked object
  field, 0xE00-0xFFF own field (+64+idx*4), 0xE1F stack.
* NO instruction in the EXE writes either light pointer. Crash 2 GOOL code
  items contain NATIVE MIPS blocks: opcode 0x49 (marker word 0x49BE0BE0) makes
  the VM `jalr $ra,$s5` into the following heap words; a block ends with
  `jalr $s5,$ra` (resume bytecode after it) or `jr $ra; ori $s5,0` (return).
  The firefly script `FflOC` is 2088 native words out of 2255. Its block at
  code word 121 registers the light:
      if self.f72: return
      g = *(fp+88)                      # fp = 0x1F800000 in the VM
      if g[54]==0: g[54]=self  elif g[120]==0: g[120]=self  else return
      g[55] += 1.0 ; self.f72 = 1
  and the block at word 155 unregisters (moves A into B, decrements g[55]).
* The static hunt could never see this: the store goes through a base loaded
  from the scratchpad, inside code that lives in level data.
* `_build/nsf.py` extracts NSFs from the disc, lists GOOLs, disassembles
  bytecode (`code`), external refs (`ext`) and camera records (`records`).
  `_build/psxexe.py` extracts the EXE and scans it whole.

Open question, needs one run of `python _build/light_fog.py` (now traces
GLOBAL[36..164] and prints the live values of 54/55/120/126): does the native
block at word 121 ever store? If GLOBAL[120]/[54] never leave 0, the firefly
never enters its registered state (catch transition / native block execution);
if they do, the fault is in F4's params+64..76 or the per-vertex light math.

## Night Fight light: ROOT CAUSE and fix (patch 0010)

The runtime run with the retargeted tool showed a firefly registered as light
B (GLOBAL[54] = 0x800A0138, GLOBAL[55] = 1.0) with real camera-relative
coordinates in params+64/68 (dx 44..1076, dz -7702..-19751) for the whole
window, the catch marker (GLOBAL[126] |= 2, native block at FflOC word 208)
firing 3 s before Enter, and the unregister block (word 155) 15 s later - the
whole script side works. The screen stayed black, so the fault is in the
render family's per-vertex light, func_80044BB4:

    mfc2 $t8, VXY0 ; mfc2 $a0, VZ0      (0x80044E60/64, per vertex)
    dz = a0 - lightZ ; |dx|+|dy|+|dz| against a 5600 radius, -800, -> IR0 -> DPCS

VZ0..VZ2 (cop2r1/3/5) are S16 registers: MFC2 returns them sign-expanded, the
same as IR0..IR3 (psx-spx GTE register table; Mednafen GTE_ReadDR case 1/3/5
casts through int16; Duckstation stores them SignExtend32 on write). Only OTZ
and SZ0..3 are U16. psxrecomp treated 1/3/5 as U16 everywhere: gte_read_data,
gte_mfc2, gte_export_cpu_state, gte_write_data and the backing canonicaliser
all masked them to 16 bits, and the recompiler reads them RAW from
cpu->gte_data (data_read_needs_helper excludes them), so every mfc2 VZ after
an RTPT or any helper call came back zero-expanded. The firefly sits at
negative camera-relative z here, so dz = 0xE1E2 - (-7702) ~ 65528 > 5600 for
every vertex, intensity 0, DPCS to black. PSX_FORCE_INTERP could not help: the
interpreter's mfc2 goes through the same gte_read_data.

Fix (runtime only, no codegen change, no hash bump): the guest-visible backing
form of data 1/3/5 and ctrl 4/12/20/26 (RT33, L33, LB3 and H, which hardware
also sign-expands on read) is now sign-expanded on every read/write/export/
canonicalise path; U16 stays for 7 and 16..19. gte_import already unpacks the
low 16 bits, so GTE math is unchanged. cosim's canonical hash includes 1/3/5.
Old savestates canonicalise on the first helper call.

Same bug class to watch for: any game that reads V registers back after a
GTE op (per-vertex lighting done on the CPU). Verified only by the user's
Night Fight test at the time of writing.

## Sound effects cut off early: SPU guest-time catch-up (patch 0011)

Symptom the user reports now that the game is completable: a jump/spin/crate
effect stops abruptly when several sounds overlap. NOTES.md:257-262 already
named voice stealing as the last open lead. This is the mechanism.

**The SPU is pumped in blocks, and the guest reads envelopes between pumps.**
spu_render() emits a whole block while the guest is frozen; the pump runs twice
per vblank, so a block is ~370 frames = ~8 ms (main.cpp:2884-2936, and
spu.c already called this out as "the write-to-render quantization audit").
Between pumps every envelope-derived register is stale by up to a whole block.

That is invisible for OUTPUT and fatal for ALLOCATION. The earlier capture work
established that Crash 2 makes 0 ENDX reads against ~280k CURVOL reads: CURVOL
is the only signal it uses to find a free voice, and it takes any voice reading
0. So the game keys a sound on, polls a few hundred cycles later, reads the
env_level we have not advanced yet (still exactly 0 from key_on's memset), and
keys the next sound onto the voice it started microseconds ago. The first effect
is truncated. On hardware that envelope has already advanced hundreds of
samples and the voice reads busy. Every allocation decision inside a block is
made against a stale envelope, so this fires constantly in dense scenes.

Fix: render up to the guest's current cycle BEFORE serving the registers the
decision depends on.
  * spu.c gains a catch-up callback (spu_set_catchup, spu.h). Called at the
    CURVOL branch of spu_read, the per-voice CURRENT-volume block
    (0x1F801E00..), and on KEYON/KEYOFF writes (0x1F801D88..0x1F801D8E). NOT on
    ordinary register writes - pitch/volume do not need it and the extra
    renders would fragment the block for nothing.
  * main.cpp installs sdl_audio_catch_up next to the mid-frame pump
    registration. It routes through sdl_audio_pump_midframe, so the turbo mute
    and discard-sink gates keep their existing validated semantics.
  * The audio clock statics (last_cycles/cycle_carry) moved to file scope as
    s_audio_last_cycles/s_audio_cycle_carry so the hook can early-out on
    "less than one output frame (768 cycles) owed" with a subtract and a
    compare. That path runs on every CURVOL poll; it must stay this cheap.
  * Re-entrancy guarded, though spu_render never reads registers.

KEYON also flushes: without it the whole block renders with post-edge voice
state, so the previous sound's last ~8 ms is overwritten rather than merely cut.

No new SPU state, so spu_snapshot_* is untouched. Netplay/rollback keeps the
same cycle accounting and g_audio_cycle_resync behaviour.

Watch for: the event ring now takes an AUDIO_EV_RENDER per catch-up, so it
wraps sooner during capture. Per-call spu_render overhead is small (register
reads plus a 24-voice active scan, no allocation on the default path), but if
FPS regresses the mitigation is a minimum-chunk threshold in the early-out.

**Also in 0011, unrelated to the above:**

* Pitch > 1.0 lost phase at every block boundary. The phase-advance loop broke
  out when sample_idx hit 28 without consuming the remaining phase;
  decode_block() then zeroed sample_idx but left phase >= 0x1000, and the
  Gaussian index is (phase >> 4) & 0xFF so 0x1000 aliases to index 0. Up to
  three whole sample steps were dropped and the interpolation window restarted
  in the wrong place, once per 28 samples, on exactly the pitched-up sound
  effects. The loop now runs to completion and the overflow is carried into the
  new block (bounded by 3 for 14-bit pitch, clamped anyway).
* Reverb FIR comment said the taps sum to 0x8000; they sum to 0x7FFE
  (-0.0005 dB). Comment corrected, table untouched.

UNVERIFIED at the time of writing: both trees build clean, but the audible
result and the LIVE-key-on count still need the user's A/B run. The pre-0011
debugtools binary is preserved in the session scratchpad as
Crash2_BEFORE_debugtools.exe so the baseline can still be taken.

## Audio latency, volume and mute (patch 0012 + launcher)

Three dead knobs, all wired now.

* **game.toml [audio] buffer_ms was parsed and never read.** config_loader has
  range-checked it (30..500, default 180) since it was written, and nothing in
  runtime/src ever looked at runtime.audio_buffer_ms, so the DRC bridge always
  used its built-in 180 ms target. main.cpp now picks it up next to
  audio_spu_hq and applies it at rab_config time, keeping the shipped 100 ms of
  slack between target_ms and ring_ms rather than the absolute 280 ms.
  PSX_AUDIO_BUFFER_MS overrides for A/B, matching the convention of the other
  latency knobs. The launcher offers Low 60 / Normal 90 / Safe 180 and now
  defaults to 90: 180 ms of output latency is a lot for a platformer, and the
  bridge's controller has always had the headroom - it just was not asked.
* **Volume and Mute were dead controls.** They were declared, clamped, drawn
  and persisted, and reached nothing: absent from _build_env, absent from the
  settings.toml writer (which only ever emitted [video]). The runtime already
  had the sink - host_volume_get(), applied after the fade, the same value the
  numpad +/- keys drive. PSX_AUDIO_VOLUME now carries it, and Mute is expressed
  as volume 0 rather than a second flag so the two cannot disagree. An
  environment variable rather than settings.toml deliberately: the alternative
  meant adding a field to the recompiler's config_loader, and while that header
  is NOT in codegen_hash_sources.cmake (checked), it would still have forced a
  recompiler rebuild for a launcher feature.
* **spu_hq had no UI at all.** Exposed as "Higher-quality sound mixing".

Launcher, same pass:

* fps_telemetry was classified as a diagnostic while defaulting to True. Since
  active_diagnostics() reports anything deviating from its default, turning it
  OFF made the Play page announce "Diagnostics active: fps_telemetry" - the
  warning fired exactly when nothing was wrong. It is not a diagnostic: it
  prints lines to a log we already capture, and the Play page's performance
  readout is parsed from them. Moved to Settings -> Performance, still on.
* Developer mode (default off) hides the Advanced page AND hard-gates every
  diagnostic in _build_env and the debugtools binary swap, so a settings.json
  carried over from a debugging session cannot keep degrading a player's game
  after the page that set it is hidden.
* Diagnostics are reported by display name now, not raw field name.
* "Reset all diagnostics" cleared seven settings and re-synced four checkboxes,
  leaving three visibly ticked while off. Driven from DIAGNOSTIC_SETTINGS now.
* apply_config_settings returned early when game.toml was missing, which also
  skipped the settings.toml write - so in a tree without one, fullscreen,
  window size, CRT and texture filtering silently did nothing.
* Relaunch raced stop/start (terminate falls back to kill after 4 s without
  waiting, and QProcess.start on a live process fails silently); it waits for
  the exit now. Stop, Rebuild, Clear log and close-during-build all confirm.
* Crashes were reported as "Exited (-1073741819)" in small grey text. Named
  exit codes now map to plain language, the Log page comes forward, and the log
  can be saved to a file - the runtime writes no log of its own, so the
  launcher's capture is the only record.
* _HashWorker.cancel() had no caller: Cancel during the SHA-1 of a ~700 MB
  image did nothing. Wired, and the button is enabled during hashing.
* Closing mid-build orphaned cmake/ninja and the hash thread. SetupPage.busy /
  shutdown() now take the job down.
* The file dialog offered .chd while the inspector only reads cue sheets, so a
  CHD reported "Cue sheet lists no FILE entries". A CHD is now accepted as
  buildable-but-unverifiable, and "buildable" is tracked separately from
  "verified" so the Build button still works for it.
* Added: app/window icon (drawn, no shipped art), AppUserModelID so the taskbar
  stops grouping under python.exe, version.py, an About panel carrying the
  licence position, a startup try/except that reports failure in a dialog
  rather than a traceback on a console a player does not have, README.md, and
  launcher/test_ui_smoke.py (builds the window, toggles developer mode, selects
  every page, asserts no diagnostic env leaks).

UNVERIFIED: the audio changes still need the user's ears. Both test suites pass
and both runtime trees build clean.
