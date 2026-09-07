"""light_writer.py - WHO writes the firefly light intensity, and what do they write?

light_scan.py found the light intensity at 0x80060928 (12.12 fixed point,
4096 = full): it jumps to ~4083 at the catch and collapses to ~58 about two
seconds later, while on hardware the light lasts 10-15 s. Watching the value
tells us WHAT happens; this tells us WHO does it.

It arms the debug server's write trace on the light's neighbourhood, lets you
catch the firefly, then pulls every store to the intensity word tagged with the
exact PC of the SW/SH/SB, the recompiled function it belongs to, the caller,
and the frame. Each writer PC is resolved against the game's function map and
located in the recompiled C, so the formula can be read rather than guessed.

Usage (game running from the launcher with Advanced -> Debug server port 4370,
already in Night Fight, standing in the dark):

    python _build/light_writer.py            # then catch the firefly, wait for
                                             # the light to die, press Enter
    python _build/light_writer.py 0x800609XX # a different word in the block
"""

from __future__ import annotations

import glob
import json
import os
import re
import socket
import sys
from collections import defaultdict

PORT = 4370
LIGHT = 0x80060928
BLOCK_LO, BLOCK_HI = 0x80060900, 0x80060960
PROJ = r"C:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\Crash2Recomp"
RANGES = os.path.join(PROJ, "generated", "SCUS_941.54_full.ranges")
GEN_GLOB = os.path.join(PROJ, "generated", "*.c")
OVL_GLOB = os.path.join(PROJ, "build-clang", "cache", "**", "*_patched.c")


def ask(cmd: str, **kw) -> dict:
    sock = socket.create_connection(("127.0.0.1", PORT), timeout=30)
    sock.settimeout(30)
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


def phys(a: int) -> int:
    return a & 0x1FFFFFFF


def s32(v: int) -> int:
    return v - (1 << 32) if v & 0x80000000 else v


# --- PC -> function ----------------------------------------------------------
def load_functions() -> list[int]:
    """Function entry addresses from the recompiler's range manifest, sorted."""
    entries = []
    try:
        with open(RANGES, encoding="utf-8") as f:
            for line in f:
                if line.startswith("F "):
                    entries.append(int(line.split()[1], 16))
    except OSError:
        pass
    return sorted(entries)


def func_for_pc(pc: int, funcs: list[int]) -> int | None:
    """Largest function entry <= pc, or None if pc is outside the main exe."""
    import bisect
    pc &= 0xFFFFFFFF
    i = bisect.bisect_right(funcs, pc) - 1
    return funcs[i] if i >= 0 else None


def locate_func(entry: int) -> str:
    """file:line of `void func_XXXXXXXX(` in the generated main-exe C, or a
    note that it lives in overlay code (level code streamed from disc)."""
    name = f"func_{entry:08X}"
    # [ \t]* not \s*: with re.M, \s* would happily start the match at the
    # newline ending the PREVIOUS line, and the reported line number then comes
    # out one short. The file:line here is meant to be clicked on.
    pat = re.compile(r"^[ \t]*(?:static\s+)?void\s+" + name + r"\s*\(", re.M)
    for path in glob.glob(GEN_GLOB):
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        m = pat.search(text)
        if m:
            line = text.count("\n", 0, m.start()) + 1
            return f"{os.path.relpath(path, PROJ)}:{line}  {name}"
    for path in glob.glob(OVL_GLOB, recursive=True):
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        m = pat.search(text)
        if m:
            line = text.count("\n", 0, m.start()) + 1
            return f"{os.path.relpath(path, PROJ)}:{line}  {name}  (OVERLAY)"
    return f"{name}  (not in generated C - overlay not cached, or interpreter-only)"


# --- capture -----------------------------------------------------------------
def main() -> int:
    target = int(sys.argv[1], 16) if len(sys.argv) > 1 else LIGHT
    try:
        ask("wtrace_stats")
    except Exception as e:  # noqa: BLE001
        print(f"cannot reach debug server on {PORT}: {e}")
        print("Launcher -> Advanced -> Debug server port = 4370, then Play.")
        return 1

    # Fresh trace on the light's block.
    try:
        ask("wtrace_disarm_all")
    except Exception:  # noqa: BLE001
        pass
    ask("wtrace_clear")
    r = ask("wtrace_add", lo="0x%08X" % phys(BLOCK_LO), hi="0x%08X" % phys(BLOCK_HI))
    print(f"write trace armed on 0x{BLOCK_LO:08X}..0x{BLOCK_HI:08X} (slot {r.get('slot')})")
    print(f"target word: 0x{target:08X}")
    print("\nCatch the firefly, wait until the light has DIED again, then press Enter... ",
          end="", flush=True)
    input()

    d = ask("wtrace_dump", addr_lo="0x%08X" % phys(target), addr_hi="0x%08X" % (phys(target) + 4),
            count=8192, newest=0)
    entries = d.get("entries", [])
    print(f"\n{len(entries)} writes to 0x{target:08X} captured "
          f"(trace total {d.get('total')}, available {d.get('available')})")
    if not entries:
        print("No writes seen. Either the light never engaged in that window, or the word")
        print("is written by DMA/interpreter path the trace does not tag. Try again, and")
        print("confirm the intensity moved with:  python _build/light_scan.py watch 0x80060928")
        return 0

    funcs = load_functions()
    by_writer: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in entries:
        by_writer[(e["func"], e["pc"])].append(e)

    print(f"\n=== {len(by_writer)} distinct writer(s) ===")
    for (func, pc), ws in sorted(by_writer.items(), key=lambda kv: -len(kv[1])):
        fpc = int(pc, 16)
        entry = func_for_pc(fpc, funcs)
        where = locate_func(entry) if entry is not None else "(PC outside the main-exe map)"
        vals = [s32(int(w["new"], 16)) for w in ws]
        frames = [w["frame"] for w in ws]
        print(f"\n  store pc {pc}   func {func}   ra {ws[0]['ra']}   {len(ws)} writes")
        print(f"    {where}")
        print(f"    frames {frames[0]}..{frames[-1]}   width {ws[0]['w']}   dma_ch {ws[0]['dma_ch']}")
        # Compact value trajectory: show runs.
        seq, prev = [], None
        for v, f in zip(vals, frames):
            if v != prev:
                seq.append(f"f{f}:{v}")
                prev = v
        shown = "  ".join(seq[:24]) + (f"  ... +{len(seq) - 24}" if len(seq) > 24 else "")
        print(f"    values: {shown}")
        # Registers at the first and the collapse write, for reading the C.
        first = ws[0]
        drop = next((w for w in ws if s32(int(w["new"], 16)) < 512
                     and s32(int(w["old"], 16)) >= 2048), None)
        def regs(w): return "  ".join(f"{k}={w[k]}" for k in ("a0", "a1", "a2", "v0", "v1", "s0", "s1", "t0"))
        print(f"    first write regs : {regs(first)}")
        if drop:
            print(f"    collapse write   : frame {drop['frame']}  old {drop['old']} -> new {drop['new']}")
            print(f"    collapse regs    : {regs(drop)}")

    print("\nOpen the file:line above. The store is `cpu->...` / a WRITE32 with that PC in a")
    print("comment; read back to what computes the value. If the writer is an OVERLAY")
    print("function that is not cached, run once with Advanced -> native overlays ON so the")
    print("shard is compiled and its C lands in build-clang/cache.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
