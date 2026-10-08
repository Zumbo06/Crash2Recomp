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

## Release packaging (tools/package.ps1, v0.9.0)

Model: BUILD ON THE PLAYER'S PC. We ship the launcher and the recompiler; the
player supplies a disc image they own and the game is generated and compiled
locally. That is a legal requirement rather than a preference - the compiled
runtime contains the recompiled game code and is a derivative work of
Activision's copyright, so it can never be distributed.

Result: 144 MB staged, 56 MB zipped. The 761 MB clang/cmake/ninja toolchain is
NOT bundled (a separate prerequisite); bundling it would treble the download
and mean redistributing and licence-auditing all of LLVM, MinGW, CMake, Ninja.

**Two traps found while building it, both of which would have shipped a
silently degraded game:**

1. **The stock CLI's codegen hash is c5974900; ours is 5cb10a8a.** The runtime
   refuses a native overlay shard whose hash disagrees with the recompiler that
   emitted it and falls back to the interpreter WITHOUT SAYING SO - the exact
   failure documented at NOTES.md:270-291 that made months of audio captures
   worthless. package.ps1 replaces libexec/psxrecomp-game.exe with ours and
   prints both hashes.
2. **The CLI bundles its own framework/ tree, which is stock upstream.** A
   player building from it would get a runtime with NONE of tuning/patches/ -
   no GTE sign-extension fix (Night Fight renders black), no SPU catch-up, no
   pause menu, no presentation modes. package.ps1 ships the PATCHED tree from
   _build/Crash2Recomp/psxrecomp/ instead, and the result is verified identical
   to the working tree for spu.c, gte.cpp and main.cpp.

Also needed and easy to miss: tools/compile_overlays.py lives only in the full
source checkout (not in the CLI bundle, not in the project tree). Without it
the native overlay tier cannot run at all. package.ps1 fails rather than
shipping without it.

**Three launcher bugs that only exist when frozen**, found by running the
packaged exe rather than reasoning about it:

* paths.detect() keyed PLAYER mode purely on the runtime binary sitting next to
  the launcher. A FRESH bundle has no runtime yet, so the very first launch -
  the only one that matters for setup - fell through to WORKSPACE paths and
  looked for a _build tree a bundle does not contain. Now keyed on a
  bundle.json marker that package.ps1 writes.
* PyInstaller onedir puts the exe one level below the bundle root, so app_dir()
  anchored inside the program folder and would have written saves next to the
  Qt DLLs. It now walks up to the marker.
* overlay_autocompile_cmd used sys.executable, which frozen is
  Crash2Launcher.exe - the "compile overlays" command would have relaunched the
  launcher. find_overlay_python() looks for a real interpreter, and
  can_compile_overlays reports honestly when there is none.

The audit step is a deny-list re-scan of what was actually staged, run after
the allow-list copy, and it fails the build rather than warning: no disc image,
no SCUS_941* boot executable, no generated/ (keyed on recompiler output names,
since rabbitizer legitimately ships a directory of that name), no cache, no
.mcd/.pst, no compiled runtime. bios/openbios.bin is allow-listed by name and
location because it is MIT and required at boot.

Verified: `Crash2Launcher.exe --paths` from the staged bundle reports
mode=player, root=bundle root, and finds the recompiler, codegen and overlay
script. runtime/game.toml correctly absent until the player builds.

NOT yet verified end to end: an actual disc -> build -> play run from the
bundle on a clean machine. That is the remaining acceptance test, and the thing
to watch in its log is "overlay autocompile enabled (gcc)" rather than
"overlay gaps -> interpreter".

## The packaged bundle could not build (four bugs, all fixed)

Reported as "packaged cannot build, we can't recompile code to C". Reproduced
by running the bundle's own recompiler by hand. Four separate faults, in the
order they fire:

1. **The Setup page never passed --bios.** `psxrecomp.exe build` requires
   --disc, --bios AND --output; without it the build exits on the usage message
   having done nothing. This was never a bundle-only bug - the Setup page's
   build flow could never have worked. It was masked because the workspace
   project already existed, so nobody ran the flow end to end.
   Fixed: Layout.bios_rom (cli_exe.parent/framework/bios/openbios.bin, correct
   in both layouts) and page_setup passes it, with an explicit error if absent.

2. **Windows MAX_PATH.** The build copies the framework into the project, and
   rabbitizer's instruction tables are ~140 characters of relative path on
   their own. Past 260 total the copy dies with "cannot copy: No such file or
   directory" naming a path that plainly exists. Fixed: page_setup checks the
   project path length before starting and says to move the folder.

3. **The framework was staged with an allow-list, which dropped .gitignore.**
   This one was subtle and cost the most to find. config_loader.cpp's
   find_project_root() walks UP from the BIOS profile looking for exactly
   `.gitignore`, `.git` or `CMakeLists.txt`. Our framework root has a
   .gitignore; my packaging copied only named directories and named files, so
   dotfiles never made it. Without the marker the walk went one level too far,
   and `seeds = "recompiler/seeds/openbios_elf_seeds.json"` resolved against
   the PROJECT root instead of the framework root:
       wanted <out>/recompiler/seeds/...   (missing)
       actual <out>/psxrecomp/recompiler/seeds/...  (present all along)
   which is why the file "did not exist" while sitting right there. Fixed:
   package.ps1 copies the framework root wholesale with -Force and prunes,
   rather than allow-listing, and asserts .gitignore, bios/OpenBIOS.toml and
   the seed file all reached the bundle. The copyright audit is the control,
   so an allow-list buys nothing here and cannot express "and whatever else
   this tree needs".

   Bisecting this needed the full ladder: stock CLI works -> bundle CLI fails
   -> same binaries (md5) -> same game.toml -> seed file present in both ->
   only structural difference was the framework tree -> read find_project_root.
   `ls` hides dotfiles, which is why the diff looked clean for several rounds.

4. **The launcher could not find the game it had just built.** build.ps1
   configures cmake into <project>/build, so the exe lands at
   <root>/build/<NAME>_Recompiled.exe, and PLAYER mode searched only <root>.
   A completely successful build still reported "not built". Fixed: detect()
   searches root, root/build and root/build-clang. Note the generated name is
   taken from the disc serial (SCUS_94154_Recompiled.exe), which the existing
   *Recompiled.exe glob already handles.

Verified end to end from the staged bundle: all four recompiler steps pass
(Ready), 30 files / 33 MB of generated C, BIOS C produced, the copied framework
carries our patches (SPU catch-up, GTE S16, audio latency, pause menu), and
`cmake --build` links SCUS_94154_Recompiled.exe with exit 0. A fully built tree
then resolves as mode=player with the runtime found.

Still unverified: actually PLAYING the bundle-built binary (the user runs game
tests), and a clean-machine run where the toolchain and Python are absent.

### Fifth bug: "output directory is not empty"

`psxrecomp.exe build` hard-refuses a non-empty --output (main_cli.cpp:264) and
has no override flag - the options are only --disc, --bios, --output, --name.
The bundle root is full of what we ship (the launcher, the recompiler,
LICENSES, userdata), so building into it could never work.

My earlier end-to-end tests missed this because I ran the CLI by hand into
clean scratch directories (C:/t8) instead of the path the Setup page actually
passes, which is layout.project. Testing the tool is not testing the caller.

Fixed: in PLAYER mode the generated project goes to `<root>/game/` rather than
the bundle root. That directory is entirely launcher-owned, so a rebuild can
clear it without touching userdata/ (saves, settings) which sits beside it.
`Layout.project_is_disposable` gates the clearing to PLAYER mode - a workspace
project is the developer's tree and is never deleted on our initiative; there
the page says to empty it by hand.

Knock-on: runtime discovery had to follow, since the exe now lands at
`<root>/game/build/`. find_runtime searches game/build, build, root and
build-clang in that order.

## Widescreen, part 2: the native-wide rejection was wrong

Three files independently recorded the same explanation for why Crash 2 uses
the GTE X-squash hack (mode 1) instead of native-wide (mode 2):

> mode 2 renders extra columns from per-game viewport data, which Crash 2 does
> not have, so `nw_extra` stays 0 and nothing widens

(`main.cpp` pause flush, `launcher/.../config.py`, `launcher/.../runtime.py`,
and this file at the "SOLVED: it was mode selection" section.)

**That is not what the code does.** `ws_nw_configured_offset()` (gpu.c:390)
derives the per-side reveal from the LIVE DISPLAY WIDTH and the target aspect:

    numr = 3*num - 4*den;  w = ws_disp_w();
    offset = (w*numr + 4*den) / (8*den)

No per-game table is consulted. At 16:9 that is `(w*12 + 36)/72`. There is no
viewport data to be missing.

What `nw_extra = 0` actually meant is that native-wide never **activated**:

    ws_native_wide_active() = ws_mode == 2 && !gpu_ws_present_native_43()

and `gpu_ws_present_native_43()` returns 1 for any frame the gameplay detector
does not classify as gameplay. That observation predates
`[widescreen] gte_game_mode = true`, which is exactly the opt-in for a fully-3D
title with no sprite-tag hook. With it set, `ws_game_mode()` (gpu.c:273) takes
the GTE-activity branch — 3 verts, 45-frame hysteresis — and `ws_2d_only_scene()`
returns 0 unconditionally (gpu.c:308). So the gate that held mode 2 shut is
already open; nobody re-tested mode 2 after opening it.

### Arithmetic that settles the display width

Two margins were recorded at the same 16:9 aspect and looked contradictory:
85 px (this file, the pause-menu crash section) and ~53 px (the Night Fight
suspect list). Both are right, and the difference identifies a real defect:

| path | formula | at 16:9 |
|---|---|---|
| mode 2 `ws_nw_configured_offset` | uses real `ws_disp_w()` | `(512*12+36)/72` = **85** |
| mode 1 `psx_ws_x_margin` | hardcodes **160** = 320/2 | `160*(den-num)/num` = **53** |

Only `w = 512` produces the observed 85, so **Crash 2 renders 512 wide**. That
is corroborated independently by `crash2_wide_probe.h`, which rescales the
margin by `512.0f/320.0f` and bounds polygons against `512+margin`.

So `psx_ws_x_margin()` (gpu.c:966) is **computed for a 320-wide game**. Its own
comment says as much: "the game's draw classifier works in objX-camX where 1
unit ~= 1 native-4:3 screen pixel ... half-view of 160/s pixels". For a 512-wide
title every margin it returns is short by 512/320 = 1.6x. This is currently
LATENT — see below — but it bites the moment any cull hook is enabled.

### Why the edges pop today: nothing widens the cull at all

Crash 2's `[widescreen]` has no `cull` table, so every site list is empty and
every predicate is false. Walking the interpreter's SLTI/SLTIU cases
(dirty_ram_interp.c:1994-2046), every widening branch falls through to vanilla:

- `psx_ws_is_cull_{keep,depth,slti,slti_lower,vxrange,range,bias}_site()` — no sites
- `psx_ws_auto_cull_on()` — `[widescreen.cull] auto_screen_x` is default-OFF

The FOV widens; the game keeps culling, activating and clipping at its original
4:3 bounds. That is the edge popping, and it is mode-independent — switching to
mode 2 reveals more columns but does not make the game submit geometry for them.

### auto_screen_x cannot help this title

The automatic path scans for a screen-extent trivial-reject — a width compare
AND a height compare in the same function (`psx_ws_func_has_screen_cull`),
immediates defaulting to 0x140/0x141 + 0xE0/0xF1 (Tomba, 320-wide; Ape Escape
uses 0x181 on 368). Census of the 69,095 instructions in `generated/`:

    SLTI  (0x28) = 271 sites    SLTIU (0x2C) = 263 sites
    imm 0x140/0x141 : 0 / 0     imm 0x0E0/0x0F0/0x0F1 : 0 / 0 / 0
    imm 0x200/0x201 : slti 1 / 2, sltiu 0 / 0

Zero width signatures and zero height signatures. Crash 2 does not use the
idiom, so `auto_screen_x` would find nothing even with corrected immediates —
and with no height compare anywhere the detector cannot fire regardless.

Crash 2 culls by building an authored **draw list** of polygon ids instead,
which is what `crash2_wide_probe.h` (now recorded, patch 0013) intercepts at
`0x80041E5C`.

### Patch 0014 — native-wide allocation guard

Prerequisite for testing mode 2 at all. The first mode-2 engage allocates
3410x2560 RGBA8 + D24S8 per surface at supersampling 5, up to WIDE_MAX_SURF —
~280 MB, synchronously, and NEITHER `glTexImage2D` NOR `glRenderbufferStorage`
reports failure through a return value. An out-of-memory driver handed back
object ids with no storage and every later draw mirrored into them.

`wide_fbo_for()` now pre-checks against GL_MAX_TEXTURE_SIZE /
GL_MAX_RENDERBUFFER_SIZE, checks `glGetError()` after each allocation, and
LATCHES failure (retrying 70 MB every frame is worse than degrading). All three
callers already treated 0 as "skip the wide mirror", so the graceful path
existed — it was simply never reached. `wide_free_all()` clears the latch so a
reconfigure at a lower scale retries.

The pause-menu force of `g_ws_native_wide = 0` **stays** for now. Its stated
reason was wrong, but a second, real hazard remains: `psx_ws_x_margin()` jumps
0 -> 85 the instant `ws_mode` becomes 2, live-rewriting emitted clip/cull
constants on a frame already built at 4:3. That is a property of the live
TRANSITION, not of mode 2 — launching directly into native-wide never crosses
it, which is why that is the supported way to exercise mode 2 today.

## Widescreen, part 3: mode 2 measured working; the real problem is submission

Native-wide validated in-game (frame 13696, 16:9):

    mode 2   squash [1,1]   nw_extra 170   present_native_43 0
    x_margin 85   activation_margin 85   game_mode 1   gte_verts 206

`nw_extra = 170 = 2*85` and 85 = `(512*12+36)/72`, so the 512-wide display
derived in part 2 is now measured, not inferred. `game_mode 1` with
`last_tag_frame` at its never-set sentinel confirms the GTE-activity branch is
the only thing classifying gameplay here — `gte_game_mode` is exactly what
opens the gate that the old "no viewport data" note mistook for a missing
feature. `aspect_cone` / `terrain_angle` counters are all zero, confirming
those hooks are Tomba-specific.

**The decisive number is `ovh_prims = 0`**, with `last_ovh_frame` still at the
never-set sentinel: the >=4-prim overhang threshold has not been crossed once
since boot. `ws_note_overhang` (gpu.c:4791) measures against the real
`ws_disp_w()` and uses raw pre-draw-offset SX, so our own offset injection
cannot feed it. Crash 2 submits **zero** geometry past its own window. The
compositor reveals 170 px the game will never draw into — so this was never a
compositor problem, and switching modes cannot fix it.

Draw-list structures mapped (detail and the Phase 4 plan in
`tuning/WIDESCREEN-PLAN.md`):

- two 3044-byte (`0x0BE4`) blocks allocated at `func_80029768`;
  `[0x8005F390]`/`[0x8005F3D0]` take the first, `[0x8005F398]` the second.
  Whether they are a double-buffer pair is NOT established: `mipsdis` reports
  `GAP` for every jump in that function so the call structure is unrecoverable
  from the emitted comments, and `[0x8005F398]` is read only at teardown
  (`func_800297C8`), never by a renderer. The capacity figure is independent of
  this — the consumer's list comes from `[0x8005F390]`, a 3044-byte block
- layout `+0` count (halfword), `+2` zeroed at alloc, `+4..` polygon ids
  => **1520 ids capacity**
- consumer `func_80041E5C` (repurposes `$sp` as the list end pointer), called
  from **two** sites: `0x80011CB0` and `0x8001845C`
- `zone = [[0x800608CC]+16]`, `nw = [zone]` (1..8), 48-byte world descriptors
  from `zone+4`; ids encode `(world<<13)|index` with `0x1800` marking a quad
- the render driver `func_80011800` packs those descriptors to scratchpad
  `0x1F800280`, reading `zone+8,12,16,20,24,28,32` — which matches the probe's
  world-entry model for `wi=0` exactly, so the probe reads the data correctly

Two probe defects found while mapping this: it guards on
`gpr[31] == 0x80011CB8` so it misses the second call site entirely, and
`room = count-1-nk` means it can never grow the list — it only refills slots
vacated by now-invisible polygons. Its own buffer is 4096 entries, so the
game's 1520 is not the binding constraint; the cap is self-imposed and the true
ceiling is GPU packet/OT budget, which has not been measured.

Where the list is FILLED is still unlocated: the buffer is heap-allocated and
written register-indirect, so address grepping cannot find it, and it may live
in overlay code rather than the main executable.

## Widescreen, part 4: the meshes end at the 4:3 frustum — result and consequence

Probe run (`PSX_CRASH2_WIDE_PROBE=1`, native-wide, outdoor level): the zone
meshes contain essentially **no** polygons that project into the widened view
but were omitted from the authored draw list. `cand` ~ 0.

### What this settles

**The draw list was never the limit.** Extending it cannot help, so the whole
Phase 4 "extend the render list" approach — and with it the native-wide
migration as specified — is dead for this title. Native-wide is now strictly
WORSE than the squash: it reveals 170 px that provably contain nothing.

**It also explains the ORIGINAL complaint.** "Widescreen hack edge issues" was
never a cull bug. Crash 2's levels are authored to the 4:3 frustum; the world
geometry simply ends there. ANY widening of the field of view — mode 1's GTE
squash exactly as much as mode 2's extra columns — walks the camera past the
authored edge of the world and exposes the void. That is a level-data
limitation, and no runtime change can conjure geometry that was never shipped.

This is consistent with every measurement taken:

- `ovh_prims = 0` — nothing is ever submitted past the canonical window
- no screen-extent cull idiom anywhere in the executable (0 width, 0 height
  immediates) — there is no cull to widen because there is nothing to cull
- `cand` ~ 0 — and now, no omitted content to add

Three independent measurements agreeing that the world stops at the frame.

### The one unexcluded possibility

The probe only sees the CURRENT zone's resident worlds (`nw <= 8`). If side
geometry lives in adjacent zones that are not resident, it would read as absent.
Distinguishing that from "does not exist" means following render-data prefetch,
i.e. streaming more level data per frame — a far larger problem than widescreen,
with a memory and load-time cost. Not worth opening unless the edge artifact
matters more than everything else on the list.

### What to ship instead

No FOV widening is artifact-free on this title, so the real choice is between
losing content and showing void:

| option | result | cost |
|---|---|---|
| 4:3, `letterbox` | fully correct image | pillarbox bars |
| 4:3, `fill` | fills a 16:9 screen, no void | crops top/bottom — real in a platformer |
| 16:9 mode 1 (squash) | fills the screen | the void at the edges = the reported artifact |
| 16:9 mode 2 (native-wide) | fills the screen | same void, plus ~280 MB of surfaces. No reason to use it |

`native_wide` goes back to **false**. Mode 2 buys nothing here and costs memory.

The remaining tractable improvement, if 16:9 is kept, is `[widescreen.backdrop]
x_sites` (`psx_ws_backdrop_x`): the parallax 2D backdrop computes screen-X
without the GTE, so it misses the squash and fails to cover the widened FOV.
Configuring those sites would pull the sky/backdrop out to the new edges. It
covers the BACKDROP portion of the void only — terrain that simply ends still
ends — but that is the cheap part of the artifact, and it needs per-game site
addresses rather than a code change.

## Widescreen, part 5: correcting part 4 — and no backdrop layer exists

Draw census, 120 frames of open-scenery gameplay at 16:9 mode 2, 78,769
primitives (`_build/ws_census.py`, ring cap 2^21 so nothing wrapped):

    opcode  0x3C 32280   0x30 29428   0x34 6650   0x36 4340   0x3E 1440
            0x24  1430   0x26  1401   0x2E 1200   0x38  600
    xmin -429   xmax 629   past the 512 window: 5735 prims

### 1. There are no `[widescreen.backdrop] x_sites`, because there is no 2D layer

Every opcode recorded is in 0x24..0x3E — **polygons only**. Across 120 frames
there is not one rect, sprite or line (0x40..0x7F), even though the census gate
is `opcode >= 0x20 && opcode <= 0x7F` and would have caught them. Crash 2 draws
everything, HUD included, as GTE-projected polygons.

`psx_ws_backdrop_x` exists to correct a parallax 2D backdrop that computes
screen-X *without* the GTE. Crash 2 has no such layer, so there is nothing to
correct and no site to configure. That also means the codegen-hash cost and the
full regeneration it implies are avoided — the feature is simply inapplicable.

### 2. Part 4's "nothing to reveal" was WRONG — retracted

Part 4 concluded the meshes stop at the 4:3 frustum and no FOV widening could
ever be filled. The census disproves it: **5,735 primitives extend past the
canonical window**, reaching to xmax 629 and xmin -429 against a 512-wide
display. Geometry does reach into the revealed margins.

The error was mine: I generalised from `ovh_prims = 0` in a single `gpu_state`
sample. That field is `ws_ovh_prev` (gpu.c:1630) — ONE previous frame's count,
in whatever scene happened to be on screen at that instant — and
`last_ovh_frame` only stamps when a frame crosses the >=4-prim bar. A single
instant in one scene is not evidence about the game; 120 frames of open-scenery
play is. The two disagree, and the larger sample wins.

