# Crash 2 native widescreen — migration tracker

Migrating from the GTE X-squash hack (mode 1) to the framework's native-wide
compositor (mode 2) with a Crash 2 rendering profile.

Background and the corrected diagnosis are in `NOTES.md`, section
**"Widescreen, part 2: the native-wide rejection was wrong"**. Read that first;
it replaces the "Crash 2 has no per-game viewport data" explanation that three
files and this repo's own notes previously carried.

---

## Phase 1 — Baseline

### Fixed identifiers

| | |
|---|---|
| Repo revision | `7fb96e600ca4b42c15e1eb4b565b80edf6a903e6` (2026-09-09) |
| Game executable | `SCUS_941.54`, 327,680 bytes |
| SHA-1 | `aabdade44a4ca863f71224daef51c45f34fb1bf8` |
| SHA-256 (head) | `6e5b2449310b1ed87915f53c38f296f2…` |
| Codegen hash | `0x5cb10a8a` |
| Toolchain | clang 22.1.8, cmake 3.31.12 |
| Release exe | `build-clang/Crash_Bandicoot_2_Recompiled.exe` |
| Diagnostics exe | `build-debugtools/…` — both rebuilt and in sync |

### Derived rendering facts

- **Crash 2 renders 512 px wide.** Established three ways: the only display
  width for which `ws_nw_configured_offset()` yields the 85 px recorded in
  NOTES; `crash2_wide_probe.h` rescaling by `512.0/320.0`; and the same file
  bounding polygons against `512 + margin`.
- At 16:9 the per-side reveal is **85 px** (mode 2) but `psx_ws_x_margin()`
  returns **53 px** (mode 1), because it hardcodes `160` = half of *320*. For a
  512-wide title every mode-1 margin is short by 1.6×. Latent today — no cull
  hook consumes it — but it bites the moment one is enabled.

### Inventory: experimental vs persistent

The vendored tree is gitignored; `tuning/patches/` is the authoritative record.
`_build/psxrecomp-src` has **drifted** (it carries `freeze_dump_policy.*` and a
`tests/` tree the vendored copy lacks), so `diff -rq` against it cannot separate
our changes from upstream movement — do not use it as a diff base.

Files present in the vendored tree and not upstream:

| File | Status |
|---|---|
| `psx_pause_menu.{c,h}` | patch 0009 |
| `overlay_codegen_hash.h` | generated |
| `spu.c.trace` | scratch, not code |
| `crash2_wide_probe.h` | **was unrecorded** → now patch 0013 |

`crash2_wide_probe.h` is an opt-in experiment (`PSX_CRASH2_WIDE_PROBE=1`),
compiled in via `interrupts.c` and inert unless that variable is set. It hooks
`0x80041E5C` (verified present, prologue `0x27BDFFBC`), re-projects the zone
geometry itself, and appends polygons visible in the widened field to the draw
list. Its fixed 4096-entry caps and average-depth ordering make it research,
not an implementation — but it is the only code here that addresses the real
mechanism, so it is now tracked rather than floating.

### Still open in Phase 1

Reference scene capture — **requires running the game** (see Handoff).

---

## Phase 2 — Crash 2 profile

Not started. Note for when it is: `[widescreen.cull] auto_screen_x` is folded
into the codegen signature (`h.u32(c.ws_auto_screen_x_cull …)`), so toggling it
changes the codegen hash and forces a recompiler rebuild plus regeneration —
the same desync class that once silently dropped every overlay to the
interpreter. It is also **useless for this title** (see Phase 4).

---

## Phase 3 — Validate the compositor  ✅ PASS

Measured in-game at frame 13696:

    mode 2   squash [1,1]   nw_extra 170   present_native_43 0
    x_margin 85   activation_margin 85   game_mode 1   gte_verts 206

All four completion criteria met. Three things this settles:

- **The display really is 512 wide.** `nw_extra = 170 = 2 x 85`, and 85 is
  `(512*12 + 36)/72`. No other width produces it.
