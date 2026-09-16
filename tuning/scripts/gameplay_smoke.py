"""Deterministic input-route smoke test for a *debugtools* Crash 2 process.

Run against an isolated game directory/save card, never a player's session:
  python tuning/scripts/gameplay_smoke.py --port 28714 --scenario tuning/scenarios/idle.json

The debug server consumes each route segment on a guest VBlank. This runner
checks the actual recorded pad words and frame cadence; it does not yet claim
to validate gameplay state, which needs stable player-field mappings.
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
    return route


def run(client: DebugClient, scenario: dict, timeout: float) -> dict:
    route = validate_scenario(scenario)
    client.call("ping")
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
    client.call("input_route_clear")
    for frames, buttons in route:
        client.call("input_route_append", frames=frames, buttons=buttons)
    client.call("frame_rates", reset=1)
    start = client.call("input_route_start")["start_frame"]
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if not client.call("input_route_status")["active"]:
                break
            time.sleep(0.05)
        else:
            raise TimeoutError("input route did not complete")
    finally:
        client.call("input_route_stop")

    total = sum(frames for frames, _ in route)
    # The frame ring and pad sampler are both VBlank based. Check a short
    # interior span of each segment to tolerate the boundary capture order.
    failures = []
    cursor = start
    for frames, buttons in route:
        if frames >= 5:
            sample = cursor + frames // 2
            reply = client.call("frame_range", start=sample, end=sample)
            record = reply["frames"][0]
            observed = int(record.get("pad", "0"), 16) if record.get("available", True) else None
            if observed != buttons:
                failures.append({"frame": sample, "expected_pad": f"0x{buttons:04X}",
                                 "observed_pad": None if observed is None else f"0x{observed:04X}"})
        cursor += frames
    rates = client.call("frame_rates")
    expected = scenario.get("expected_game_loop_hz")
    if expected and not expected[0] <= rates.get("game_loop_steps", -1) <= expected[1]:
        failures.append({"game_loop_hz": rates.get("game_loop_steps"),
                         "expected_range": expected})
    result = {"schema_version": 1, "scenario": scenario.get("name", "unnamed"),
              "start_frame": start, "route_frames": total,
              "end_frame": client.call("history")["newest"], "rates": rates,
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
