"""Deterministic input-route smoke test for a *debugtools* Crash 2 process.

Run against an isolated game directory/save card, never a player's session:
  python tuning/scripts/gameplay_smoke.py --port 28714 --scenario tuning/scenarios/idle.json

The debug server consumes each route segment on a guest VBlank. This runner
checks recorded pad words, frame cadence, and optional RAM state assertions.
Only use mapped gameplay fields in disposable test sessions.

A scenario can also ask for the native 60 FPS mode with "native_60fps": true.
The mode is turned on before the route and off again afterwards, whatever the
outcome, and the route still measures the same real time either way because
segments are consumed per guest VBlank. Assert the mode with
"expected_game_frame_ticks" (17 = the engine simulated 60 Hz steps, 34 = 30 Hz),
"min_speed" (guest time keeping up with the wall clock) and "max_backoffs" (how
many times the runtime's sustain guard gave up and fell back to 30).

A scenario can also drive the assists with "cheat_lives" (bool) and "cheat_aku"
(0 off, 1 keep masks, 2 no damage). Both are set before the route and cleared
afterwards whatever the outcome, so a scenario can never inherit the previous
run's state - the same rule the 60 FPS mode follows, and for the same reason.
"""

from __future__ import annotations

import argparse
import json
import socket
import time
from pathlib import Path


class DebugClient:
    def __init__(self, port: int):
        self.port = port

    def call(self, command: str, **args: object) -> dict:
        # The server accepts one connection per guest VBlank while this polls a
        # running route several times per 50 ms, so an occasional connection is
        # dropped rather than served. Losing a whole 12-second route to one
        # reset reads as a failed test, which is worse than waiting a frame and
        # asking again. A genuinely dead server still fails, on the last try.
        for attempt in range(3):
            try:
                return self._call_once(command, **args)
            except (ConnectionError, TimeoutError, socket.timeout):
                if attempt == 2:
                    raise
                time.sleep(0.05)
        raise AssertionError("unreachable")

    def _call_once(self, command: str, **args: object) -> dict:
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as conn:
            conn.settimeout(10)
            conn.sendall(json.dumps({"id": 1, "cmd": command, **args}).encode() + b"\n")
            chunks = []
            while chunk := conn.recv(65536):
                chunks.append(chunk)
        reply = json.loads(b"".join(chunks))
        if not reply.get("ok"):
            raise RuntimeError(f"{command}: {reply.get('error', reply)}")
        return reply


def validate_scenario(scenario: dict) -> list[tuple[int, int]]:
    steps = scenario.get("steps")
    if not isinstance(steps, list) or not steps or len(steps) > 4096:
        raise ValueError("steps must contain 1..4096 segments")
    route = []
    for step in steps:
        frames = step.get("frames")
        buttons = step.get("buttons")
        if not isinstance(frames, int) or not 1 <= frames <= 3600:
            raise ValueError("each frames value must be 1..3600")
        if isinstance(buttons, str):
            buttons = int(buttons, 0)
        if not isinstance(buttons, int) or not 0 <= buttons <= 0xFFFF:
            raise ValueError("buttons must be an active-low 16-bit pad word")
        route.append((frames, buttons))
    for check in scenario.get("assert_ram", []):
        _ram_address(check["addr"], len(bytes.fromhex(check["hex"])))
    for check in scenario.get("assert_ram_delta", []):
        width = check["bytes"]
        if width not in (1, 2, 4) or not isinstance(check["delta"], int):
            raise ValueError("assert_ram_delta needs 1, 2 or 4 bytes and an integer delta")
        _ram_address(check["addr"], width)
    percent = scenario.get("cpu_percent", 125)
    # Mirrors C2_60_CPU_CAP in crash2_60fps.h. This said 150 until the cap was
    # raised to 200 (NOTES part 11), which left the shipping configuration -
    # 200% - impossible to express in a scenario.
    if not isinstance(percent, int) or not 100 <= percent <= 200:
        raise ValueError("cpu_percent must be 100..200")
    ticks = scenario.get("expected_game_frame_ticks")
    if ticks is not None and ticks not in (17, 34, 51):
        raise ValueError("expected_game_frame_ticks must be 17, 34 or 51 "
                         "- func_80016F04 emits nothing else below 53")
    speed = scenario.get("min_speed")
    if speed is not None and not 0.0 < float(speed) <= 1.0:
        raise ValueError("min_speed must be a fraction of real time, 0..1")
    share = scenario.get("min_one_field_pct")
    if share is not None and not 0 <= share <= 100:
        raise ValueError("min_one_field_pct must be 0..100")
    aku = scenario.get("cheat_aku")
    if aku is not None and aku not in (0, 1, 2):
        raise ValueError("cheat_aku must be 0 (off), 1 (keep masks) or 2 (no damage)")
    return route


