"""Find guest RAM counters that run at 30 Hz stock and ~60 Hz in a CPU probe.

Diagnostic only. Use a stationary, isolated debugtools game; this temporarily
patches the game frame gate and CPU clock, then restores both in finally.
Candidate addresses require write tracing before they are named as timers.
"""

from __future__ import annotations

import argparse
import json
import time

from gameplay_smoke import DebugClient


DEFAULT_BASE = 0x80060000
DEFAULT_LENGTH = 0x10000


def sample(client: DebugClient, seconds: float, base: int, length: int) -> tuple[bytes, bytes, float]:
    first = bytes.fromhex(client.call("read_ram", addr=f"0x{base:08X}", len=length)["hex"])
    start = time.monotonic()
    time.sleep(seconds)
    second = bytes.fromhex(client.call("read_ram", addr=f"0x{base:08X}", len=length)["hex"])
    return first, second, time.monotonic() - start


def scan(stock: tuple[bytes, bytes, float], fast: tuple[bytes, bytes, float],
         base: int, length: int) -> list[dict]:
    before_s, after_s, elapsed_s = stock
    before_f, after_f, elapsed_f = fast
    if any(len(data) != length for data in (before_s, after_s, before_f, after_f)):
        raise ValueError("unexpected RAM length")
    matches = []
    for width in (2, 4):
        mask = (1 << (8 * width)) - 1
        for offset in range(0, length, width):
            old_s = int.from_bytes(before_s[offset:offset + width], "little")
            new_s = int.from_bytes(after_s[offset:offset + width], "little")
            old_f = int.from_bytes(before_f[offset:offset + width], "little")
            new_f = int.from_bytes(after_f[offset:offset + width], "little")
            rate_s = ((new_s - old_s) & mask) / elapsed_s
            rate_f = ((new_f - old_f) & mask) / elapsed_f
            if 25 <= rate_s <= 35 and 48 <= rate_f <= 72:
                matches.append({"addr": f"0x{base + offset:08X}", "bytes": width,
                                "stock_per_s": round(rate_s, 2),
                                "probe_per_s": round(rate_f, 2),
                                "stock_before": old_s, "probe_before": old_f})
    return matches


def run(client: DebugClient, seconds: float, percent: int,
        base: int = DEFAULT_BASE, length: int = DEFAULT_LENGTH) -> dict:
    if not 100 < percent <= 150:
        raise ValueError("percent must be 101..150")
    if (base % 4 or length % 4 or not 0 < length <= 0x100000
            or not 0x80000000 <= base <= 0x80200000 - length):
        raise ValueError("range must be word-aligned canonical RAM, at most 1 MiB")
    client.call("ping")
    client.call("crash2_no_wait_probe", enabled=0)
    client.call("crash2_frame_gate", enabled=0)
    client.call("crash2_cpu_clock_probe", percent=100)
    stock = sample(client, seconds, base, length)
    try:
        client.call("crash2_cpu_clock_probe", percent=percent)
        client.call("crash2_frame_gate", enabled=1)
        time.sleep(1)
        fast = sample(client, seconds, base, length)
    finally:
        try:
            client.call("crash2_cpu_clock_probe", percent=100)
        finally:
            client.call("crash2_frame_gate", enabled=0)
    candidates = scan(stock, fast, base, length)
    return {"schema_version": 1, "range": f"0x{base:08X}+0x{length:X}",
            "cpu_percent": percent, "stock_seconds": round(stock[2], 3),
            "probe_seconds": round(fast[2], 3), "candidate_count": len(candidates),
            "candidate_counters": candidates[:200]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated", action="store_true")
    parser.add_argument("--port", type=int, default=28714)
    parser.add_argument("--seconds", type=float, default=5)
    parser.add_argument("--cpu-percent", type=int, default=125)
    parser.add_argument("--base", type=lambda s: int(s, 0), default=DEFAULT_BASE)
    parser.add_argument("--length", type=lambda s: int(s, 0), default=DEFAULT_LENGTH)
    args = parser.parse_args()
    if not args.isolated:
        parser.error("--isolated is required; this tool temporarily patches guest timing")
    if not 2 <= args.seconds <= 20:
        parser.error("seconds must be 2..20")
    print(json.dumps(run(DebugClient(args.port), args.seconds, args.cpu_percent,
                         args.base, args.length), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
