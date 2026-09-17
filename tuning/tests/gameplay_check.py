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

    def test_ram_assertion_bounds(self):
        valid = {"steps": [{"frames": 5, "buttons": "0xFFFF"}],
                 "assert_ram": [{"addr": "0x8006CBD1", "hex": "04"}],
                 "assert_ram_delta": [{"addr": "0x8006CBD1", "bytes": 1, "delta": 0}]}
        self.assertEqual(module.validate_scenario(valid), [(5, 0xFFFF)])
        for address in ("0x801FFFFF", "0x1F801000", "0xA006CBD1"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                module.validate_scenario({"steps": valid["steps"],
                                          "assert_ram": [{"addr": address, "hex": "0000"}]})
        with self.assertRaises(ValueError):
            module.validate_scenario({"steps": valid["steps"],
                                      "assert_ram_delta": [{"addr": "0x8006CBD1",
                                                            "bytes": 3, "delta": 0}]})

    def test_state_delta_failure_is_reported(self):
        class FakeClient:
            reads = 0

            def call(self, command, **args):
                if command == "read_ram":
                    self.reads += 1
                    return {"hex": "04" if self.reads == 1 else "03"}
                if command == "input_route_start":
                    return {"start_frame": 100}
                if command == "input_route_status":
                    return {"active": False}
                if command == "frame_range":
                    return {"frames": [{"pad": "0xFFFF"}]}
                if command == "frame_rates":
                    return {"game_loop_steps": 30, "game_frame_ticks": 34,
                            "native_60fps_gate_open": 0,
                            "native_60fps_one_field_pct": 0}
                if command == "history":
                    return {"newest": 110}
                return {"ok": True}

        result = module.run(FakeClient(), {
            "name": "mock-damage", "require_game_loop": False,
            "steps": [{"frames": 10, "buttons": "0xFFFF"}],
            "assert_ram_delta": [{"addr": "0x8006CBD1", "bytes": 1, "delta": 0}],
        }, timeout=1)
        self.assertFalse(result["passed"])
        self.assertEqual(result["failures"][0]["actual_delta"], -1)


class Native60ScenarioTests(unittest.TestCase):
    def _client(self, rates):
        calls = []

        class FakeClient:
            def call(_self, command, **args):
                calls.append((command, args))
                if command == "input_route_start":
                    return {"start_frame": 100}
                if command == "input_route_status":
                    return {"active": False}
                if command == "frame_range":
                    return {"frames": [{"pad": "0xFFFF"}]}
                if command == "frame_rates":
                    return rates
                if command == "history":
                    return {"newest": 110}
                return {"ok": True}

        return FakeClient(), calls

    BASE = {"steps": [{"frames": 10, "buttons": "0xFFFF"}],
            "require_game_loop": False}
    MODERN = {"native_60fps_gate_open": 1, "native_60fps_one_field_pct": 95}

    def test_a_runtime_without_the_guard_is_refused(self):
        # A stale build answers everything and reports the old behaviour. That
        # already cost one Turtle Woods run, so it has to fail loudly.
        client, _ = self._client({"game_loop_steps": 48, "speed": 1.0,
                                  "game_frame_ticks": 34})
        with self.assertRaises(RuntimeError) as caught:
            module.run(client, {**self.BASE, "name": "stale",
                                "native_60fps": True}, timeout=1)
        self.assertIn("sustain guard", str(caught.exception))

    def test_alternating_fields_fail_even_at_a_plausible_loop_rate(self):
        # The measured Turtle Woods regime: 48.5 loops/s, speed 1.0, and a
        # frame-ticks sample that can come back either 17 or 34.
        client, _ = self._client({**self.MODERN, "game_loop_steps": 48.5,
                                  "speed": 1.0, "game_frame_ticks": 17,
                                  "native_60fps_one_field_pct": 76})
        result = module.run(client, {**self.BASE, "name": "ragged",
                                     "native_60fps": True,
                                     "min_one_field_pct": 85}, timeout=1)
        self.assertFalse(result["passed"])
        self.assertEqual(result["failures"][0]["one_field_pct"], 76)

    def test_mode_is_set_both_ways_and_always_restored(self):
        client, calls = self._client({**self.MODERN, "game_loop_steps": 59,
                                      "speed": 1.0, "game_frame_ticks": 17})
        module.run(client, {**self.BASE, "name": "on", "native_60fps": True,
                            "cpu_percent": 130}, timeout=1)
        sets = [args for name, args in calls if name == "crash2_60fps"]
        self.assertEqual(sets[0], {"enabled": 1, "cpu_percent": 130})
        # Restored no matter how the route ended.
        self.assertEqual(sets[-1], {"enabled": 0})

    def test_engine_frame_time_is_asserted_not_just_the_loop_rate(self):
        # 60 loops a second while the engine still scales motion by two fields
        # is the game running at double speed, so this has to fail.
        client, _ = self._client({**self.MODERN, "game_loop_steps": 59,
                                  "speed": 1.0, "game_frame_ticks": 34})
        result = module.run(client, {**self.BASE, "name": "doubled",
                                     "native_60fps": True,
                                     "expected_game_frame_ticks": 17}, timeout=1)
        self.assertFalse(result["passed"])
        self.assertEqual(result["failures"][0]["game_frame_ticks"], 34)

    def test_slow_guest_time_fails_even_at_a_good_loop_rate(self):
        client, _ = self._client({**self.MODERN, "game_loop_steps": 58,
                                  "speed": 0.82, "game_frame_ticks": 17})
        result = module.run(client, {**self.BASE, "name": "slow",
                                     "native_60fps": True,
                                     "min_speed": 0.95}, timeout=1)
        self.assertFalse(result["passed"])
        self.assertEqual(result["failures"][0]["speed"], 0.82)

    def test_sustain_backoffs_are_counted_over_the_route_only(self):
        client, _ = self._client({**self.MODERN, "game_loop_steps": 30,
                                  "speed": 1.0, "game_frame_ticks": 34,
                                  "native_60fps_backoffs": 7})
        # Baseline and final reading are the same 7, so nothing happened here.
        result = module.run(client, {**self.BASE, "name": "quiet",
                                     "native_60fps": True,
                                     "max_backoffs": 0}, timeout=1)
        self.assertTrue(result["passed"], result["failures"])

    def test_scenario_rejects_impossible_frame_time(self):
        for bad in ({"expected_game_frame_ticks": 20}, {"cpu_percent": 200},
                    {"min_speed": 2}, {"min_one_field_pct": 101}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                module.validate_scenario({**self.BASE, **bad})


if __name__ == "__main__":
    unittest.main()
