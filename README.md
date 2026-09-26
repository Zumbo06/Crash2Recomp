# Crash Bandicoot 2 Recompiled

> **An unofficial, non-commercial fan project.** Not affiliated with,
> authorised or endorsed by Activision, Naughty Dog or Sony Interactive
> Entertainment. Crash Bandicoot is a trademark of Activision Publishing, Inc.
> **No game data is distributed here — bring your own disc.**

The PlayStation game *Crash Bandicoot 2: Cortex Strikes Back* translated to
native code and run directly, rather than emulated. A launcher takes a disc
image you already own, builds the game on your machine, and runs it.

> **Work in progress.** The game is completable from start to finish, but there
> are still minor sound and graphical issues. Treat this as a preview rather
> than a finished release, and expect rough edges.

**This project contains no game data.** No game code, audio or disc content is
distributed here. You supply your own disc image; everything derived from it is
produced locally and never leaves your machine. 

---

## What you need

| | |
|---|---|
| Windows | 64-bit, 10 or later |
| A disc image you own | `.cue` with its `.bin` alongside, or a `.chd`. The build targets the North American release, serial `SCUS-94154`. |
| Disk space | About 3 GB while building; roughly 100 MB once built |
| Time | Five to twenty minutes for the first build, once |

No PlayStation BIOS is required. OpenBIOS, a free MIT-licensed replacement, is
included.

## Getting started

1. Run the launcher.
2. Open **Setup** and choose your disc image. It is checked for complete
   tracks, whole sectors and the boot serial, then hashed.
3. Press **Build the game**. This translates the game to C and compiles it.
   Live output appears below the progress bar.
4. When it finishes, the **Play** page is ready.

Settings apply on the next launch. If the game is already running, the Play
page tells you to relaunch.

## While playing

| Key | |
|---|---|
| **Home** | Pause menu: restart, display options, quick save/load, and live lives/Aku Aku assists. On a controller, Guide or Start+Select. |
| F5 / F9 | Quick save / quick load |
| F7 | Save state slots |
| F8 | Rewind |
| F | Toggle the performance readout |

Saves live in `userdata/`, next to the launcher.

## Controls

**Settings > Input** remaps both the keyboard and the controller for player 1.
For a controller, click a PS1 button and press the button you want; each PS1
button can have two. Press-to-assign hears Xbox-compatible controllers; any
other pad (a PlayStation controller without Steam Input, say) is assigned from
the list and still works in the game. Names are positions: A is the bottom
face button on every pad, Cross on a PlayStation controller. The stick deadzone
is set there too.

## Video

**Settings > Video** sets internal resolution, aspect and output, and has a
**Post-processing** card: anti-aliasing (FXAA or SMAA), sharpening, colour
controls, bloom, vignette, film grain, and *Smooth dithered art*, which
softens the checkerboard dithering painted into many textures. The Authentic
preset leaves all of it off; Enhanced turns on SMAA and light sharpening. The
Home menu's **POST FX** row switches it on and off while you play, so you can
compare.

The renderer is OpenGL by default. **Direct3D 12 (experimental)** draws the
same picture through Direct3D 12 - the same renderer built for the other
API - for systems whose OpenGL driver misbehaves. If it cannot start, the log
says so and the game runs on OpenGL.

## Mods

The **Mods** page lists what is installed, switches individual features on and
off, and exposes whatever settings each one declares. Four enhancements ship
with the launcher and appear once the game has been built:

| | |
|---|---|
| **PGXP Precision** | Sub-pixel vertex precision and perspective-correct texturing, so polygons stop wobbling and large floor textures stop warping. Needs internal resolution 2x or higher to see. |
| **Fast Loading** | Speeds up the wall-clock pacing of loads. Nothing the game can observe changes, but it does run faster while a load is detected. |
| **CD Speed** | Shortens loads by dividing the emulated drive's sector delay. Unlike the above this changes *when* the game receives CD interrupts, so raise it gradually. |
| **Bezel Artwork** | Draws an image of your choosing in the letterbox or pillarbox margins. |

All four are off by default. Changes apply on the next launch.

To add one, press **Install a .psxmod...** and pick the file. It is checked
before anything is written, so a package with an unexpected layout is refused
rather than half-installed. If a selection cannot work, the page says so before
you launch rather than leaving you to read an error on startup.

**Settings -> Performance has an experimental, opt-in 60 FPS mode.** It runs
Crash 2's own game loop at 60 instead of 30 - a real change to how the game
executes, not a smoothing filter on the picture. Once enabled it stays at 60
rather than dropping you back to 30.

