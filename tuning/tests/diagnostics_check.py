import json
import tempfile
import unittest
from pathlib import Path

from crash2launcher.config import Settings
from crash2launcher.diagnostics import cadence_sample, make_report


class DiagnosticReportTests(unittest.TestCase):
    def test_allowlist_excludes_private_values(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "heartbeat.json"
            source.write_text(json.dumps({
                "backend": "psx-runtime", "frame_count": 120,
                "raw_ram": "SECRET", "absolute_path": "C:/private",
                "ring": [{"wall": 1, "frame": 120, "store_pc": "SECRET"}],
            }), encoding="utf-8")
            settings = Settings(disc_path="C:/private/game.cue")
            report = make_report(settings, source)
            encoded = json.dumps(report)
            self.assertEqual(report["schema_version"], 1)
            self.assertEqual(report["heartbeat"]["samples"][0],
                             {"wall": 1, "frame": 120})
            self.assertNotIn("SECRET", encoded)
            self.assertNotIn("C:/private", encoded)

    def test_missing_heartbeat_is_explicit(self):
        report = make_report(Settings(), Path("no-such-heartbeat.json"))
        self.assertEqual(report["heartbeat_status"], "unavailable")

    def test_cadence_window_rates(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "heartbeat.json"
            source.write_text(json.dumps({"frame_count": 120,
                                          "vblank_raise_count": 120,
                                          "game_loop_count": 60,
                                          "display_flip_count": 60,
                                          "host_swap_count": 60}), encoding="utf-8")
            first = cadence_sample(source, 10.0)
            source.write_text(json.dumps({"frame_count": 240,
                                          "vblank_raise_count": 240,
                                          "game_loop_count": 120,
                                          "display_flip_count": 120,
                                          "host_swap_count": 120}), encoding="utf-8")
            last = cadence_sample(source, 12.0)
            rates = make_report(Settings(), source, [first, last])["cadence_per_second"]
            self.assertEqual(rates["vblank_raise_count"], 60)
            self.assertEqual(rates["game_loop_count"], 30)


if __name__ == "__main__":
    unittest.main()
