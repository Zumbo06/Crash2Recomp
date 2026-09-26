import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "scale_bench.py"
spec = importlib.util.spec_from_file_location("scale_bench", SCRIPT)
scale_bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scale_bench)


def heartbeat(vbl, swaps, loops, **gl):
    base = {key: 0 for key in scale_bench.GL_TOTALS}
    base.update({"scale": 5, "wide": 1, "gpu_scene_max_us": 0})
    base.update(gl)
    return {"vblank_raise_count": vbl, "host_swap_count": swaps,
            "game_loop_count": loops, "native_60fps_one_field_pct": 97, "gl": base}


class ScaleBenchTests(unittest.TestCase):
    def test_rates_per_second_and_per_frame(self):
        first = heartbeat(0, 0, 0)
        last = heartbeat(600, 400, 400, presents=400, swap_us=4_000_000,
                         batches=80_000, semi_isolated=20_000, sync_reads=20,
                         sync_us=500_000, stencil_px=50_000_000,
                         gpu_frames=400, gpu_scene_us=5_600_000,
                         gpu_present_us=400_000, break_semi=20_000)
        row = scale_bench.rates(first, last, 10.0)
        self.assertEqual(row["vblank_hz"], 60.0)
        self.assertEqual(row["swaps_per_vblank"], 0.667)
        self.assertEqual(row["swap_ms_s"], 400.0)
        self.assertEqual(row["sync_ms_s"], 50.0)
        self.assertEqual(row["batches_frame"], 200.0)
        self.assertEqual(row["breaks_frame"]["semi"], 50.0)
        self.assertEqual(row["stencil_mpx_s"], 5.0)
        self.assertEqual(row["gpu_scene_ms"], 14.0)
        self.assertIn("SwapWindow", scale_bench.verdict(row))

    def test_keeping_up_and_missing_gpu_timing(self):
        row = scale_bench.rates(heartbeat(0, 0, 0), heartbeat(600, 599, 600, presents=599), 10.0)
        self.assertEqual(scale_bench.verdict(row), "host keeps up")
        self.assertIsNone(row["gpu_scene_ms"])

    def test_cpu_bound_verdict(self):
        row = scale_bench.rates(heartbeat(0, 0, 0),
                                heartbeat(600, 450, 450, presents=450, swap_us=10_000), 10.0)
        self.assertIn("emulation thread itself", scale_bench.verdict(row))

    def test_old_build_is_explicit(self):
        with self.assertRaises(ValueError):
            scale_bench.rates({"gl": None}, {"gl": None}, 5.0)


if __name__ == "__main__":
    unittest.main()
