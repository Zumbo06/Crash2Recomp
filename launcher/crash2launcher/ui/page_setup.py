"""Setup - bring your own disc, then build the game.

This is the first-run flow. The launcher ships no game data: the player selects
a dump they own, it is verified, and the recompiler turns it into a native
executable on their machine. Nothing is uploaded and nothing copyrighted is
distributed.

Three steps, in order:

  1. Select a .cue and verify it (tracks present, sector-aligned, boot serial)
  2. Generate C from the disc and compile it - minutes, with live output
  3. Play

Reuses :mod:`disc` for inspection and :class:`pipeline.Job` for the long build,
both of which already exist and are tested.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import disc
from ..config import Settings
from ..paths import Layout
from ..pipeline import EXIT_DISC_VERIFY_FAILED, Job
from .common import card, dim, heading, section, set_status
from .dialogs import confirm
from .theme import ERROR, OK, PAGE_MARGINS, TEXT_DIM, WARN
from .widgets.log_console import LogConsole
from .widgets.step_list import ACTIVE, DONE, FAILED, PENDING, StepList

# The title this launcher is built for. A different dump still works, but the
# player should know it is not what the project was tuned against.
EXPECTED_SERIAL = "SCUS-94154"

BUILD_STEPS = [
    ("verify", "Verify disc"),
    ("generate", "Translate game code to C"),
    ("compile", "Compile to a native executable"),
]


class _HashWorker(QObject):
    """Hashes the dump off the GUI thread - a disc image is hundreds of MB."""

    progress = Signal(int, int)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, paths: list[Path]):
        super().__init__()
        self._paths = paths
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            digest = disc.hash_files(
                self._paths,
                progress=lambda d, t: (self.progress.emit(d, t), not self._cancel)[1],
            )
        except disc.Cancelled:
            self.failed.emit("Cancelled.")
        except OSError as exc:
            self.failed.emit("Could not read the disc image: %s" % exc)
        else:
            self.done.emit(digest)


class SetupPage(QWidget):
    """Disc selection, verification and the build."""

    ready = Signal()          # the game is built and playable

    def __init__(self, layout_: Layout, settings: Settings,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.layout_ = layout_
        self.settings = settings
        self._info: disc.DiscInfo | None = None
        # Set for any image the build can use. Verification is separate: a CHD
        # is buildable but not inspectable by this page.
        self._disc_ok = False
        self._thread: QThread | None = None
        self._worker: _HashWorker | None = None
        self._job: Job | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(16)
        scroll.setWidget(body)

        lay.addWidget(heading(
            "Setup",
            "This launcher contains no game data. Point it at a disc image you "
            "own and it will build the game on this machine - nothing is "
            "uploaded, and your disc is only ever read.",
        ))
        lay.addWidget(self._disc_card())
        lay.addWidget(self._build_card())
        lay.addStretch(1)

        if self.settings.disc_path:
            self._inspect(Path(self.settings.disc_path))
        self._refresh()

    # -- disc --------------------------------------------------------------
    def _disc_card(self) -> QWidget:
        self.path_edit = QLineEdit(self.settings.disc_path)
        self.path_edit.setPlaceholderText("Select your .cue file...")
        self.path_edit.setReadOnly(True)

        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)

        picker = QWidget()
        prow = QHBoxLayout(picker)
        prow.setContentsMargins(0, 0, 0, 0)
        prow.setSpacing(8)
        prow.addWidget(self.path_edit, 1)
        prow.addWidget(browse)

        self.summary = QLabel("No disc selected.")
        self.summary.setObjectName("Dim")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)

        self.hash_bar = QProgressBar()
        self.hash_bar.setVisible(False)

        return card(
            section("1.  Your disc"),
            picker,
            dim("A .cue sheet with its .bin track alongside it. The image is "
                "checked for complete tracks, whole sectors and the boot serial."),
            self.summary,
            self.hash_bar,
        )

    def _disc_path(self) -> Path | None:
        """The image to hand the recompiler. For a cue this is the parsed cue
        path; for anything we could not parse (CHD) it is what was selected."""
        if self._info is not None:
            return self._info.cue_path
        return Path(self.settings.disc_path) if self.settings.disc_path else None

    def _browse(self) -> None:
        start = (str(Path(self.settings.disc_path).parent)
                 if self.settings.disc_path else str(Path.home()))
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Select your disc image", start,
            "Disc images (*.cue *.chd);;Cue sheets (*.cue);;All files (*)")
        if chosen:
            self._inspect(Path(chosen))

    def _inspect(self, path: Path) -> None:
        self.path_edit.setText(str(path))
        self.settings.disc_path = str(path)

        # The dialog offers .chd because the recompiler and the runtime both
        # read it, but our inspector only understands cue sheets - it used to
        # report "Cue sheet lists no FILE entries", which describes nothing a
        # player can act on. Say what is actually true instead.
        if path.suffix.lower() == ".chd":
            self._info = None
            self._disc_ok = True
            set_status(
                self.summary, "Warn",
                "CHD selected. The build accepts it, but this page cannot "
                "check the tracks or read the boot serial - only .cue images "
                "can be verified here.")
            self.steps.set_state("verify", DONE, "not verified (CHD)")
            self._refresh()
            return

        self._info = info = disc.parse_cue(path)
        self._disc_ok = info.ok

        bits = ["<b>%d</b> track(s), <b>%s</b> bytes"
                % (len(info.tracks), format(info.total_bytes, ","))]
        if info.is_sector_aligned:
            bits[0] += " = <b>%s</b> sectors" % format(info.sector_count, ",")
        if info.serial:
            match = info.serial == EXPECTED_SERIAL
            colour = OK if match else WARN
            note = "" if match else " - not the title this build targets"
            bits.append('Serial: <span style="color:%s"><b>%s</b></span>%s'
                        % (colour, info.serial, note))
        for problem in info.problems:
            bits.append('<span style="color:%s">%s</span>' % (ERROR, problem))

        self.summary.setText("<br>".join(bits))
        self._refresh()
        if info.ok:
            self._start_hash(info)

    def _start_hash(self, info: disc.DiscInfo) -> None:
        self.hash_bar.setVisible(True)
        self.hash_bar.setValue(0)
        self.cancel_btn.setEnabled(True)   # hashing is minutes on a big dump
        self._thread = QThread(self)
        self._worker = _HashWorker(info.bin_paths)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_hash_progress)
        self._worker.done.connect(self._on_hash_done)
        self._worker.failed.connect(self._on_hash_failed)
        self._thread.start()

    def _on_hash_progress(self, done: int, total: int) -> None:
        self.hash_bar.setMaximum(max(1, total // 1_000_000))
        self.hash_bar.setValue(done // 1_000_000)

    def _on_hash_done(self, digest: str) -> None:
        self._stop_thread()
        self.hash_bar.setVisible(False)
        self.cancel_btn.setEnabled(False)
        self.settings.disc_sha1 = digest
        self.settings.disc_verified = bool(self._info and self._info.ok)
        self.steps.set_state("verify", DONE, "SHA-1 " + digest[:12])
        self.summary.setText(
            self.summary.text()
            + '<br><span style="color:%s">SHA-1: %s</span>' % (TEXT_DIM, digest))
        self._refresh()

    def _on_hash_failed(self, message: str) -> None:
        self._stop_thread()
        self.hash_bar.setVisible(False)
        self.cancel_btn.setEnabled(False)
        self.steps.set_state("verify", FAILED, message)

    def _stop_thread(self) -> None:
        if self._thread:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread = None
            self._worker = None

    # -- build -------------------------------------------------------------
    def _build_card(self) -> QWidget:
        self.steps = StepList(BUILD_STEPS)

        self.build_btn = QPushButton("Build the game")
        self.build_btn.setObjectName("Primary")
        self.build_btn.clicked.connect(self._on_build)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel)

        buttons = QWidget()
        brow = QHBoxLayout(buttons)
        brow.setContentsMargins(0, 0, 0, 0)
        brow.setSpacing(8)
        brow.addWidget(self.build_btn)
        brow.addWidget(self.cancel_btn)
        brow.addStretch(1)

        self.build_bar = QProgressBar()
        self.build_bar.setVisible(False)
        self.build_log = LogConsole()
        self.build_log.setMinimumHeight(170)

        self.build_note = QLabel()
        self.build_note.setWordWrap(True)
        self.build_note.setTextFormat(Qt.TextFormat.RichText)

        return card(
            section("2.  Build"),
            dim("Translates the game to C and compiles it. This takes several "
                "minutes the first time and only needs doing once."),
            self.steps,
            buttons,
            self.build_bar,
            self.build_note,
            self.build_log,
        )

    def _on_build(self) -> None:
        if not self.layout_.cli_exe.is_file():
            set_status(self.build_note, "Error",
                       "The recompiler is missing. It should sit next to this "
                       "launcher at %s - if the download is incomplete, "
                       "unpack it again." % self.layout_.cli_exe)
            return
        if not self._disc_ok:
            set_status(self.build_note, "Warn", "Select a valid disc first.")
            return

        # Rebuilding throws away a working build and costs minutes.
        if self.layout_.has_runtime and not confirm(
                self, "Rebuild the game?",
                "The game is already built and playable. Rebuilding takes "
                "several minutes and is only needed if you have changed discs.",
                "Rebuild"):
            return

        self.steps.set_state("generate", ACTIVE)
        self.build_bar.setVisible(True)
        self.build_bar.setRange(0, 0)      # indeterminate until progress arrives
        self.build_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.build_note.setText("")

        self._job = Job(
            str(self.layout_.cli_exe),
            ["build",
             "--disc", str(self._disc_path()),
             "--output", str(self.layout_.project)],
            cwd=self.layout_.cli_exe.parent,
        )
        self._job.line.connect(self.build_log.append_line)
        self._job.progress.connect(self._on_build_progress)
        self._job.finished.connect(self._on_generate_finished)
        self._job.start()

    def _on_build_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.build_bar.setRange(0, total)
            self.build_bar.setValue(done)

    def _on_generate_finished(self, code: int, message: str) -> None:
        if code != 0:
            self.steps.set_state("generate", FAILED, message)
            self._finish_build(False, message, code)
            return
        self.steps.set_state("generate", DONE)
        self.steps.set_state("compile", ACTIVE)

        script = self.layout_.project / "build.ps1"
        if not script.is_file():
            self.steps.set_state("compile", FAILED, "build script missing")
            self._finish_build(False, "No build script at %s" % script, 1)
            return

        self._job = Job(
            "powershell",
            ["-ExecutionPolicy", "Bypass", "-File", str(script)],
            cwd=self.layout_.project,
        )
        self._job.line.connect(self.build_log.append_line)
        self._job.progress.connect(self._on_build_progress)
        self._job.finished.connect(self._on_compile_finished)
        self._job.start()

    def _on_compile_finished(self, code: int, message: str) -> None:
        ok = code == 0 and self.layout_.has_runtime
        self.steps.set_state("compile", DONE if ok else FAILED,
                             "" if ok else message)
        self._finish_build(ok, message, code)

    def _finish_build(self, ok: bool, message: str, code: int) -> None:
        self.build_bar.setVisible(False)
        self.build_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self._job = None

        if ok:
            self.build_note.setText(
                '<span style="color:%s"><b>Ready.</b></span> '
                "The game is built - open the Play page." % OK)
            self.ready.emit()
        else:
            hint = ""
            if code == EXIT_DISC_VERIFY_FAILED:
                hint = ("<br>The dump did not match what the recompiler "
                        "expected. Re-dump the disc, or check the .bin sits "
                        "next to the .cue.")
            self.build_note.setText(
                '<span style="color:%s">Build failed: %s</span>%s'
                % (ERROR, message, hint))

    def _on_cancel(self) -> None:
        # Cancel whichever long job is actually running. The hasher was never
        # wired up: _HashWorker.cancel() existed and had no caller, so pressing
        # Cancel during the SHA-1 of a ~700 MB image did nothing at all.
        if self._worker:
            self._worker.cancel()
        if self._job:
            self._job.cancel()
        for key, _ in BUILD_STEPS[1:]:
            self.steps.set_state(key, PENDING)
        self._finish_build(False, "Cancelled.", -1)

    # -- lifecycle ---------------------------------------------------------
    @property
    def busy(self) -> bool:
        """A build is in flight. The window asks before closing on this."""
        return bool(self._job and self._job.running)

    def shutdown(self) -> None:
        """Stop everything this page owns. Without it, closing the launcher
        mid-build orphaned cmake/ninja and left the hash thread running."""
        if self._worker:
            self._worker.cancel()
        if self._job:
            self._job.cancel()
            self._job = None
        self._stop_thread()

    # -- state -------------------------------------------------------------
    def _refresh(self) -> None:
        have_disc = self._disc_ok
        self.build_btn.setEnabled(have_disc and not (self._job and self._job.running))

        if self.layout_.has_runtime:
            for key, _ in BUILD_STEPS:
                self.steps.set_state(key, DONE)
            self.build_btn.setText("Rebuild")
            self.build_note.setText(
                '<span style="color:%s">Already built - the Play page is '
                "ready. Rebuild only if you change discs.</span>" % TEXT_DIM)
        elif have_disc:
            self.steps.set_state("verify", DONE)
