"""Launcher contract for the single-switch native-60 behavior."""

import json
import tempfile
import unittest
from pathlib import Path

from crash2launcher.config import Settings, load
from crash2launcher.runtime import _build_env


class Native60LauncherTests(unittest.TestCase):
    def test_disabled_mode_exports_no_patch_controls(self):
        env = _build_env(Settings(native_60fps=False))
        self.assertNotIn("PSX_CRASH2_60FPS", env)
        self.assertNotIn("PSX_CRASH2_60FPS_CPU_PCT", env)
        self.assertNotIn("PSX_CRASH2_60FPS_FORCE_GATE", env)

    def test_enabled_mode_is_fixed_at_200_and_prefer_60(self):
        env = _build_env(Settings(native_60fps=True))
        self.assertEqual(env.get("PSX_CRASH2_60FPS"), "1")
        self.assertEqual(env.get("PSX_CRASH2_60FPS_CPU_PCT"), "200")
        self.assertEqual(env.get("PSX_CRASH2_60FPS_FORCE_GATE"), "1")
        self.assertEqual(env.get("PSX_CRASH2_60FPS_HOLD_PCT"), "0")

    def test_legacy_tuning_keys_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({
                "native_60fps": True,
                "native_60fps_cpu_percent": 100,
                "native_60fps_fallback": "smooth",
            }), encoding="utf-8")
            settings = load(path)
            self.assertFalse(hasattr(settings, "native_60fps_cpu_percent"))
            self.assertFalse(hasattr(settings, "native_60fps_fallback"))
            env = _build_env(settings)
            self.assertEqual(env["PSX_CRASH2_60FPS_CPU_PCT"], "200")
            self.assertEqual(env["PSX_CRASH2_60FPS_FORCE_GATE"], "1")


if __name__ == "__main__":
    unittest.main()
