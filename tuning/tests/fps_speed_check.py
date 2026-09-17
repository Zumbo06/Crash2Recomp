import importlib.util
import struct
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location(
    "fps_speed", SCRIPT_DIR / "fps_speed.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def blob(values: list[int]) -> bytes:
    return b"".join(struct.pack("<i", value) for value in values)


class DisplacementTests(unittest.TestCase):
    ORIGIN = list(range(0, 4000, 100))

    def test_matched_travel_reads_as_unchanged(self):
        moved = [v + 500 for v in self.ORIGIN]
        result = module.compare((blob(self.ORIGIN), blob(moved)),
                                (blob(self.ORIGIN), blob(moved)), 0x80060000)
        self.assertEqual(result["speed_ratio_median"], 1.0)
        self.assertEqual(result["verdict"], "world speed unchanged")

    def test_double_travel_is_caught(self):
        slow = [v + 500 for v in self.ORIGIN]
        fast = [v + 1000 for v in self.ORIGIN]
        result = module.compare((blob(self.ORIGIN), blob(slow)),
                                (blob(self.ORIGIN), blob(fast)), 0x80060000)
        self.assertEqual(result["speed_ratio_median"], 2.0)
        self.assertEqual(result["verdict"], "world speed roughly doubled")

    def test_each_run_uses_its_own_baseline(self):
        # The whole point of the rewrite: the 60 Hz leg starts from a state a
        # few frames past where the 30 Hz leg did, because a savestate load
        # completes at a safe boundary and the emulator keeps running. Travel
        # is identical, so the verdict must be, even though no two arrays match.
        slow_before = self.ORIGIN
        slow_after = [v + 500 for v in slow_before]
        fast_before = [v + 37 for v in self.ORIGIN]     # drifted start
        fast_after = [v + 500 for v in fast_before]
        result = module.compare((blob(slow_before), blob(slow_after)),
                                (blob(fast_before), blob(fast_after)), 0x80060000)
        self.assertEqual(result["speed_ratio_median"], 1.0)
        self.assertEqual(result["verdict"], "world speed unchanged")

    def test_words_that_moved_only_one_way_are_not_counted(self):
        origin = [0] * 40
        slow = [1000] * 40
        result = module.compare((blob(origin), blob(slow)),
                                (blob(origin), blob(origin)), 0x80060000)
        self.assertEqual(result["words_compared"], 0)
        self.assertEqual(result["verdict"], "inconclusive")

    def test_opposite_directions_and_huge_jumps_are_excluded(self):
        origin = [0, 0, 0]
        result = module.compare((blob(origin), blob([500, 500, 500])),
                                (blob(origin), blob([-500, 1 << 28, 501])),
                                0x80060000)
        self.assertEqual(result["words_compared"], 1)
        self.assertEqual(result["movers"][0]["addr"], "0x80060008")

    def test_length_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            module.compare((blob([1]), blob([1, 2])),
                           (blob([1]), blob([1])), 0x80060000)


if __name__ == "__main__":
    unittest.main()