It is off by default and is a preview. World movement keeps the correct speed
(the engine already scales motion by measured frame time and this reuses
that), but scripted sequences, cutscene pacing, music and sound timing, bosses,
vehicle levels and FMV transitions have **not** been verified across the whole
game. It also runs the emulated PlayStation processor at 200% so a busy frame
can fit into one screen refresh, which is the furthest this gets from how the
console behaved.

While the game runs, the Play page says whether 60 is holding and, if not,
which of two limits you are hitting, because they need opposite responses. If
the machine is behind on *drawing* the frames, lower **Internal resolution** on
the Video page - at 5x the renderer draws twenty-five times the pixels of
native and running at 60 doubles that again, so it is usually what runs out
first. If instead some frames in a scene need longer than one refresh,
internal resolution will not help; that is the emulated console running out of
time inside the frame.

If something behaves strangely, turn it off and see whether the problem goes
away - that is a useful thing to report. It changes timing, not saved data.

**120 FPS (experimental)** sits under the 60 FPS switch and needs it on. It
runs the game loop at 120: physics every 120 Hz refresh, while scripted
animation and music keep their normal speed. It is only worth it on a 120 Hz
or faster display. It runs the emulated processor at 400% and turns frame
interpolation off. When a scene cannot hold 120 it steps down to 60 by
itself and tries again later, and the Play page says which it is doing. How
the physics behaves at 120 across whole levels has not been verified - if
something moves or collides strangely, compare with it off.

The same page offers **Keep 99 lives** and a **Damage** setting with three
positions, all off by default:

| Damage | |
|---|---|
| Off | normal rules |
| Keep 2 masks | Aku Aku is held at two, so a hit is always absorbed |
| No damage | Crash is held in the invincible state the gold Aku Aku mask uses |

**No damage** permits everything that mask permits - it simply does not lapse
after fifteen seconds. It does not stop falls, crushing or drowning, does not
make enemies die on contact, and switches itself off during the attract-mode
demos. Because it holds an invincible state permanently, a scripted sequence
that expects Crash to be interruptible could in principle stall; if you meet
one, drop to *Keep 2 masks*.

These change saved progression: lives and masks are written to the memory card
as you play, so switching an assist off stops further writes but cannot undo
values already saved. The Home-menu rows apply immediately for that session,
while launcher choices apply on the next launch.

## If something goes wrong

The **Log** page captures everything the game prints, and has a **Save to
file** button. If the game crashes, the launcher says so and brings that page
forward. Attach the saved log to any bug report.

**Sound crackles.** Settings → Audio → Latency → Safe.

**The game will not start.** Confirm the build finished on the Setup page. If
it did not, the log there records why.

## Developer mode

Settings → Performance → Developer mode reveals an **Advanced** page holding
measurement tools: a TCP debug server, interpreter fallbacks, audio path
overrides and tracing. They exist to investigate bugs and most of them make
the game slower or worse. They stay switched off, and unreachable, unless you
turn this on.

## Legal

An unofficial, non-commercial fan project. Not affiliated with, authorised or
endorsed by Activision, Naughty Dog or Sony Interactive Entertainment. Crash
Bandicoot is a trademark of Activision Publishing, Inc.

Licences:

- **psxrecomp**, the recompiler and runtime: PolyForm Noncommercial 1.0.0.
  Free to use and share for any non-commercial purpose. Selling it, or
  bundling it with anything commercial, is not permitted.
- **OpenBIOS** (PCSX-Redux): MIT. Shipped as `bios/openbios.bin`; the notice
  is in `bios/OpenBIOS.LICENSE` and must travel with it.
- **SDL3**: zlib licence.
- **Qt / PySide6**: LGPL v3. The Qt libraries ship as separate files and may
  be replaced.

Dumping a disc you own for personal use is permitted in some countries and not
in others. Check where you live.

---

## Building from this repository

The launcher is the supported route. Directly:

```
python launcher/main.py           # needs Python 3.11+ and PySide6
_build/build_clang.ps1            # builds the runtime, both trees
```

Runtime changes are made in the gitignored vendored tree at
`_build/Crash2Recomp/psxrecomp/` and recorded as patches in `tuning/patches/`,
with the reasoning in `tuning/NOTES.md`. Read `tuning/NOTES.md` before changing
anything in the runtime: it records what has already been tried, what was
measured, and which theories were disproved.

Tests:

```
python launcher/test_settings_coverage.py   # every setting has a control
python launcher/test_ui_smoke.py            # the UI builds and its paths run
```
