"""Does the 60 FPS mode change Crash 2's world speed? A/B, one run, no pausing.

Static reading of SCUS-94154 says it does not: func_80016F04 snaps each
measured frame time to whole NTSC fields and 0x8001697C stores it in the draw
buffer at +20, and the motion sites (0x8001D400, 0x8001D9D4, 0x8001DADC) all
compute (velocity * that) >> 10. At 60 Hz the field count is 17 instead of 34,
so each frame moves half as far and a second covers the same ground. A
measured run has since shown the engine reporting exactly that - 17 ticks at
59.9 loops/s. This closes the loop by measuring displacement itself.

Method: save a state, drive one fixed input route, snapshot RAM; reload the
same state, turn the mode on, drive the identical route, snapshot again. Route
segments are consumed one per guest VBlank and VBlank stays at ~59.94 Hz in
both modes, so both runs cover the same real time. Every RAM word that moved in
both runs gives a ratio, and the median over the largest movers is the answer:
~1.0 means world speed is unchanged, ~2.0 means the mode doubled it. No address
map is needed and none is assumed.

**Each run is measured against its own baseline, deliberately.** An earlier
version of this script demanded that reloading the state reproduce the first
run's starting RAM exactly, and that can never hold: a savestate request is
only applied at the next safe boundary and the emulator keeps running until
then, so the two "same" worlds are always a few frames apart. This framework
has no pause (it was removed in favour of ring buffers), so the fix is to stop
needing a shared start - a few frames of drift against a four-second route is
noise, and the ratio is taken over many words.

Run it against an isolated debugtools game, standing somewhere Crash can hold a
direction for the whole route without dying or hitting a wall. The recipe lives
in tuning/60FPS-FINDINGS.md.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from gameplay_smoke import DebugClient
from fps_ablation import FIELD_TICKS_60, GUEST_TICK_HZ, restore


# Active-low pad: hold Up. 0xFFEF clears bit 4 (D-pad up) and nothing else.
DEFAULT_HOLD = 0xFFEF
# The GOOL object pool and the engine globals both live in this window. It is
# clear of the 0x800A0000+ level overlay, which streams and would contribute
# movement that has nothing to do with the player.
DEFAULT_BASE = 0x80060000
DEFAULT_LENGTH = 0x40000


def _words(blob: bytes) -> list[int]:
    return [int.from_bytes(blob[i:i + 4], "little", signed=True)
            for i in range(0, len(blob) - 3, 4)]


def compare(slow: tuple[bytes, bytes], fast: tuple[bytes, bytes],
            base_addr: int, min_delta: int = 16, max_delta: int = 1 << 24,
            top: int = 64) -> dict:
    """Ratio of how far each RAM word travelled at 60 Hz versus at 30 Hz.

    Each argument is that run's own (before, after) pair. Only words that moved
    in BOTH runs, in the SAME direction, by a plausible amount count: a word
    that moved one way and not the other is a branch the two runs took
    differently rather than a speed difference, and a huge delta is a pointer
    or a counter reset rather than a coordinate.
    """
    lengths = {len(b) for pair in (slow, fast) for b in pair}
    if len(lengths) != 1:
        raise ValueError("snapshots differ in length")
    s0, s1 = _words(slow[0]), _words(slow[1])
    f0, f1 = _words(fast[0]), _words(fast[1])
    rows = []
    for index in range(len(s0)):
        ds, df = s1[index] - s0[index], f1[index] - f0[index]
        if abs(ds) < min_delta or abs(df) < min_delta:
            continue
        if (ds > 0) != (df > 0):
            continue
        if abs(ds) > max_delta or abs(df) > max_delta:
            continue
        rows.append({"addr": f"0x{base_addr + index * 4:08X}",
                     "delta_30hz": ds, "delta_60hz": df,
                     "ratio": round(df / ds, 3)})
    rows.sort(key=lambda row: -abs(row["delta_30hz"]))
    sample = rows[:top]
    ratios = sorted(row["ratio"] for row in sample)
    if len(ratios) < 8:
        return {"words_compared": len(rows), "verdict": "inconclusive",
                "reason": "fewer than 8 comparable moving words; hold a "
                          "direction somewhere Crash actually travels",
                "movers": sample}
    median = statistics.median(ratios)
    return {"words_compared": len(rows), "sample_size": len(sample),
            "speed_ratio_median": round(median, 3),
            "speed_ratio_p25": round(ratios[len(ratios) // 4], 3),
            "speed_ratio_p75": round(ratios[3 * len(ratios) // 4], 3),
            # 2.0 is the failure this whole exercise exists to rule out: the
            # loop running twice as often and the world moving twice as fast.
            "verdict": ("world speed unchanged" if 0.85 <= median <= 1.15
                        else "world speed roughly doubled" if 1.7 <= median <= 2.3
                        else "off-target, investigate"),
            "movers": sample[:16]}


def _read(client: DebugClient, base: int, length: int) -> bytes:
    return bytes.fromhex(client.call("read_ram", addr=f"0x{base:08X}",
                                     len=length)["hex"])


def _savestate(client: DebugClient, op: str, slot: int, timeout: float = 10.0) -> None:
    """Request a save/load and wait for the completion the runtime reports.

    The reply to `savestate` only means the request was staged; the work
    happens at the next safe boundary, and `savestate_status` reports the
    generation after it has actually been applied or rejected.
    """
    before = client.call("savestate_status")["generation"]
    client.call("savestate", op=op, slot=slot)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.05)
        status = client.call("savestate_status")
        if status["generation"] != before and not status.get("pending"):
            if not status.get("last_ok"):
                raise RuntimeError(f"savestate {op} slot {slot} failed")
            return
    raise TimeoutError(f"savestate {op} slot {slot} did not complete")


def _drive(client: DebugClient, frames: int, buttons: int,
           timeout: float) -> dict:
    client.call("input_route_clear")
    client.call("input_route_append", frames=frames, buttons=buttons)
    client.call("frame_rates", reset=1)
    client.call("input_route_start")
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
    return client.call("frame_rates")


def _leg(client: DebugClient, native: bool, frames: int, buttons: int,
         base: int, length: int, cpu_percent: int, settle: float,
         timeout: float) -> tuple[tuple[bytes, bytes], dict]:
    """One half of the A/B, with the mode set before the baseline is read.

    Both legs pay the same settle between the switch and the baseline, so the
    two runs start the same distance past their savestate.
    """
    client.call("crash2_60fps", enabled=1 if native else 0,
                cpu_percent=cpu_percent)
    time.sleep(settle)
    before = _read(client, base, length)
    rates = _drive(client, frames, buttons, timeout)
    return (before, _read(client, base, length)), rates


def run(client: DebugClient, slot: int, frames: int, buttons: int,
        base: int, length: int, cpu_percent: int, settle: float,
        timeout: float) -> dict:
    client.call("ping")
    restore(client)
    _savestate(client, "save", slot)

    try:
        stock_pair, stock_rates = _leg(client, False, frames, buttons, base,
                                       length, cpu_percent, settle, timeout)
        _savestate(client, "load", slot)
        native_pair, native_rates = _leg(client, True, frames, buttons, base,
                                         length, cpu_percent, settle, timeout)
    finally:
        restore(client)

    result = {
        "schema_version": 2,
        "route": {"frames": frames, "buttons": f"0x{buttons:04X}",
                  "seconds": round(frames / 59.94, 2)},
        "window": f"0x{base:08X}+0x{length:X}",
        "cpu_percent": cpu_percent,
        "stock": stock_rates,
        "native": native_rates,
        "displacement": compare(stock_pair, native_pair, base),
    }
    # The engine-side reading of the same question, from the value motion is
    # multiplied by. It should say 34 at stock and 17 while the gate is open.
    result["engine_frame_ticks"] = {
        "stock": stock_rates.get("game_frame_ticks"),
        "native": native_rates.get("game_frame_ticks"),
        "native_one_field_pct": native_rates.get("native_60fps_one_field_pct"),
    }
    result["guest_time_base_held"] = all(
        abs(rates.get("guest_ticks_per_s", 0) - GUEST_TICK_HZ) <= 25
        for rates in (stock_rates, native_rates))
    # A leg the sustain guard pulled back to 30 measures stock twice, so say so
    # rather than reporting a meaningless 1.0.
    result["native_leg_held_sixty"] = (
        native_rates.get("game_frame_ticks") == FIELD_TICKS_60
        and native_rates.get("native_60fps_gate_open") == 1)
    result["passed"] = bool(
        result["displacement"].get("verdict") == "world speed unchanged"
        and result["native_leg_held_sixty"]
        and result["guest_time_base_held"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated", action="store_true",
                        help="confirm the port belongs to a disposable debug game")
    parser.add_argument("--port", type=int, default=28714)
    parser.add_argument("--slot", type=int, default=9,
                        help="savestate slot to borrow; it is overwritten")
    parser.add_argument("--frames", type=int, default=240,
                        help="route length in guest VBlanks (240 = 4 s)")
    parser.add_argument("--hold", type=lambda s: int(s, 0), default=DEFAULT_HOLD,
                        help="active-low pad word held for the whole route")
    parser.add_argument("--base", type=lambda s: int(s, 0), default=DEFAULT_BASE)
    parser.add_argument("--length", type=lambda s: int(s, 0), default=DEFAULT_LENGTH)
    parser.add_argument("--cpu-percent", type=int, default=125)
    parser.add_argument("--settle", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.isolated:
        parser.error("--isolated is required; this tool overwrites a savestate "
                     "slot and temporarily patches guest code")
    if not 0 <= args.slot <= 15:
        parser.error("slot must be 0..15")
    if not 60 <= args.frames <= 3600:
        parser.error("frames must be 60..3600 guest VBlanks")
    if not 0 <= args.hold <= 0xFFFF:
        parser.error("hold must be an active-low 16-bit pad word")
    if (args.base % 4 or args.length % 4 or not 0 < args.length <= 0x100000
            or not 0x80000000 <= args.base <= 0x80200000 - args.length):
        parser.error("window must be word-aligned canonical RAM, at most 1 MiB")
    if not 100 <= args.cpu_percent <= 150:
        parser.error("cpu-percent must be 100..150")
    result = run(DebugClient(args.port), args.slot, args.frames, args.hold,
                 args.base, args.length, args.cpu_percent, args.settle,
                 args.timeout)
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
