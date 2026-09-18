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
