"""light_scan.py - find the game's own variable for the firefly light.

light_diag.py showed the game submits brighter vertex colours for ~2.4 s after
a firefly catch, then snaps back - while the GTE depth-cue registers never
move. So the light is driven by game state we have not located. This finds it.

It diffs the full 2 MB of PSX RAM across five moments you mark with Enter:

    D1  dark, standing still
    D2  dark, a moment later            -> what changes ANYWAY (noise)
    L1  lit, right after catching       -> what changed when the light came on
    L2  lit, ~1 s later                 -> what keeps changing while lit (timers)
    D3  dark again                      -> what reverted

    TIMER : stable across D1/D2, changing L1->L2, back toward the dark value in D3
    FLAG  : one value in D1/D2/D3, a different value in L1/L2

Then watch the candidates through a catch at high rate:

    python _build/light_scan.py watch 0x800A1234 0x800A1238 ...

Usage: game running from the launcher with Advanced -> Debug server port 4370.
"""

from __future__ import annotations

import json
import socket
import struct
import sys
import time
from array import array

PORT = 4370
RAM_BASE = 0x80000000
RAM_LEN = 0x200000


def ask(cmd: str, **kw) -> dict:
    sock = socket.create_connection(("127.0.0.1", PORT), timeout=60)
    sock.settimeout(60)
    msg = {"id": 1, "cmd": cmd}
    msg.update(kw)
    sock.sendall(json.dumps(msg).encode() + b"\n")
    buf = b""
    try:
        while b"\n" not in buf:
            chunk = sock.recv(1 << 22)
            if not chunk:
                break
            buf += chunk
    finally:
        sock.close()
    if not buf:
        raise RuntimeError("empty response to " + cmd)
    return json.loads(buf.split(b"\n", 1)[0].decode())


def snap(label: str) -> tuple[array, int]:
    """Whole RAM as little-endian u32 words, plus the frame it was taken at."""
    frame = ask("gpu_ring_stats")["newest_frame"]
    r = ask("read_ram", addr="0x%08X" % RAM_BASE, len=RAM_LEN)
    raw = bytes.fromhex(r["hex"])
    words = array("I")
    words.frombytes(raw)
    if sys.byteorder != "little":
        words.byteswap()
    print(f"  {label}: frame {frame}, {len(words)} words")
    return words, frame


def s32(v: int) -> int:
    return v - (1 << 32) if v & 0x80000000 else v


def scan() -> int:
    def mark(prompt: str) -> None:
        print(prompt, end="", flush=True)
        input()

    mark("Stand still in the DARK (no firefly). Enter for D1... ")
    d1, f1 = snap("D1")
    mark("Stay still. Enter for D2... ")
    d2, f2 = snap("D2")
    mark("Catch the firefly; the moment it is caught press Enter for L1... ")
    l1, f3 = snap("L1")
    mark("Wait about a second while lit. Enter for L2... ")
    l2, f4 = snap("L2")
    mark("Wait until it is DARK again. Enter for D3... ")
    d3, f5 = snap("D3")

    n = len(d1)
    timers, flags = [], []
    for i in range(n):
        a, b, c, d, e = d1[i], d2[i], l1[i], l2[i], d3[i]
        if a != b:
            continue                      # noise: changes even in the dark
        if c != d and c != a:
            # moving while lit, not moving while dark, and D3 heads back
            if e == a or abs(s32(e) - s32(a)) < abs(s32(d) - s32(a)):
                timers.append((i, a, c, d, e))
        elif c == d and c != a and e == a:
            flags.append((i, a, c))

    lit_frames = max(1, f4 - f3)
    print(f"\n=== TIMER-like words ({len(timers)}) : dark value -> L1 -> L2 -> D3   [rate/frame over {lit_frames} frames]")
    # Plausible game timers are small integers moving a few units per frame.
    # Sorting by raw delta (the first version) put display-list scratch moving
    # by millions per frame on top and buried the real candidates.
    def plaus(t):
        _, a, c, d, e = t
        rate = abs(s32(d) - s32(c)) / lit_frames
        small = max(abs(s32(a)), abs(s32(c)), abs(s32(d)), abs(s32(e))) < 1_000_000
        good_rate = 0.2 <= rate <= 32.0
        return (0 if (small and good_rate) else 1, rate)
    timers.sort(key=plaus)
    for i, a, c, d, e in timers[:40]:
        rate = (s32(d) - s32(c)) / lit_frames
        print(f"  0x{RAM_BASE + i*4:08X}  {s32(a):>10d} -> {s32(c):>10d} -> {s32(d):>10d} -> {s32(e):>10d}   "
              f"{rate:+8.2f}/frame")
    if len(timers) > 40:
        print(f"  ... {len(timers) - 40} more")

    print(f"\n=== FLAG-like words ({len(flags)}) : dark value -> lit value")
    for i, a, c in flags[:40]:
        print(f"  0x{RAM_BASE + i*4:08X}  0x{a:08X} -> 0x{c:08X}   ({s32(a)} -> {s32(c)})")
    if len(flags) > 40:
        print(f"  ... {len(flags) - 40} more")

    print("\nA countdown timer shows as a steadily negative rate/frame whose L1 value is the")
    print("starting duration. A rate near -1.00 with a small start (~150) means the game")
    print("INITIALISES it short; a rate well below -1.00 means it is DECREMENTED too often.")
    print("Then:  python _build/light_scan.py watch <addr> [<addr>...]   through a catch.")
    return 0


