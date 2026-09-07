"""Long-running external jobs (recompile, build) driven from the GUI.

Everything here is built on ``QProcess`` rather than ``subprocess`` in a thread.
That is deliberate: ``QProcess`` delivers output through the Qt event loop, so
the log view stays responsive without any cross-thread marshalling, and
``kill()`` actually terminates a twenty-minute compile when the user hits
Cancel. A ``subprocess`` blocked in ``read()`` cannot be interrupted cleanly.

The psxrecomp CLI can emit newline-delimited JSON progress records under
``--json-progress``; when present we drive the progress bar from those instead
of scraping percentages out of prose.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

# The CLI's documented exit codes.
EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1
EXIT_USAGE = 2
EXIT_DISC_VERIFY_FAILED = 3

EXIT_MEANING = {
    EXIT_OK: "Completed successfully.",
    EXIT_RUNTIME_ERROR: "The tool hit an error while running.",
    EXIT_USAGE: "The launcher invoked the tool incorrectly (usage error).",
    EXIT_DISC_VERIFY_FAILED: (
        "Disc verification failed - this dump does not match what the project "
        "was generated from."
    ),
}

# Ninja prints "[123/456] Building ..." which is a reliable progress source.
_NINJA_RE = re.compile(r"^\[(\d+)/(\d+)\]")



class Job(QObject):
    """One external command, streamed to the UI."""

    line = Signal(str)                 # a line of output
    progress = Signal(int, int)        # done, total  (either -1 => indeterminate)
    stage = Signal(str)                # human-readable current stage
    finished = Signal(int, str)        # exit code, explanation
    started = Signal()

    def __init__(self, program: str, args: list[str], cwd: Path | None = None,
                 env_extra: dict[str, str] | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.program = program
        self.args = args
        self.cwd = cwd
        self._buf = ""
        self._cancelled = False

        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        if cwd:
            self.proc.setWorkingDirectory(str(cwd))
        if env_extra:
            env = QProcessEnvironment.systemEnvironment()
            for k, v in env_extra.items():
                env.insert(k, v)
            self.proc.setProcessEnvironment(env)

        self.proc.readyReadStandardOutput.connect(self._drain)
        self.proc.finished.connect(self._on_finished)
        self.proc.errorOccurred.connect(self._on_error)
        self.proc.started.connect(self.started.emit)

    # --- control ----------------------------------------------------------
    def start(self) -> None:
        self.proc.start(self.program, self.args)

    def cancel(self) -> None:
        """Ask nicely, then insist. Prevents orphaned compilers."""
        if self.proc.state() == QProcess.ProcessState.NotRunning:
            return
        self._cancelled = True
        self.proc.terminate()
        if not self.proc.waitForFinished(3000):
            self.proc.kill()
            self.proc.waitForFinished(2000)

    @property
    def running(self) -> bool:
        return self.proc.state() != QProcess.ProcessState.NotRunning

    # --- output handling --------------------------------------------------
    def _drain(self) -> None:
        raw = bytes(self.proc.readAllStandardOutput())
        self._buf += raw.decode("utf-8", errors="replace")
        while "\n" in self._buf:
            chunk, self._buf = self._buf.split("\n", 1)
            self._handle_line(chunk.rstrip("\r"))

    def _handle_line(self, text: str) -> None:
        if not text:
            return

        # Structured progress takes priority over anything we could infer.
        if text.lstrip().startswith("{"):
            try:
                rec = json.loads(text)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(rec, dict):
                    self._handle_record(rec)
                    return

        m = _NINJA_RE.match(text)
        if m:
            self.progress.emit(int(m.group(1)), int(m.group(2)))

        self.line.emit(text)

    def _handle_record(self, rec: dict) -> None:
        stage = rec.get("stage") or rec.get("step") or rec.get("phase")
        if stage:
            self.stage.emit(str(stage))

        done, total = rec.get("done"), rec.get("total")
        if isinstance(done, int) and isinstance(total, int) and total > 0:
            self.progress.emit(done, total)
        elif isinstance(rec.get("percent"), (int, float)):
            self.progress.emit(int(rec["percent"]), 100)

        msg = rec.get("message") or rec.get("msg")
        if msg:
            self.line.emit(str(msg))

    # --- completion -------------------------------------------------------
    def _on_finished(self, code: int, _status) -> None:
        if self._buf.strip():
            self._handle_line(self._buf.strip())
            self._buf = ""
        if self._cancelled:
            self.finished.emit(-1, "Cancelled.")
            return
        self.finished.emit(code, EXIT_MEANING.get(code, f"Exited with code {code}."))

    def _on_error(self, err) -> None:
        if err == QProcess.ProcessError.FailedToStart:
            self.finished.emit(
                EXIT_RUNTIME_ERROR,
                f"Could not start '{self.program}'. Is it present and executable?",
            )
