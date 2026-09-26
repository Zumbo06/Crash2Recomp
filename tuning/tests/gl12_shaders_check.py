"""The Direct3D 12 renderer's shader table must match the GLSL it translates.

gpu_gl12.cpp finds the HLSL for each GLSL source by the hash of the GLSL text.
A GLSL edit in gpu_gl_renderer.c or gpu_gl_postfx.c that is not mirrored in
gl12_hlsl/*.hlsl (and the regenerated gpu_gl12_shaders.h) makes the D3D12
renderer refuse to start - safe, but it should be caught here first.
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GEN = (ROOT / "_build" / "Crash2Recomp" / "psxrecomp" / "runtime" / "src"
       / "gl12_hlsl" / "gen_gl12_shaders.py")


@unittest.skipUnless(GEN.is_file(), "framework tree not built")
class Gl12ShaderTableTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("gen_gl12_shaders", GEN)
        self.gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.gen)

    def test_header_matches_sources(self):
        header = self.gen.build()
        self.assertEqual(self.gen.OUT.read_text(encoding="utf-8"), header,
                         "gpu_gl12_shaders.h is stale: run gen_gl12_shaders.py")

    def test_every_glsl_program_has_a_translation(self):
        names = [n for names in self.gen.SOURCES.values() for n in names]
        for name in names:
            self.assertTrue((GEN.parent / f"{name}.hlsl").is_file(), name)
        self.assertEqual(len(names), len(set(names)))

    def test_c_string_parser(self):
        text = ('static const char *X =\n  "a\\n"  /* note */\n  "b\\"c\\\\"\n'
                '  "/* kept: inside the string */";\n')
        self.assertEqual(self.gen.c_string_initializer(text, "X"),
                         'a\nb"c\\/* kept: inside the string */')


if __name__ == "__main__":
    unittest.main()
