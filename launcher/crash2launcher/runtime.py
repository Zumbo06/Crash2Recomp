"""Launching the recompiled game.

Flags here are the ones the runtime actually parses (confirmed against the
runtime sources), not a guess. We always pass ``--no-launcher``: the runtime has
its own built-in ImGui front end, and showing that on top of this launcher would
give the player two competing menus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from .config import Settings
from .paths import Layout


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

    return LaunchPlan(
        program=layout.runtime_exe,
        args=args,
        cwd=layout.runtime_exe.parent,
        env=_build_env(settings),
    )


def _build_env(settings: Settings) -> dict[str, str]:
    """Environment overrides for the runtime.

    ``PSX_DEV_INPUT=1`` makes player 1 read the keyboard *and* every connected
    controller at once. Without it a Release build defaults player 1 to
    "keyboard" and never opens a gamepad at all - the runtime expects its own
    built-in launcher to assign a physical device, and we deliberately run with
    ``--no-launcher`` because this launcher replaces it.
    """
    env: dict[str, str] = {}
    if settings.merge_all_input:
        env["PSX_DEV_INPUT"] = "1"
    return env


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
