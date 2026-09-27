"""tuning/scripts/host_profile.py: names, groups and phase splits."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "host_profile", ROOT / "tuning" / "scripts" / "host_profile.py")
hp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hp)


class HostProfileTests(unittest.TestCase):
    def setUp(self):
        self.base = 0x140000000
        hp.image_base = lambda exe: self.base
        hp.symbols = lambda exe: ([self.base + 0x1000, self.base + 0x2000,
                                   self.base + 0x3000],
                                  ["func_8001D63C", "gpu_textured_triangle",
                                   "gl12_DrawArrays"])
        profile = {
            "tag": "native120", "active_ms": 20000, "samples": 100, "dropped": 0,
            "distinct": 5, "exe_base": "0x7FF600000000",
            "phases": {"emulate": 70, "present": 20, "pace": 10, "other": 0},
            "leaf": [
                {"m": "Game.exe", "rva": "0x1010", "n": [40, 0, 0, 0]},
                {"m": "Game.exe", "rva": "0x1020", "n": [10, 0, 0, 0]},
                {"m": "Game.exe", "rva": "0x2004", "n": [15, 5, 0, 0]},
                {"m": "nvwgf2umx.dll", "rva": "0x99", "n": [5, 15, 0, 0]},
                {"m": "ntdll.dll", "rva": "0x10", "n": [0, 0, 10, 0]},
            ],
        }
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "psx_host_profile.json"
        self.path.write_text(json.dumps(profile), encoding="utf-8")

    def test_named_and_grouped(self):
        r = hp.load(self.path, Path("Game.exe"))
        self.assertEqual(r["by_func"]["func_8001D63C"], [50, 0, 0, 0])
        self.assertEqual(r["by_func"]["gpu_textured_triangle"], [15, 5, 0, 0])
        self.assertEqual(r["by_group"]["guest code (recompiled)"], [50, 0, 0, 0])
        self.assertEqual(r["by_group"]["graphics driver (nvwgf2umx.dll)"], [5, 15, 0, 0])
        self.assertEqual(r["by_group"]["OS (ntdll.dll)"], [0, 0, 10, 0])
        self.assertEqual(r["by_module"]["Game.exe"], 70)

    def test_show_runs(self):
        hp.show(hp.load(self.path, Path("Game.exe")), top=5)


if __name__ == "__main__":
    unittest.main()