- **`gte_game_mode` is what opens the gate.** `game_mode 1` with
  `last_tag_frame` at its never-set sentinel — Crash 2 emits no sprite tags, so
  the GTE-activity branch is the only thing classifying gameplay. Without it
  native-wide stays shut, which is exactly the state the old notes recorded.
- **Mode 2 needs no per-game viewport data**, now demonstrated rather than
  argued.

Also inert as expected, confirming they are Tomba-specific: `aspect_cone` and
`terrain_angle` counters all zero.

Probe: `_build/ws_phase3.py` — attach-only, never launches the game. (The
debug server is one-command-per-connection: `accept` -> `recv_line` -> respond
-> `close`. Reusing a socket gets WinError 10053.)

---

## Phase 4 — Static-world visibility  ← the critical milestone

**`auto_screen_x` cannot serve this title, and this is settled.** The detector
requires a width compare *and* a height compare in one function. Census of all
69,095 instructions in `generated/`:

    SLTI (0x28)  271 sites     SLTIU (0x2C)  263 sites
    width  imms 0x140 / 0x141 :  0 / 0
    height imms 0x0E0 / 0x0F0 / 0x0F1 :  0 / 0 / 0
    (0x200/0x201: slti 1 / 2 — not a screen-extent pair)

Zero of either. Crash 2 culls by building an authored **draw list** of polygon
ids, which is the mechanism `crash2_wide_probe.h` intercepts. Widening
submission means extending that list, not widening a compare.

### The measurement that scopes the work

`ovh_prims = 0`, and `last_ovh_frame` is still the never-set sentinel — the
>=4-prim threshold has not been reached **once since boot**. `ws_note_overhang`
(gpu.c:4791) counts polygons whose raw SX extent crosses outside
`[-24, ws_disp_w()+24]`, measured against the real 512 width and pre-draw-offset
so our own injection cannot feed back into it.

So the game submits **zero** geometry beyond its own window. The compositor is
revealing 170 px the game will never draw into. Nothing short of making Crash 2
submit more can fill those columns.

### Structures mapped

| | |
|---|---|
| Allocation | two `0x0BE4` = 3044-byte blocks at `func_80029768` |
| Pointers | `[0x8005F390]` + `[0x8005F3D0]` take the first result; `[0x8005F398]` the second |
| Layout | `+0` count (halfword), `+2` zeroed at alloc, `+4..` polygon ids |
| **Capacity** | **1520 ids** — the list is read from `[0x8005F390]`, a 3044-byte block |

> Not established: whether those two blocks are a double-buffer pair.
> `mipsdis` reports `GAP` for every jump in `func_80029768`, so the call
> structure is not recoverable from the emitted comments, and `[0x8005F398]` is
> read **only** at teardown (`func_800297C8`) — never by a renderer, which is
> not how a double buffer behaves. The capacity figure does not depend on this:
> the consumer takes its list from `[0x8005F390]`, and that block is 3044 bytes.
| Consumer | `func_80041E5C`, 89 instrs; repurposes `$sp` as the list end pointer |
| Call sites | `0x80011CB0` **and** `0x8001845C` |
| Zone | `zone = [[0x800608CC] + 16]`; `nw = [zone]`, 1..8 worlds, 48 bytes each from `zone+4` |
| Per-world | `+4/+8/+12` origin xyz, `+16` header, `+20` tris, `+24` quads, `+28` verts |
| Id encoding | `(world << 13) \| index`, bit `0x1800` marks a quad |

The render driver (`func_80011800`) packs each world descriptor to scratchpad
`0x1F800280`, reading `zone+8,12,16,20,24,28,32` — matching the probe's
world-entry model for `wi=0` exactly, so the probe's view of the data is sound.

### Two defects in the probe, now known

1. **It covers only one of the two call sites.** It guards on
   `cpu->gpr[31] == 0x80011CB8`, so the call at `0x8001845C` (returns to
   `0x80018464`) passes through untouched.