### 3. The two results are compatible

`cand ~ 0` (probe) and 5,735 past-edge prims (census) are not in conflict. The
polygons reaching into the margins are ALREADY in the authored draw list —
large near-camera surfaces naturally spanning beyond the view. `cand` measured
something different: polygons the list OMITTED. Nothing needs adding because
what covers the margins is already submitted.

### 4. What is actually still unmeasured

The probe walks the zone's static world meshes only. **Dynamic objects — Crash,
enemies, crates, pickups, platforms — are drawn by a different path and have
never been measured.** The original report was edge *popping*, which is the
signature of object activation bounds, not of static terrain. That is Phase 5
territory (`psx_ws_activation_margin`, the bias/range cull sites), and it was
never tested because Phase 4 was closed prematurely.

## Widescreen, part 6: the eight-corner render cull

Patches 0016 (the cull fix) and 0017 (the margin hardcodes). This is the first
part of the widescreen work that changes what the game *submits* rather than how
the compositor presents it.

### The routine

`func_80041D14` is Crash 2's render-visibility test. It reads a packed XY at
scratchpad `0x1F800118` and Z at `0x1F80011C`, builds a 1020-unit (`0x3FC`) AABB,
projects the eight corners with three RTPTs, and calls `func_80041E20` once per
corner. Decoded from the executable:

    80041E20: 0480FFFD  bltz $a0, 80041E18    ; bit 31 of packed = Y < 0  -> out
    80041E24: 00043400  sll  $a2, $a0, 16
    80041E28: 04C0FFFB  bltz $a2, 80041E18    ; X < 0                     -> out
    80041E2C: 00042402  srl  $a0, $a0, 16
    80041E30: 04A0FFF9  bltz $a1, 80041E18    ; SZ < 0   (DEAD - see below)
    80041E34: 00063402  srl  $a2, $a2, 16
    80041E38: 2084FF28  addi $a0, $a0, -216
    80041E3C: 0481FFF6  bgez $a0, 80041E18    ; Y >= 216                  -> out
    80041E40: 20C6FE00  addi $a2, $a2, -512
    80041E44: 04C1FFF4  bgez $a2, 80041E18    ; X >= 512                  -> out
    80041E4C: 8C7F0064  lw   $ra, 0x64($v1)   ; INSIDE: restore 80041D14's ra
    80041E50: 24180000  li   $t8, 0           ; visible
    80041E54: 03E00008  jr   $ra              ; ...and return ALL THE WAY OUT

The inside path restores `func_80041D14`'s own saved return address from
scratchpad, so a corner landing on screen exits the whole routine immediately.
Only the eighth call site links to `0x80041E10`, which sets `t8 = -1`.

> **The box is visible iff ANY corner is inside; rejected iff ALL EIGHT are
> outside.**

`bltz $a1` is provably dead. `$a1` is `mfc2` of SZ (`gte_data[19]`), and
`gte_export_cpu_state` writes `d[16+i] = gte->SZ[i]` from a `uint16_t`, so it is
always in `[0,65535]`. Same on real hardware - `mfc2` of SZ zero-extends. That is
why patching `$a0` alone is sufficient to force the visible path.

### Two defects, not one

**A. A box that spans the viewport is culled.** Every corner is outside while the
box covers the screen. Present in the original game, independent of aspect.

**B. The revealed margins are culled - native-wide only.** Mode 2 does not squash
the GTE; it grows the frame by `ws_nw_configured_offset()` = 85 px per side via
the GPU draw offset. The guest still tests `[0,512)`, so everything in `[-85,0)`
and `[512,597)` is rejected and snaps in at the old 4:3 edge. **This is the
reported artifact.** Mode 1 is structurally immune: `gte.cpp` squashes X *before*
the guest's own test, so that test already sees widened coordinates.

### Why the previous gate was dead

`crash2_wide_bbox.h` shipped gated `if (!ws.active || ws.mode != 1) return;` -
scoped to the one mode where defect B does not exist, and with hardcoded 512/216
bounds that could not address it anyway. Worse, the obvious repair (gate on
`ws.active`) is *equally* dead:

    gpu.c gpu_ws_configure():  else { ws_xnum = ws_xden = 1; }   /* modes 0 and 2 */
    gpu.c ws_configured():     return ws_xnum != ws_xden;
    gpu.c ws_active():         return ws_configured() && !gpu_ws_present_native_43();

Mode 2 forces the squash factor to 1/1, so `ws_configured()` - and with it
`ws_active()` and `GpuWsDebug.active` - is **false under native-wide**. `ws.active`
means "squashing", not "widescreen is on". The working gate has to name both
modes explicitly:

    const int off = ws.nw_extra / 2;        /* 0 in mode 1, 85 in mode 2 */
    if (ws.present_native_43) return;
    if (!((ws.mode == 1 && ws.active) || (ws.mode == 2 && off > 0))) return;

`ws_nw_extra()` is `2 * ws_nw_offset()` and returns 0 unless native-wide is live
*this frame*, so `off > 0` already implies mode 2 is engaged. 4:3 returns at the
mode test - byte-identical, deliberately: defect A is original-game behaviour and
4:3 stays the untouched reference build.

### The pre-check must NOT be widened

`if (x >= 0 && x < 512 && y >= 0 && y < 216) return;` is **not** a consistency
check against the replay window. It mirrors the guest's own accept test and
means *"the guest is about to keep this box anyway, so do nothing"*. Widening it
to `[-off, 512+off)` makes the hook return for a corner at X = -50, which the
guest then rejects - i.e. it would skip precisely the geometry defect B is about.
This was caught in review, not in testing; it would have looked like "the fix
does nothing" while every counter read plausibly.

### Why the replay is safe

- **`$t2` (the wrap mask) is live at corner 8.** Nothing in `0x80041D14..0x80041E0C`
  writes it, and `func_80041E20` writes only `$a0`, `$a2`, `$t8`, `$ra`. The replay
  reads the *live* `cpu->gpr[10]` rather than reconstructing it, so the argument
  does not depend on identifying the caller - which matters, because there is no
  `jal 0x80041D14` anywhere: the routine is reached only through a function
  pointer at `[$s3+0x1C]`.
- **Patching `$a0` is invisible.** It is caller-saved and `func_80041D14` clobbers
  it via `mfc2` at every corner, so no correct caller can read it across the call.
  Both exits touch only `$ra` and `$t8`.
- **The replay writes nothing back.** `gte_replay_side_effects_begin/end` suppress
  the exec counter, PGXP vertex pushes, gameplay stamps, SZ statistics, the dome
  probe and the trace rings; the projection runs on a private `GTEState` copy and
  never passes through `gte_export_cpu_state`.
- **The replay still squashes.** `do_squash` is deliberately *not* sandbox-guarded,
  so in mode 1 the replay's coordinates match the guest's exactly. Likewise
  `s_gte_caller_ra` is preserved across the sandbox, so `ws_dome_call_matches()`
  makes the same decision the guest's own RTPT just made. Both are load-bearing;
  neither is obvious from reading `gte_replay_side_effects_begin` alone.
- **The eight corners are reconstructed exactly**, including `points[3] ==
  points[4]` (one XY column visited at both Z values, matching the third RTPT
  re-transforming corner 4 as a throwaway V0).

### Cost control

The hook fires once per *rejected* box, which for a cull routine is the common
case, so a naive eight-projection replay is not free. Three mitigations:

1. **Zero-projection fast accept.** At corner 8 the guest's GTE FIFO still holds
   the third RTPT's vertices - cube corners 4, 7, 8 - in SXY0/1/2 and SZ1/2/3.
   `common` is an AND over corners, so if those three already fail to share an
   outside edge, the full eight-corner AND is 0 too. Catches the straddle case
   with no GTE work at all.
2. **Opposite-corner ordering** `{0,7,1,6,2,5,3,4}` plus an early break once
   `positive && common == 0`. The result cannot change after that, so both are
   semantically free; a straddling box typically settles in 2 projections.
3. **Per-frame revive budget** (default 64). Whether this cull feeds the 1520-id
   draw list at `[0x8005F390]` is unproven, and its consumer `func_80041E5C` sits
   directly after this routine in memory. `budget_drops` reports if it ever binds.

### Measuring it

`c2_bbox` on the debug server switches all three states live, so one launch
covers the whole A/B: `{"on":0}` vanilla, `{"on":1}` the
conservative re-test, `{"on":2}` force-visible. That last one imitates something
the game already ships - `0x8001AFF8` installs `0x80041E50` ("li $t8,0; jr $ra")
in place of the cull when flag bit `0x00040000` is set - giving a true upper
bound on what this routine can possibly reveal. If `on:2` shows substantially
more than `on:1`, the residue is either an over-strict replay or, more likely,
**object activation** rather than render visibility (part 5) - which this change
explicitly does not touch.

Counters: `hits` (boxes about to be rejected), `guest_keep` (would have been kept
anyway), `fast` (accepted from the FIFO), `replays`, `recovered`, `budget_drops`.
At 4:3 every one of them must stay zero.

The handler and its table entry sit outside every `PSX_NO_DEBUG_TOOLS` guard, so
`c2_bbox` is present in **both** build trees and works wherever the debug server
is listening (`--debug-port`). The debugtools tree is simply what the launcher
selects in Developer mode, and it additionally carries the per-block
instrumentation that `PSX_DEBUG_TOOLS=ON` compiles in.

`PSX_CRASH2_WIDE_BBOX` sets the initial state (`0`/`1`/`2`) before the first
`c2_bbox` command arrives; a debug-server call always wins over it afterwards.
**Set it to `0` for any `PSX_COSIM` run** - this is an intentional divergence and
would otherwise be reported as a defect.

### Measured in-game, 2026-09-14 (attract-demo scenes only)

Run against `build-debugtools`, settings 16:9, ss=5, via the attract demo - see
the caveat at the end. Frame budget held at **16.68 ms avg / 24.29 ms max**
(60 fps, GPU-bound: scene_gpu 13.76 ms avg), and `budget_drops` stayed **0**
throughout at the default 64/frame - the hook fires only ~0.3-3 times per frame,
nowhere near the cap.

**Patch 0017 confirmed live.** Mode 1, squash `[3,4]`, `x_margin` reads **85**,
not the old 53. That is the same value mode 2 derives independently, which is
the cross-check that the two paths now agree.

**The gate reasoning confirmed empirically.** In mode 2 `gpu_state` reports:

    mode=2  nw_extra=170  squash=[1,1]  configured=0  active=0  x_margin=85

`active` really is **0** under native-wide. The shipped `mode != 1` gate *and*
the obvious `!ws.active` repair would both have been dead here; only the explicit
two-mode form runs. This is now measured, not merely read off the source.

**Test F - 4:3 byte-identity: PASS.** Forced to `on:1` (the shipping mode) across
~3,300 frames of real 3D gameplay (`gte_verts` 2400-2800), every counter stayed
at zero: `hits=0 keep=0 fast=0 replays=0 recovered=0`.

**Mode 2 A/B**, interleaved 6 s legs x 3 rounds to average out scene drift:

    on:0  vanilla        1081 frames   hits=535   recovered=0
    on:1  conservative   1082 frames   hits=752   recovered=34     (4.5% of hits)
    on:2  force visible  1081 frames   hits=690   recovered=690    (100%, by definition)

**Draw census**, ~208k primitives per leg, raw pre-draw-offset SX extent:

    on:0  861 prims/frame   past_right(>512) 56.9/frame   xmax 875
    on:1  858 prims/frame   past_right       58.2/frame   xmax 875
    on:2  867 prims/frame   past_right       65.9/frame   xmax 980

So forcing every rejected box visible *does* push measurably more geometry into
the revealed columns (+14.8% past-right, xmax 875 -> 980), which proves the
routine is a real gate on margin content. The conservative test, though, revives
only ~5% of rejected boxes and moves past-right by ~2% with no change in xmax.

### What that means, and what it does not

The mechanism works and is correct: it fires, it recovers, it is inert at 4:3,
and it costs nothing measurable. But in these scenes the honest reading is that
**most boxes the guest rejects really are off-screen** - the conservative test
agrees with the original cull 95% of the time. That is what a correct culling fix
should look like; it is not evidence of a large visual win.

Two things this run could NOT establish, both for the same reason - the debug
server has no input injection, so the only gameplay available was the attract
demo, on a route nobody chose:

1. **Whether the reported edge popping is fixed.** The demo may simply never
   visit the level edges where it was seen. No visual comparison was made.
2. **A clean same-scene A/B.** `gte_verts` varied 470-2800 between legs, so the
   per-frame rates above carry real scene noise. The interleaving reduces it but
   does not remove it.

The `on:2` upper bound is the useful diagnostic here: it says this routine gates
at most ~15% more past-window geometry. If the popping persists with `on:1` in a
hand-played level, that ceiling is the argument for looking at **object
activation** (part 5) rather than tightening this test further.

### Incidental finding: `ovh_prims` is unreliable

`ovh_prims` stayed **0** through every leg - including `on:2`, where the census
independently counted ~66 past-window primitives per frame and 500+ forced
revives. `ws_note_overhang` is not seeing what the census sees. This is the same
counter whose zero reading produced the retracted part-4 conclusion, so the
retraction was right for a deeper reason than part 5 recorded: the counter is not
merely a one-frame sample, it does not appear to work. Do not use it as evidence
for anything until it is re-derived.

## Widescreen, part 7: fill the screen without widening the view

Patch 0018, plus launcher changes (tracked in git). This is the answer to the
edge popping that parts 5 and 6 could not remove: stop trying to reveal more
world, and change how the picture is PRESENTED instead.

### Why this, and not more cull work

Part 6's cull fix landed and works, but measured in-game it recovers ~5% of the
boxes the guest rejects and moves past-window geometry by ~2%. The popping
persisted in native-wide. The remaining cause is either object activation
(never measured, part 5) or simply that Crash 2's levels end where the 4:3
frustum ends - and neither is fixable from the present path.

So the goal changed: fill a 16:9 panel *without* asking the engine to draw
anything it was not authored to draw. Three dials do that, and the interesting
result is that combining them beats any one of them.

### The three dials (all in `letterbox_rect_aspect`)

Everything routes through that one function - all five present paths call it,
including `interp_present`, which matters because frame interpolation OWNS the
frame interval when enabled and returns before `present_target_quad`. A dial
added anywhere else would silently do nothing for anyone with interpolation on.

1. **Zoom** (`PSX_PRESENT_ZOOM`, 0..100). Interpolates between the largest rect
   that FITS the canvas (letterbox) and the smallest that COVERS it (fill).
   Both endpoints are special-cased to reproduce the historical rects exactly,
   so leaving it unset changes nothing. Aspect is exact at every value: this
   trades bars for crop and can never distort.
2. **Stretch** (`PSX_PRESENT_STRETCH`, 0..100). Pulls whatever mismatch the zoom
   left toward filling the canvas exactly. 100 reproduces the old all-or-nothing
   stretch mode. This is the only dial that distorts.
3. **Pan** (`PSX_PRESENT_PAN`, source scanlines of 240). Slides the zoomed
   window; POSITIVE reveals more of the TOP. Clamped to the actual overflow.

### The overscan bug this exposed

`apply_overscan_crop` trims the SOURCE rect; `letterbox_rect_aspect` sized the
destination from the aspect alone and never saw that. The surviving band was
therefore magnified to fill a rect built for the UNCROPPED frame - scaled
vertically but not horizontally. **The shipped Enhanced and Performance presets
used overscan 16/16, so they had a ~1.15x vertical stretch.**

Fixed by `overscan_aspect_mul`: fold the kept fractions into the aspect, so the
band is described as the sub-rectangle it actually is. Applied via
`present_rect_cropped` at the four paths that crop, and deliberately NOT at
`gl_renderer_present` (24-bit FMV / forced-CPU), which never crops and would
otherwise be sized for a trim it did not apply. This is distinct from the
short-display-mode case the old comment defended: there the short band IS the
whole picture and must fill the rect, which is why the fix keys on what WE
removed rather than on absolute height.

### The measured result, and why 14:9 is the setting

Measured live at 2560x1440 via the new `present_fit` command. Crash 2 renders
512x240 but only DRAWS rows 12..227 - 12 blank scanlines at each end that are
black image, not letterboxing, and that no scaling mode can remove.

Trimming those 12 lines does two things at once: it removes the bands, and it
makes the drawn content wider relative to its height, which collapses the
stretch needed to fill a 16:9 panel:

    trim   drawn aspect   stretch to fill 16:9
    0      1.556          +14%
    4      1.609          +10%
    8      1.667          +6%
    12     1.728          +2%
    16     1.795          -1%

Combined with a 14:9 render aspect the result is:

    14:9 + trim 12 + zoom 0 + stretch 100
      -> fills 2560x1440 exactly, ZERO crop of drawn picture, +2% distortion

14:9 is the useful middle because the widening is a dial, not a switch:
`parse_aspect_ratio` (config_loader.cpp) accepts anything from 4:3 to 32:9. At
14:9 the GTE squash is `[6,7]` and `x_margin` is **43**, against 85 at 16:9 -
so it reaches about half as far past the authored edge, halving the exposure
that causes the popping, while the remaining gap to the panel is small enough
for the stretch dial to close invisibly.

Full pan-and-scan (4:3 + zoom 100) also works and is genuinely distortion-free,
but costs 30 of 240 scanlines per edge - 18 of them real picture. Kept as an
option; not the default, because +2% distortion is cheaper than 17% of the
image, and because zoom cannot add field of view: zoom 0 IS the maximum view in
that mode.

### Launcher

`aspect` (what the GAME renders) and the new `output_aspect` (the shape of the
CANVAS) are now separate - they had been conflated in two places that both
rebuilt a 4:3 window whenever the render aspect was 4:3, leaving nothing to
crop: `usersettings._auto_windowed_size` and the width-without-height back-fill
in `Settings.clamp`. Both now use `Settings.canvas_aspect()`.

Enhanced and Performance are repointed at 14:9 + trim 12 + stretch 100. The
FOV-widening hack is kept as an opt-in preset ("Widescreen (wider view)") since
a genuinely wider view is a real benefit for anyone who prefers it to the
popping. `test_settings_coverage.py` caught all three new settings as orphans
until real UI controls existed - that guard did its job.

### Live tuning

`present_fit` on the debug server takes `zoom`, `stretch`, `pan` and `crop`
(symmetric overscan) and reports the resulting rect, the crop in source
scanlines per edge, and `distort_pct` - so a setting can be judged by number
rather than by eye, and the whole space explored in one session instead of one
game boot per value.

## Frame cadence: Crash 2 presents at 30 Hz. Measured, 8 windows.

Patch 0019. This is Phase 1 of `tuning/FEATURE-PLAN-60FPS-DIAGNOSTICS-CHEATS.md`
and the gate everything else in that document depends on.

### What was wrong before

