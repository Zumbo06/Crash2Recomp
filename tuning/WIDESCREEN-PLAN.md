# Crash 2 native widescreen — migration tracker  (CLOSED)

**Outcome: the migration is closed and `native_wide` stays false.** Not because
native-wide fails — Phase 3 proved it works — but because there is nothing for
it to show. Crash 2's level meshes are authored to the 4:3 frustum and end
there, so widening the field of view exposes void no matter which mode does the
widening. Jump to **RESULT** at the bottom; the phases above are kept because
the measurements in them are what closed the question.

This also identifies the root cause of the original "widescreen edge issues"
report: it was never a cull bug.

Background and the corrected diagnosis are in `NOTES.md`, sections
**"Widescreen, part 2/3/4"**. Part 2 replaces the "Crash 2 has no per-game
viewport data" explanation that three files and this repo's own notes
previously carried.

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
| Consumer | `func_80041E5C`, 89 instrs; repurposes `$sp` as the list end pointer |
| Call sites | `0x80011CB0` **and** `0x8001845C` |
| Zone | `zone = [[0x800608CC] + 16]`; `nw = [zone]`, 1..8 worlds, 48 bytes each from `zone+4` |
| Per-world | `+4/+8/+12` origin xyz, `+16` header, `+20` tris, `+24` quads, `+28` verts |
| Id encoding | `(world << 13) \| index`, bit `0x1800` marks a quad |

> Not established: whether those two blocks are a double-buffer pair.
> `mipsdis` reports `GAP` for every jump in `func_80029768`, so the call
> structure is not recoverable from the emitted comments, and `[0x8005F398]` is
> read **only** at teardown (`func_800297C8`) — never by a renderer, which is
> not how a double buffer behaves. The capacity figure does not depend on this:
> the consumer takes its list from `[0x8005F390]`, and that block is 3044 bytes.

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

## RESULT — Phase 4 answered, migration closed

Probe run on an outdoor level with native-wide active: **`cand` ~ 0**. The zone
meshes hold essentially no geometry that projects into the widened view but was
left out of the authored draw list.

**The draw list was never the limit**, so extending it cannot help and the
native-wide migration is closed for this title. Full reasoning in `NOTES.md`,
"Widescreen, part 4".

The same result explains the original "widescreen edge issues" report: Crash 2's
levels are authored to the 4:3 frustum and the world ends there. Any FOV
widening — mode 1's squash exactly as much as mode 2's extra columns — walks
past the authored edge and exposes the void. It is a level-data limitation.

Three independent measurements agree: `ovh_prims = 0` (nothing ever submitted
past the window), zero screen-extent cull immediates in the executable (no cull
to widen, because nothing is culled), and now `cand` ~ 0 (no omitted content).

### Phases 2 and 5-9: closed, not deferred

They existed to serve the native-wide migration. With no content to reveal
there is nothing for a title profile, a visibility extension, a HUD pass or a
transition-safety pass to do. Phase 6's allocation guard (patch 0014) is kept —
it is correct regardless and protects any future native-wide use.

### Settings

`native_wide` back to **false**. Mode 2 costs ~280 MB of GL surfaces to show the
same void the squash shows.

| option | result | cost |
|---|---|---|
| 4:3, `letterbox` | fully correct image | pillarbox bars |
| 4:3, `fill` | fills a 16:9 screen, no void | crops top/bottom — real in a platformer |
| 16:9 squash (mode 1) | fills the screen | the void at the edges = the reported artifact |

### Only remaining tractable improvement

`[widescreen.backdrop] x_sites` (`psx_ws_backdrop_x`): the parallax 2D backdrop
computes screen-X without the GTE, so it misses the squash and stops short of
the widened FOV. Configuring those per-game sites pulls the sky/backdrop out to
the new edges. It covers the BACKDROP part of the void only — terrain that ends
still ends — but that is the cheap part, and it is configuration, not code.

### Not excluded

The probe sees only the current zone's resident worlds (`nw <= 8`). Side
geometry living in non-resident adjacent zones would read as absent. Separating
that from "does not exist" means following render-data prefetch — streaming more
level data per frame, with memory and load-time cost. A much larger problem than
widescreen.
