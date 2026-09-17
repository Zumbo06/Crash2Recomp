"""Measure real Crash 2 game-loop/image cadence in one isolated debug scene.

Modes, all reversible:
  stock     the game's own 30 Hz gate, untouched
  native    the shipped 60 FPS mode (`crash2_60fps`): gate open, CPU headroom.
            This is the same runtime path the launcher setting drives, so what
            it measures is what a player gets.
  gate      gate open, CPU left at 100%. Isolates how much of `native` is the
            guest CPU budget rather than the gate.
  no_wait   both VSync waits removed. A ceiling measurement only: it is
            uncapped, can exceed 60 game updates a second and can overload the
            host. Never a playable mode.

Example:
  python tuning/scripts/fps_ablation.py --isolated --port 28714 --scene turtle_woods_crates
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from gameplay_smoke import DebugClient


NTSC_VBLANK_CYCLES = 564480
LOOP_PC = "0x80011848"
# Root counter 2 at target 0x1000 off the system clock / 8: 4233600 / 4096.
GUEST_TICK_HZ = 1033.59
# func_80016F04 snaps each measured frame time to a whole number of NTSC
# fields. That value is what motion is multiplied by, so it is the engine's own
# answer to "what rate am I simulating at": 17 for 60 Hz, 34 for 30, 51 for 20.
FIELD_TICKS_60 = 17

MODES = ("stock", "native", "gate", "no_wait")


def cycle_stats(entries: list[dict]) -> dict:
    ticks = [int(row["cycles"]) for row in entries]
    deltas = [b - a for a, b in zip(ticks, ticks[1:]) if b > a]
    if not deltas:
        return {"samples": 0, "median": None, "p95": None,
                "p95_vblank_budget_ratio": None}
    ordered = sorted(deltas)
    p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
    return {"samples": len(deltas), "median": round(statistics.median(deltas)),
            "p95": p95,
            "p95_vblank_budget_ratio": round(p95 / NTSC_VBLANK_CYCLES, 3)}


def stable_60(rates: dict) -> bool:
    """Sustained 60 Hz at the host AND in the engine's own frame time.

    The last two terms are what separate this from a cadence count. The engine
    multiplies motion by game_frame_ticks, so 60 loops a second with that still
    reading 34 would be the game running at double speed rather than at 60 FPS.
    And the tick rate it derives that from has to stay on the original root
    counter, or the frame time is being measured against the wrong second.
    """
    return (rates.get("game_loop_verified") == 1
            and 58 <= rates.get("vblank_raise", -1) <= 62
            and 58 <= rates.get("game_loop_steps", -1) <= 62
            and 58 <= rates.get("distinct_frames", -1) <= 62
            and 58 <= rates.get("host_swaps", -1) <= 62
            and rates.get("speed", 0) >= 0.98
            and rates.get("game_frame_ticks") == FIELD_TICKS_60
            and abs(rates.get("guest_ticks_per_s", 0) - GUEST_TICK_HZ) <= 25)


def overlay_usage(before: dict, after: dict) -> dict:
    native = max(0, after.get("dispatch_native", 0) - before.get("dispatch_native", 0))
    fallback = max(0, after.get("dispatch_interp_fallback", 0)
                   - before.get("dispatch_interp_fallback", 0))
    total = native + fallback
    return {"active": bool(after.get("active")), "native_dispatches": native,
            "interpreter_fallbacks": fallback,
            "native_fraction": round(native / total, 3) if total else None}


def restore(client: DebugClient) -> None:
    """Put every patched instruction and the CPU clock back."""
    client.call("crash2_60fps", enabled=0)
    client.call("crash2_no_wait_probe", enabled=0)
    client.call("crash2_cpu_clock_probe", percent=100)
    client.call("crash2_frame_gate", enabled=0)


def set_mode(client: DebugClient, mode: str, cpu_percent: int) -> None:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode}")
    restore(client)
    if mode == "native":
        client.call("crash2_60fps", enabled=1, cpu_percent=cpu_percent)
    elif mode == "gate":
        client.call("crash2_frame_gate", enabled=1)
    elif mode == "no_wait":
        client.call("crash2_no_wait_probe", enabled=1)


def measure(client: DebugClient, mode: str, settle: float, window: float,
            cpu_percent: int) -> dict:
    set_mode(client, mode, cpu_percent)
    time.sleep(settle)
    overlay_before = client.call("overlay_loader_status")
    client.call("frame_rates", reset=1)
    client.call("cyc_watch", pc=LOOP_PC, n=32)
    time.sleep(window)
    rates = client.call("frame_rates")
    watch = client.call("cyc_watch_dump")
    overlay_after = client.call("overlay_loader_status")
    if rates.get("game_loop_verified") != 1:
        raise RuntimeError("SCUS-94154 game-loop signature was not verified")
    return {"mode": mode, "rates": rates,
            "stable_60": stable_60(rates),
            "overlay_usage": overlay_usage(overlay_before, overlay_after),
            "loop_cycles": cycle_stats(watch.get("entries", []))}


def run(client: DebugClient, scene: str, settle: float, window: float,
        cpu_percent: int = 125,
        modes: tuple[str, ...] = ("stock", "native")) -> dict:
    client.call("ping")
    if not 100 <= cpu_percent <= 150:
        raise ValueError("CPU percent must be 100..150")
    results = []
    try:
        for mode in modes:
            results.append(measure(client, mode, settle, window, cpu_percent))
    finally:
        # Also runs when a measurement or a socket read failed part way.
        # Leaving a patched instruction or a raised clock behind in someone's
        # session is the one outcome this tool must never have.
        restore(client)
    return {"schema_version": 2, "scene": scene, "cpu_percent": cpu_percent,
            "vblank_cycle_budget": NTSC_VBLANK_CYCLES,
            "guest_tick_hz": GUEST_TICK_HZ,
            "measurements": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated", action="store_true",
                        help="confirm the port belongs to a disposable debug game")
    parser.add_argument("--port", type=int, default=28714)
    parser.add_argument("--scene", required=True)
    parser.add_argument("--settle", type=float, default=1.0)
    parser.add_argument("--window", type=float, default=5.0)
    parser.add_argument("--cpu-percent", type=int, default=125,
                        help="CPU-only clock for `native`; peripherals stay at 100%%")
    parser.add_argument("--modes", default="stock,native",
                        help="comma-separated subset of " + ",".join(MODES))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.isolated:
        parser.error("--isolated is required; this tool temporarily patches guest code")
    if not 0 <= args.settle <= 10 or not 1 <= args.window <= 30:
        parser.error("settle must be 0..10 seconds and window 1..30 seconds")
    modes = tuple(part.strip() for part in args.modes.split(",") if part.strip())
    if not modes or any(mode not in MODES for mode in modes):
        parser.error("--modes must name only: " + ",".join(MODES))
    result = run(DebugClient(args.port), args.scene, args.settle, args.window,
                 args.cpu_percent, modes)
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