The FPS readout counts guest VBLANKS (`main.cpp`, "Count simulated vblanks
rather than presents"), so it reports ~60 whether the game draws every VBlank or
every other one. `NOTES.md` already said so at :210-216 and it was still the
number everyone quoted.

The only real evidence for 30 Hz was **one** measurement in **one** scene -
:700-701, 982 world-render calls over 30 s of dark play during the Night Fight
work, and that counted render dispatches, not frames reaching the screen.

Meanwhile the launcher asserted the opposite in three places: a "guarded native
59.94 Hz title patch". **No such patch exists** - nothing calls
`psx_mod_set_native_vblank_rate` (its two state variables are write-only and it
has zero callers repo-wide), `_build/Crash2Recomp/mods/` is empty, and
`game.toml` declares no patch. Two of those comments also contradicted each
other: `runtime.py:139` said "duplicate 30 Hz frames", `:131` said "native
59.94 Hz update". Corrected in the same change; `smooth_60fps`, a launcher
setting that reached no code at all, is deleted.

### The counter that settles it

`g_display_flip_count` in `gpu.c`, incremented in `gp1_display_area_start()`
when the display base actually CHANGES. A double-buffered title flips this to
show the buffer it just finished, so it counts **new images reaching the
screen** - the one signal a static scene cannot fake. Counting presents answers
a different question (the present path runs every VBlank regardless), and
hashing pixels answers a third (identical pixels can follow a real update).

Deliberately counts changes, not writes: games re-send GP1(05h) with the same
value and that is not a new frame.

Exposed with four existing counters through the new `frame_rates` debug command,
which never conflates the five things all called "FPS":

    vblank_raise    guest VBlanks raised (cycle-paced, 564480 cycles each)
    vblank_deliver  ...actually taken by the guest as an exception
    present_bodies  host present path runs
    distinct_frames display base changed - a NEW image was scanned out
    host_swaps      SDL_GL_SwapWindow calls

`vblanks_per_frame = vblank_raise / distinct_frames` is the whole answer in one
number: 1.0 means every VBlank, 2.0 means every other one.

### Result

Eight 6-second windows across the attract demo, scene complexity 0 to 4813
GTE verts:

    #   verts   vbl/s    frames/s  swaps/s  vbl/frame  note
    1    465    60.12    29.98     29.98    2.006
    2   2976    60.03    29.85     30.02    2.011
    3   4065    59.91    26.72     26.88    2.242     dropped a few
    4      0    59.96     7.97      7.81    7.521     load / 2D screen
    5    947    59.99    29.83     30.00    2.011
    6   4813    60.05    29.94     30.11    2.006
    7   1488    59.91    29.87     30.04    2.006
    8   4725    59.97    29.99     29.99    2.000

**Crash 2 puts up a new image every other VBlank. 30 Hz, and it holds across
scene complexity** - window 6 has ten times the geometry of window 1 and the
same 2.006. Window 4 is a load/2D screen where the game legitimately updates
the display far less often; window 3 is a transition that dropped a few frames.

Two independent counters agree: `distinct_frames` comes from guest GP1(05h)
register writes, `host_swaps` from host SDL calls, and both read ~30 while
VBlanks hold at ~60.

Frame pacing itself is excellent: p50 16.683 ms, p95 16.696, p99 16.718.

### What this does NOT establish

**Render cadence is 30 Hz. Simulation cadence is still open.** The game could
update physics every VBlank and draw every other one; this counter cannot tell
the difference, and that distinction is the whole point of the feature
document's Phase 1.

Counting entries to the named frame routines does not answer it either.
`cyc_watch` on `CORE_Main 0x800117BC`, `CORE_Loop 0x80011800` and
`CORE_VSync 0x8004A864` returned **zero hits** over 6 s each during live
gameplay - these are entered once and loop internally, so there is no entry to
count. The per-frame gate is something called from inside that loop and is not
yet identified. That is the remaining Phase 1 work.

### Consequences already visible

1. **Interpolation is mis-parameterised.** `main.cpp` hands the interpolator the
   VBLANK rate as its `source_hz`. The game produces 30 distinct images per
   second, so half the "source" frames are byte-identical duplicates and the
   crossfade blends a frame against itself half the time. Fixing it means
   feeding the measured distinct-frame rate instead.
2. **`PSX_SMOOTH_60FPS` was right about the game and wrong about the fix.** Its
   duplicate-frame detection existed precisely because the guest repeats frames
   - which is now confirmed - but it is a pixel blend, superseded by the
   interpolation path, and its launcher setting reached no code. Deleted.
3. **The deadline threshold needs slack.** `frame_rates` first defaulted the
   missed-deadline budget to exactly the frame period and reported 130 of 256
   frames "over budget" on a run whose p99 was 16.718 ms. At exactly the period
   the host frame time IS the pacer period, so about half a healthy run lands
   microseconds above it. Default is now 1.5x.

### Tooling note

`frame_perf` gained p50/p95/p99 and an over-deadline count
(`gl_renderer_perf_percentiles`), kept separate from `gl_renderer_perf_aggregate`
because that function's `out[18]` is indexed positionally by its caller and
widening it would silently renumber every field.

**This is developer-only.** `gl_perf_init()` returns immediately under
`PSX_NO_DEBUG_TOOLS`, so release builds have no perf ring and no percentiles.
Any player-facing report must be built on `psx_freeze_heartbeat.json` instead -
that writer is explicitly NOT gated, runs in release, and already carries ~60
counters plus a 64-entry ring.

## Native 60 FPS: the lock is one instruction, and the engine already compensates

The earlier cadence work stopped at "the game presents at 30 Hz and a gate
ablation reaches 60 in a quiet room", with the open question being whether a
60 Hz loop would simply run the game at double speed. It does not, and the
reason is in the game's own code.

**The time base.** `0x800156A0` opens event `0xF2000002` on handler
`0x8003BE88` and calls `SetRCnt` with target `0x1000`. Root counter 2 at system
clock / 8 is 4233600 Hz, so that handler - three instructions that do
`[0x8003BEA4]++` - fires at 4233600/4096 = **1033.6 Hz**. One NTSC field is
~17.24 ticks. Every timing constant in the engine is in that unit, and they all
suddenly read as field counts: the gate's `25` is halfway between one field and
two, the deterministic clock's `+34` at `0x800168D0` is two fields, and the
forced `17` at `0x800167F4` is one.

**The lock.** `func_8001658C` always calls `VSync(0)` at `0x80016838`. The
`sltiu v1,v1,25` at `0x8001685C` can send it to a *second* `VSync(0)` at
`0x80016868` - "if the last frame was shorter than a field and a half, round it
up to two". That second wait is the entire 30 Hz lock.

**The compensation, which was there all along.** `0x80016F04` is a quantizer:
`<19 -> 17`, `<36 -> 34`, `<53 -> 51`, else pass through. `0x8001697C` stores
its result in the draw buffer at +20, and the motion sites read it straight
back - `0x8001D400`, `0x8001D9D4` and `0x8001DADC` are all
`clamp(db[20], <=102)`, then `(velocity * that) >> 10`. That is why Crash does
not slow down when a busy scene drops to 20 FPS, and it is exactly what a 60 Hz
loop needs: `db[20]` becomes 17 instead of 34, per-frame displacement halves,
and a second covers the same ground. The "no compensation field has been
verified" line in 60FPS-FINDINGS.md was wrong only in that nobody had looked
for it.

**Why the first wait stays.** With it, a frame whose work does not fit in one
field lands on the next VBlank and the engine falls back to 30 or 20 the way it
does on hardware, with `db[20]` following. That is why the shipped mode opens
only the gate. `crash2_no_wait_probe` removes both and is uncapped - it was
measured at 67 loops/s against 60 VBlanks, which is not a frame rate, it is the
game running fast.

**Why the CPU clock goes with it.** The gate-only fallback at the Turtle Woods
crates is a guest budget problem: median 646,373 and p95 672,072 loop cycles
against 564,480 per field. So the mode also raises the CPU-only clock to 125%
and leaves VBlank, CD, SPU and the timers alone - including RCnt2, or the
engine's own frame time would be measured against a different second.
`frame_rates` reports `guest_ticks_per_s` so that assumption is checked rather
than asserted.

### Consequences

- `psx_cycles.c`, `psx_cyc.h` and `overlay_loader.c` no longer hide the CPU
  clock behind `PSX_NO_DEBUG_TOOLS`. A 60 FPS setting that only worked in the
  diagnostics build would not be a feature. Overlay DLLs route through the same
  wrapper in both builds now - they were the majority of the work, so scaling
  only the main executable would have been a half-applied clock.
- `game_frame_ticks` is the field to read, not the loop rate. 60 loops a second
  with it still at 34 is double speed; 60 with 17 is 60 FPS.
- The world-speed A/B was attempted with savestates and removed. It read RAM
  after requesting a save, ran the route, then reloaded and read again - but
  the emulator keeps running between a savestate request and its completion at
  the next safe boundary, so the two "same" worlds never were, and the run
  aborted on its own sanity check. A replacement has to hold the machine still
  across each read (`pause`/`continue`) or compare each run against its own
  measured start. The compensation above is still read from the code, not
  measured in play.

## Native 60 FPS, part 2: a mode that is only 60 where 60 is actually stable

The first build of the mode was played and was not good. Measured on this
machine: **Snow Go 55-56 loops/s and stable, Turtle Woods 45-50 on average
with dips below 30**. A measured 300-frame route in the bad case read 48.49
loops/s at `speed` 1.0 and 1.236 VBlanks per new image - ~76% of frames on one
field, against Snow Go's ~93%.

Two distinct faults, and neither is the "guest ran out of budget, fall back to
30" case the design already relied on.

**1. Host overrun is not frame drop.** The claim that the surviving first
`VSync(0)` makes the mode degrade gracefully is only true of the *guest*
budget. When the *host* cannot keep up, an emulator does not drop guest frames
- guest time dilates. VBlank itself arrives late, `speed` goes below 1.0, and
the whole machine including audio runs slow. That is worse than the 30 Hz lock
being removed, and it is the "below 30" in the report.

**2. The one-field boundary is its own failure.** With the gate open a frame
costs one field if its work fits and two if it does not. A scene sitting *on*
that line alternates, and three things go wrong together: presentation
alternates 16.7/33.4 ms; the engine's compensation reads `db_prev[20]`, the
PREVIOUS frame's field count, so the motion scale lands a frame late; and the
average is a number like 47 that no display period divides. This is the 45-50.
A clean 30 is better to watch than a ragged 47, and the game locks itself to
that clean 30 without being asked.

So `crash2_60fps.h` now judges a one-second window on both - guest VBlank
against the wall clock, and the share of frames the engine simulated as one
field (>= 85%, which sits between Turtle Woods' measured 76% and Snow Go's
93%, and puts the line near 52 loops/s). A host failure drops the CPU headroom first, because the headroom is
exactly what costs the host its extra guest instructions. Anything else closes
the gate. Retry is 8 s doubling to a 64 s cap, and ten seconds of sustained 60
clears the penalty and earns the headroom back. `PSX_CRASH2_60FPS_HOLD_PCT=0`
turns the hold check off for anyone who prefers the ragged 47; it is not in the
launcher UI.

Windows with fewer than 20 loops are not judged on hold at all - a load or 2D
screen legitimately updates about eight times a second, and that is not a
failing scene.

### The other defect this turned up

`psx_crash2_cpu_clock_charge` did `numerator % pct` and `numerator / pct` on a
64-bit value, **per cycle charge** - once per basic block of guest code. That
was written for short debugtools measurements where nothing depended on its
cost. Moving the clock scale into the release build put two 64-bit divisions in
the emulator's hottest path, in the one mode whose entire problem is host
throughput. It is a 32.32 reciprocal computed once in `psx_crash2_cpu_clock_set`
now; the fractional carry still makes the long-run rate exact.

### Testing moved into the scenario runner

`gameplay_smoke.py` scenarios can now ask for the mode (`"native_60fps"`) and
assert it (`expected_game_frame_ticks`, `min_speed`, `max_backoffs`), and the
mode is set before the route and cleared afterwards whatever happens. Route
segments are consumed per guest VBlank, so a 60 Hz run and a 30 Hz run of the
same scenario still cover the same real time. `native60_sustain.json` is the
regression test for this whole section: it asserts only that the game never
runs *slow*, so Turtle Woods has to pass it by falling back to 30.

The runner also refuses a runtime that does not report the guard's fields. The
first scenario run went against a debugtools binary that had failed to relink -
the game was running and holds its own .exe - so it measured the pre-guard code
and reported it as a plain failure. A stale build answers every command; it has
to be caught by what it does *not* answer.

That run did confirm the time base from part 1 outright: `guest_ticks_per_s`
came back **1030** against the predicted 1033.6, and stock `game_frame_ticks`
**34**, which is the value the motion sites multiply by.

A separate savestate A/B script for world speed was written and deleted. It
compared a RAM window before and after reloading a state, but a savestate
request only completes at the next safe boundary and the emulator keeps running
until then, so the two snapshots were different worlds and the script failed its
own sanity check. Holding the machine still across the reads, or giving each run
its own measured baseline, is what a replacement needs.

## Native 60 FPS, part 3: it lands, and most of the win was a division

`native60_hold.json` passes: **59.9 loops/s, 59.9 new images/s, 1.000 VBlanks
per frame, 100% of frames on one field, `game_frame_ticks` 17, `speed` 0.999,
`guest_ticks_per_s` 1029.95**, host frame p95 20.8 ms with nothing over budget.

The instructive part is where that came from. The same 300-frame route measured
**48.49** loops/s before the cycle-charge fix and **59.9** after. The guard did
not do that - the guard only decides between 60 and a clean 30. Removing two
64-bit divisions from `psx_crash2_cpu_clock_charge`, which ran once per basic
block of guest code, was worth about eleven frames a second on its own. The
lesson is narrow and worth keeping: a helper written for a debugtools
measurement has no performance budget attached to it, and moving one into the
player path without re-reading it is how a feature ends up blaming the wrong
thing. The first diagnosis here was "the host cannot do 2.5x the work", and
part of the host's work was arithmetic this code had no reason to be doing.

`game_frame_ticks` 17 at 59.9 loops/s is also the engine-side confirmation of
part 1: the quantizer at `0x80016F04` is emitting one field, which is the value
`0x8001D400`, `0x8001D9D4` and `0x8001DADC` multiply velocity by. World speed
is now read from the code AND confirmed by the number the code uses; only the
end-to-end displacement A/B is outstanding.

### Demo playback is suspended, not backed off

`0x8006CD14` is the engine's timing mode, and its writers name it: 2 for demo
playback (`0x8002FD10`, which sets the recorded stream up on the next
instructions), 3 for recording (`0x8002FF34`), 4 when a demo ends
(`0x80015D60`), 0 for live play (`0x80015D90`, `0x8001F9EC`, `0x8002FCDC`). In
playback the clock is taken from the recording at `0x80016940` instead of the
root counter, so the loop rate decides how fast the recorded timeline is
consumed. The gate is therefore held closed while that word is non-zero.

Deliberately a suspension and not a fall-back: no back-off counted, no retry
delay, and it returns the instant live play does. Reported separately as
`native_60fps_demo_mode`, because an attract loop counted as "this scene could
not hold 60" would send someone chasing performance that was never the problem.

### The world-speed A/B, second attempt

`fps_speed.py` is back, built on the constraint that killed the first one: this
framework has no pause - `handle_pause` answers "pause is removed; query a ring
buffer instead" - so nothing can hold the machine still across a RAM read, and
a savestate is applied at the next safe boundary with the emulator running
until then. Demanding a shared starting world was therefore never satisfiable.
It now measures each leg against its own baseline with the same settle on both
sides, and compares travel, not positions. A few frames of drift against a
four-second route is noise once the ratio is taken over many words.

## Assists, part 2: the collision branch is narrower than it looked

The "candidate collision bypass" at `0x8001CE34` had been carried as unshippable
since it was first found, on the grounds that it might also suppress pickups and
crate breaks. Reading the whole basic block shows why that fear was misplaced,
and it is a good lesson in how far a partial disassembly can mislead.

`func_8001CC10` is the GOOL conditional state-change entry: event -> state, read
that state's entry-block mask, refuse the transition if the object's status word
already carries any of those bits. Looking only at `0x8001CE24-50` - which is
what the first pass did - it reads as a generic collision-mask test, and forcing
the branch looks like it would change every collision the function handles.

The line that changes the picture is four instructions earlier. `0x8001CE0C`
loads the object pointer at `0x8005F38C` and `0x8001CE1C` is `bne s0, a0`:
**everything below it runs for that one object only.** For it, the game reads
`obj+0x108`, a timed invincibility mode, and ORs `0x1002` into the status word
when the mode is 2, 3 or 4. The mode is demonstrably timed - `0x8001C0A8` is
`sltiu v0, v0, 61`, the mercy frames after a hit, and `0x8001C0BC` times mode 3,
the gold Aku Aku mask.

So the branch asks one question: *is Crash invincible right now.* Forcing it
makes that predicate permanently true for the player alone. Anything the gold
mask permits, this permits; the only difference is that it does not lapse after
fifteen seconds. That is a far smaller claim than "bypass collisions", and it is
checkable - which is why it now ships as the Damage -> *No damage* level.

The residual risk is the honest one and it is in the launcher text: an event the
gold mask blocks harmlessly for fifteen seconds is blocked indefinitely here, so
a scripted mount or vehicle sequence that needs one could stall. Only play
settles that.

**A data-side alternative was designed, approved, and then withdrawn.** Holding
the global Aku byte at 3 reaches the *same* predicate - but it drags the gold
mask's audio, HUD and contact-kill behaviour with it, and writes a field the
engine's own mode machine owns. One word of code, reverted on demand, turned out
to be the *less* invasive of the two. Worth remembering: "data-side" is not a
synonym for "safer".

### `0x8003ED04` was never progression code

`CHEAT-MAPPING.md` justified the level-shadow mappings with a write trace that
named `0x8003ED04` as game code restoring the Aku shadow. It is inside the
polygon/display-list builder: `swc2` GTE stores into a primitive at `s7`, packet
headers built with `lui 0x3400` / `0x3600` / `0x0900`, and `0x8003ED04` itself is
`sw a1, 24(v0)` with `a1` freshly masked to 24 bits by `sll a1,s7,8` /
`srl a1,a1,8` - the ordering-table tag link. The renderer was writing through the
address.

Both level shadows are marked unconfirmed now, and the shipped assists write only
the globals. A write trace that reports a PC is reporting *a* writer, not the
writer you were looking for.

### The assists now suspend like the 60 FPS gate

Writes are gated on the timing mode at `0x8006CD14`, the same word the frame gate
uses. Writing 99 lives into an attract-mode demo - a recorded input stream
replayed against the simulation - was possible before and is not now. The
no-damage patch is restored on entering a demo rather than merely left alone, for
the same reason: an invincible Crash desyncs a recorded run.

### Two dead branches in the 60 FPS guard, and a verdict worth exporting

Patch `0028` made every failed window close the gate, which quietly orphaned two
things. The CPU restore on the sustained-good path could no longer fire, because
every reopen already sets the clock. The `wait > RETRY_MAX` clamp could never
fire either: the shift is capped at 3 and `8000 << 3` is exactly `RETRY_MAX`.
Both are gone; the ceiling now lives in the shift, with the constant kept as
documentation of the result.

More useful: `FAIL_HOST` and `FAIL_HOLD` were handled identically and never
exported, so a report could not distinguish "your machine is behind the wall
clock" from "this scene alternates between one field and two". They want
different answers - more host headroom versus accepting a clean 30 - so the last
verdict is now reported by both `frame_rates` and `crash2_60fps`.

## Assists, part 3: patching hot guest code is not free

The no-damage assist shipped as an instruction patch on `0x8001CE34` and was
measured broken within minutes: **8.4 seconds without a heartbeat, starvation
watchdog abort**, with the ring dump containing nothing but the BIOS pad driver
spinning at `0xBFC21xxx`.

The mechanism is not in the cheat at all. `psx_mod_write_code_word` calls
`dirty_ram_mark_executable_range` (`memory.c:590`), which sets the dirty bit for
the **whole page** containing the address - and this runtime deliberately
dispatches dirty pages through the MIPS interpreter instead of the compiled
image. `0x8001CE34` lives inside `func_8001CC10`, the GOOL conditional
state-change entry, which runs for every event on every object. Dropping it to
the interpreter collapses the frame rate; everything else, the pad driver
included, then times out.

The 60 FPS gate does exactly the same thing at `0x8001685C` and has never shown
a problem, because that page holds `func_8001658C` - the frame finalizer, once
per frame. **Patching cold guest code is cheap here. Patching hot guest code
costs the whole page's native execution.** That distinction was not in any of
the reasoning that led to the patch, and it is the one that mattered.

The assist now holds `player+0x108` - the same invincibility mode the branch
reads - as a data write, restamping `player+0x10C` from the tick at
`0x8006CB64` so the 452-frame expiry never arrives. Modes 1, 2, 5, 6 and 7 are
left alone because the engine's own mode machine owns them. No code is
modified, so no page is marked dirty and nothing leaves the compiled image.

Worth keeping: a code patch and a data write look equally "small" in a diff, and
here they differ by an order of magnitude in cost. `crash2_damage_probe` still
exists and still patches the word - that is fine for a measurement, and its
comment now says why it is not a feature.

### The release tree is not a release build

Chasing the above turned up something else. `PSX_NO_DEBUG_TOOLS` appears **zero
times** in `build-clang/build.ninja` - and zero times in `build-debugtools` too.
`runtime.cmake:1341` defines it under `if(NOT PSX_DEBUG_TOOLS)`, and
`build_clang.ps1` passes `-DPSX_DEBUG_TOOLS=OFF` for the release tree, but the
definition is not reaching the target. Both trees compile with the debug tools
in.

That is why the run log says `debug server LISTENING on 127.0.0.1:4370` from
`build-clang`, and why the starvation watchdog - itself a debug tool - was able
to abort a player build at all. It also means earlier claims in this file about
things being "compiled in but dead in release" were wrong: they are live.

Not fixed here, because turning it on changes what every release measurement so
far was measuring. It needs its own change and its own re-baseline.

## 60 FPS, part 4: the guard was most of the instability

Reported as "native 60fps is not stable". It was, and the biggest single cause
was the sustain guard closing the gate on **one** bad window.

A crate cascade, an explosion or a room transition is one bad second. Under the
old rule that cost the whole level 8-64 seconds of 30 Hz, then a retry, then
often the same thing again - so a scene that could mostly hold 60 flapped
between 60 and 30 instead. The guard was built to stop the ragged 45-50 regime
and it did, but it was tuned as though every bad second were evidence about the
scene rather than about that second.

What changed:

- **Two consecutive windows before a hold failure closes anything.** Host
  failures still act immediately - they mean the machine is already running
  slow, audio included, and a second opinion costs another second of it.
- **A hold failure now buys guest headroom first.** "Host fine, hold failing"
  is exactly "the guest cannot fit its frame in one field", and guest clock is
  the one lever that addresses it. It jumps straight to 150% rather than
  laddering 135/145/150: the question is whether more clock fixes this scene,
  and three extra ragged seconds discovering it does not is the wrong trade.
  Any close restores 100% immediately, so a host overrun caused by the extra
  clock self-corrects within one window.
- **A penalty is only forgiven by a comfortable window** (92% against an 85%
  line). Without the gap a scene sitting on the threshold alternates fail/pass,
  banks credit between failures, and retries forever - flapping by
  construction.
- **Loads and 2D screens became their own verdict, C2_60_QUIET.** They were
  silently counted as OK, which let a long load bank credit a scene had never
  earned. They now bank nothing, and since whatever follows a load is a
  different scene, they clear the penalty and release the latch.
- **After four back-offs the retry timer stops.** A level that has failed four
  times is not going to start holding 60 because a clock expired, and each
  retry costs a second of the ragged regime. The next load releases it.

The shape of the lesson: a stability guard that reacts to single samples is not
a stability guard. Every one of these is hysteresis of some kind - a streak
before acting, a margin before forgiving, a latch before retrying - and the
first version had none of them.

## 60 FPS, part 5: retuned for smoothness, and one change reverted

Reported as "runs terrible, fps drops, lots of stutters" - after part 4, so the
first question was what part 4 broke.

**The CPU escalation was a mistake and is gone.** Part 4 added an automatic
jump to 150% guest clock on a hold failure, reasoning that "host fine, hold
failing" is a guest-budget shortfall and guest clock is the lever for it. The
logic holds; the premise did not. A measured sweep on this machine had already
found no trustworthy improvement from a higher clock, and that result was in
hand before the escalation was written. Worse, the escalation fires in exactly
the scenes where the host is closest to its limit, so it adds 50% more emulated
work per wall-clock second at the moment there is least room for it. It
generated the stutter it was meant to cure.

`native_60fps_cpu_percent` now defaults to **100**. The cycle measurement that
justified 125 (p95 672,072 against a 564,480-cycle field) is still true and the
knob is still there, but a number derived from guest cycles says nothing about
whether the host can afford them.

**The acceptance threshold was far too lenient.** `C2_60_MIN_HOLD` was 85,
which accepts one frame in seven being doubled - several visible hitches every
second - and then reports the scene as holding 60. That single constant is
probably most of what "lots of stutters" was. It is 95 now, with the forgive
margin at 99. Scenes that cannot keep nearly every frame on one field get a
clean 30 instead, which is steadier than a ragged 56 because a rate no display
period divides never looks smooth however high its average.

**Closing is fast again, retrying is slow.** Part 4's two-window streak made
each bad episode last twice as long. The flapping it was aimed at is better
solved at the other end: close on the first bad window, then wait 20 s (then
40 s) and latch after two failures. Total visible ragged time per level drops
from repeated multi-second cycles to about one second.

The general shape, worth keeping: **for a smoothness guard, the cost of being
wrong is asymmetric.** Being slow to close is paid in visible judder every
time; being slow to re-open is paid in a frame rate that is merely lower. Tune
the two ends differently.

## 60 FPS, part 6: the CPU clock was only half connected

Asked for 60 "stable, constant, almost the entire game". The binding
constraint was never the guard - it is the guest cycle budget. The crates
measurement (p95 672,072 against a 564,480-cycle field, 1.19x over) says the
game needs roughly 20% more guest work per field in busy scenes, and the
CPU-only clock is the only lever that provides it.

That lever was mostly disconnected. `psx_cyc_charge` scaled instruction
charges, but two CPU-side costs call `psx_advance_cycles` directly and were
never scaled:

- `psx_icache.c:87,103` - instruction-cache refill, 4-7 cycles per miss,
  active by default (`PSX_ENABLE_BLOCK_CYCLES=1`, `PSX_ICACHE` defaults on).
- `memory.c` `psx_cyc_readmem` - the load completion cost and the fudge.

In MIPS code, icache misses and loads are a large fraction of a frame. So
"125%" was charging full price for a big share of the budget and delivering
far less than 25%. **The sweep that concluded "higher clock does not help" was
measuring a knob that was mostly disconnected** - a reminder that a negative
result about a control is only as good as the control.

Both now go through `psx_cyc_cpu_side` in `psx_cycles.h` (chosen because it is
the one header both `memory.c` and `psx_icache.c` already include).
Device-region waits are deliberately excluded: SPU/CDC/MMIO timing is the
devices' own, and root counter 2 - the clock the engine measures frame time
with - must not move, or every conclusion in 60FPS-FINDINGS.md is void.

The load path needed care. `cost = region + compl_cost` is also stored in
`cpu->ld_absorb` and given back later, so the completion is scaled **once** and
the same value used for both the advance and the absorb; scaling one and not
the other would leak cycles. At 100% the helper is the identity, so stock
timing is bit-for-bit what it was and only the 60 FPS mode is affected.

`native_60fps_cpu_percent` goes back to 125, which is what the original cycle
analysis asked for and what should now actually be delivered.

### What this does not promise

It does not make 60 unconditional. The guard still measures each second and
still hands a scene back its 30 Hz lock when it cannot hold a steady cadence,
and that is deliberate - a rate no display period divides looks worse than a
clean 30 whatever its average. What changed is that scenes previously failing
by a margin the clock should have covered now have the headroom the
measurement always said they needed.

## 60 FPS, part 7: the guard reported itself enabled while running at 30

Reported as "60 fps does not work, it still runs at 30". The heartbeat from
that run settled it in one read:

```
native_60fps            1     mode on, env reaching the runtime
native_60fps_gate_open  0     gate CLOSED
game_frame_ticks        34    engine simulating 30 Hz steps
```

So nothing was broken in the plumbing. The guard was refusing to open, and the
reason was the previous round's threshold change.

`C2_60_MIN_HOLD` had been raised 85 -> 95 to chase smoothness. That number was
a guess about what "smooth" needs, and the only two measurements this project
has say it is in the wrong place:

| Scene | one-field share | how it was described |
| --- | ---: | --- |
| Turtle Woods, ragged | ~76% | "lots of stutters" |
| Snow Go | ~93% | "stable 56 fps" |

95 rejects both. Combined with a latch that gave up permanently after two
back-offs, the gate shut for whole levels and the mode advertised itself as
enabled while delivering 30. It is 80 now - just above the bad case, well
below the good one - with the forgive margin at 90 and the latch at four
failures rather than two.

Two lessons, both about method rather than about this game:

- **A tightened acceptance threshold can present as a total feature failure.**
  "60 FPS does not work" and "the guard is working exactly as configured" were
  the same state. Nothing in the symptom pointed at a constant.
- **Tune to the measurements you have.** 85 was derived from measured scenes;
  95 was derived from reasoning about frame doubling. The reasoning was sound
  and the number was still wrong, because it ignored that a partly-doubled 56
  beats a clean 30 for this game.

### The heartbeat now says why

A closed gate in a report used to be indistinguishable from a mode that never
engaged. `freeze_heartbeat.c` now also emits `native_60fps_verdict` (0 ok,
1 host behind, 2 cadence, 3 too few loops to judge),
`native_60fps_one_field_pct` and `native_60fps_backoffs`, all allow-listed in
`diagnostics.py`. That is the difference between reading the next diagnosis
and guessing it.

## 60 FPS, part 8: editing a runtime header silently disabled native overlays

Reported as "runs 60 then drops to 30" plus a crash. The runtime's own counter
disagreed with the player: `[FPS] game: 59.9 fps (1.00x)` for hundreds of
consecutive lines, right up to `starvation_watchdog: 5119701 us without
heartbeat - aborting`. The guest loop was at 60 the whole time. What collapsed
was the host.

The cause was in the same log:

```
WARNING: overlay autocompile has failed 3 consecutive runs (last exit 1).
  Nothing is being compiled to native code, so overlay execution stays in the
  interpreter and frame times will be far worse than this build is capable of.
FATAL: STALE RECOMPILER BINARY.
  built from emitter sources hashing 649c6245, but the runtime
  tree stamps cache tag hash 3d2b5a19.
```

Patch `0030` edited `psx_cycles.h`. That file is listed in
`codegen_hash_sources.cmake`, so touching it moved the runtime tree's codegen
tag from `649c6245` to `3d2b5a19` and left `_build/build-recompiler/
psxrecomp-game.exe` stale. `compile_overlays.py` then refused to emit shards -
correctly, that guard exists to prevent the silent stale-shard class - so every
streamed level function fell back to the MIPS interpreter. Performance decays
as more overlay code is touched, and eventually the host misses the heartbeat
for five seconds and the watchdog kills the process.

`_build/build_recompiler.ps1` exists precisely for this and its header says so:
"patch 0002 edits cpu_state.h and psx_cycles.h, both of which are in
codegen_hash_sources.cmake". Running it puts both sides on `3d2b5a19`. The
shard cache from the old tag was cleared too, since those entries would be
rejected and re-emitted anyway.

**The rule this produces: any edit to a file in `codegen_hash_sources.cmake` -
`psx_cycles.h` and `cpu_state.h` among them - must be followed by
`_build/build_recompiler.ps1`, not just `build_clang.ps1`.** Nothing in the
normal build fails when you skip it. The runtime builds, links and runs; it
just quietly stops using native overlays, and the only symptom is that it gets
slower than it should be. That is the same failure class the packaging script
guards against for release bundles, reached from the development side instead.

### Why the player's counter and the runtime's disagreed

Worth keeping straight, because it sent the first diagnosis in the wrong
direction. `[FPS] game:` counts guest frames and reported a steady 59.9 at
1.00x speed. The NVIDIA overlay counts host presents. With overlays
interpreted, the guest kept its cadence while the host fell further behind
until it stalled outright - so the two numbers describing "the frame rate"
genuinely disagreed, and only one of them was about the thing that was broken.

## 60 FPS, part 9: stop guessing the threshold, expose the choice

Three rounds were spent moving `C2_60_MIN_HOLD` (85 -> 95 -> 80) on reasoning
rather than data, and each time the player's answer was "still 30". The
telemetry could not settle it because of a defect in the telemetry itself:
`native_60fps_one_field_pct` reports the CURRENT window, and once the gate has
closed every frame is two fields, so it always reads 0. Every report said
"verdict 2, 0%" regardless of whether the scene missed by 5% or by 50%.

Fixed: `c2_60_back_off` now latches `native_60fps_fail_pct` and
`native_60fps_fail_loop_hz` - the failing window's numbers - alongside
`native_60fps_cpu_now`, the clock actually in force. That last one matters
because the clock is only applied while the gate is open, so a report showing
100 with the mode enabled means the headroom never got a chance to act.

**And the threshold stopped being mine to guess.** `native_60fps_fallback` is
a launcher setting with two values:

- `smooth` (default) - an unsteady scene is handed back its 30 Hz lock.
- `always60` - exports `PSX_CRASH2_60FPS_HOLD_PCT=0` and
  `PSX_CRASH2_60FPS_FORCE_GATE=1`, so diagnostics still identify cadence or
  host failures but neither silently restores the 30 Hz gate.

That second variable fixes a contradiction in the first launcher version: the
label said "never drop", while the host-overrun guard could still close the
gate after one slow second. `smooth` retains host protection, with a two-window
streak so one startup compilation hitch is not treated as sustained overload.

This is the right shape for the problem. Whether a partly-doubled 56 beats a
clean 30 is a matter of taste about a specific display and a specific scene,
and three rounds of evidence say it cannot be settled from here. What can be
settled from here is making both available and saying plainly what each does.

### A self-inflicted false alarm worth recording

Between two of those rounds the shard cache was cleared (correctly - the
entries carried the pre-0030 codegen tag and would have been rejected) but not
rebuilt. The next play session therefore started with an empty cache, ran
every overlay interpreted, and produced exactly the symptom the clear was
meant to fix. Clearing a cache and repopulating it are one operation, not two;
`compile_overlays.py` rebuilds from the surviving captures in seconds and
should have been run in the same breath.

## 60 FPS, part 10: it was the internal resolution all along

The first run with working failure telemetry ended the search:

```
native_60fps_gate_open      1     gate open
game_frame_ticks           17     engine simulating 60 Hz steps
native_60fps_one_field_pct 87
native_60fps_fail_pct      98     <- the window that backed off
native_60fps_cpu_now      125
host_swap_count          2580  vs  frame_count 3823
```

**A window with 98% of its frames on one field triggered a back-off.** No
cadence threshold rejects 98%, so those back-offs were `FAIL_HOST` - the
emulator falling behind the wall clock - not `FAIL_HOLD`. The guest was never
the problem. It was holding a near-perfect 60 Hz cadence while the host failed
to keep up, which the swap count confirms: 2580 presents against 3823 emulated
VBlanks.

The cause is in the player's settings, not in this code: `supersampling: 5`.
The renderer draws at five times native in each dimension - twenty-five times
the pixels - and every VRAM copy, upload and readback scales with it. Running
the game at 60 instead of 30 then doubles the rate of all of it. The log said
so from the very first report (`GL GPU pipeline ready (internal scale 5x`) and
it took ten rounds to read it, because every round was spent looking at the
guest.

Three rounds of threshold tuning, a CPU-clock rework and a fallback setting
were all aimed at a guest that was already doing its job. The CPU-clock fix
(0030) was a real defect and stands on its own; the threshold churn was not.

### The lesson

`FAIL_HOST` and `FAIL_HOLD` were separated and exported precisely so this
distinction could be read, and then the first several diagnoses still assumed
cadence. The reporting has to say which one fired **in the window that
failed** - `native_60fps_fail_pct` is what finally made it obvious, and it
only exists because the earlier field reported the current window and was
therefore always 0 once the gate closed.

When a performance guard fires, the first question is which of its conditions
tripped, not which threshold to move.

## 60 FPS, part 11: host fixed, guest budget is what is left

After the player dropped internal resolution 5x -> 2x:

```
native_60fps_backoffs   0     the guard never fired
native_60fps_verdict    0     no host failure - the host keeps up now
game_loop_count/frame_count = 0.767
```

That ratio is the whole diagnosis. With `always60` the gate never closes, so
the loop runs at whatever cadence the guest manages, and
`loops/VBlank = 1/(2 - f)` where `f` is the fraction of frames fitting in one
field. 0.767 gives **f = 0.70**: seven frames in ten fit, three take two
fields. Alternating like that is exactly "average looks like 46, 1% lows are
30, feels stuttery".

So the host is no longer the constraint and the guard is not intervening. What
remains is the original, measured fact: this game's busy frames need more
cycles than one NTSC field provides, and the only lever is the emulated CPU
clock.

It was capped at 150 and had **no UI control** - it was on the
`test_settings_coverage.py` NOT_IN_UI allow-list as "follows native_60fps",
which was true when it was a fixed tuning constant and wrong once it became
the deciding knob. The cap is 200 now (`C2_60_CPU_CAP`) and there is a combo
on the Performance page.

Rough arithmetic for anyone tuning it: if a fraction `f` of frames fit at
clock `c`, the frames that miss need somewhere above `c / f` to fit. At
f = 0.70 and c = 125 that points at roughly 175, which is why the ladder goes
to 200 rather than stopping at 150.

**If 200 is not enough, constant 60 is not reachable this way for that scene**
- the guest would need more than twice the PS1's cycles per field, and at that
point the honest answer is `smooth` mode and a clean 30.

## 60 FPS, part 12: 200% default and overlay quit

Project policy now starts native 60 at the runtime's existing 200% virtual-CPU
ceiling. This changes only the emulated PS1 CPU budget; it does not overclock
the physical CPU, and CD/SPU/timers keep their original clock. The launcher
still exposes lower values for machines where extra guest work makes the host
fall behind.

The Home overlay gains `QUIT GAME` after Restart. Both destructive actions use
the same two-press confirmation state, moving away disarms it, and Quit calls
the established orderly shutdown path so memory cards and capture data are
flushed and background compilation is joined before process exit.

## 60 FPS, part 13: one launcher switch

The fallback and virtual-CPU selectors were removed from the launcher. The
single `60 FPS game updates` checkbox now has one unambiguous meaning: 200%
virtual PS1 CPU, hold threshold disabled, and the 30 Hz gate forced open.
`native_60fps_cpu_percent` and `native_60fps_fallback` were removed from the
settings model, so stale values in an older JSON file are filtered out rather
than silently changing the checkbox's behavior.

## 60 FPS, part 14: "200%" was not 2x

Part 11 left one lever - the emulated CPU clock - and part 12 set it to the
200% cap. Nobody measured whether 200% closed the gap, and reading the cost
model says it could not have: the overclock was still only partly applied.

Patch 0030 scaled instruction charges, icache refills and load completion.
Three CPU-side costs still ran at 1x:

    main-RAM read wait   3 cycles per load   memory.c psx_mmio_read_wait
    GTE command latency  RTPT 22, NCDT 43... psx_cycles.c psx_gte_set
    MULT/DIV latency     14/10/7, DIV 37     psx_cycles.c psx_muldiv_set

The first is the big one. The PS1 has no data cache beyond the 1 KB
scratchpad, so nearly every load in a frame is a main-RAM load, and 0030 had
lumped RAM in with the genuine device waits. The GTE case is worse than
"not faster": the instructions between a command and its read advance the
clock by half as much at 200%, so the read stalls for MORE of the fixed
latency. Beetle arms all three in its CPU timestamp domain, where its own
overclock shortens them. Patch 0033 makes them scale; SPU, CDC, GPU, MDEC,
DMA and the timers keep their timing, and at 100% nothing changes.

### The fix had a consequence that had to ship with it

PsyQ's `v_wait` (0x8004A5CC), which every `VSync` waits in, times out on loop
ITERATIONS: `t = timeout << 15`. On expiry it prints "VSync: timeout" and
calls `ChangeClearPAD(0)` and `ChangeClearRCnt(3, 0)` - the latter changes
how the kernel acknowledges VBlank for the rest of the session. Per-iteration
cost from the model:

    stock 100%            ~26 cycles   32768 iterations ~1.5 fields
    200%, before 0033     ~19          ~1.10  (knife-edge)
    200%, after 0033      ~13          ~0.76  fires if a frame ends in <24%

The game also calls `VSync(60)`, `VSync(20)` and `VSync(5)` in transitions,
whose first wait takes `(n-1) << 15`. So while the clock is raised
`crash2_60fps.h` widens both argument words - `0x8004A510` n-1 -> 2n and
`0x8004A534` 1 -> 2 - inside `VSync`'s own range. `v_wait` is a separate
function and stays native; a code write inside it would have sent the spin
loop, where the idle half of every field is spent, to the interpreter.

The other two timeout strings in the executable were checked and are not
clock-sensitive: libgpu's `get_alarm` times out on VBlank count (or 983,040
passes of a loop that itself calls `VSync(-1)`), and "intr timeout" counts
2,048 interrupt dispatches. The memory card was checked too, because a
faster CPU is exactly what broke MMX6's card poll: the game's only card loop
is a pure `TestEvent` spin with no counter, and OpenBIOS has no card timeout.
Save and load at 60 are still on the test list.

