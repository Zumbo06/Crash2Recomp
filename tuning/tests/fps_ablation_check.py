import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location("fps_ablation", SCRIPT_DIR / "fps_ablation.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CycleStatsTests(unittest.TestCase):
    def test_cycle_budget(self):
        rows = [{"cycles": 100}, {"cycles": 564580}, {"cycles": 1224580}]
        stats = module.cycle_stats(rows)
        self.assertEqual(stats["samples"], 2)
        self.assertEqual(stats["median"], 612240)
        self.assertGreater(stats["p95_vblank_budget_ratio"], 1.0)

    def test_empty_watch(self):
        self.assertIsNone(module.cycle_stats([])["median"])

    def test_stable_60_requires_engine_frame_time_too(self):
        good = {"game_loop_verified": 1, "vblank_raise": 59.94,
                "game_loop_steps": 59.8, "distinct_frames": 59.8,
                "host_swaps": 59.9, "speed": 1.0,
                "game_frame_ticks": 17, "guest_ticks_per_s": 1033.6}
        self.assertTrue(module.stable_60(good))
        for key, bad in (("game_loop_steps", 30), ("distinct_frames", 30),
                         ("host_swaps", 30), ("speed", 0.82),
                         ("game_loop_steps", 67),
                         # 60 loops a second while the engine still believes a
                         # frame lasted two fields is double speed, not 60 FPS.
                         ("game_frame_ticks", 34),
                         # A moved time base invalidates the frame time it is
                         # derived from, however good the cadence looks.
                         ("guest_ticks_per_s", 1292.0)):
            with self.subTest(key=key):
                self.assertFalse(module.stable_60({**good, key: bad}))

    def test_overlay_usage_deltas(self):
        before = {"dispatch_native": 100, "dispatch_interp_fallback": 80}
        after = {"active": 1, "dispatch_native": 130,
                 "dispatch_interp_fallback": 90}
        self.assertEqual(module.overlay_usage(before, after)["native_fraction"], 0.75)


if __name__ == "__main__":
    unittest.main()
