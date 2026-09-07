"""Construct the whole UI offscreen and exercise the paths a click reaches.

test_settings_coverage.py proves every setting HAS a control. This proves the
window actually builds and that the interactions which rebuild or re-enter it
do not raise - the class of bug an import check cannot see (a nav rebuild that
runs before the stack exists, a handler wired to a widget that moved page, a
reset that touches a control which no longer exists).

    python launcher/test_ui_smoke.py
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication          # noqa: E402

from crash2launcher import config, paths            # noqa: E402
from crash2launcher.ui.main_window import MainWindow, nav_groups  # noqa: E402
from crash2launcher.ui.theme import apply_theme     # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  ok   " if ok else "  FAIL ") + label + (" " + detail if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    apply_theme(app)

    # Run against a THROWAWAY tree. Selecting a page and toggling developer
    # mode both reach _on_settings_changed, which writes settings.toml and
    # rewrites game.toml - pointed at the real project, this test silently
    # replaced the developer's tuned supersampling and widescreen values with
    # dataclass defaults. A test must not be able to do that.
    tmp = Path(tempfile.mkdtemp(prefix="crash2-uitest-"))
    # runtime_exe must move too: _settings_targets writes settings.toml beside
    # it, so leaving it pointed at the real build tree would still escape.
    (tmp / "build-clang").mkdir(parents=True, exist_ok=True)
    fake_exe = tmp / "build-clang" / "Crash_Bandicoot_2_Recompiled.exe"
    fake_exe.write_bytes(b"")            # present, so the Play page builds fully
    layout_ = dataclasses.replace(
        paths.detect(),
        root=tmp,
        project=tmp,
        runtime_exe=fake_exe,
        game_toml=tmp / "game.toml",     # absent: the game.toml rewrite is skipped
        userdata=tmp / "userdata",
        mods=tmp / "mods",
        build_dir=tmp / "build-clang",
    )
    layout_.ensure_writable_dirs()
    settings = config.Settings().clamp()

    print("\n1. the window builds")
    win = MainWindow(layout_, settings)
    check("MainWindow constructed", win is not None)
    check("a page is selected", win.stack.currentIndex() >= 0)

    print("\n2. developer mode hides and restores Advanced")
    player = [k for _, items in nav_groups(False) for k, _, _ in items]
    dev = [k for _, items in nav_groups(True) for k, _, _ in items]
    check("Advanced hidden for players", "advanced" not in player)
    check("Log stays visible for players", "log" in player,
          "(it is how a player reports a problem)")
    check("Advanced present in developer mode", "advanced" in dev)

    settings.developer_mode = True
    win._on_settings_changed()
    check("nav rebuilt with Advanced",
          any(k == "advanced" for k, _, _ in win._pages))
    settings.developer_mode = False
    win._on_settings_changed()
    check("nav rebuilt without Advanced",
          not any(k == "advanced" for k, _, _ in win._pages))

    print("\n3. every nav entry selects without raising")
    for i, (key, label, _) in enumerate(win._pages):
        try:
            win._select(i)
            ok, detail = True, ""
        except Exception as exc:                      # noqa: BLE001
            ok, detail = False, repr(exc)
        check("select %-22s" % label, ok, detail)

    print("\n4. diagnostics never reach the runtime outside developer mode")
    from crash2launcher.runtime import _build_env
    loud = config.Settings()
    for name in config.DIAGNOSTIC_SETTINGS:
        cur = getattr(loud, name)
        setattr(loud, name, 4370 if isinstance(cur, int) and not isinstance(cur, bool) else True)
    loud.developer_mode = False
    env = _build_env(loud)
    leaked = sorted(k for k in env if k in {
        "PSXRECOMP_AUDIO_LEGACY", "PSX_AUDIO_SHADOW", "PSX_VOICE_ALLOC_TRACE",
        "PSX_OVERLAY_NATIVE_OFF", "PSX_FORCE_INTERP"})
    check("no diagnostic env with developer mode off", not leaked, str(leaked))
    loud.developer_mode = True
    check("diagnostics do apply in developer mode",
          "PSX_FORCE_INTERP" in _build_env(loud))

    print("\n5. audio settings reach the runtime")
    s = config.Settings(volume=55, mute=False, audio_latency_ms=60).clamp()
    env = _build_env(s)
    check("volume passed", env.get("PSX_AUDIO_VOLUME") == "55", env.get("PSX_AUDIO_VOLUME", "-"))
    check("latency passed", env.get("PSX_AUDIO_BUFFER_MS") == "60", env.get("PSX_AUDIO_BUFFER_MS", "-"))
    muted = _build_env(config.Settings(volume=80, mute=True).clamp())
    check("mute wins over volume", muted.get("PSX_AUDIO_VOLUME") == "0",
          muted.get("PSX_AUDIO_VOLUME", "-"))

    win.close()
    shutil.rmtree(str(tmp), ignore_errors=True)
    print()
    if failures:
        print("%d FAILURE(S)" % len(failures))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