### What it does not do

This is not measured in play yet. It removes the reason 200% could not reach
2x; whether 2x is enough for Turtle Woods is what `native60_hold.json`
answers. Anything a frame spends waiting on a real device - SPU register
access, CD, DMA - is still outside the clock's reach by design. And the
launcher's fallback selector, reintroduced by mistake after part 13 had
removed it, is gone again; the launcher also stopped setting `HOLD_PCT=0`,
which had made a scene alternating between one and two fields report "ok".


## 60 FPS, part 15: the scripts ran at double speed

Reported in play with the gate open: animations and moving platforms ran twice
as fast, while Crash's own movement was right. Part 1's claim that the engine
"already scales motion by measured time" was only half true.

### What is scaled and what is not

Every reader of the frame tick count (draw buffer +20) is physics:
`0x8001D63C` and the helpers only it calls (`0x8001D2C4`, `0x8001E3DC`,
`0x8001E544`), plus `0x80019F08`, which copies it to the scratchpad for the
GOOL interpreter. That is a scan of the whole executable for loads through the
draw-buffer pointers, and a scan of every sector of the disc's user data for
native code doing the same - none outside the executable. The Crash 1 port
(wurlyfox/c1, `GoolObjectUpdate` / `GoolObjectPhysics`) has the same shape:
physics multiplies by `ticks_per_frame`, the interpreter never does.

GoolObjectUpdate (`0x8001C718`) was written for one call per 34-tick frame,
and four things in it happen once per CALL:

    trans block          0x8001C930  runs every call
    code block           0x8001C974  resumes when frames_elapsed - stamp >= wait;
                                     wait 0 (same as 1 at 30 Hz) = every call
    stall countdown      0x8001C8B8  obj+228, once per call
    pad read             0x8001C750  0x800158E0 from Crash's update

and three per-loop values feed the scripts:

    0x1F800054   clamped ticks, refilled by 0x80019F08 every loop; read by the
                 GOOL opcodes at 0x8003A530 (b + a*ticks >> 10) and 0x8003A758
                 (angle seek) - these were RIGHT at 60 before: 17 per call x 2
    draw_count   0x80060944, +1 per loop in the finalizer: world texture
                 animation (0x80011CA4 -> 0x80041E5C), the draw_count % 128
                 wave in 0x80017BC4, object texture phase (0x8001AFD4), colour
                 cycling (0x8001C198), the (a + draw_count) % b opcode
                 (0x8003A5E8), flicker (draw_count & 1 at 0x8001CB34)
    pad tapped   pad[i]+36 (0x80069944 for pad 0) - also tested for START by
                 the main loop at 0x80011848, every loop

### The fix: scripts at the stock step, physics every field