2. **It can never grow the list.** `room = count - 1 - nk` caps additions to
   the slots freed by polygons that became invisible, so if the authored list
   is fully visible it adds nothing even when revealable content exists. It
   allocates its own 4096-entry buffer, so the game's 1520-id buffer is not the
   binding constraint — the restriction is self-imposed. The real ceiling is
   GPU packet/OT capacity, which must be measured before lifting it.

### Open

Where the list is *filled* is still unlocated. The buffer is heap-allocated and
written register-indirect, so it cannot be found by address grep, and it may
live in overlay (level) code rather than the main executable. The probe's own
counters answer the question that actually matters — how much content is there
to add — without needing to find the filler first.

---

## Phases 5–9

Not started; each depends on Phase 4.

---

## Handoff — the Phase 4 experiment

Phase 3 is done. The next question is the one that decides how Phase 4 is
built, and it is cheap to answer because the instrument already exists:

> Is there revealable geometry in the zone meshes at all, and how much?

`crash2_wide_probe.h` answers it directly. It re-projects each world's polygons
itself and marks the ones visible in the widened field that the authored list
omitted. Patch 0015 fixes its reporting first — as found, it clamped the count
to `room` and then hit `if(!ne)return` **before** logging, so the single most
informative case (list fully visible, `room == 0`, nothing addable) printed
nothing at all and read as "the probe never ran". It now reports unconditionally
for 40 frames:

    C2 wide probe: count=<list> kept=<still visible> cand=<REVEALABLE> added=<permitted> room=<slots free> site1=<n> site2=<n> other=<n>

**`cand` is the answer.** It is the number of polygons the probe found visible
in the widened view that the authored list omitted, *before* any cap. `added`
is only what the probe's self-imposed `room` limit then allowed through.

`site1`/`site2` count the consumer's two call sites (`0x80011CB0` /
`0x8001845C`); only site 1 is intercepted, so a large `site2` means a
list-extension covering one site would be a half-fix.

Run it **with native-wide on**, so anything it adds is actually presented:

1. Keep the Phase 3 launcher setup (16:9, native-wide, supersampling 2,
   debugtools build, developer mode).
2. Set the environment variable before launching:

       PSX_CRASH2_WIDE_PROBE=1

   In the launcher this is easiest from a terminal:

       $env:PSX_CRASH2_WIDE_PROBE = "1"
       .\Crash2Launcher.exe            # or launch the exe directly

3. Load an outdoor level with distant scenery — a wide jungle or ruins area is
   a better test than a corridor or a tunnel.
4. Capture the game's stdout (the launcher's **Log** page → Save to file) and
   paste the `C2 wide probe:` lines.
5. Say whether the left/right margins visibly gained scenery.

**Reading the result**

| `cand` | meaning | consequence |
|---|---|---|
| consistently > 0 | the zone meshes DO hold geometry outside the 4:3 view | extend the draw list; the probe becomes the basis of the implementation. If `cand > room`, lifting the cap is the next step — against a measured GPU packet budget, not blindly |
| ~0 | the meshes themselves stop at the 4:3 frustum | a list change cannot help. Filling the margins would need the level data to carry more geometry, which is a different and much larger problem — and the honest answer may be that mode 1's stretch is the better trade for this title |

`added` vs `cand` separates "nothing to reveal" from "the probe refused to
reveal it" — the distinction the original code destroyed by returning before it
logged. Ignore `added` when judging feasibility; it measures the probe, not the
game.

**Do not** change aspect or widescreen mode from the in-game pause menu during
any of this: toggling mode live rewrites emitted cull constants mid-frame and
is the known crash path. Launching straight into native-wide never crosses it.

### Still outstanding from Phase 1

Reference scene capture: a 4:3 and a 16:9 screenshot of the same spot, for the
comparison set. Convenient to grab in the same session.
