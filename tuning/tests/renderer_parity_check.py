"""The renderer parity harness (tuning/renderer_parity) on this machine's GPU.

Builds parity.exe against the live framework tree and runs the OpenGL and the
Direct3D 12 compilation of gpu_gl_renderer.c at scale 2. Each must pass its
own checks (the native-wide border rows, the asynchronous VRAM readback), and
the two must agree: VRAM exactly, every present to within 2/255. Skipped
without the toolchain, the tree or a POSIX shell, and when this machine cannot
open the renderer (no GPU, a remote session).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tuning" / "renderer_parity"
RENDERER = (ROOT / "_build" / "Crash2Recomp" / "psxrecomp" / "runtime" / "src"
            / "gpu_gl_renderer.c")
TOOLCHAIN = Path(os.environ.get(
    "RETCOMM_TOOLCHAIN",
    Path.home() / ".local" / "share" / "retcomm" / "toolchains" / "cmake-clang-v1"
    / "latest"))
NO_RENDERER = ("renderer init failed", "window:", "SDL_Init:", "d3d12 not compiled in")


def _posix_shell():
    sh = shutil.which("sh")
    if sh:
        return sh
    git = shutil.which("git")
    candidates = [Path(git).resolve().parents[1] / "bin" / "sh.exe"] if git else []
    candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
                      / "Git" / "bin" / "sh.exe")
    return next((str(c) for c in candidates if c.is_file()), None)


SH = _posix_shell()


@unittest.skipUnless(RENDERER.is_file() and (TOOLCHAIN / "bin" / "clang.exe").is_file()
                     and SH, "framework tree, toolchain or POSIX shell missing")
class RendererParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build = subprocess.run([SH, str(HARNESS / "build.sh")], capture_output=True,
                               text=True, timeout=900)
        if build.returncode != 0:
            raise AssertionError("parity build failed:\n" + build.stdout[-3000:]
                                 + build.stderr[-3000:])
        cls.tmp = tempfile.TemporaryDirectory()
        cls.runs = {api: cls._run(api) for api in ("gl", "d3d12")}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def _run(cls, api):
        prefix = str(Path(cls.tmp.name) / api)
        run = subprocess.run([str(HARNESS / "parity.exe"), api, "2", prefix],
                             capture_output=True, text=True, timeout=300)
        return prefix, run

    def _checked_run(self, api):
        prefix, run = self.runs[api]
        out = run.stdout + run.stderr
        if run.returncode != 0 and any(s in out for s in NO_RENDERER):
            self.skipTest(f"{api} renderer unavailable here: {out.strip()[-300:]}")
        self.assertEqual(run.returncode, 0, out[-3000:])
        return prefix, out

    def _own_checks(self, api):
        _, out = self._checked_run(api)
        self.assertIn("wide border rows (GPU present): ok", out)
        self.assertIn("wide border rows (readback present): ok", out)
        self.assertNotIn("FAILED", out)
        self.assertNotIn("async readback: DIFFERS", out)

    def test_opengl(self):
        self._own_checks("gl")

    def test_direct3d12(self):
        self._own_checks("d3d12")

    def test_apis_agree(self):
        try:
            import numpy  # noqa: F401  (compare.py's dependencies)
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("compare.py needs numpy and Pillow")
        gl, _ = self._checked_run("gl")
        dx, _ = self._checked_run("d3d12")
        cmp = subprocess.run([sys.executable, str(HARNESS / "compare.py"), gl, dx],
                             capture_output=True, text=True, timeout=300)
        self.assertEqual(cmp.returncode, 0, cmp.stdout + cmp.stderr)
        self.assertIn("VRAM: 0 of", cmp.stdout)
        presents = re.findall(r"present \d+: \d+ px differ, (\d+) by more than", cmp.stdout)
        self.assertTrue(presents, cmp.stdout)
        self.assertEqual(set(presents), {"0"}, cmp.stdout)


if __name__ == "__main__":
    unittest.main()
