import importlib.util
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "gameplay_smoke", Path(__file__).parents[1] / "scripts" / "gameplay_smoke.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RouteValidationTests(unittest.TestCase):
    def test_active_low_pad_word(self):
        self.assertEqual(module.validate_scenario({"steps": [
            {"frames": 5, "buttons": "0xFFBF"}]}), [(5, 0xFFBF)])

    def test_rejects_bad_route(self):
        with self.assertRaises(ValueError):
            module.validate_scenario({"steps": [{"frames": 0, "buttons": 0}]})
        with self.assertRaises(ValueError):
            module.validate_scenario({"steps": [{"frames": 1, "buttons": 65536}]})


if __name__ == "__main__":
    unittest.main()
