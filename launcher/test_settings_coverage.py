"""Guards against settings silently disappearing from the UI.

The launcher once wrote settings to a file the running game never opened, and
a diagnostic that degrades playback sat on the ordinary settings page. Both
were invisible until they caused real problems. These checks make that class of
mistake fail loudly instead.

Run:  python test_settings_coverage.py
"""

from __future__ import annotations

import os
import sys
from dataclasses import fields

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from crash2launcher import config, paths, runtime  # noqa: E402
from crash2launcher.ui.page_advanced import AdvancedPage  # noqa: E402
from crash2launcher.ui.page_settings import SettingsPage  # noqa: E402

# Settings that are deliberately not user-facing: internal bookkeeping, or
# handled by a dedicated page rather than a control.
NOT_IN_UI = {
    "disc_path", "disc_verified", "disc_sha1",   # Setup flow
    "bindings", "enabled_mods",                  # not implemented yet
    "last_page", "window_geometry",              # launcher's own state
    "integer_scaling", "smooth_60fps", "frame_blend",
    "fast_loading", "cd_speed_boost", "turbo_key",
    "pgxp_cpu_mode",                             # follows geometry_correction
}

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if condition:
        print("  ok   " + message)
    else:
        print("  FAIL " + message)
        failures.append(message)


def main() -> int:
    app = QApplication([])  # noqa: F841 - required for widget construction
    settings = config.Settings()
    settings_page = SettingsPage(settings)
    advanced_page = AdvancedPage(settings)

    all_fields = {f.name for f in fields(config.Settings)}
    diagnostics = set(config.DIAGNOSTIC_SETTINGS)

    print("\n1. every setting is reachable somewhere")
    settings_attrs = {a for a in dir(settings_page) if not a.startswith("_")}
    advanced_attrs = {a for a in dir(advanced_page) if not a.startswith("_")}
    # Controls are named after their setting wherever possible; the rest are
    # covered by the explicit allow-list above.
    orphans = sorted(
        name for name in all_fields
        if name not in NOT_IN_UI
        and name not in diagnostics
        and name not in settings_attrs
        and not _has_control(settings_page, name)
    )
    check(not orphans, "no orphaned settings (%s)" % (orphans or "none"))

    print("\n2. diagnostics stay off the ordinary settings page")
    leaked = sorted(d for d in diagnostics if d in settings_attrs)
    check(not leaked, "no diagnostics on Settings (%s)" % (leaked or "none"))
    missing = sorted(d for d in diagnostics if d not in advanced_attrs)
    check(not missing, "all diagnostics on Advanced (%s)" % (missing or "none"))

    print("\n3. defaults are a clean, diagnostic-free state")
    fresh = config.Settings()
    check(not config.active_diagnostics(fresh),
          "no diagnostic enabled by default")
    check(fresh.audio_legacy is False,
          "legacy audio path off by default (it causes underruns)")

    print("\n4. presets never enable a diagnostic")
    for name in config.PRESETS:
        applied = config.apply_preset(config.Settings(), name)
        check(not config.active_diagnostics(applied),
              "preset %r leaves diagnostics alone" % name)

    print("\n5. settings reach every build tree")
    layout = paths.detect()
    targets = runtime._settings_targets(layout)
    check(len(targets) >= 1, "at least one build tree targeted (%d)" % len(targets))
    if (layout.project / "build-debugtools").is_dir():
        check(any(t.name == "build-debugtools" for t in targets),
              "debugtools tree included (the bug that made nothing apply)")

    print("\n%s" % ("ALL CHECKS PASSED" if not failures
                    else "%d FAILURE(S)" % len(failures)))
    return 1 if failures else 0


def _has_control(page: SettingsPage, setting: str) -> bool:
    """Some controls are named for the concept, not the field."""
    aliases = {
        "window_width": "output_resolution",
        "window_height": "output_resolution",
        "overscan_top": "overscan",
        "overscan_bottom": "overscan",
        "overscan_left": "overscan",
        "overscan_right": "overscan",
        "widescreen_native_wide": "ws_mode",
        "scaling_mode": "scaling",
        "texture_filter": "tex_filter",
        "crt_filter": "crt",
        "antialiasing": "aa",
        "geometry_correction": "geom",
        "perspective_texturing": "persp",
        "fullscreen_mode": "fullscreen",
        "supersampling": "scale",
        "merge_all_input": "merge_input",
        "frame_interpolation_fps": "interp_fps",
        "frame_interpolation": "interp",
    }
    return hasattr(page, aliases.get(setting, setting))


if __name__ == "__main__":
    raise SystemExit(main())