def _ram_address(value: str | int, width: int) -> int:
    address = int(value, 0) if isinstance(value, str) else value
    if not isinstance(address, int) or not 1 <= width <= 128:
        raise ValueError("RAM assertion needs 1..128 bytes")
    # Avoid MMIO, BIOS, or mirrored regions; a diagnostic read must be inert.
    if not 0x80000000 <= address <= 0x80200000 - width:
        raise ValueError("RAM assertion must stay within canonical 2 MiB RAM")
    return address


def _read_bytes(client: DebugClient, address: int, width: int) -> bytes:
    return bytes.fromhex(client.call("read_ram", addr=f"0x{address:08X}", len=width)["hex"])


def run(client: DebugClient, scenario: dict, timeout: float) -> dict:
    route = validate_scenario(scenario)
    client.call("ping")
    # Refuse to measure a runtime older than the fields being asserted. A stale
    # build answers every command and quietly reports the behaviour of the code
    # it was built from - which has already cost one test run.
    probe = client.call("frame_rates")
    for field in ("game_frame_ticks", "native_60fps_gate_open",
                  "native_60fps_one_field_pct", "native_60fps_verdict"):
        if field not in probe:
            raise RuntimeError(
                f"the running game does not report {field!r}: it predates the "
                "60 FPS sustain guard. Rebuild with _build/build_clang.ps1 "
                "(close the game first - it holds its own .exe) and relaunch.")
    cheat_probe = client.call("crash2_cheats")
    for field in ("aku", "god_word", "suspended"):
        if field not in cheat_probe:
            raise RuntimeError(
                f"the running game does not report {field!r}: its assists "
                "predate the Aku levels. Rebuild with _build/build_clang.ps1 "
                "(close the game first - it holds its own .exe) and relaunch.")
    # Always set the mode explicitly, both ways. A scenario that expects stock
    # 30 Hz has to say so to the runtime, or it inherits whatever the previous
    # run left behind and measures the wrong thing.
    client.call("crash2_60fps", enabled=1 if scenario.get("native_60fps") else 0,
                cpu_percent=scenario.get("cpu_percent", 125))
    client.call("crash2_cheats",
                lives=1 if scenario.get("cheat_lives") else 0,
                aku=scenario.get("cheat_aku", 0))
    try:
        return _run_route(client, scenario, route, timeout)
    finally:
        client.call("crash2_cheats", lives=0, aku=0)
        client.call("crash2_60fps", enabled=0)