def frame_brightness(frame: int) -> float:
    """Mean vertex brightness of one rendered frame (same measure as light_diag)."""
    dump = ask("gpu_frame_dump", frame=frame, count=4096)
    tot = n = 0
    for e in dump.get("entries", []):
        op = int(e["op"], 16)
        if not (0x20 <= op <= 0x3F):
            continue
        w = [int(x, 16) for x in e.get("w", [])]
        shaded, quad, tex = op & 0x10, op & 0x08, op & 0x04
        nv = 4 if quad else 3
        if not shaded:
            cols = [w[0] & 0xFFFFFF] * nv if w else []
        else:
            st = 3 if tex else 2
            cols = [w[v * st] & 0xFFFFFF for v in range(nv) if v * st < len(w)]
        for c in cols:
            tot += ((c & 0xFF) + ((c >> 8) & 0xFF) + ((c >> 16) & 0xFF)) / 3.0
            n += 1
    return tot / n if n else 0.0


def watch(addrs: list[int], seconds: float = 30.0) -> int:
    """Follow specific words through a catch, next to what the frame looks like."""
    print(f"watching {len(addrs)} address(es) for {seconds:.0f}s - catch the firefly now.")
    print("   t     frame    bright  " + "  ".join(f"{a:>10X}" for a in addrs))
    t0 = time.time()
    last = None
    while time.time() - t0 < seconds:
        frame = ask("gpu_ring_stats")["newest_frame"]
        br = frame_brightness(frame)
        vals = []
        for a in addrs:
            r = ask("mem_words", addr="0x%08X" % a, count=1)
            vals.append(s32(int(r["words"][0], 16)))
        mark = "" if last is None or vals == last else "  <-- changed"
        print(f"  {time.time()-t0:5.1f}s  f{frame:<7d} {br:6.1f}  "
              + "  ".join(f"{v:>10d}" for v in vals) + mark)
        last = vals
        time.sleep(0.1)
    return 0


def region(base: int, nwords: int, seconds: float = 30.0) -> int:
    """Poll a block of RAM through a catch and report every word that moves.

    Globals for one feature sit together, so the neighbourhood of a known flag
    (0x80060300) is where its timer will be. Words that change more than
    `noisy` times are scratch and are dropped from the report."""
    noisy = 60
    print(f"watching 0x{base:08X}..0x{base + nwords*4:08X} ({nwords} words) for "
          f"{seconds:.0f}s - catch the firefly now.")
    hist: dict[int, list[tuple[float, int, int]]] = {}
    prev: dict[int, int] = {}
    t0 = time.time()
    samples = 0
    while time.time() - t0 < seconds:
        frame = ask("gpu_ring_stats")["newest_frame"]
        t = time.time() - t0
        for off in range(0, nwords, 256):
            cnt = min(256, nwords - off)
            r = ask("mem_words", addr="0x%08X" % (base + off * 4), count=cnt)
            for i, hx in enumerate(r["words"]):
                a = base + (off + i) * 4
                v = s32(int(hx, 16))
                if a not in prev:
                    prev[a] = v
                    hist[a] = [(t, frame, v)]
                elif v != prev[a]:
                    prev[a] = v
                    hist[a].append((t, frame, v))
        samples += 1
        time.sleep(0.1)

    changed = {a: h for a, h in hist.items() if len(h) > 1}
    quiet = {a: h for a, h in changed.items() if len(h) <= noisy}
    print(f"\n{samples} samples. {len(changed)} words changed; {len(changed) - len(quiet)} "
          f"dropped as scratch (> {noisy} changes).\n")
    for a in sorted(quiet):
        h = quiet[a]
        seq = "  ".join(f"{t:4.1f}s:{v}" for t, _, v in h[:14])
        more = f"  ... +{len(h) - 14}" if len(h) > 14 else ""
        print(f"  0x{a:08X}  ({len(h) - 1:2d} changes)  {seq}{more}")
    print("\nA countdown shows as a value that appears at the catch and steps down at a")
    print("steady rate; its first value is the duration the game intended. A flag shows")
    print("as two values. Note the wall time each reverts to its dark value.")
    return 0


SCRATCH = 0x1F800000          # PS1 scratchpad: the world renderer's context lives here
FOG_SHIFT_OFF, FOG_START_OFF, PARAMS_PTR_OFF = 8, 16, 96   # scratch[8], scratch[16], scratch[96]
P_FARCOL, P_SHIFT, P_START = 156, 160, 164                  # in the params struct


def word(addr: int) -> int:
    return int(ask("mem_words", addr="0x%08X" % addr, count=1)["words"][0], 16)


