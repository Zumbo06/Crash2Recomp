# Crash Bandicoot 2 Recompiled

The PlayStation game *Crash Bandicoot 2: Cortex Strikes Back* translated to
native code and run directly, rather than emulated. A launcher takes a disc
image you already own, builds the game on your machine, and runs it.

**This project contains no game data.** No game code, artwork, audio or disc
content is distributed here. You supply your own disc image; everything derived
from it is produced locally and never leaves your machine.

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
| **Home** | Pause menu: restart, aspect ratio, image fit, quick save and load. On a controller, Guide or Start+Select. |
| F5 / F9 | Quick save / quick load |
| F7 | Save state slots |
| F8 | Rewind |
| F | Toggle the performance readout |

Saves live in `userdata/`, next to the launcher.

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
