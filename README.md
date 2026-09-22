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

**Settings -> Performance has an experimental, opt-in 60 FPS mode.** It runs
Crash 2's own game loop at 60 instead of 30 - a real change to how the game
executes, not a smoothing filter on the picture. Once enabled it stays at 60
rather than dropping you back.

It is off by default and is a preview. World movement keeps the correct speed
(the engine already scales motion by measured frame time and this reuses
that), but scripted sequences, cutscene pacing, music and sound timing, bosses,
vehicle levels and FMV transitions have **not** been verified across the whole
game. It also runs the emulated PlayStation processor at 200% so a busy frame
can fit into one screen refresh, which is the furthest this gets from how the
console behaved.

Busy scenes will land between 30 and 60 rather than holding a clean 60. If that
bothers you, lower **Internal resolution** on the Video page first - at 5x the
renderer draws twenty-five times the pixels of native, and that is usually what
runs out before the game does.

If something behaves strangely, turn it off and see whether the problem goes
away - that is a useful thing to report. It changes timing, not saved data.

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