def fog(seconds: float = 30.0) -> int:
    """The fog parameters ARE the Night Fight light. Read them where the
    renderer reads them (scratchpad), find the params struct they are copied
    from, watch both through a catch, and name whoever writes the struct."""
    import time
    sh, st = s32(word(SCRATCH + FOG_SHIFT_OFF)), s32(word(SCRATCH + FOG_START_OFF))
    params = word(SCRATCH + PARAMS_PTR_OFF)
    print(f"scratchpad: fog shift={sh}  fog start={st}  params struct @ 0x{params:08X}")
    if not (0x80000000 <= params < 0x80200000):
        print("params pointer is not a RAM address - the renderer has not run this frame, or")
        print("the scratchpad read did not reach 0x1F800000. Re-run during gameplay.")
        return 1
    fc, psh, pst = word(params + P_FARCOL), s32(word(params + P_SHIFT)), s32(word(params + P_START))
    print(f"params:     far colour=0x{fc:06X}  fog shift={psh}  fog start={pst}")
    print(f"  -> darkness begins at depth {pst}, reaches black {(0x1000 >> psh) if 0 <= psh < 12 else '?'} units later\n")

    # Catch every write to the struct's fog fields with the writer's PC.
    try: ask("wtrace_disarm_all")
    except Exception: pass  # noqa: BLE001
    ask("wtrace_clear")
    ask("wtrace_add", lo="0x%08X" % ((params + P_FARCOL) & 0x1FFFFFFF), hi="0x%08X" % ((params + P_START + 4) & 0x1FFFFFFF))

    print(f"watching {seconds:.0f}s - catch the firefly now.")
    print("   t     frame   bright   scratch shift/start   params shift/start   far colour")
    t0 = time.time(); last = None
    while time.time() - t0 < seconds:
        frame = ask("gpu_ring_stats")["newest_frame"]
        br = frame_brightness(frame)
        row = (s32(word(SCRATCH + FOG_SHIFT_OFF)), s32(word(SCRATCH + FOG_START_OFF)),
               s32(word(params + P_SHIFT)), s32(word(params + P_START)), word(params + P_FARCOL))
        mark = "" if last is None or row == last else "  <-- changed"
        print(f"  {time.time()-t0:5.1f}s  f{frame:<7d} {br:6.1f}   {row[0]:>5d} / {row[1]:<7d}    {row[2]:>5d} / {row[3]:<7d}   0x{row[4]:06X}{mark}")
        last = row
        time.sleep(0.2)

    d = ask("wtrace_dump", addr_lo="0x%08X" % ((params + P_FARCOL) & 0x1FFFFFFF),
            addr_hi="0x%08X" % ((params + P_START + 4) & 0x1FFFFFFF), count=4096, newest=0)
    ents = d.get("entries", [])
    print(f"\n{len(ents)} writes to params+156..+167 during the window")
    seen = {}
    for e in ents:
        key = (e["pc"], e["func"], int(e["addr"], 16) & 0xFF)
        seen.setdefault(key, []).append((e["frame"], s32(int(e["new"], 16))))
    for (pc, func, off), ws in sorted(seen.items(), key=lambda kv: -len(kv[1])):
        field = {P_FARCOL & 0xFF: "far colour", P_SHIFT & 0xFF: "fog shift", P_START & 0xFF: "fog start"}.get(off & 0xFF, "?")
        vals = [v for _, v in ws]
        print(f"  {field:10}  store pc {pc}  func {func}  x{len(ws)}   values f{ws[0][0]}:{vals[0]} .. f{ws[-1][0]}:{vals[-1]}"
              f"   distinct {sorted(set(vals))[:8]}")
    print("\nIf 'fog start' never changes at the catch, the firefly logic is not reaching it.")
    print("If it changes but by little, compare the values against what the light SHOULD do.")
    print("The store pc names the writer: python _build/mipsdis.py func <func>")
    return 0


def main() -> int:
    try:
        ask("gpu_ring_stats")
    except Exception as e:  # noqa: BLE001
        print(f"cannot reach debug server on {PORT}: {e}")
        print("Launcher -> Advanced -> Debug server port = 4370, then Play.")
        return 1
    if len(sys.argv) > 1 and sys.argv[1] == "watch":
        addrs = [int(a, 16) for a in sys.argv[2:]]
        if not addrs:
            print("watch needs at least one address, e.g. watch 0x80060300")
            return 1
        return watch(addrs)
    if len(sys.argv) > 1 and sys.argv[1] == "fog":
        return fog(float(sys.argv[2]) if len(sys.argv) > 2 else 30.0)
    if len(sys.argv) > 1 and sys.argv[1] == "region":
        base = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x80060000
        nwords = int(sys.argv[3]) if len(sys.argv) > 3 else 1024
        secs = float(sys.argv[4]) if len(sys.argv) > 4 else 30.0
        return region(base, nwords, secs)
    return scan()


if __name__ == "__main__":
    sys.exit(main())
