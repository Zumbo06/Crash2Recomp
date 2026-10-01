"""Widescreen scenery range (crash2_wide_slst.h) against every SLST entry on
the disc, in wide_slst_sim.

Exports the entries (and hashes of the reference node lists) to a temporary
folder - they are game data, never written into the repository - builds
tuning/wide_slst_sim/sim.c against the live framework tree and runs it with the
real executable as guest RAM. Skipped when the toolchain, the tree, the
executable or the disc is missing.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SIM = ROOT / "tuning" / "wide_slst_sim"
HEADER = (ROOT / "_build" / "Crash2Recomp" / "psxrecomp" / "runtime" / "src"
          / "crash2_wide_slst.h")
EXE = ROOT / "_build" / "Crash2Recomp" / "input" / "SCUS_941.54"
NSF_CACHE = ROOT / "_build" / "nsf_cache"
TOOLCHAIN = Path(os.environ.get(
    "RETCOMM_TOOLCHAIN",
    Path.home() / ".local" / "share" / "retcomm" / "toolchains" / "cmake-clang-v1"
    / "latest"))
CLANG = TOOLCHAIN / "bin" / "clang.exe"


def _have_levels() -> bool:
    if any(NSF_CACHE.glob("*.NSF")):
        return True
    try:
        sys.path.insert(0, str(ROOT / "_build"))
        import psxexe
        return os.path.isfile(psxexe.bin_path())
    except Exception:
        return False


@unittest.skipUnless(HEADER.is_file() and EXE.is_file() and CLANG.is_file()
                     and _have_levels(),
                     "framework tree, executable, toolchain or disc missing")
class WideSlstModelTests(unittest.TestCase):
    def test_model(self):
        with tempfile.TemporaryDirectory() as td:
            blob = Path(td) / "slst.bin"
            exp = subprocess.run([sys.executable, str(SIM / "export.py"), str(blob),
                                  "--nsf-dir", str(NSF_CACHE)],
                                 capture_output=True, text=True, timeout=600)
            self.assertEqual(exp.returncode, 0, exp.stdout + exp.stderr)
            out = SIM / "sim.exe"
            subprocess.run([str(CLANG), "-O1", "-Wall", "-Wno-unused-function",
                            f"-I{HEADER.parent}", str(SIM / "sim.c"), "-o", str(out)],
                           check=True, capture_output=True)
            run = subprocess.run([str(out), str(blob), str(EXE)], capture_output=True,
                                 text=True, timeout=600)
        self.assertEqual(run.returncode, 0, run.stdout[-4000:] + run.stderr)
        self.assertIn("ALL CHECKS PASSED", run.stdout)
        self.assertIn(" 0 differ from the reference", run.stdout)


if __name__ == "__main__":
    unittest.main()