A loop is a script step once 34 ticks have passed since the last one - every
loop at 30 Hz, every other one at 60, straight away after a two-field frame.
Nine opcode-verified words (game.toml `[[recompiler.patch]]` `c2-60-gool-*`)
make the four per-call things depend on a flag the runtime stores at sp+60 of
the function's 64-byte frame, its unused padding word:

    0x8001C73C/40  crash pointer compared against comes from the flag (0 = no pad read)
    0x8001C898     t0 = flag, in a delay slot
    0x8001C8A8     t2 = trans pointer, early, in a load-delay nop
    0x8001C8BC/C4  stall countdown subtracts (flag != 0) instead of 1
    0x8001C930-38  flag 0 -> 0x8001C9D4 (0x8001BFDC + physics), else the
                   original trans test on t2

The flag is the crash pointer (1 if none) on a script step - exactly the value
the original compared against - so with the mode off the function is the
original. `mod_function_entry_funcs = ["0x8001C718"]` routes the entry to
`c2_60_gool_entry`, which writes it before the first instruction. Guest RAM
keeps the disc's words: the text-image guard compares RAM to the disc image,
so the patched function stays native.

Around it, in `crash2_60fps.h`: the scratchpad ticks are rewritten with the
ticks the step covers when `0x80019F08` returns (`0x8001C2F0`, `0x8001DF8C`);
a physics-only field takes back the finalizer's draw_count advance; the pad's
tapped words are hidden for that field and restored before the next read (so
START is seen once and "tapped last time" still works); and FIRST_FRAME,
which the function clears after physics, is put back on a physics-only field
so an event-driven state change still reaches the next script step.

Not done and why: a runtime code-word patch would send the object update -
every object, every loop - to the dirty-RAM interpreter; skipping whole object
updates on alternate fields would put everything back to 30 Hz motion;
interpolating from 30 Hz logic needs a second render pass per logic frame.

### Shipping it

`psxrecomp.exe build` writes a fresh game.toml and translates from it, so the
profile lives in `launcher/crash2launcher/recompprofile.py`: the Setup page
re-applies it and runs our `psxrecomp-game` again before compiling, and the
workspace game.toml carries exactly what it writes. The Play page warns when a
project's game.toml lacks it. The runtime only paces once the entry hook has
fired - the proof the generated code is the patched build - so an old project
keeps the old behaviour rather than getting the tick override alone.

The profile moves the overlay config hash (7399b41b -> d6362e32). Both trees'
caches were repopulated in the same step from the 358 unique stored captures
(5 shards). Separately, this is the first release build without debug tools:
build_clang.ps1's quoting fix only takes effect on reconfigure, and the release
tree's CMake cache had still held the literal `$DebugTools`.

### What to check in play

- `native_60fps_script_hz` in the heartbeat must read ~30 with the gate open
  (~60 is the old double speed); `native_60fps_script_hooked` must be 1.
  `tuning/scenarios/native60_script_rate.json` and `stock30_baseline.json`
  assert `expected_script_hz` [27, 33]. `PSX_CRASH2_60FPS_SCRIPT_RATE=0` is the
  A/B switch.
- Motion scripts set directly - path-following platforms - now steps at 30 Hz:
  right speed, 30 Hz cadence under a 60 Hz camera. Physics-driven motion stays
  at 60. Interpolating script-set positions across the physics-only field is
  the follow-up if that reads as judder.
- Physics clears its per-step collision bits (mask 0xFACA207E at 0x8001DD24)
  on every call, so a script step sees the latest physics result, not the union
  of both fields'. Landing is time-stamped (obj+272) and is not lost; a
  momentary contact bit could be.
- The camera update and other non-script per-loop code still run per field:
  catch-up limits converge sooner. Smoother, not faster gameplay.
- Gravity integrates in two 17-tick steps instead of one 34-tick step, which
  with the engine's integration raises a jump's apex slightly; the stock
  engine has the same dependence on frame time, in the other direction, when
  it drops frames.

## 60 FPS, part 16: the 1% lows

Reported after part 15: 60 FPS "seems stable" but the 1% low sometimes drops
far enough to cause pacing hitches and slowdowns. The session's heartbeat and
run report (build-debugtools, 24 Sep):

    native_60fps_one_field_pct   100     every frame fitted one field
    native_60fps_script_hz        29     part 15 working
    vblank_raise_count        11,083
    host_swap_count            9,830     host fell short of VBlank
    overlay disp native/interp 2.32M / 2.67M
    display                    2560x1440 @ 280 Hz (RTX 5070)

The guest is not the problem. Three host-side costs were found, one of them
periodic by construction.

**Rewind stalled on the GPU four times a second.** Rewind is on by default
("short" in the launcher) and captures a full snapshot every 15 VBlanks. Its
VRAM went through `gr_vram_transfer_out` -> `ensure_cpu()`: flush the batches,
pack, then a synchronous `glReadPixels` of all of VRAM into client memory,
which makes the CPU wait for the GPU to finish everything queued. On the
emulation thread, one frame in fifteen - a pattern that lands squarely in a 1%
low. Patch 0037 queues the same read into a pixel-pack buffer with a fence,
stores the snapshot at once with a zero VRAM payload, and fills it a frame or
two later; the snapshot is listed, and its thumbnail made from the downloaded
bytes, only then. Heartbeat `rewind_async_captures` / `rewind_sync_captures`
show which path each capture took.

**The emulation thread ran at normal priority.** Emulation, rendering and
pacing are all on the main thread, so any busy process could preempt it for a
scheduler quantum - an isolated spike. 0037 raises it to
`SDL_THREAD_PRIORITY_HIGH` (`THREAD_PRIORITY_HIGHEST`); the pacer sleeps on a
high-resolution waitable timer, so it does not become a busy core.
`PSX_THREAD_PRIORITY=0` opts out.

**The session ran the debug-tools build.** Developer mode with a debug port
set makes the launcher run `build-debugtools` (per-block tracing, TCP server).
That is the player's setting, not a code change: clear the debug port in
Advanced for normal play.

Found and not changed, with the reasons:

- Over half the overlay dispatches were interpreted. They are the small MIPS
  snippets Crash 2 embeds in GOOL entries and runs with `jalr $s5` from the
  interpreter (0x8003AAD4): no prologue and GOOL bytecode before them, so
  `compile_overlays.py` cannot prove a function boundary and, correctly, will
  not build them. A few percent of steady CPU, not spikes.
- VSync (0x8004A484) runs in the dirty-RAM interpreter because the timeout
  widening writes two words into it. Small; it could move into the recompile
  profile like the script words if it ever shows up in a profile.
- The pacer repays debt by running frames unpaced after a stall. That is
  deliberate (audio rate, see the comment in frame_pacing.c); fewer stalls is
  the fix, not a different pacer.
- At 280 Hz, immediate presents (`vsync = 0`) hold each 60 FPS frame for 4 or 5
  refreshes; with G-Sync on, for exactly as long as it took. Neither is a
  pacing defect worth code.

## Controller mapping (launcher)

The runtime already had a full controller map: `input.ini [mapping]`, one or
more SDL sources per PS1 button, honoured by both the per-port path and the
launcher's default "keyboard and all controllers" merge. The launcher now edits
it: Settings > Input, press-to-assign through XInput (same positional names
SDL uses), a list for everything else. Two findings shaped it. Stick
directions are not offered as sources, because the runtime ignores them as
buttons whenever the pad presents as analog. And the deadzone is written to
settings.toml, not input.ini: the runtime applies settings.toml's value over
input.ini's right after reading it.

## 60 FPS, part 17: what grows with the square of the internal scale

Goal: 5x internal resolution holding 60. Part 10 measured 5x missing a third of
its presents while the game itself kept up, and frame_perf at 5x put the scene
at 13.8 ms of GPU time in a 16.7 ms frame. On an RTX 5070, 5x is 5120x2560 -
13 Mpx, small as plain fill at 60 Hz - so the miss points at work done at
full-surface size per event, or at the CPU waiting for the GPU, both of which
grow with S squared. None of it had a release-build counter.

**Counters (patch 0038).** `PSX_GPU_PERF=1` (the launcher's performance
diagnostic sets it) enables the GL timer queries in release builds, and the
heartbeat gains a `gl` object: presents and swap time, batches and batch
breaks by reason, stencil rebuilds and their pixels, synchronous readbacks,
skipped ones and their wait, hold copies, pack pixels, and GPU scene/present
time. `tuning/scripts/scale_bench.py --seconds 20 --label 5x` turns a window
of it into per-second rates and a verdict, appended to
`tuning/runs/scale_bench.jsonl`.

**Fixes (patch 0039).**

- Mask stencil rebuilds were full-surface: every mask-check-on edge after
  unmasked draws blitted and redrew all 1024S x 512S and every native-wide
  surface - 13 Mpx twice per surface at 5x for a 320x240 band. They now cover
  only the rect the invalidating batches drew (one pixel of guard), and the
  same rows on the wide surfaces.
- Guest VRAM reads (GP0 C0h, DMA from VRAM) went through a synchronous
  `glReadPixels` whenever any pixel had been drawn since the last readback,
  paying the whole frame's GPU time on the emulation thread. VRAM is now
  tracked GPU-dirty per 64x64 tile; a read that touches no GPU-written tile
  is served from the CPU copy. `gl.sync_skips` counts them.
- Opt-in for A/B only: `PSX_GL_SEMI_BATCH=1` lets consecutive semi-transparent
  prims share a draw.

Not changed: the hold-last copy per present is window-sized, not S-sized.

**Status: not measured in play yet.** Run `scale_bench.py` at 1x, 3x and 5x
with 60 FPS on. Until those numbers exist the launcher keeps recommending 2x
at 60 and warning above 3x.

## Post-processing (part 18)

Patch 0040. A chain on the finished picture at output resolution, after the
present filter and bezel, before the OSD and menus: SMAA 1x or FXAA, CAS
sharpening, bloom (5-level dual-filter chain in RGBA16F, normalised by level
count - at 100% it washed the picture out before that), then brightness,
contrast, saturation, gamma, temperature, vignette, grain and an 8-bit output
dither. It hooks the three draws of the game image into the window (VRAM and
hold-last presents, frame blending, the CPU/FMV path) and works top row first,
the orientation SMAA's tables assume and the one D3D12 uses. With nothing set
the renderer draws exactly as before.

Texture dedither is a separate `TEX_FS` pass: with nearest sampling, a texel
within 2.5/31 of its neighbours' mean, whose opposite neighbours agree, is
pulled halfway to it - the checkerboard painted into texture art, not the
output. The renderer itself draws in true colour and never had the PS1 dither
matrix.

Launcher: Settings > Video > Post-processing, a reset button, Enhanced preset
= SMAA + CAS 35, Authentic = off. The pause menu's POST FX row switches it
for the session, for an A/B by eye. Checked offline on the target driver:
every program compiles, neutral settings reproduce the input exactly.

## Direct3D 12 (part 19)

Patch 0041. Not a second renderer: `gpu_gl_renderer.c` and `gpu_gl_postfx.c`
are compiled a second time with every GL call redirected to a GL-subset layer
on Direct3D 12 (`gpu_gl12.cpp`), so batching, mask, semi-transparency, the
wide compositor, interpolation, hold-last and post-processing are the same
code on both APIs and stay that way. Row r of a GL surface is row r of the
D3D12 resource (vertex shaders negate y); framebuffer 0 is an offscreen
texture flipped into a flip-discard swap chain. GLSL is matched by hash to
hand-written HLSL, compiled once by `d3dcompiler_47` and cached. An unknown
GLSL source refuses to start rather than draw wrong, and
`tuning/tests/gl12_shaders_check.py` catches it first.

Launcher: renderer "Direct3D 12 (experimental)". If it cannot start, the
window is recreated for OpenGL and the game runs there.

Parity (`tuning/renderer_parity`, both compilations driven with one PS1
command stream): read-back VRAM bit-identical to OpenGL at 1x-5x; every
present path (4:3, letterbox + bezel, CPU/FMV, hold-last, native-wide, blank)
byte-identical; async readback equal to sync; SMAA/FXAA/CAS/dedither
identical, bloom and grade within 1/255; the debug layer reports nothing.
Recording costs about 0.5 ms per 1500 prims against 0.25 ms on GL in a hidden
window. That timing is noisy and is not a verdict on play.

## 120 FPS, part 20: the same gate at twice the field rate

Patch 0042, experimental and opt-in (launcher: "120 FPS (experimental)" under
60 FPS game updates; env `PSX_CRASH2_FPS=120`).

**Feasibility, from the executable.**

- The quantizer rounds any frame under 19 ticks up to 17. A 120 Hz field is
  8.62 ticks, so left alone every field would be simulated as a 60 Hz one -
  double speed.
- `VSync(n >= 2)` appears only outside the 3D loop: VSync(60)/(20) at
  0x80034AC4, VSync(20) at 0x80035444, VSync(5) x4 at 0x80036164. The loop
  itself uses VSync(0). The library calls are VSync(-1) timeouts.
- Music: the plan assumed libsnd's tick sat in libetc's VSync callback table.
  It does not. `SsSetTickMode(1)` becomes mode 5 (VSync ticks) on NTSC, and
  `SsStart(1)` takes the root-counter-3 path. That chains the tick behind the
  VBlank handler (0x80054E74 calls the displaced handler, then
  `[0x8005EFE0]`). libsnd's own half-rate wrapper (0x80054EB4) cannot be used
  there, because it calls `[0x8005EFE0]` itself.

**Design.** VBlank at 120 Hz while the loop turns
(`interrupts_set_vblank_divisor`). At the top of each loop `[DB_CUR]+20` is
replaced by the frame's length in fields (from the game's own stamps) times
8.5 ticks, carried as half-ticks: 8 and 9 alternately, 17 a pair, the 60 Hz
timeline. The VBlank edge alternates `[0x8005EFE0]` between the tick and an
unreferenced empty function (0x80016EFC), so music keeps its tempo. Twelve
fields without a loop (a load) drop VBlank back to NTSC from the edge itself.
Scripts need nothing: the 0036 pacing accumulates ticks and steps every
fourth field. The clock is 400% with VSync's timeout widened x4. A window that
fails at 120 steps down to 60 with the gate open, then retries after 10 s,
20 s and 40 s.

**The simulator's catch.** `tuning/native120_sim` drives `crash2_60fps.h`
with a model of VBlank, root counter 2, the frame end and libsnd's chain,
running over the real executable. It found a design error before anything
shipped. Stepping down to 60 restored 60's 200% clock, but 120 at 400% and 60
at 200% give a frame the same instruction budget (282,240 x 4 = 564,480 x 2).
So a step-down could never rescue a scene that missed its field: it fell
through to 30. With 120 asked for, the clock now stays at 400% at 60 too.

Modelled results (all checks pass):

| Case | Result |
|---|---|
| Engaged at 120 | 120 loops/s, ticks 8..9, physics 1020 ticks/s (17 x 60, the stock timeline), music 60 ticks/s, scripts 30 steps/s |
| A 330 ms loop-less stretch | NTSC after 12 fields, tempo intact, re-armed after 4 quick loops |
| A scene of 375k cycles | Steps down to 60 and holds it on 17-tick frames. Retries after 10 s and falls back again. The gate never closes. |
| Three-field frames | 25/26 ticks, 25.5 on average |
| Mode off | Every word, the clock and the VBlank rate restored |

**Not verifiable offline:**

- Physics on 8/9-tick steps. Fixed-point truncation happens twice as often.
- Any per-loop logic that is not tick-scaled. It would run twice as fast as
  at 60, where it looked right.
- Pacing on a real 120/144 Hz panel.

To test: the Play page says "120 FPS: holding" or why it stepped down. On the
debug-tools build, run
`gameplay_smoke.py --scenario tuning/scenarios/native120_hold.json`.

## 120 FPS, part 21: slow motion, and a POST FX switch that froze the menu

Two reports after part 20. Direct3D 12 worked. Native 120 ran "in slow
motion, even though the FPS is 100+". The pause menu's POST FX row "does not
work".

**Slow motion.** The heartbeat was overwritten by a later 60 FPS session, but
the freeze dumps keep a ring of 100 ms samples. Cycles per VBlank give the
rate in force, and guest cycles per wall second give the speed:

| Session | VBlank | VBlanks / wall s | Speed |
|---|---|---|---|
| 15:41 | 120 Hz (280,690 cyc) | 79-90 | 0.66-0.75x |
| 15:42 | 120 Hz | 68-78 | 0.57-0.65x |
| 15:44 | 120 Hz | 90-96 (one second 125) | 0.75-0.80x |
| later, 60 FPS | 60 Hz | 60-62 | 1.00x |

The host could not keep 120, and 120 never let go. The host check fired, but
under the launcher's forced gate ("Prefer 60") the branch that handles it ran
on every loop, not only on the loop that completes a window. It cleared the
host streak each time, so the streak never reached two. Patch 0043:

- A loop that completes no window returns before any branch.
- At 120, one slow window steps down: missing 120 costs slow motion, not
  dropped frames.
- A host step-down backs off 10 s doubling to 160 s. A load does not reset
  this (a new scene does not make the machine faster); ten comfortable
  seconds at 120 do.
- `native_120fps_host_fallbacks` reports host step-downs, and the Play page
  now says which kind happened.

`tuning/native120_sim` gained a host model (wall time per field). Against a
host that manages ~91 fields a second it shows 1.0 s of slow motion at 0.76x,
then 60 at full speed, a retry after 10 s that steps down at once, the next
after 20 s. The old code would have stayed at 0.76x indefinitely.

**The other half of the slowness was the build, not the runtime.** The run
report said `autocompile_degraded`: "overlay autocompile failed 3 consecutive
runs (last exit 1)". Choosing Direct3D 12 makes the launcher write
`renderer = "d3d12"` into game.toml. The recompiler binary that
`compile_overlays.py` runs for `--overlay-config-hash` dated from 21 Sep, so
it rejected the file. The pre-flight probes pass with an OpenGL game.toml,
and the same command succeeds from a shell, which is why this looked like an
environment problem at first.

Every Direct3D 12 session therefore ran uncompiled code interpreted,
including the VSync page (0x8004A000) that the 60/120 mode patches. That was
~1,950 interpreted dispatches per VBlank, against ~240 on 24 Sep.
`_build/build_recompiler.ps1` fixes it: the codegen hash is still `3d2b5a19`
and the config hash is the same for either renderer, so the existing cache
stays valid. The missing shard was built straight away.

The rule from part 8 widens: a change to the game.toml schema
(`recompiler/src/config_loader.*`) also needs `build_recompiler.ps1`. The
recompiler parses game.toml every time the game compiles level code.

**POST FX.** The pause loop redraws the window from the drawable captured at
the last live present. The switch called `gl_renderer_invalidate_present()`,
which drops that capture, so from then on the pause loop presented nothing.
The menu stopped updating until it was closed, and neither the value nor the
picture changed. Now:

- The switch only flips the setting.
- With post-processing configured, the image always passes through the
  chain's input target, even with the switch off, where it is copied through
  unchanged.
- The hold-last redraw re-runs the chain from that pre-effect image, so the
  paused frame shows the switch at once. The texture dedither belongs to
  drawing the scene and follows on resume.

Also: the dedither only ever ran with nearest texture sampling. With the
launcher's default bilinear filtering it did nothing, so with the reporter's
FXAA + dedither at 5x the switch would have shown almost no difference even
working. Each bilinear tap is now smoothed as well.

The parity harness gained `PARITY_FILTER=1` and a paused-toggle step:

- VRAM is bit-identical between GL and D3D12 with bilinear + dedither.
- With the switch off, the paused frame differs from "on" in every pixel.
- Switched back on, it matches the first frame to the 1/255 output dither.

To test:

1. Choose Direct3D 12 and play a minute. The run report should no longer say
   `autocompile_degraded`.
2. Try 120. It either holds, or drops to 60 within a second or two and says
   why on the Play page.
3. In the Home menu, POST FX OFF/ON changes the paused picture.

## 120 FPS, part 22: host-bound at 2x, and a profiler to say why

Report: "can't keep 120, drops to 60 at 2x" (Direct3D 12, FXAA + dedither).