def _run_route(client: DebugClient, scenario: dict, route: list[tuple[int, int]],
               timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    # Do not call a route successful while the BIOS is still booting. The
    # frame-history pad can reflect injected input before the game loop starts.
    if scenario.get("require_game_loop", True):
        client.call("frame_rates", reset=1)
        while time.monotonic() < deadline:
            time.sleep(1)
            warmup = client.call("frame_rates")
            if (warmup.get("game_loop_verified") == 1
                    and warmup.get("game_loop_steps", 0) >= 20
                    and warmup.get("distinct_frames", 0) >= 20):
                break
        else:
            raise TimeoutError("game loop did not reach steady gameplay cadence")
    delta_checks = [(_ram_address(check["addr"], check["bytes"]), check)
                    for check in scenario.get("assert_ram_delta", [])]
    before = [_read_bytes(client, address, check["bytes"])
              for address, check in delta_checks]
    client.call("input_route_clear")
    for frames, buttons in route:
        client.call("input_route_append", frames=frames, buttons=buttons)
    backoffs_before = client.call("frame_rates").get("native_60fps_backoffs", 0)
    client.call("frame_rates", reset=1)
    start = client.call("input_route_start")["start_frame"]
    deadline = time.monotonic() + timeout
    samples = []
    cursor = start
    for frames, buttons in route:
        if frames >= 5:
            samples.append((cursor + frames // 2, buttons))
        cursor += frames
    failures = []

    def check_reached(newest: int) -> None:
        while samples and samples[0][0] <= newest:
            frame, buttons = samples.pop(0)
            reply = client.call("frame_range", start=frame, end=frame)
            record = reply["frames"][0]
            observed = (int(record.get("pad", "0"), 16)
                        if record.get("available", True) else None)
            if observed != buttons:
                failures.append({"frame": frame, "expected_pad": f"0x{buttons:04X}",
                                 "observed_pad": None if observed is None
                                 else f"0x{observed:04X}"})

    try:
        while time.monotonic() < deadline:
            active = client.call("input_route_status")["active"]
            check_reached(client.call("history")["newest"])
            if not active:
                break
            time.sleep(0.05)
        else:
            raise TimeoutError("input route did not complete")
    finally:
        client.call("input_route_stop")

    total = sum(frames for frames, _ in route)
    # Sample while the route runs: the debug frame ring is finite and may no
    # longer hold the beginning of a longer gameplay path at completion.
    if samples:
        failures.append({"missing_pad_samples": [frame for frame, _ in samples]})
    rates = client.call("frame_rates")
    expected = scenario.get("expected_game_loop_hz")
    if expected and not expected[0] <= rates.get("game_loop_steps", -1) <= expected[1]:
        failures.append({"game_loop_hz": rates.get("game_loop_steps"),
                         "expected_range": expected})
    # The share of the window's frames the engine simulated as one field. This
    # is the honest reading of "is it at 60": game_frame_ticks below is a single
    # instantaneous sample, and in a scene alternating between one field and two
    # it is a coin toss.
    min_share = scenario.get("min_one_field_pct")
    if min_share is not None:
        share = rates.get("native_60fps_one_field_pct", 0)
        if share < min_share:
            failures.append({"one_field_pct": share, "min_one_field_pct": min_share,
                             "loop_hz": rates.get("game_loop_steps"),
                             "note": "frames alternated between one and two "
                                     "fields instead of holding 60"})
    expected_ticks = scenario.get("expected_game_frame_ticks")
    if expected_ticks is not None and rates.get("game_frame_ticks") != expected_ticks:
        # The value motion is multiplied by. 60 loops a second with this still
        # reading 34 would be double speed, not 60 FPS.
        failures.append({"game_frame_ticks": rates.get("game_frame_ticks"),
                         "expected": expected_ticks})
    min_speed = scenario.get("min_speed")
    if min_speed is not None and rates.get("speed", 0) < min_speed:
        failures.append({"speed": rates.get("speed"), "min_speed": min_speed})
    max_backoffs = scenario.get("max_backoffs")
    if max_backoffs is not None:
        backoffs = rates.get("native_60fps_backoffs", 0) - backoffs_before
        if backoffs > max_backoffs:
            failures.append({"sustain_backoffs": backoffs,
                             "max_backoffs": max_backoffs,
                             "note": "the runtime could not hold 60 here and "
                                     "fell back to the game's own 30 Hz lock"})
    for check in scenario.get("assert_ram", []):
        address = _ram_address(check["addr"], len(bytes.fromhex(check["hex"])))
        wanted = bytes.fromhex(check["hex"])
        observed = _read_bytes(client, address, len(wanted))
        if observed != wanted:
            failures.append({"addr": f"0x{address:08X}", "expected_hex": wanted.hex(),
                             "observed_hex": observed.hex()})
    for (address, check), old in zip(delta_checks, before):
        new = _read_bytes(client, address, check["bytes"])
        actual = int.from_bytes(new, "little") - int.from_bytes(old, "little")
        if actual != check["delta"]:
            failures.append({"addr": f"0x{address:08X}", "expected_delta": check["delta"],
                             "actual_delta": actual, "before_hex": old.hex(),
                             "after_hex": new.hex()})
    result = {"schema_version": 2, "scenario": scenario.get("name", "unnamed"),
              "start_frame": start, "route_frames": total,
              "end_frame": client.call("history")["newest"], "rates": rates,
              "sustain_backoffs": (rates.get("native_60fps_backoffs", 0)
                                   - backoffs_before),
              "failures": failures, "passed": not failures}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    scenario = json.loads(args.scenario.read_text(encoding="utf-8"))
    result = run(DebugClient(args.port), scenario, args.timeout)
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
