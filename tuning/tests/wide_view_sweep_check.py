"""The widescreen view sweep (tuning/wide_view_sweep) on one level.

Builds raster.dll, then sweeps Air Crash (S0000020) with the hook's rules and
0054's, checking the first views' lists against frustum_ref.draw. The hook
must add no polygon with a corner the GTE cannot place on any path, and touch
no more of the 4:3 frame than 0054 did. Skipped when the toolchain, numpy or
the level files are missing.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SWEEP = ROOT / "tuning" / "wide_view_sweep"
NSF_CACHE = ROOT / "_build" / "nsf_cache"
LEVEL = "S0000020.NSF"
TOOLCHAIN = Path(os.environ.get(
    "RETCOMM_TOOLCHAIN",
    Path.home() / ".local" / "share" / "retcomm" / "toolchains" / "cmake-clang-v1"
    / "latest"))
CLANG = TOOLCHAIN / "bin" / "clang.exe"


def _have_numpy() -> bool:
    try:
        import numpy  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(CLANG.is_file() and (NSF_CACHE / LEVEL).is_file() and _have_numpy(),
                     "toolchain, numpy or level files missing")
class WideViewSweepTests(unittest.TestCase):
    def test_air_crash(self):
        subprocess.run(["sh", str(SWEEP / "build.sh")], check=True, capture_output=True,
                       timeout=300)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "views.json"
            run = subprocess.run(
                [sys.executable, str(SWEEP / "sweep.py"), "--levels", LEVEL, "--jobs", "1",
                 "--variants", "hook,0054", "--verify", "40", "--json", str(out),
                 "--nsf-dir", str(NSF_CACHE)],
                capture_output=True, text=True, timeout=1200)
            self.assertEqual(run.returncode, 0, run.stdout[-2000:] + run.stderr[-2000:])
            views = json.loads(out.read_text())
        self.assertGreater(len(views), 400)
        fwd = [v for v in views if not v["side"] and v["m"]["hook"]["model"]]
        self.assertGreater(len(fwd), 300)

        def total(name, key, vs=fwd):
            return sum(v["m"][name][key] for v in vs)

        self.assertEqual(total("hook", "wedges", [v for v in views if v["m"]["hook"]["model"]]),
                         0, "an added polygon with a corner the GTE cannot place")
        self.assertGreater(total("0054", "wedges"), 0, "0054 added wedges here")
        self.assertLessEqual(total("hook", "in43_changed"), total("0054", "in43_changed"))
        self.assertLessEqual(total("hook", "in43_drawn_spike"), total("0054", "in43_drawn_spike"))
        self.assertEqual(total("hook", "gap"), total("0054", "gap"),
                         "the wedges refused drew nothing a gap needed")


if __name__ == "__main__":
    unittest.main()