| Heartbeat | Value |
|---|---|
| Engages | 5 |
| Fallbacks | 4, all host |
| `autocompile_degraded` | 0 (0043's recompiler rebuild worked) |

So the machine could not produce 120 fields a second. It was no longer the
compile problem.

Per field the game does ~1M PS1 cycles of work: 0.9M at 60/200%, 1.04M at
120/400% in stock-equivalent terms. Almost all of that is instruction work,
not waiting. At 120, emulation, GPU emulation, recording and present share
one 8.3 ms slot on one thread.

Patch 0044 does three things.

**Fewer draws and cheaper recording.**

- Semi-transparent batching is now on by default. It is byte-identical in
  the parity harness, including overlapping same-mode prims and interleaved
  modes.
- The Direct3D 12 command list now skips re-binding state that has not
  changed.

Recording on the parity bench, ms per frame:

| API | Before | After |
|---|---|---|
| Direct3D 12 | 0.59-0.65 | 0.39-0.42 |
| OpenGL | 0.31 | 0.26 |

**Fairer host check at 120.**

- Below 108 fields/s the runtime steps down at once.
- Between 108 and 114, one hitchy second no longer costs 10 s at 60; a second
  slow window is needed.
- The rate reached is reported as `native_120fps_fail_hz`, and the Play page
  quotes it.

**A sampling profiler** (`host_profiler.c`).

- 120 FPS profiles its first 20 s engaged into `psx_host_profile.json`.
- `tuning/scripts/host_profile.py` names every address from the executable's
  symbols and splits time into emulate/present/pace.
- The next 120 session will show exactly what the host spends its 8.3 ms on,
  which is what any further 120 work should be aimed at.
- The renderer part was profiled offline with the parity harness
  (`PARITY_PROFILE=1`). That profile found the per-draw driver cost the
  Direct3D 12 cache now removes.

Launcher: explanations cut to a line each across Settings, Play, Mods,
Advanced and Setup.

## Part 23: 120 FPS goes behind Developer mode; quality of life

Report after part 22: 120 FPS "hits 120 once or twice, otherwise 60, and when
it hits it's laggy". The decision was to hide it behind Developer mode and
spend the round on polish and quality of life.

**120 FPS.**

- `config.native_120fps_active()` now also requires `developer_mode`. That is
  a hard gate, like the one on diagnostics, so a box left ticked in an old
  settings.json does not run 120.
- The checkbox reads "120 FPS (developer preview)" and shows only in
  Developer mode. The runtime is unchanged: it runs 120 only when the launcher
  sets `PSX_CRASH2_FPS=120`. The host profiler (part 22) starts only with 120,
  so it is inert for players.

**Patch 0045 (runtime).**

- **Home-menu changes are kept.**
  - With `PSX_MENU_PREFS_FILE` set, `menu_prefs_write` records the rows the
    player changed this session, plus the F readout key and the volume keys.
    It writes them as JSON under the launcher's own setting names.
  - Only touched values are written, so a launcher change made mid-session is
    not undone. Alt+Enter and Ctrl+F stay temporary.
  - The file is written on menu close and first thing in `shutdown_runtime`,
    never from the frame path, via temp file + `MoveFileExW`.
  - `launcher/crash2launcher/ingame.py` validates each value against the
    launcher's own rules. A 21:9 *game* aspect is refused by design: the
    launcher offers 21:9 only as a screen shape (part on `OUTPUT_ASPECTS`).
- **IMAGE FIT really changes the picture.**
  - `letterbox_rect_aspect` lets a set Zoom dial replace the letterbox/fill
    choice, and Stretch 100 fills like STRETCH.
  - So on Enhanced (zoom 0, stretch 100) LETTERBOX, STRETCH and FILL drew the
    same frame.
  - A fit picked in the menu now clears zoom (-1) and stretch (0), in the game
    and in the kept settings.
- **Pause on focus loss** (`PSX_PAUSE_ON_FOCUS_LOSS`). Losing focus opens the
  Home menu once the game has started, outside netplay and other modals. While
  the window is unfocused the menu ignores the pad.
- **Fast-forward toggle** (`PSX_FAST_FORWARD_TOGGLE`). The launcher also
  passes `PSX_FAST_FORWARD_SPEED`, which existed but was never set.
- **Restart through the launcher** (`PSX_RESTART_EXIT_CODE`, 75). RESTART GAME
  used to `CreateProcessW` a child the launcher could not see, so the Play
  page said "Ready to play" over a running game. Now the launcher restarts it.
- **Post-processing master switch.** `PSX_POSTFX_ENABLED=0` starts the effects
  off but loaded. POST FX with no effect set (NONE) no longer flips hidden
  state.
- **Two small fixes.**
  - Ctrl+C "CD reinsert" is now debug-build only; it used to eject the disc in
    release, with a Spanish log line.
  - The SDL3 float refresh rate was printed with `%d`.

**Launcher.**

- **Hotkeys** (`hotkeys.py`): the runtime's `config.ini [KeyMap]`, edited on
  Settings > Input, with clash warnings against the game's buttons.
  - The Play page's key list and the game-key editor's warning now come from
    the live bindings, not three hand-kept lists.
  - The dead `turbo_key` setting is gone.
- **Saves page** (`saves.py`, `page_saves.py`):
  - the 12 slots with their thumbnails (runtime's "PSTH" format);
  - Play from here (`PSX_LOAD_SLOT`, which the runtime already had);
  - delete;
  - memory-card backup and restore. A restore backs up the replaced cards
    first and reads only members named like a card.
- **Direct3D 12** loses "(experimental)": it holds up in play, and the parity
  harness holds it to OpenGL's VRAM.
- **Presets updated stale controls.** `_rebuild_from_settings` never re-synced
  Zoom, Stretch, Vertical pan or Screen shape, which every preset sets. The
  new check in `test_settings_apply.py` fails against the old code (zoom
  combo 75 while the setting was 0).

**Open.**

- `PSX_LOAD_SLOT` at boot had not been used by this project's tools before.
  If a boot-time load fails, the runtime says "Load failed slot N" and the
  game starts normally.

## Widescreen, part 8: object activation, found and widened

Patch 0046. Parts 5-7 left one cause of the edge popping unmeasured: how
dynamic objects come and go (part 5, "Phase 5"). It is the whole story for
objects, and it is authored level data, not a cull.

### How Crash 2 decides which objects exist

Found statically in the executable.

- **The camera-path lists.** Each camera path entity carries per-node lists
  (CrashEdit's names):
  - 0x13C: entities visible from that node on (forward);
  - 0x13B: entities last visible at that node;
  - 0x208/0x209: level data to load and unload there.
- **The per-node call.** The camera update (`0x80020200..0x80020830`) walks the
  path a node at a time. At each node it calls `func_800217F8` (load lists),
  then `func_8001A13C(entity, node<<8, dir)` (draw lists).
  - Forward (dir 2): queues a kill for 0x13B(n-1) and a spawn for 0x13C(n).
  - Backward: the mirror, 0x13C(n+1) and 0x13B(n).
- **The queue.** `func_8001A054` records the actions in a queue at `0x8006302C`:
  12-byte `{value, object, action}` entries, count at `[gp+0x23C]`
  (`gp = 0x8005F17C`, so `0x8005F3B8`). The last action per entity wins.
- **Carrying the queue out.** `func_8001A23C` does it:
  - 0x13C spawns through `func_80019098`, which returns -22 when the object
    pool is full;
  - 0x13B kills through `func_80019BBC`.
- **Path entry and jumps.** Entering a path or jumping along it marks everything
  queued for a kill (`func_8001A014`) and replays from the nearer end.
- **The accessor.** `0x80031AE8` is the generic entity-property accessor. In the
  mode these calls use, it returns a row only on an exact node match.
- **Reference-counted loads.** A load row adds a reference through
  `0x80014260`; an unload row drops one through `0x8001434C(rec, -1)`.
- **No bounds check.** The queue region ends where other data begins,
  `0x800632AC`, so it holds 53 entries.

A value is `(index<<24) | (id<<8) | zone link`. The entity's word at
`0x8007B1C4 + id*4`, bit 0, marks "spawned".

So objects appear exactly at the node where the 4:3 frustum starts seeing
them, and vanish where it stops. Any wider view pops them at its edges. Part
6's render-box fix could never touch this, which is why its measured revival
rate was about 5%.

### The fix: widen in time, never touch the original

`crash2_wide_spawn.h`: at node n an object is wanted if the original lists show
it anywhere within [n-k, n+k].

- **Leaves the game's call alone.** `jr $ra` is a hook point, so at the return
  the hook rebuilds each nearby object's original presence from all its rows in
  the path. That presence is exactly the game's own state: every step is one
  row, and entry replays from an end.
- **Acts only where the window wants an object and the original does not.**
  It spawns it early, or overrides the game's fresh kill to keep it. Objects
  neither wants but still alive are killed again.
- **Identity at k = 0.** With k = 0 nothing is ever changed.

Safety:

- **Data lifetime.** Early spawn only if no load row lies before the object's
  original spawn node; late keep only if no unload row lies after its original
  kill node. An object never runs ahead of, or outlives, its reference-counted
  data. At the first unload it dies in the same update its original did.
- **Open intervals.** Objects whose rows do not open and close inside the path
  are left to the game. Their state depends on the entry end: a replay from the
  start never spawns an end-only object, and one from the end never spawns a
  start-only one. Well-formed data has none; the model found this, not play.
- **Queue capacity.** The hook appends at most up to 45 of the 53 queue slots.
- **Failed spawns.** A spawn that fails (object pool full, code not resident)
  returns an error the game already handles, and is retried at the next node.
- **Stale state.** A state load (`interrupts_resync_after_restore`) or a window
  change re-checks every queued object at the next node.
- **Code words.** 19 words across `func_8001A13C`, the queue, the flush, the
  three call sites, the load lists, the accessor and `gp` are checked first.

`PSX_CRASH2_WIDE_SPAWN=N`: nodes at 16:9, scaled by the live `x_margin` (so
14:9 gets about half). Zero at 4:3 and on frames presented 4:3.

### Checked without the game

`tuning/wide_spawn_sim` runs the real header against a C model of the queue,
the per-node call, entry replay and the flush. Guest RAM holds the real
executable, so the signature check runs on actual bytes. The camera entity is
written in the engine's layout. All 11 checks pass:

- k=0 is identity;
- k=1..3 match the widened set at every node through walks, reversals, jumps
  and entries from both ends;
- malformed objects are never touched;
- load and unload rows block in both directions;
- the queue limit holds;
- failed spawns are retried;
- a state load retires far-away objects;
- 14:9 halves the window;
- a changed word disables the hook.

### Not verified yet: play

Things only a real run can show:

- that the lists behave as read in every level (vehicle, chase and bonus
  paths);
- that nothing spawned early misbehaves, since its AI starts at spawn;
- how often `unsafe_load` / `unsafe_unload` hold things back.

The heartbeat's `wide_spawn` object and the `c2_spawn` debug command, which
changes k live, report arrivals, early, kept, retired, unsafe_load,
unsafe_unload, full and bad.

Launcher:

- **Object range.** Settings > Video > Object range (Off / Slightly wider /
  Wider / Widest = k 0-3), on in the Widescreen preset.
- **Widescreen mode labels.** The native-wide option is labelled for what it
  is: part 2 showed it never needed per-game data; what it costs is memory.

## Widescreen, part 9: native-wide dropped the polygons in its margins

Patch 0047. Reported after part 8, in native-wide at 14:9 with the object range
on: "mostly the same culling issue". The heartbeat showed the object window
working (1011 arrivals, 110 early, 98 kept, 0 bad), so objects were not it.

### What was still wrong: the per-polygon screen test

Crash 2's renderers drop a polygon when every projected vertex lies past the
same screen edge. The test is packed, on the GTE's SXY words (Y<<16 | X), with
`B = 0x00D90200` (217<<16 | 512):

    t8 = ~((s0-B) | (s1-B) | (s2-B)) | (s0 & s1 & s2)
    bltz t8, reject      ; all Y above the top, or all at 217 and below
    sll  t8, t8, 16
    bltz t8, reject      ; all X < 0, or all X >= 512

Bit 15 of `s` is X's sign; bit 15 of `s-B` is clear exactly when X >= 512 for
the GTE's range [-1024, 1023]. There are no SLTI/SLTIU compares here, which is
why part 2's census of width immediates found nothing. Seven sites use it:

| X branch     | kind     | where                           | vertices           |
|--------------|----------|---------------------------------|--------------------|
| `0x80041C18` | triangle | model polygons (`0x80041B80`)   | SXY0..2            |
| `0x800424E0` | triangle | world, `func_80041E5C`          | SXY0..2            |
| `0x800427A0` | quad     | world                           | `[v1+376]`, SXY0..2 |
| `0x8004518C` | line     | `0x80044F54`                    | SXY0..1            |
| `0x80045404` | dot      | `0x80045218`, right edge only   | `$a1` (SXY2)       |
| `0x80045EC4` | triangle | world, second loop              | SXY0..2            |
| `0x800460D0` | quad     | world, second loop              | `[v1+376]`, SXY0..2 |

Squash (mode 1) squeezes X in the GTE before this test, so the game already
tests the wider view. Native-wide (mode 2) leaves the GTE alone and moves the
picture with the GPU draw offset, so the game still tested [0,512): every
polygon wholly inside a revealed margin was dropped, and only polygons
straddling the old 4:3 edge reached it. Part 5's 5,735 "past the window"
primitives were exactly those straddlers. Part 6 fixed the object box test
(`func_80041D14`) for mode 2 but never looked at this one. That part-6 fix was
measured in mode 2 with the per-polygon test still dropping the margins, which
may be part of why it showed so little.

### Where to hook it without regenerating code

Every branch in the generated code calls `psx_check_interrupts_at(cpu, pc)`
with the PC it continues at, taken or not, so a hook can act on entry to the
block holding the X branch. That block is reached only by falling through the
Y branch: no branch, jump or table word in the executable targets these PCs.

Two recompile-time routes were considered and not taken:

- A new codegen site type. It would change `code_generator.cpp`, which is in
  the codegen hash, so every overlay shard would have to be rebuilt.
- A `[[recompiler.patch]]` rewrite. The widened test needs about four more
  instructions than the original has.

### The fix

`crash2_wide_reject.h`, at the X-branch block entry:

- **Re-decides X** against `[-off, 512+off)`, the window native-wide shows
  (`off = ws_nw_extra()/2`: 43 at 14:9, 85 at 16:9). When the polygon reaches
  into it, the hook clears bit 31 of `t8` and the game takes its own keep path.
- **Only turns rejects into keeps.** A squash of 4/3 (16:9) maps `[-85.3,
  597.3)` onto `[0,512)`, so both modes accept the same polygons. Native-wide's
  primitive-buffer and ordering-table use is therefore what squash already
  produced.
- **`t8` is scratch.** Every kept path writes it before reading it, and the
  value left is one the game itself leaves for a kept polygon.
- **Quads.** A quad projects its fourth vertex (RTPS) between reading three
  vertices and testing, which pushes vertex 0 out of the GTE. Both quad paths
  store it first with `swc2 SXY0, 376($v1)`, and that is where it is read.
- **Self-check.** Before changing anything, the hook recomputes the game's own
  verdict from the vertices it read. If that differs from the game's bit, it
  counts a `mismatch` and leaves the polygon alone.
- **Guarded.** 141 code words are checked once, and again after a state load.
- **Identity** at 4:3, in squash mode, and on frames presented 4:3.

`PSX_CRASH2_WIDE_REJECT=0` turns it off. The `c2_reject` debug command switches
it live and reports `off`, `checked`, `kept` and `mismatch`; the heartbeat
carries the same counters as `wide_reject`.

### Checked without the game

`tuning/wide_reject_sim` runs the actual instruction bytes of all seven sites,
read from the executable, through a small MIPS interpreter, with the real header
hooked in where the generated code calls it. Random vertices are weighted to
the edges at 0, 512, 14:9 and 16:9. All checks pass:

- **Identity at margin 0.** 20,000 runs per site; not one register differs.
- **The game's verdict reproduced.** At 14:9 and 16:9, 40,000 runs per site:
  - the game's X verdict equals the plain rule at [0,512), and `mismatch` is
    0 everywhere;
  - the result is the widened rule every time, and no other register or GTE
    word changes.
- **Coverage.** At 16:9 the world triangle loop drew 5,127 of the 6,023
  polygons the game dropped.
- **Quads.** A quad whose only vertex in the margin is vertex 0 is kept.
- **Off and guarded.** `on:0` is the game's own test, and a changed code word
  disables the hook until the word is back.

### To check in play

- The heartbeat's `wide_reject` should show:
  - in native-wide gameplay: `code_ok` 1, `off` 43 (14:9) or 85 (16:9), and
    `kept` rising while `mismatch` stays 0;
  - in squash mode: `off` 0 and nothing counted.
- Compare `c2_reject {"on":0}` and `{"on":1}` on the same scene: the margins
  should go from ragged to filled.
- Anything still missing at the edges in both modes is then either the level
  itself ending (part 4's caveat) or an object the object range holds back
  (`unsafe_load` / `unsafe_unload`).

## Build: two failures from player logs (0.9.5)

Patch 0048, plus packaging. Two players' Setup logs, neither reproducible on
this machine:

### 1. A Vulkan SDK on the machine stopped every build in ninja

    -- Vulkan backend: headers C:\VulkanSDK\1.3.224.1/Include, glslc ...
    ninja: error: '.../game/psxrecomp/tools/embed_spirv.py', needed by
    'psx-runtime_vkgen/vk_shaders_spv.h', missing and no known rule to make it

`runtime.cmake` defaulted `PSX_ENABLE_VULKAN` ON and turned the backend on
whenever `$VULKAN_SDK` supplied headers and `glslc`. That adds a custom step
running `${PSXRECOMP_ROOT}/tools/embed_spirv.py`, and the framework payload we
ship has no `tools/` (the memory note on missing directories). Here there is
no SDK, so the backend always built as a stub and this never showed.

The fix:

- **Default OFF.** The launcher offers only OpenGL and Direct3D 12.
- **Stub fallback.** With the option ON anyway (a stale cache, or `-D`), a
  missing `embed_spirv.py` falls back to the software stub.

### 2. A space in the install path failed the BIOS step

    psxrecomp-bios: FATAL: config file not found: D:\Crash
    psxrecomp: error: BIOS recompilation failed

The player unpacked into `D:\Crash Bandicoot 2`. The CLI (`main_cli.cpp`
`run_process`) starts `psxrecomp-game` and `psxrecomp-bios` with `_spawnv`,
which joins `argv` with single spaces and quotes nothing, so the child split
`--config D:\Crash Bandicoot 2\...` again.

- **Why the game step survived.** It gets `--config game.toml`, a relative
  path.
- **Why the BIOS step failed.** It gets absolute paths.

The fix:

- **Quoting.** Every argument is now quoted by the C runtime's parsing rules.
- **Our CLI ships.** The launcher now ships a CLI built from the vendored tree
  instead of the stock one. `_build/build_recompiler.ps1` builds it in
  `_build/build-cli` with `PSXRECOMP_ENABLE_CHD=ON`, so `.chd` discs still
  read. It is statically linked and import-checked like `psxrecomp-game`.
- **Packaging.** `tools/package.ps1` takes it from there.

The runtime's own overlay autocompile was already safe with spaces: the
launcher quotes every path, and `autocompile.c` wraps the whole command for
`cmd /C`.

### Checked

The staged 0.9.5 bundle was copied into `...\c2 e2e\Crash Bandicoot 2\`, with
a fake Vulkan SDK in `VULKAN_SDK`. The launcher's three steps then ran as
`page_setup.py` runs them:

1. `psxrecomp.exe build`;
2. the recompile profile, then `psxrecomp-game`;
3. `build.ps1` with the bundled toolchain.

A configure with `-DPSX_ENABLE_VULKAN=ON` checked the fallback.

| Check | Old pieces | New pieces |
|---|---|---|
| BIOS step in a spaced path | `FATAL: config file not found: C:\...\Temp\c2` (the stock CLI) | passes |
| Build with a Vulkan SDK present | `ninja: error: '.../tools/embed_spirv.py' ... missing and no known rule to make it` (the patch 0048 original) | configure says "Vulkan backend: disabled"; `SCUS_94154_Recompiled.exe` (18 MB) builds |
| Vulkan forced ON | not tested | "SDK found but ... embed_spirv.py is not in this framework tree", stub |

Both old failures are the players' own errors, reproduced exactly.

### Also in 0.9.5

Native-wide and the object range are developer previews for now, like
120 FPS:

- **Hidden.** Their controls show only in Developer mode.
- **Not applied.** `config.widescreen_native_wide_active()` and
  `widescreen_object_range_active()` keep them off otherwise, even if a
  settings file still has them on.
- **Preset.** The Widescreen preset no longer sets the object range.

## Widescreen, part 10: the scenery lists, widened

Patch 0049. After part 9 no code anywhere still clipped drawing to the old
screen:

- **Main executable.** A full scan for screen-bound idioms found only the
  seven polygon tests (0047) and the box cull (0016). The other hits were
  texture packing, vertex-depth checks and a setup routine.
- **Level code.** The overlay captures in both build trees (448 regions) hold
  no screen tests. Their 512/682 compares are angle clamps: 45° and 60° in
  12-bit units.

One mechanism was left, and it applies to squash as much as to native-wide.

### Which polygons get drawn: the SLST lists

Each camera path carries an SLST entry: one polygon list per camera node,
stored as the list at node 0, a delta per step and the list at the last node.

- **Resolving.** The camera update resolves the entry (property 0x103 of the
  path, through `0x80014260`; `$v0` at `0x8002037C`).
- **Stepping.** It steps node by node through `func_80029800`, with the
  forward delta at `0x8003AE54`, into the current list `[gp+0x214]` =
  `0x8005F390`.
- **Recording.** It stores the node and the path at `0x800608E8` and
  `0x800608D4`.
- **Drawing.** `func_80041E5C` draws the list:
  - it walks it from the end;
  - each id is `(world<<13)|index`, with 0x1800 marking a quad;
  - each polygon goes into the ordering table by summed depth, so the list's
    order only settles ties.

**The lists are 4:3-tight.** Across all 1,986 entries on the disc, merging a
node's list with its neighbours' adds, by polygon, about 7% for one node
either side and 13.5% for two. Counted by exact id it looks twice that,
because a quad's variant bits are per-node draw state (4% of shared polygons
change them from one node to the next). Part 4's verdict, that the lists
already cover a wider view, came from a probe that projected world vertices
with the camera rotation alone, without its translation. It proved nothing.

### The fix: draw the neighbours' lists too

`crash2_wide_slst.h`:

- **What it draws.** At node n it draws the union of the lists of nodes n-k
  to n+k. The renderer's own tests still drop anything that is not on screen:
  backface, depth, and the screen test that 0047 widens in native-wide.
- **The game's list stays as built**, repeats included (936 lists hold an
  exact repeat).
  - Polygons are only added, each once. A quad is matched on its index.
  - Nearest node first; each goes right after the polygon that precedes it in
    the neighbour's list.
  - The added polygons are capped at half the game's list plus 32, and the
    total at 1520, the game's own list size.
- **Lists are rebuilt here.** A C port of the forward delta reproduces all
  63,854 node lists. The 4 entries whose last list the reference does not
  close are refused, so they are simply not widened.
  - The rebuilt list of the current node must equal the game's list. That is
    checked on every call, so a merged list is never drawn over a list it was
    not built for.
- **Nothing of the game's is written.** The merged list lives in mod memory,
  and only the render call's `$a0` points at it.
  - Reads from there take the runtime's slow load path. That costs one call
    per polygon id.
- **The primitive buffer.** The renderer appends to a per-frame buffer of
  0x16400 bytes (allocated at `0x80016288`; 0x2C800 in one mode) and never
  checks its end.
  - A merged list is used only while the room left holds 64 bytes per added
    polygon plus 16 KB.
  - The census put normal use around 28 KB, so this should rarely bind;
    `prim_skips` counts when it does.
- **Guarded.** 50 code words are checked. Identity at 4:3 and on 4:3 frames.

`PSX_CRASH2_WIDE_SLST=N` scales like the object range. The launcher's
*Object range* became **Edge range** and drives both: it is still Developer
mode only, and now defaults to Wider. The `c2_slst` debug command and the
heartbeat's `wide_slst` report:

- `built` and `used`;
- `added` and `added_max`;
- `mismatch` and `bad`;
- `prim_skips`.

### Checked without the game

`tuning/wide_slst_sim`:

- `export.py` writes every SLST entry on the disc, with hashes of the
  reference node lists, to a temporary file: they are game data.
- `sim.c` checks three things:
  - **The rebuild.** 63,854 of 63,854 node lists match.
  - **The merge.** 65,895 merges over every level for k = 1 to 3 obey the
    rules above, with the union complete whenever the cap does not bind
    (255 hit it).
  - **The hook end to end.** It does nothing when:
    - the game's list is not the node's;
    - the camera path changed;
    - the primitive buffer is nearly full;
    - the frame is 4:3, or presented 4:3;
    - the range is 0;
    - a code word changed.
    It widens in squash and in native-wide, and 14:9 reaches one node.

### To check in play (Developer mode, a widescreen aspect)

- **Counters.** In the heartbeat's `wide_slst`, `used` should climb and
  `mismatch`, `bad` and `prim_skips` should stay at or near 0.
- **The view.** Scenery that used to appear at the sides as the camera moved
  should already be there.
- **A/B.** `c2_slst {"k":0}` against `{"k":2}` on the same spot.
- **Performance.** Watch the 60 FPS readout for the extra world polygons.

## Widescreen, part 11: white corners in the border rows

Patch 0050. In native-wide the warp room showed white blocks in all four
corners: the side margins of the 12 rows above and below the picture, beside
a black 4:3 centre.

### Why

- **The border rows.** Crash 2 draws into 512x216 at y=12 of its 512x240
  display (`SetDefDrawEnv` at `0x80016130`). Rows 0-11 and 228-239 are written
  only by fills and uploads, and they are black.
- **Two ways their margins went white**, both in `gpu_gl_renderer.c`:
  - **The overlay rect.** A flat rect covering the whole 4:3 frame (fades,
    flashes) has its own wide pass, `wide_flat_rect_direct`. It drew with a
    full-surface scissor, so it filled the margins of every row the rect
    covered, while the canonical rect stopped at the drawing area. Every other
    mirror pass clips to the drawing area's rows, and so does the software
    renderer's `rt_wide()`.
  - **Fills and uploads.** The wide surface never follows an upload, and
    follows a fill only while native-wide is active and the fill starts at a
    display buffer's base. Anything else that reached those margins stayed.
- **Nothing repaints them.** The game's own drawing stops at row 227, so a
  margin painted once stays painted.

Which of the two made the screenshot can't be told without the game; the fix
covers both.

### The fix

- **The overlay's wide pass keeps to the drawing area's rows.**
- **Each wide present repairs the border rows.** `wide_fill_border_rows`
  finds the displayed rows that no clipped draw reached since the last
  present, and copies the canonical frame's outermost columns of those rows
  into each margin.
  - The drawn rows are tracked per wide surface at `hr_begin`.
  - The copy is an unscaled framebuffer blit, like the centre blit, so it runs
    the same through the Direct3D 12 layer.
  - It does nothing on a repeated frame.
  - The gl cost JSON counts the copies as `border_fills`.
- **A block, not a stretched column.** The Direct3D 12 layer only does
  unscaled blits, so the margin gets the edge block rather than the edge
  column stretched. On a one-colour row that is the same thing. A picture
  uploaded into the border rows would repeat its edge strip in the corners.

### Checked without the game

- **The harness.** `tuning/renderer_parity` gained the case, as section 5b:
  - stale white margins;
  - a picture in rows 12-227;
  - an overlay rect drawn with a drawing area of rows 100-150.

  It reads the wide surface back after both presents, GPU-direct and readback.
  Both pass on OpenGL and Direct3D 12. The old renderer fails, and so does
  each half of the fix on its own.
- **The test.** `tuning/tests/renderer_parity_check.py` builds and runs the
  harness, about 10 s. It checks:
  - the harness's own checks on both APIs;
  - OpenGL against Direct3D 12: VRAM exact, presents within 2/255.

### To check in play (Developer mode, Native-wide)

- **The corners.** They stay black through the warp-room flash and through
  the fades in and out of levels.
- **The fades.** They still cover the whole wide picture; only the 12-row
  borders are left alone.
- **The counter.** `border_fills` in the heartbeat's `gl` climbs with wide
  presents.

## Widescreen, part 12: side-on paths, path ends, and then the camera itself

Patches 0051 and 0052.

### What was still wrong after part 10

Both edge ranges (objects, part 8; scenery, part 10) widened by a fixed number
of camera-path nodes, k = 2 at 16:9. Measured on the disc, that was far short:

- **Scenery.** Growing the window until the lists gain the 16:9 share (+33%)
  needs 6 or more nodes at half of all nodes; k = 2 got there at 1.7%.
- **Objects on side-on paths.** Kind 3 paths (property 0x029; 8 behaves the
  same) look across the play. Every object on them appears where its offset
  along the path from the camera is 0.94-2.3 times its distance off it (median
  1.1) and vanishes at the mirror value: the 4:3 frame's side edges. A 16:9
  frame sees each a third of that offset earlier: 5-7 nodes, past 14 for some.
  k = 2 covered 1% of them. This was the "2D side scroll" report.
- **Path ends.** Paths are 26-30 nodes and joined end to end (1,815 of 1,990 at
  both ends); a window stopped at a path's end.

### Patch 0051: side-on reach, and scenery across path ends

`crash2_wide_geom.h` (new) reads the level data read-only. Every decoding was
checked against the files:

- **Entries.** Found as the game's bounded lookup (`0x80014B90`) finds them;
  resident or not.
- **Objects.** A draw-list value `(index<<24)|(id<<8)|zone slot` is item
  `index + [+0x184] + [+0x188]` of that zone. All 53,862 values on the disc
  resolve to their entity. Positions are in units of 4 from the zone origin.
- **Camera links.** Property 0x109 is a row per end. The record's byte 0 is the
  end of the next path entered, byte 1 its path index, byte 2 the zone slot.
  All 3,837 resolve to a path.

Using it:

- **Objects on side-on paths** get their own reach from their position.
- **Scenery on side-on paths** gets the 90th percentile of the path's objects'
  reaches. That met 98% of their needs; growing the lists met 37%.
- **Past a path's end** the scenery window continues into the linked path. Its
  polygon ids name worlds by the joined zone's slots, so they are remapped by
  world EID: unmapped, joined lists differ by 71% at the shared node; mapped, by
  5%, as close as two lists of one zone.

Reported after 0051: better, still artifacts.

### Patch 0052: ask the camera

Every node-count rule is a guess about where the camera looks. The camera
itself is readable:

- **Rotation.** At `0x80060774`, loaded into the GTE by `0x8004EF28` just before
  the world draw. The renderer only clears the translation (`0x80041E9C`).
- **Position.** At `0x800607F4`, in 24.8. Each frame every world record of the
  zone header gets origin − camera (`0x80018344`). These are world units, the
  same as zone origins, path points and object positions.
- **World geometry** (WGEO, type 3):
  - item 0 holds the origin and counts;
  - item 1 vertices: XY words last-first, then Z halves;
  - item 2 triangles: a word each last-first, then a half each;
  - item 3 quads, 8 bytes each;
  - coordinates are the field & 0xFFF0 from the origin.

  All 1.57M polygon references of the side-on paths decode. They lie inside the
  4:3 angle with the same sharp edge the objects show.

**Scenery** (`crash2_wide_slst.h`), at the render call:

- **Candidates.** Per node, the polygons the lists within 20 nodes either side
  hold, and the linked paths' lists, that the game's list does not.
- **Each frame,** every candidate is projected as the renderer will project it.
  Those reaching into the extra columns are added: outside the 4:3 frame,
  inside the wide one, in front of the eye.
- **Inside the 4:3 frame nothing is added.** The game's list there is
  authoritative: what it omits is hidden, and adding it would risk polygons
  sorting through walls (the node windows did add such polygons).
- **Model check.** Once per node, the model must put at least half the game's
  own list on screen; otherwise the node window draws. The test cases ran at
  60-96%.

**Objects** (`crash2_wide_spawn.h`): with that camera, an object the original
has not spawned, or has just killed, is wanted while its position projects
into the extra columns, on any kind of path. Inside the 4:3 frame the original
lists decide. The load and unload rules are unchanged.

**A bug the model check caught.** The game shifts camera y unsigned. That is
harmless for its 16-bit offsets, but camera y is negative in some levels, so a
whole position needs the signed value.

### Checked without the game

`tuning/wide_frustum_sim` covers 72 cases, two per level: side-on and other
paths, mid-path and near an end, 70 of them reaching into a linked path.

- **The setup.**
  - Real zones, worlds and SLSTs are laid out as the game holds them.
  - The camera sits at the node, rotated to put the node's list on screen.
  - `frustum_ref.py` is written apart from the C.
- **The checks.**
  - Each drawn list equals the reference by length and hash.
  - All 799 object verdicts match.
  - The object hook never spawns against the camera's verdict.
  - It refuses as it should: a camera facing away, unreadable worlds, the wrong
    zone, linked SLSTs not resident.
  - A 4-pixel change in the reference's guard band makes cases fail, so the
    comparison is sensitive.
- **The cost.** Lists grow 17%; the worst frame, including the once-per-node
  build, took 1 ms.

### To check in play (Developer mode, a widescreen aspect)

- **`wide_slst`:**
  - `camera` should climb with frames;
  - `model_bad` should stay near 0;
  - `model_pct` should be well over 50.

  If `model_bad` climbs, the model is wrong somewhere, and that node window is
  what draws.
- **`wide_spawn`:** `cam_yes` / `cam_no` show the camera deciding objects.
- **The view.** Side margins in forward sections, side-scrollers and across
  path ends should no longer fill in late. Nothing should show through walls
  inside the 4:3 area.

## Widescreen, part 13: black triangles in side-on scenes

Patch 0053.

**The report.** After 0052, black triangles still showed at the outer edges of
2D (side-on) scenes: Snow Go's ice cave and the bonus rounds.

**Two attempts after 0052 were withdrawn.**

- One dropped near-plane wedges from the additions.
- The other drew polygons no list held, which put the sky over the walls of
  a forward room.

The request was then to fix only the triangles, and only in 2D scenes, so this
part starts again from 0052.

### The game's camera, read exactly

Part 12 judged offline views with a camera fitted to each node's list. The
game's own camera is now decoded:

- **Where.** At rest it sits on its path's point for the node: zone origin +
  0x04B[node] (`0x80026A08`, called by the camera update at `0x80022C14`).
- **Which way.** The item after the path in the zone (three items per path;
  property 0x173 of every one of the 1,990 paths is its index) holds 0x04B
  rows of two int16 triples per node.
  - The first triple is the camera's x, y, z angle, 4096 to a turn.
  - `0x80026A08` writes it beside the position (`0x800607F4` + 12).
- **The matrix** (`0x80017BC4`):
  - Rz(-z), times Rx(-x), times Ry(-y), using the game's sine table
    (`0x8005BCB0`) and MVMVA's >> 12;
  - `0x80017AF8` then copies it to `0x80060774` for the world draw, with row 1
    times -5/8 (the frame's aspect) and row 2 negated.
- **H** is the path's 0x130 at the node, 288 without it. OFX/OFY are 256/108
  (`0x8004EFE8`, called from `0x80017F70`).
- **In play**, a look toward Crash turns it from there, within per-node limits
  (0x119; `0x800231B0`).

On side-on paths, 71 to 95% of the game's own list lands inside the 4:3 frame
with this camera (median 86%). The fitted cameras reached 55 to 76%.

### What the triangles are

Snow Go's side-on paths were rendered offline with that camera:

- **What was drawn:** the game's list, 0047's widened test and 0052's
  additions, far to near, with the GPU's size limit applied.
- **The reference:** every polygon of the zone's worlds.

What the comparison showed:

- **No wedges.** In Snow Go's views neither 0052's additions nor the game's own
  polygons had a corner the GTE cannot place. Across the game, 0052 drew 8 such
  wedges, all in S0000026. The withdrawn near-plane fix had answered the fitted
  cameras, not the game.
- **Gaps.** 1.1% of the extra columns stayed black although the zone's worlds
  have geometry there.
  - They are mostly at the bottom corners, where a path starts or ends.
  - Each such polygon is held by the list of another camera path: 35% by
    another path of the same zone, 65% by a path of another zone.
  - From this path's nodes the 4:3 frame never sees it, so no list within
    reach holds it.
- **Void.** 1.3% has no geometry in any world of the zone or its neighbours.
  Nothing can be drawn there.

### Patch 0053

Everything is in `crash2_wide_slst.h`, for kind 3 and 8 paths only. Forward
paths behave exactly as in 0052.

- **The pool.** These lists become candidates, after the nodes' own and last
  in the budget:
  - every other camera path of the zone;
  - every path of its neighbour zones, mapped to this zone's world slots by
    world EID.

  The pool is built when the path changes, and rebuilt when the zone or the set
  of resident SLST entries changes. The largest seen held 7,529 polygons.
- **The 4:3 frame stays the game's.** A pool polygon is drawn only while every
  corner lies outside the 4:3 columns, all on one side. Another path's list
  says nothing about what this view should show inside the frame.
- **No wedges, by rule.** On these paths a candidate with a corner the GTE
  cannot place (behind the eye, or H >= 2 SZ) is never added. The look toward
  Crash could otherwise turn one into view.
- **Four SLST entries** rebuild cleanly but do not end on their own end list.
  - They differ by 1 to 6 polygons: three side-on paths of S000000F, and one
    forward path of S000001F.
  - Walking forward, the game builds exactly these lists.
  - 0052 refused the whole path. Side-on paths now use them, and each node
    still has to match the game's own list.

**Counters.** `wide_slst` gains `pool` (the current side-on path's pool) and
`pool_drawn` (pool polygons the last frame drew). `bad` still counts those four
entries.

### Checked without the game

**Every side-on view of the game, with the real camera** (one node in four,
3,069 views):

| | 0052 | 0053 |
|---|---|---|
| extra columns with geometry but nothing drawn | 1.52% | 0.45% |
| additions with a corner the GTE cannot place | 8 | 0 |
| 4:3 pixels not as the game's own list draws them | 0.241% | 0.241% |

- **Snow Go:** 1.11% to 0.02%.
- **The bonus rounds:** for example, 1.91% to 0.04% in S000000A.
- **The budget** (count + 32 additions) fills in 97 views (3.2%), in
  S0000026, S0000010, S000000F and S0000016, leaving pool polygons out. It
  stays as it is: it protects the frame's primitive buffer.
- **What stays missing** is mostly in those four levels, and in S0000018 to
  S0000021, where the resting camera fits the game least: offline, the game's
  own list leaves 2 to 7% of the 4:3 frame empty there.
- **Void,** where there is nothing to draw: 4.5% overall, 1.3% in Snow Go.

**`wide_frustum_sim`** now runs on the game's own camera, and on the extra
data the pool reads.

- **Cases.** 122: two per level on side-on paths and two on others.
  - 50 were judged on side-on paths, every one with a pool (the largest 3,655).
- **The checks.**
  - Every list equals `frustum_ref.py`'s.
  - All 11,326 additions on side-on paths have every corner placeable.
  - Every pool polygon lies wholly in the extra columns.
  - Other paths have no pool.
- **The cost,** at -O2:
  - the first frame on a node, pool build included: at most 0.74 ms;
  - later frames: 0.04 ms on average, 0.14 ms at most.

**`wide_slst_sim`.**

- 1,990 entries and 63,950 node lists, all equal to the reference.
- 4 entries do not end on their end list.
- Its stats buffer held 6 of the 12 values `crash2_wide_slst_stats` wrote (14
  now). Fixed.

### To check in play

- **Snow Go's ice cave and the bonus rounds.** The black triangles at the
  bottom corners and at path ends should be gone.
  - What can stay black is void, where the level has no geometry at all.
  - Inside the 4:3 frame nothing should change.
- **Forward rooms** should look exactly as with 0052.
- **The counters.** On a side-on path, `wide_slst` `pool` should be in the
  hundreds to thousands, and `pool_drawn` above 0 near path ends.

## Widescreen, part 14: a straight edge where the level ends

Patch 0054.

**The report.** After 0053 the black triangles at the edges of 2D scenes were
still there. The reporter's guess was right: the engine has nothing to render
there.

### What is left after 0053

The offline renders of part 13 (the game's camera, everything the runtime
draws) leave two kinds of black in the extra columns:

- **Void.** The zone's worlds and its neighbours' have no geometry there at
  all: 1.3% of Snow Go's side-on extra columns, and up to a fifth in a few
  views.
  - The cleared frame shows through, bounded by the polygon edges where the
    modelled scene stops: wedges, mostly in the bottom corners.
  - The 4:3 frame never looks there, so nothing was ever built for it.
- **Darkness the scene shows anyway.** Some levels have open black space in
  the 4:3 view itself (S000000F's caves between ceiling and platforms) and
  dark gaps between structures. More of the same at the edges is how the
  scene looks.

Nothing can be drawn into void. So the void wedges are covered, and the scene's
own darkness is left alone.

### Patch 0054: the void cover

In `crash2_wide_slst.h`, on kind 3 and 8 paths only, once the camera test has
judged the frame:

- **Coverage.** Every polygon the world draw gets is rasterised as the renderer
  will project it: its screen test applies, and so does the GPU's 1023 x 511
  limit.
  - Resolution: 2-px cells, over the extra columns and the 48 px of the 4:3
    frame beside them.
  - Each triangle is grown by 1.5 px, so the cracks between polygons do not
    count as void.
- **Left alone:**
  - void connected to the first 16 px of the 4:3 frame: open space the 4:3
    view shows too;
  - gaps inside the extra columns that do not reach the frame's edge;
  - a whole side whose 48 px of the 4:3 frame show more than 2% void of their
    own: a dark, open scene.
- **Covered:** the rest of the void that reaches the frame's outer edge, from
  that edge to its innermost cell.
  - It is one black, opaque POLY_F4 per side, written to the frame's primitive
    buffer.
  - It is linked into ordering-table slot 2046. The table is drawn from slot 0
    up (`0x8003BC5C` links it forward), and world polygons use slots 0-1904,
    so the cover is drawn after all the scenery.
  - No draw mode is set or left behind.
- **Over time.** The cover grows at once to hide a new wedge. When less is
  needed it holds for 15 frames, then shrinks 2 px a frame. It never enters
  the 4:3 frame.

**Counters.** `wide_slst` gains `cover_l` / `cover_r` (px, as drawn) and
`covered` (frames drawn with a cover).

### A bug the checks caught

The first version split quads into one triangle too many, which read a fifth
corner past the end of the array. Results then depended on what was in
memory: the simulator passed at -O1 and -O3 and failed at -O0 and -O2. It now
passes at every level, and under UBSan and ASan.

### Checked without the game

**Every side-on view of the game, with the real camera** (one node in four):

- **All levels.** 3,069 views; a cover in 588 (19%), covering 5.6% of the
  extra columns. Black there goes from 4.98% to 3.80%. Most of what stays is
  the dark caves' own darkness (S000000F 18.5%, S0000016 15.5%), left alone on
  purpose.

- **Snow Go:** a cover in 35 of 304 views. Black in the extra columns goes from
  1.34% to 0.05%, and 5.5% of the extra columns are covered.
- **S000000F's dark caves:** a cover in 22 of 192 views, against 145 before the
  dark-scene rule. 2.8% is covered.
- **The bonus areas of S0000018 to S0000021** are where the cover is most often
  drawn: black goes from 4.5-9.3% to 0.5-0.9%, and 22-34% is covered. The
  resting camera fits the game least there, so play may differ.

**`wide_frustum_sim`.**

- **Cases.** 144, of which 72 are side-on.
  - The export adds one side-on case per level that needs a cover; 33 cases
    have one.
- **The checks.**
  - `frustum_ref.py` scans the same way, in float32 in the C's order, and the
    C matches it exactly on every case.
  - Every cover is a black opaque POLY_F4, linked in slot 2046, and stays in
    the extra columns.
  - Other paths have no cover.
- **The cost,** at -O2: 0.18 ms per frame on average, 0.65 ms at most. The
  first frame on a node, with the pool build, takes at most 1.35 ms.

### To check in play

- **Snow Go's ice cave and the bonus rounds.** Where the black triangles were,
  each side should now show a straight black edge that slides in and out as
  the level runs out of geometry.
- **Elsewhere on side-on paths,** nothing should change: no cover in front of
  scenery that exists.
- **Dark caves** (black space between structures in the 4:3 view too) should
  look as before.
- **The counters.** `wide_slst` `cover_l` / `cover_r` show the cover's width.

## 60 FPS, part 24: the camera kept the field rate

Patch 0055 and the recompile-profile word `c2-60-cam-flag`.

**The report.** In Air Crash, getting on the skull platform at 60 FPS sends the
camera back to the start of the level, where it stays. It does not happen at
30.

### The skull platform's warp

Air Crash is `S0000020`. Its NSD lists two entrances: zone 01 (the level start)
and zone S3, the start of a secret jet-board section (S3 to S9, then S0) with
its own Crash spawn, board launch (S4) and drop-off (S9). There are two
`obj_warp_secret` platforms (WarpC, subtype 9): one in zone 09 of the main
route, and `#2` at the end of the secret section.

The end platform's warp is a handshake between Crash's script and the camera:

1. **The platform** (WarpC state 8) sends Crash event `0x1600`.
2. **Crash, state 65:**
   - spins, with the camera pointed at the spin (global 4 bit `0x20000`,
     target `0x8006CD70`);
   - fades to black, by waiting for global 106 to reach -1;
   - queues camera event `0x10`.
3. **The camera matches `0x10`** against the `0x1A8` records of the path it is
   on, at both of its ends (`0x80026334`). In Air Crash only S0 has one for
   it: at S0's end, "prepare a cut to link 1", which is zone 04's path at its
   start.
4. **Preparing the cut** (`0x8002655C`):
   - sets mode 7;
   - sends Crash the `0x198` event of that path: `0x3200`, with x, y, z in
     zone 04;
   - leaving state 65 runs its exit callback (sub 4819), which clears
     `0x20000`, so the camera targets Crash again.
5. **Crash, state 39:** moves to x, y, z and queues "cut now" (`0x400`).
6. **The camera cuts** to zone 04 and sets Crash's zone to it.

Coming back near the level start is the design. The camera and Crash arrive
together.

The zone-09 platform takes the same branch of state 65 (current level 32), but
no main-route path has a `0x10` record, so it cannot move the camera.

### Why 60 FPS breaks it

During steps 2 to 4 the camera keeps following its path toward the spin. That
motion (`0x80023E60`, called from the kind-0 handler `0x8002271C`) is one step
per call, with no frame-time term. The only tick reads in the camera code are
the angle-blend timer and a timestamp in the seat routine. With the camera
update running once per field, the camera moved twice as far per second.

S0's end links into zones 03 and 04, the level's start. A camera that reaches
it before the fade is over takes the link and leaves S0. Event `0x10` then
finds no record on the path it is on:

- no prepared cut;
- no event `0x3200`, so Crash stays at the platform;
- a camera at the start of the level with nothing there to follow.

This was derived from the code (GOOL disassembly of WarpC and WillC, the camera
update, the event and state-change routines), not observed in a trace. It is
the only route from a skull platform to the level's start that leaves Crash
behind.

### Patch 0055: the camera update keeps the script step

`crash2_60fps.h`, `C2_60_CAM_UPDATE`:

- **The flag.** The camera update (`0x80026CA0`) runs on script steps only,
  like the scripts it trades messages and events with.
  - Its entry hook stores a flag at sp+44 of the 48-byte frame, the unused
    padding: the crash pointer on a script step, 0 on a physics-only field.
  - The profile word makes `0x80026D04` load the crash pointer from that flag
    instead of `0x8005F38C`.
  - 0 takes the update's own "no crash" exit.
  - With the mode off the flag is always the pointer, so the function is the
    original.
- **The same rates as 30 Hz.** Every per-call rate inside the update is now
  the stock one: path motion, the mode-6 blend countdown, the look-at
  smoothing.
  - Objects update after the camera in each loop. A message queued on a script
    step drains at the next step's camera update, before that step's scripts,
    exactly as at 30 Hz.
- **Drawn ahead.** At 30 Hz under 60 Hz objects, the camera would visibly lag.
  - On a physics-only field the frame is drawn from a pose extrapolated from
    the last two updates' poses (position and angles, angles the short way
    round), by the share of a step since the last one.
  - The real pose goes back at the next loop's top, before anything runs. The
    game's own logic only ever sees poses its camera update made.
- **Held instead of drawn ahead** in these cases:
  - after a cut: the update set "cut now", `0x8005B99B`;
  - after a jump of more than 2048 units or 512/4096 of a turn in one step;
  - without poses from two consecutive steps;
  - after a RAM restore, when RAM is the camera and nothing is put back.
- **A/B:** `PSX_CRASH2_60FPS_CAMERA_RATE=0` brings back the per-field camera.
- **Heartbeat:** `native_60fps_camera_hz`, `native_60fps_camera_hooked`,
  `native_60fps_camera_extrapolated`, `native_60fps_camera_held`.

### Shipping it

- **The profile.** `launcher/crash2launcher/recompprofile.py` adds `0x80026CA0`
  to `mod_function_entry_funcs` and the `c2-60-cam-flag` word (`0x8C84F38C` to
  `0x8FA4002C`).
  - The workspace `game.toml` was re-applied.
  - `psxrecomp-game` regenerated one shard (`full_07`): the entry-hook call and
    the load.
  - The Play page's "Rebuild recommended" now also shows for a build that has
    the script words but not this one.
- **Overlay caches.** The profile moves the overlay config hash (d6362e32 to
  9ced4f89).
  - Both trees' caches were rebuilt from every stored capture: each tree's
    manifest and `.json.d` history, 1,063 files, merged to 523 distinct
    (address, bytes) captures with their execution evidence combined.
  - Result: 6 shards per tree, none failed.
  - The previous cache's seventh shard (region 0, `AF3DB9EB`) is covered by
    the new `8D44BB00`: same entry, same code CRC.

### Checked without the game

**`tuning/native120_sim`, scenario 9.** It models the patched camera update:
it reads the flag the load would, and moves the camera from the pose in RAM.

- **At 30 Hz:** an update every loop, nothing drawn ahead.
- **At 60:** 30.00 updates a second, and `camera_hz` reads 30.
  - Drawn x and the y angle advance by exactly half a step every field,
    through the angle's wrap.
  - Every update reads only poses it wrote.
- **Holds:**
  - a cut is held for one field, then drawn ahead again;
  - so is a 5,000-unit jump;
  - after a restore, the restored pose is kept and the fields are held until
    two updates have run.
- **The A/B switch:** 60 updates a second, nothing drawn ahead.
- **At 120:** three fields between updates, each 8 or 9 ticks' worth.
- **Suites:** all tuning checks (62) and launcher tests pass.

### To check in play

- **Air Crash's secret section, at 60 FPS.** Ride the end skull platform.
  After the fade, Crash and the camera should both be in zone 04 near the
  start, with the camera following.
- **Heartbeat.**
  - `native_60fps_camera_hz` should read about 30 with the gate open, and
    `native_60fps_camera_hooked` 1.
  - `camera_extrapolated` should grow with play.
  - `camera_held` should stay small: cuts and loads.
- **Elsewhere.**
  - The camera should look as smooth as before.
  - Its catch-up is the 30 FPS one again, a little lazier than the per-field
    camera was.
  - A sharp stop or turn can overshoot by up to half a step for one field.

**Found and not changed.** The screen fade (global 106, stepped once per loop
at `0x800164C4`) runs per field, so fades last half as long at 60. Scripts wait
for its end value, so only the look changes.

## Home menu: a card over the game (patch 0056)

From the design critique of the launcher and the Home menu. The launcher half
is ordinary repo code (`launcher/crash2launcher/`, checked by
`launcher/test_design_fixes.py`); this is the runtime half.

### What was wrong

- **Opaque and stretched.** A 640x480 sheet drawn over the whole window
  (`0,0,ww,wh`). Image fit, Window mode and Post-processing could not be judged
  from the menu that changes them, and the 8x8 font went wide and uneven on
  anything but 4:3.
- **Twelve rows, no grouping.** Names differed from the launcher's for the same
  settings ("GAME ASPECT", "FPS DISPLAY", "99 LIVES", "KEEP 2"), and Image fit
  cycled in mode order, not the launcher's.
- **Armed state leaked.** One flag reddened both Restart and Quit.

### The card

- **Panel.** 420x480 (`PM_W`, `PM_H`), four captioned groups: GAME, DISPLAY,
  ASSISTS, SYSTEM.
- **One row table.** `layout_rows()` fills `s_row_y` once; drawing and
  `psx_pause_menu_row_at` both read it, so the hit areas cannot drift from what
  is drawn. Captions and gaps return -1.
- **Note line.** Up to two lines of 47 characters for the selected row.
  Gameplay aspect and Image fit say "APPLIES WHEN YOU RESUME". For the fit this
  is a limit, not a choice: the held frame is a drawable capture
  (`HOLD_DRAWABLE`) with the old framing baked in, so a new fit has nothing to
  re-frame until the game runs. Post-processing does update live (the existing
  `glpfx_end` redraw), and so does Window mode (the capture is re-letterboxed to
  the new drawable).
- **Colour.** Crash orange `#F07E1E` is the only accent: 5.3:1 on the selected
  row, 6.3:1 for the title. The dimmest text, the persistence line, is 4.8:1.

### Placement and backdrop

- **`psx_pause_menu_place(sw, sh)`.** A whole-number scale when it is at least
  2 (stepping down from 3 or more if the card would come within 16 px of the
  top and bottom); otherwise 0.96 of the fitting scale. Results:
  - 1080p and 1440p: 840x960;
  - 4K: 1680x1920;
  - 720p: 604x691, fitted.
- **GL/D3D12: `gl_dim_frame()`.**
  - It draws a 1x1 grey (`PSX_PAUSE_BACKDROP_KEEP` = 96) through the present
    program with `glBlendFuncSeparate(ZERO, SRC_COLOR, ZERO, ONE)`. That is a
    multiply that keeps destination alpha, whatever the program writes.
  - The gl12 layer maps `SRC_COLOR` to `D3D12_BLEND_SRC_COLOR`.
  - All five `hold_capture_drawable()` calls come before `gl_swap_with_osd()`,
    so the 8 ms re-presents never dim an already dimmed frame.
- **SDL renderer.** A black `SDL_RenderFillRect` at alpha 255-96, then the
  panel at its rect on the logical size.
- **Mouse.**
  - GL/D3D12: window coordinates are scaled to the drawable.
  - SDL3 renderer: `SDL_RenderCoordinatesFromWindow`.
  - SDL2 renderer: already logical.
  - Then the pointer goes through `psx_pause_menu_place`.

### Checked without the game

The scratchpad harness compiles `psx_pause_menu.c` with stubs.

- Rows 0..11 hit-test in order, 20 px each.
- Every caption, and both sides of the rows, return -1.
- The last row ends above the note, and the note ends above the footer.
- Placement is inside, centred and whole-pixel from 1920x1080 up, checked at
  640x480 through 3840x2160 and at portrait 1080x1920.
- Renders of each state, and a 1080p and 720p composite over a stand-in frame,
  looked right.
- Both trees build with no new warnings.

### To check in play

- **Backdrop.** Open the menu: the game shows dimmed behind a centred card.
- **Live changes.** Post-processing and Window mode change behind it. Image
  fit changes on resume.
- **Armed state.** Arming Quit reddens Quit only.
- **Mouse.** Hover and click land on the rows, windowed and in exclusive full
  screen. The D3D12 renderer is also worth one look.

## Original 4:3 (patch 0057)

**The report.** There was no original PS1 aspect option: 4:3 with black bars at
the sides.

The renderer could always pillarbox. It only did so when four settings agreed:

- aspect 4:3;
- Image fit Letterbox;
- Zoom "Follow image fit" or "None", and Stretch 0;
- no overscan crop. Trimming the blank lines makes the kept band wider than
  4:3 (`overscan_aspect_mul`), which narrows the bars.

Picking 4:3 reset none of these. A 4:3 left on Stretch, or on Enhanced's stretch
100 and 12/12 crop, filled a 16:9 window.

**Original 4:3 is a named state of those fields, not a new setting.**

- **Launcher** (`config.ORIGINAL_43`, `is_original_43`, `apply_original_43`):
  - Gameplay aspect offers "Original 4:3 - black bars at the sides". It sets the
    framing and leaves image quality, the screen shape and the window alone.
  - A 4:3 framed any other way shows as "4:3 - custom framing". That entry
    exists only while it is the state.
  - The Authentic preset shows as Original.
- **Home menu:**
  - GAMEPLAY ASPECT index 0 reads ORIGINAL 4:3 under the same test
    (`pause_menu_framing_is_original`). Picking it applies the framing at once.
  - `MENU_PREF_OVERSCAN` writes the crop back, so `ingame.py` keeps it as
    "game aspect original 4:3".
  - `g_overscan_crop` in main.cpp mirrors the crop, because the renderer has no
    getter for it and the D3D12 dispatch would need one in four files.

A window sized to match the gameplay aspect is 4:3 itself, so it shows no bars.
Fullscreen, or a 16:9 output resolution, does.

**Checked:**

- `launcher/test_original_aspect.py`;
- `test_ingame.py` (the runtime writes exactly the keys the launcher reads);
- the Home menu harness (both labels, the redraw, the width);
- both trees build.

**To check in play:**

- Pick Original 4:3 in the launcher, in a 16:9 window: there should be bars at
  the sides, with sharpness and filtering as before.
- In the Home menu, cycle GAMEPLAY ASPECT to ORIGINAL 4:3 and resume: the same
  picture, and the launcher shows it on return.

## Image fit Original 4:3 (patch 0058)

**The request.** An image fit called Original 4:3, with no black bars at the
top and bottom.

**Why 0057's Original 4:3 had them.** It showed the whole 240-line frame, and
Crash 2 draws only rows 12..227 of it. The game's own blank lines were the
black bands.

**What it is now.** Original 4:3 is an image fit: `scaling_mode = "original"`
in the launcher, fit mode 4 (`PSX_PAUSE_FIT_ORIGINAL`) in the runtime.

- **How it is drawn.** It is letterbox with the game's blank lines trimmed:
  `config.ORIGINAL_TRIM` = 12/12, passed as `PSX_ORIGINAL_TRIM`.
  - The trim replaces the configured overscan crop while this fit is on.
  - The kept band is the 4:3 frame less those lines, 40:27.
    `overscan_aspect_mul` sizes it without stretching.
  - So the picture fills the window's height, with bars at the sides only.
    In a 2560x1440 window it is 2133x1440, with 213 px a side.
- **No renderer change.** It is `present_set_fit()` in main.cpp. The other
  fits get the configured crop back.
- **Launcher.**
  - Image fit lists it first: "Original 4:3 - PS1 picture, bars at the sides
    only".
  - Picking it sets the 4:3 gameplay aspect and turns zoom and stretch off.
    Zoom, Stretch, Vertical pan and Overscan crop are greyed out while it is
    on.
  - A wider gameplay aspect ends it and goes back to Letterbox.
  - An Auto window is shaped 40:27, so it shows no bars at all.
  - The Authentic preset uses it.
- **Home menu.**
  - IMAGE FIT cycles ORIGINAL 4:3, letterbox, fill, fit width, stretch.
  - Picking ORIGINAL 4:3 stages the 4:3 aspect.
- **0057 is gone.** Its Gameplay aspect entry and its aspect-row label are
  removed: there is one Original 4:3, under Image fit. The overscan keys 0057
  wrote back are gone too.

**Checked:**

- `launcher/test_original_aspect.py` (rewritten), with every launcher script
  passing;
- the Home menu harness (label, note, width);
- both trees build;
- 0058 round-trips byte-for-byte.

**To check in play.** Pick Original 4:3 in a 16:9 window or full screen. The
picture should fill the height with bars only at the sides, and should not be
stretched. The Home menu should show ORIGINAL 4:3 under IMAGE FIT.

## Widescreen, part 15: forward paths measured; no more wedges (patch 0059)

Every real-camera measurement before this covered side-on paths only. The
script behind parts 13 and 14 was never committed.

### The sweep: `tuning/wide_view_sweep`

**What it covers.** `sweep.py` takes every camera path:

- at every 4th node and the last (or `--nodes part13`: 1, 5, ... short of the
  last);
- with the game's resting camera (`export.level_paths`, shared with
  `wide_frustum_sim`);
- at 16:9.

**What it draws.** Three pictures per view, rasterised by `raster.c` far to
near:

- `game`: the game's list;
- `drawn`: what the hook draws, with the same rules as `frustum_ref.draw` and
  the reasons kept, plus the void cover on side-on paths;
- `ref`: every polygon of the zone's worlds.

Corners behind the eye are drawn where the GTE puts them: the world renderer
has no near test.

**What it reports.**

- 4:3 pixels the hook changes, split into holes filled and pixels drawn over;
- in the extra columns: gap, only-in-a-neighbour's-worlds, void, covered,
  wedge and see-through ("wrong");
- for every gap pixel, why its polygon was not drawn.

**Variants** let rules be measured before they are written in C. Run it with
`sh build.sh` (raster.dll), then
`python tuning/wide_view_sweep/sweep.py --variants hook,0054,pool --png 10`.

**Against parts 13 and 14** (side-on, `--nodes part13`):

- **Matches:**
  - 3,069 views, exactly;
  - Snow Go (S000000E) exactly: covers in 35 of 304 views, black 0.05%,
    covered 5.55%;
  - every merged list against `frustum_ref.draw` (`--verify`).
- **Darker than recorded:**
  - S000000F: 38.7% black, against 18.5% recorded;
  - S0000016: 45.8%, against 15.5%;
  - bonus areas S0000018-21.
  - Growing coverage the cover scan's way explains only 3 points. The
    recorded figures came from that lost script, so overall black reads 7.5%
    here against 3.8% there.
- **Comparisons below use this tool before and after**, so they hold either
  way.

### Forward paths, with the 0054 rules

14,495 views; the camera model fails in 122.

- **4:3 frame changed by the hook: 0.419%.**
  - Holes filled 0.305%. Mostly views where the resting camera does not match
    play: the warp room S0000002 (8.5%), S0000028 and S0000029.
  - Drawn over the game's pixels: 0.114%.
  - Wedges: 0.102%, against the game's own 0.053%.
- **Wedges.** 416,153 of the 1,280,809 additions had a corner the GTE cannot
  place.
  - The world renderer projects such a corner near twice its camera offset
    from the centre, and draws it.
  - In S0000015 two additions covered the whole frame. In Air Crash
    (S0000020) a slab covered the top.
- **Gaps: 3.97% of the extra columns.** Of these:
  - 71% are polygons other paths of the zone list;
  - 17% are polygons neighbour zones' paths list;
  - 11% are in no list.

### Patch 0059: the side-on wedge rule on forward paths too

`c2sl_fwd_keep()`: forward additions must have every corner placeable.

| | 0054 | 0059 |
|---|---|---|
| 4:3 changed | 0.419% | 0.369% |
| 4:3 wedges | 0.102% | 0.053% (the game's own) |
| extra-column wedge pixels | 0.24% | 0.16% (the game's own) |
| gaps | 3.97% | 3.97% |

The gaps did not change: the refused polygons drew nothing useful.

`wide_frustum_sim` now exports a forward case per level with a wedge refused
(176 cases). C equals the reference on every one, and 3,916 wedges were
refused.

### Not shipped: a forward pool

Letting forward paths read the side-on pool takes gaps from 3.97% to 1.23%,
and see-through from 0.91% to 0.47%, with the 4:3 frame unchanged.

But S0000009, a tube scene, draws only a ring and leaves black around it even
inside the 4:3 frame. With the pool, the tube walls appear at the screen edges
only.

A guard is drafted in `frustum_ref.band_open`: no pool on a side whose 48-px
band of the 4:3 frame is a dark, open scene, which is the void cover's own
test. It is the `pool_band` variant in the sweep, not yet measured or written
in C.

### To check in play (Developer mode, a widescreen aspect)

- **Forward runs.** No polygon should flash across the screen near the camera.
  Watch S0000015 and S000001A, which look like tunnel levels, and Air Crash.
  The margins should look as before otherwise.
- **Side-on views.** The latest report there ("still wrong") is open. The
  sweep's worst side-on views are its `side_*.png` pictures. A screenshot or a
  level name would say which of them it is.
