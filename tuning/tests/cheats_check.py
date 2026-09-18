"""Launcher contract for the opt-in SCUS-94154 assists.

Aku Aku is a three-value level rather than a checkbox, so the interesting
cases are the boundaries: nothing leaks when off, each level reaches the
runtime under its own name, and a settings file written by the checkbox build
still loads.
"""

import json
import tempfile
import unittest
from pathlib import Path

from crash2launcher.config import CHEAT_AKU_LEVELS, Settings, load, save
from crash2launcher.runtime import _build_env


class CheatSettingsTests(unittest.TestCase):
    def test_off_by_default_and_no_environment_leak(self):
        settings = Settings()
        env = _build_env(settings)
        self.assertFalse(settings.cheat_infinite_lives)
        self.assertEqual(settings.cheat_aku_aku, "off")
        self.assertNotIn("PSX_CRASH2_CHEAT_LIVES", env)
        self.assertNotIn("PSX_CRASH2_CHEAT_AKU", env)

    def test_independent_opt_in(self):
        settings = Settings(cheat_infinite_lives=True)
        self.assertEqual(_build_env(settings).get("PSX_CRASH2_CHEAT_LIVES"), "1")
        self.assertNotIn("PSX_CRASH2_CHEAT_AKU", _build_env(settings))

    def test_each_aku_level_reaches_the_runtime_by_name(self):
        # The runtime matches these strings exactly (crash2_cheats.h), so a
        # rename on either side has to break a test rather than a player's save.
        for level in ("keep_masks", "no_damage"):
            with self.subTest(level=level):
                env = _build_env(Settings(cheat_aku_aku=level))
                self.assertEqual(env.get("PSX_CRASH2_CHEAT_AKU"), level)

    def test_unknown_level_clamps_off_rather_than_reaching_the_game(self):
        settings = Settings(cheat_aku_aku="banana").clamp()
        self.assertEqual(settings.cheat_aku_aku, "off")
        self.assertNotIn("PSX_CRASH2_CHEAT_AKU", _build_env(settings))

    def test_levels_match_the_runtime_ordering(self):
        # The index IS the C2_AKU_* level and PSX_PAUSE_AKU_COUNT; the pause
        # menu cycles by index, so the order is part of the contract.
        self.assertEqual(CHEAT_AKU_LEVELS, ("off", "keep_masks", "no_damage"))

    def test_saved_choice_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save(path, Settings(cheat_infinite_lives=True,
                                cheat_aku_aku="no_damage"))
            restored = load(path)
            self.assertTrue(restored.cheat_infinite_lives)
            self.assertEqual(restored.cheat_aku_aku, "no_damage")

    def test_settings_file_from_the_checkbox_build_still_loads(self):
        # load() filters by field NAME only, so without a migration the old
        # bool would land in the field and clamp() would reset the player's
        # choice to off without saying so.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"cheat_aku_aku": True}), encoding="utf-8")
            self.assertEqual(load(path).cheat_aku_aku, "keep_masks")
            path.write_text(json.dumps({"cheat_aku_aku": False}), encoding="utf-8")
            self.assertEqual(load(path).cheat_aku_aku, "off")


if __name__ == "__main__":
    unittest.main()
