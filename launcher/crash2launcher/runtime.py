"""Launching the recompiled game.

Flags here are the ones the runtime actually parses (confirmed against the
runtime sources), not a guess. We always pass ``--no-launcher``: the runtime has
its own built-in ImGui front end, and showing that on top of this launcher would
give the player two competing menus.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from . import gametoml, usersettings
from .config import Settings
from .paths import Layout, find_c_toolchain_bin


@dataclass
class LaunchPlan:
    """Exactly what we are about to run - shown in the UI before launching."""

    program: Path
    args: list[str]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)

    def as_command(self) -> str:
        parts = [f'"{self.program}"'] + [
            f'"{a}"' if " " in a else a for a in self.args
        ]
        return " ".join(parts)


def build_plan(layout: Layout, settings: Settings) -> LaunchPlan:
    """Translate launcher settings into a runtime command line."""
    args: list[str] = ["--no-launcher"]

    if layout.game_toml.is_file():
        args += ["--game", str(layout.game_toml)]

    # In the shipped bundle the prepared disc lives in data/; in the workspace
    # the project points at the original dump through game.toml. Only override
    # when we can see a disc ourselves.
    disc = _resolve_disc(layout, settings)
    if disc:
        args += ["--disc", str(disc)]

    args += ["--renderer", settings.renderer]
    args += ["--memcard-dir", str(layout.save_dir)]
    args += ["--window-title", "Crash Bandicoot 2 Recompiled"]

    # Opens the runtime's TCP debug server - how we read the SPU event ring.
    if settings.debug_port:
        args += ["--debug-port", str(settings.debug_port)]

    env = _build_env(settings)
    env.update(overlay_env(layout, settings))

    program = _runtime_for(layout, settings)

    return LaunchPlan(
        program=program,
        args=args,
        cwd=program.parent,
        env=env,
    )


def _settings_targets(layout: Layout) -> list[Path]:
    """Every build directory that could be launched, newest-relevant first.

    Deduplicated and existence-checked, so this stays correct whether or not the
    diagnostics tree has been built.
    """
    dirs: list[Path] = []
    for candidate in (layout.runtime_exe.parent,
                      layout.project / "build-clang",
                      layout.project / "build-debugtools"):
        if candidate.is_dir() and candidate not in dirs:
            dirs.append(candidate)
    return dirs


def _runtime_for(layout: Layout, settings: Settings) -> Path:
    """Pick the binary that can actually honour the requested settings.

    The release build is compiled with PSX_NO_DEBUG_TOOLS, which strips the TCP
    debug server entirely - so ``--debug-port`` is accepted and then silently
    listens on nothing. Asking for a debug port has to mean the debugtools
    build, or the setting is a trap.
    """
    if settings.debug_port:
        debug_exe = layout.project / "build-debugtools" / layout.runtime_exe.name
        if debug_exe.is_file():
            return debug_exe
    return layout.runtime_exe


def _build_env(settings: Settings) -> dict[str, str]:
    """Environment overrides for the runtime.

    Most enhancement knobs live here rather than in a config file. The runtime
    reads them directly with ``getenv`` and lets them win over config, which is
    exactly what we want for fast A/B testing from the launcher.

    ``PSX_DEV_INPUT=1`` makes player 1 read the keyboard *and* every connected
    controller at once. Without it a Release build defaults player 1 to
    "keyboard" and never opens a gamepad at all - the runtime expects its own
    built-in launcher to assign a physical device, and we deliberately run with
    ``--no-launcher`` because this launcher replaces it.
    """
    env: dict[str, str] = {}

    if settings.merge_all_input:
        env["PSX_DEV_INPUT"] = "1"

    # Frame pacing. Note the runtime treats vsync and its wall-clock pacer as
    # mutually exclusive, and vsync only really clocks ~60 Hz panels.
    env["PSX_VSYNC"] = str(settings.vsync)

    # The current high-refresh compositor is implemented by the OpenGL path.
    # It is presentation interpolation above the game's native 59.94 Hz update,
    # never a guest-clock multiplier.
    if settings.frame_interpolation and settings.renderer == "opengl":
        env["PSX_FRAME_INTERPOLATION"] = "1"
        # Only 0 (follow host) or >= 90 is accepted; clamp() already enforced it.
        if settings.frame_interpolation_fps:
            env["PSX_FRAME_INTERPOLATION_FPS"] = str(settings.frame_interpolation_fps)

    # PSX_SMOOTH_60FPS blended duplicate 30 Hz frames. Crash 2 now has a guarded
    # native 59.94 Hz title patch, so enabling that legacy blend would only blur
    # already-distinct frames.
    if settings.frame_blend:
        env["PSX_FRAME_BLEND"] = "1"

    # Audio diagnostics for the sound cut-off work.
    if settings.audio_legacy:
        env["PSXRECOMP_AUDIO_LEGACY"] = "1"
    if settings.audio_shadow:
        env["PSX_AUDIO_SHADOW"] = "1"

    if settings.fps_telemetry:
        env["PSX_FPS_TELEMETRY"] = "1"
    if settings.voice_alloc_trace:
        env["PSX_VOICE_ALLOC_TRACE"] = "1"
    if settings.overlay_interpreter:
        env["PSX_OVERLAY_NATIVE_OFF"] = "1"
    if settings.force_interpreter:
        env["PSX_FORCE_INTERP"] = "1"

    # Presentation fit. Only "stretch"/"fill" change anything; letterbox is the
    # runtime default, so we still pass it explicitly to make a relaunch after
    # switching back actually take effect.
    env["PSX_SCALING_MODE"] = settings.scaling_mode

    # How the internal buffer is resampled down to the window.
    env["PSX_PRESENT_FILTER"] = settings.present_filter

    # Slot for the quick save/load keys (F5 / F9 by default).
    env["PSX_QUICK_SLOT"] = str(settings.quick_save_slot)

    # PGXP sub-pixel geometry. These have settings.toml equivalents, but the
    # env vars let a relaunch A/B them without rewriting config.
    if settings.geometry_correction:
        env["PSX_GEOMETRY_CORRECTION"] = "1"
        # Coverage must match the correction, or vertices pop between precise
        # and rounded positions - see Settings.pgxp_cpu_mode.
        env["PSX_PGXP_CPU_MODE"] = "1" if settings.pgxp_cpu_mode else "0"
    if settings.perspective_texturing:
        env["PSX_PERSPECTIVE_TEXTURING"] = "1"

    # Overscan crop, in PS1 scanlines out of 240. Only emitted when non-zero so
    # an untouched setting cannot alter the picture.
    if any((settings.overscan_top, settings.overscan_bottom,
            settings.overscan_left, settings.overscan_right)):
        env["PSX_OVERSCAN_CROP"] = ",".join(str(v) for v in (
            settings.overscan_top, settings.overscan_bottom,
            settings.overscan_left, settings.overscan_right))

    return env


def _quote(path: Path | str) -> str:
    return f'"{path}"'


def overlay_autocompile_cmd(layout: Layout) -> str:
    """The command the runtime shells out to in order to compile overlays.

    Used verbatim by the runtime, so every path is absolute and quoted.
    """
    return " ".join([
        _quote(sys.executable),
        _quote(layout.overlay_script),
        "--captures", _quote(layout.overlay_captures),
        "--game-toml", _quote(layout.game_toml),
        "--recompiler", _quote(layout.recompiler_exe),
        "--runtime-include", _quote(layout.runtime_include),
        "--out-dir", _quote(layout.overlay_cache),
    ])


def overlay_env(layout: Layout, settings: Settings) -> dict[str, str]:
    """Environment that turns on native compilation of streamed level code.

    Crash 2 loads level code as overlays. Anything the runtime cannot dispatch
    natively runs on the MIPS interpreter - correct, but far slower. Two things
    are needed to avoid that:

    * a C compiler on PATH, which is how ``autocompile_toolchain_available()``
      decides the gcc tier is usable at all
    * ``PSX_OVERLAY_AUTOCOMPILE_CMD``, the command it shells out to

    The command is used verbatim, so every path is supplied here. Without it the
    runtime falls back to its bundled-TCC tier, which needs an
    ``overlay_toolchain/`` directory we do not ship, and then gives up to the
    interpreter.
    """
    env: dict[str, str] = {}
    if not settings.native_overlays or not layout.can_compile_overlays:
        return env

    toolchain = find_c_toolchain_bin()
    if toolchain:
        env["PATH"] = str(toolchain) + os.pathsep + os.environ.get("PATH", "")

    env["PSX_OVERLAY_AUTOCOMPILE_CMD"] = overlay_autocompile_cmd(layout)
    return env


def apply_config_settings(layout: Layout, settings: Settings) -> None:
    """Write the settings that are *not* environment variables into game.toml.

    Two of these have no env override at all - the runtime only reads them from
    config - so they must be on disk before launching:

    ``[video] supersampling``
        Internal-resolution SSAA. The vendored loader validates 1..8 and *throws* outside
        that range, taking the whole config down with it, so Settings.clamp()
        enforces the bound before we ever write.

    ``[controller] p1_device``
        Assigns a physical device to player 1. Without it a release build pins
        player 1 to "keyboard" and never opens a gamepad. "auto" means the first
        connected pad. This is the supported route; PSX_DEV_INPUT (see
        _build_env) is a diagnostic merge that layers on top, and the two
        cooperate - the pad is properly assigned *and* the keyboard still works.

    Uses the line-preserving writer in :mod:`gametoml`, so comments and key
    order in the generated game.toml survive.
    """
    if not layout.game_toml.is_file():
        return

    # settings.toml sits beside the runtime executable and layers over
    # game.toml. Fullscreen mode, window size, CRT and texture filtering exist
    # ONLY here - there is no game.toml key or env override for them.
    #
    # The runtime reads it from ITS OWN exe directory, and we may launch either
    # tree (a debug port selects build-debugtools). Writing only next to the
    # release binary meant that, with a debug port set, every setting landed in
    # a file the running game never opened - so nothing applied at all. Write to
    # every tree that exists; they are alternate builds of one game, not
    # independent installs.
    for build_dir in _settings_targets(layout):
        usersettings.save(build_dir / "settings.toml", settings)

    gametoml.update(
        layout.game_toml,
        {
            "video": {
                "renderer": settings.renderer,
                "supersampling": settings.supersampling,
            },
            "controller": {"p1_device": "auto"},
            # Crash 2 has no sprite-tag hook, so opt its fully-3D gameplay into
            # the GTE activity detector. BIOS, FMV and full-2D screens stay 4:3
            # inside the output canvas either way.
            #
            # native_wide is a MODE choice, not an on/off switch, and it is the
            # one that decides whether widescreen does anything at all:
            #   True  - expand the render target. Needs per-game viewport data;
            #           Crash 2 has none, so nw_extra stays 0 and NOTHING
            #           widens. This is the framework default.
            #   False - GTE X-squash + stretched present, the DuckStation/Beetle
            #           widescreen hack. Works on any title, verified widening
            #           Crash 2 edge to edge.
            "widescreen": {
                "offer": True,
                "offer_ultrawide": False,
                "native_wide": settings.widescreen_native_wide,
                "gte_game_mode": True,
                "precise_nclip": True,
            },
            # Setting overlay_autocompile_cmd is what makes the runtime consider
            # the gcc tier available at all (it gates on
            # has_overlay_autocompile_cmd && a compiler on PATH). Without it the
            # runtime picks its bundled-TCC tier, looks for an
            # overlay_toolchain/ directory we do not ship, and falls back to the
            # interpreter.
            "runtime": {
                "overlay_backend": "auto" if settings.native_overlays else "tcc",
                "overlay_autocompile_cmd": (
                    overlay_autocompile_cmd(layout)
                    if settings.native_overlays and layout.can_compile_overlays
                    else ""
                ),
            },
        },
    )


def effective_scale_from_log(line: str) -> int | None:
    """Pull the scale the renderer *actually* used out of its startup line.

    The request is clamped to SW_MAX_INTERNAL_SCALE and can also fall back on
    an allocation failure, so the log is the only trustworthy source. Matches:
    ``psxrecomp: GL GPU pipeline ready (internal scale 2x, ...``
    """
    match = _SCALE_RE.search(line)
    return int(match.group(1)) if match else None


_SCALE_RE = re.compile(r"internal scale\s+(\d+)x")

# "psxrecomp: widescreen 16:9 (GTE X-squash + stretched present; ...)"
_WIDESCREEN_RE = re.compile(r"widescreen\s+(\d+:\d+)")
# "psxrecomp: presentation fit = fill"
_FIT_RE = re.compile(r"presentation fit = (\S+)")
# "psxrecomp: overlay autocompile enabled (gcc); ..."
_OVERLAY_RE = re.compile(r"overlay autocompile enabled \((\w+)\)")
# "GL temporal frame blending enabled: 240.0 presents/s ..."
_BLEND_RE = re.compile(r"frame blending enabled:\s*([\d.]+)\s*presents/s")


def observed_from_log(line: str) -> tuple[str, str] | None:
    """Pull a (label, value) the runtime reports about itself.

    The launcher can only ever show what it REQUESTED; these are what the
    runtime actually did. Where the two differ - the renderer clamps the
    internal scale, widescreen falls back, the overlay tier drops to the
    interpreter - the log is the only trustworthy source.
    """
    m = _SCALE_RE.search(line)
    if m:
        return ("Internal scale", m.group(1) + "x")
    m = _WIDESCREEN_RE.search(line)
    if m:
        return ("Widescreen", m.group(1))
    m = _FIT_RE.search(line)
    if m:
        return ("Image fit", m.group(1))
    m = _OVERLAY_RE.search(line)
    if m:
        return ("Overlay tier", m.group(1))
    m = _BLEND_RE.search(line)
    if m:
        return ("Presents/s", m.group(1))
    if "overlay gaps -> interpreter" in line:
        return ("Overlay tier", "interpreter (slow)")
    return None


def _resolve_disc(layout: Layout, settings: Settings) -> Path | None:
    if settings.disc_path and Path(settings.disc_path).is_file():
        return Path(settings.disc_path)
    if layout.disc_data.is_dir():
        for pattern in ("*.cue", "*.chd", "*.bin", "*.iso"):
            found = sorted(layout.disc_data.glob(pattern))
            if found:
                return found[0]
    return None


class GameSession(QObject):
    """A running game process.

    The launcher stays open behind the game so the player lands back on it when
    they quit, and so a crash surfaces its output instead of vanishing.
    """

    started = Signal()
    finished = Signal(int)
    output = Signal(str)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._drain)
        self.proc.started.connect(self.started.emit)
        self.proc.finished.connect(lambda code, _st: self.finished.emit(code))
        self.proc.errorOccurred.connect(self._on_error)
        self._buf = ""

    def launch(self, plan: LaunchPlan) -> None:
        if not plan.program.is_file():
            self.failed.emit(
                f"The recompiled game is missing:\n{plan.program}\n\n"
                "Build it first, or reinstall the bundle."
            )
            return
        self.proc.setWorkingDirectory(str(plan.cwd))
        if plan.env:
            qenv = QProcessEnvironment.systemEnvironment()
            for k, v in plan.env.items():
                qenv.insert(k, v)
            self.proc.setProcessEnvironment(qenv)
        self.proc.start(str(plan.program), plan.args)

    def stop(self) -> None:
        if self.proc.state() == QProcess.ProcessState.NotRunning:
            return
        self.proc.terminate()
        if not self.proc.waitForFinished(4000):
            self.proc.kill()

    @property
    def running(self) -> bool:
        return self.proc.state() != QProcess.ProcessState.NotRunning

    def _drain(self) -> None:
        self._buf += bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip("\r")
            if line:
                self.output.emit(line)

    def _on_error(self, err) -> None:
        if err == QProcess.ProcessError.FailedToStart:
            self.failed.emit("The game process could not be started.")
