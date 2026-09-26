"""Native 120 FPS (crash2_60fps.h) against the machine model in native120_sim.

Builds tuning/native120_sim/sim.c against the live framework tree and runs it
over the real executable. Skipped when the toolchain, the tree or the
executable is missing.
"""
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SIM = ROOT / "tuning" / "native120_sim"
HEADER = (ROOT / "_build" / "Crash2Recomp" / "psxrecomp" / "runtime" / "src"
          / "crash2_60fps.h")
EXE = ROOT / "_build" / "Crash2Recomp" / "input" / "SCUS_941.54"
TOOLCHAIN = Path(os.environ.get(
    "RETCOMM_TOOLCHAIN",
    Path.home() / ".local" / "share" / "retcomm" / "toolchains" / "cmake-clang-v1"
    / "latest"))
CLANG = TOOLCHAIN / "bin" / "clang.exe"


@unittest.skipUnless(HEADER.is_file() and EXE.is_file() and CLANG.is_file(),
                     "framework tree, executable or toolchain missing")
class Native120ModelTests(unittest.TestCase):
    def test_model(self):
        runtime = HEADER.parents[1]
        out = SIM / "sim.exe"
        subprocess.run([str(CLANG), "-O1", "-Wall", "-Wno-unused-function",
                        f"-I{runtime / 'include'}", f"-I{runtime / 'src'}",
                        str(SIM / "sim.c"), "-o", str(out)],
                       check=True, capture_output=True)
        run = subprocess.run([str(out), str(EXE)], capture_output=True,
                             text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout[-4000:] + run.stderr)
        self.assertIn("ALL CHECKS PASSED", run.stdout)


if __name__ == "__main__":
    unittest.main()
