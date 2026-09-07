"""light_diag.py - what does the game submit differently when the firefly lights up?

Both renderers and both overlay tiers show the same broken Night Fight light,
so the fault is upstream of rendering: either the game never changes what it
draws (logic / GTE / cull hooks), or it does and both renderers ignore the
change identically (gpu.c decode). This tool tells those apart.

It takes two snapshots of ONE rendered frame each - the primitive stream the
game handed the GPU, with per-vertex colours, plus GTE depth-cue registers -
and diffs them.

Usage (game running from the launcher with Advanced -> Debug server port 4370):
    python _build/light_diag.py
      -> stand in the dark, press Enter          (snapshot A)
      -> catch the firefly, press Enter          (snapshot B)

How to read the verdict:
  * vertex brightness rises A->B, prim count similar   : game IS lighting the
    geometry; both renderers drop it -> gpu.c decode / colour path.
  * more prims drawn in B, brightness similar          : light = draw distance;
    look at the cull hooks (test 4:3) and gte depth cue.
  * nothing changes A->B                               : game logic never
    reacted -> main-exe codegen (test "Run ALL game code in the interpreter")
    or an input/timer the firefly pickup depends on.
  * DQA/DQB/FC change A->B but brightness does not     : depth-cue registers
    are written but the projection ignores them.
"""

from __future__ import annotations

import json
import socket
import sys
from collections import Counter

PORT = 4370


def ask(cmd: str, **kw) -> dict:
    """One command per connection, newline-delimited JSON (the server's rule)."""
    sock = socket.create_connection(("127.0.0.1", PORT), timeout=20)
    sock.settimeout(20)
    msg = {"id": 1, "cmd": cmd}
    msg.update(kw)
    sock.sendall(json.dumps(msg).encode() + b"\n")
    buf = b""
    try:
        while b"\n" not in buf:
            chunk = sock.recv(1 << 20)
            if not chunk:
                break
            buf += chunk
    finally:
        sock.close()
    if not buf:
        raise RuntimeError("empty response to " + cmd)
    return json.loads(buf.split(b"\n", 1)[0].decode())


# --- GP0 primitive decoding -------------------------------------------------
# opcode bits (0x20..0x3F): 0x10 shaded, 0x08 quad, 0x04 textured,
#                           0x02 semi-transparent, 0x01 raw texture

def is_poly(op: int) -> bool:
    return 0x20 <= op <= 0x3F


def vertex_colours(op: int, words: list[int]) -> list[int]:
    """Per-vertex 0x00BBGGRR colour words for a polygon command."""
    if not is_poly(op):
        return []
    shaded, quad, tex = op & 0x10, op & 0x08, op & 0x04
    nverts = 4 if quad else 3
    if not shaded:
        return [words[0] & 0xFFFFFF] * nverts if words else []
    stride = 3 if tex else 2          # colour, xy, [uv]
    out = []
    for v in range(nverts):
        i = v * stride
        if i < len(words):
            out.append(words[i] & 0xFFFFFF)
    return out


def brightness(c: int) -> float:
    r, g, b = c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF
    return (r + g + b) / 3.0


def snapshot(label: str) -> dict:
    ops_before = ask("gpu_opcodes")["opcodes"]
    ring = ask("gpu_ring_stats")
    frame = ring["newest_frame"]
    dump = ask("gpu_frame_dump", frame=frame, count=65536)
    gte = ask("gte_state")
    fstat = ask("gte_frame_stats", frames=8)

    entries = dump.get("entries", [])
    kinds = Counter()
    funcs = Counter()
    bright = []
    semi = raw = shaded = textured = 0
    for e in entries:
        op = int(e["op"], 16)
        words = [int(w, 16) for w in e.get("w", [])]
        kinds[op] += 1
        funcs[e.get("func", "?")] += 1
        if is_poly(op):
            if op & 0x02: semi += 1
            if op & 0x01 and op & 0x04: raw += 1
            if op & 0x10: shaded += 1
            if op & 0x04: textured += 1
            bright.extend(brightness(c) for c in vertex_colours(op, words))

    # The server emits each register as "0x%08X", not as a number.
    ctrl = [int(x, 16) if isinstance(x, str) else int(x) for x in gte.get("gte_ctrl", [])]
    def s16(v): return v - 0x10000 if v & 0x8000 else v
    def s32(v): return v - (1 << 32) if v & 0x80000000 else v
    depth = {
        "H":    ctrl[26] & 0xFFFF if len(ctrl) > 26 else None,
        "DQA":  s16(ctrl[27] & 0xFFFF) if len(ctrl) > 27 else None,
        "DQB":  s32(ctrl[28]) if len(ctrl) > 28 else None,
        "FC":   [s32(ctrl[i]) for i in (21, 22, 23)] if len(ctrl) > 23 else None,
    }

    snap = {
        "label": label, "frame": frame, "prims": len(entries),
        "kinds": kinds, "funcs": funcs, "bright": bright,
        "semi": semi, "raw": raw, "shaded": shaded, "textured": textured,
        "depth": depth, "fstat": fstat.get("frames", []),
        "ops_total": ops_before,
    }
    return snap


def describe(s: dict) -> None:
    b = s["bright"]
    print(f"\n=== {s['label']}  (frame {s['frame']}) ===")
    print(f"  primitives in frame : {s['prims']}   shaded={s['shaded']} textured={s['textured']} "
          f"semi-transparent={s['semi']} raw-texture={s['raw']}")
    if b:
        b_sorted = sorted(b)
        dark = sum(1 for x in b if x < 0x20) / len(b)
        lit = sum(1 for x in b if x >= 0x80) / len(b)
        print(f"  vertex brightness   : mean {sum(b)/len(b):6.1f}  median {b_sorted[len(b)//2]:6.1f}  "
              f"max {b_sorted[-1]:5.1f}   dark(<0x20) {dark*100:4.1f}%   lit(>=0x80) {lit*100:4.1f}%")
    else:
        print("  vertex brightness   : (no polygons in frame)")
    d = s["depth"]
    print(f"  GTE depth cue       : H={d['H']}  DQA={d['DQA']}  DQB={d['DQB']}  far colour FC={d['FC']}")
    top = ", ".join(f"0x{op:02X}x{n}" for op, n in s["kinds"].most_common(8))
    print(f"  top opcodes         : {top}")
    if s["fstat"]:
        f0 = s["fstat"][0]
        print(f"  GTE last frame      : nproj={f0.get('nproj')} nsat={f0.get('nsat')} "
              f"nflat={f0.get('nflat')} nintpl={f0.get('nintpl')}")


def diff(a: dict, b: dict) -> None:
    print("\n=== A -> B (dark -> firefly) ===")
    ba, bb = a["bright"], b["bright"]
    ma = sum(ba) / len(ba) if ba else 0.0
    mb = sum(bb) / len(bb) if bb else 0.0
    print(f"  mean vertex brightness : {ma:6.1f} -> {mb:6.1f}   ({mb-ma:+.1f})")
    print(f"  primitives per frame   : {a['prims']:6d} -> {b['prims']:6d}   ({b['prims']-a['prims']:+d})")
    print(f"  semi-transparent prims : {a['semi']:6d} -> {b['semi']:6d}")
    da, db = a["depth"], b["depth"]
    print(f"  DQA/DQB/FC changed     : {'YES' if (da['DQA'],da['DQB'],da['FC']) != (db['DQA'],db['DQB'],db['FC']) else 'no'}")

    new_funcs = set(b["funcs"]) - set(a["funcs"])
    gone_funcs = set(a["funcs"]) - set(b["funcs"])
    if new_funcs:
        print(f"  draw functions only in B: {sorted(new_funcs)[:8]}")
    if gone_funcs:
        print(f"  draw functions only in A: {sorted(gone_funcs)[:8]}")

    # cumulative opcode deltas since A: which command kinds ran while lit
    print("  GP0 opcodes issued between snapshots (top 10):")
    delta = {}
    for op, n in b["ops_total"].items():
        delta[op] = n - a["ops_total"].get(op, 0)
    for op, n in sorted(delta.items(), key=lambda kv: -kv[1])[:10]:
        if n > 0:
            print(f"      {op}: {n}")

    print("\n--- verdict ---")
    # Brightness and the depth-cue registers are the signal. Primitive count is
    # NOT: it moves with the camera, and letting it veto the verdict is what
    # left the first run silent on a -121 prim delta.
    depth_same = (da["DQA"], da["DQB"], da["FC"]) == (db["DQA"], db["DQB"], db["FC"])
    if abs(mb - ma) < 4 and depth_same:
        print("  The game's lighting output did NOT change: depth-cue registers identical and")
        print("  submitted vertex colours equally dark. Renderer and GTE are faithful; the game")
        print("  logic never reacted to the pickup. Next: Advanced -> 'Run ALL game code in the")
        print("  interpreter'. If still broken:  python _build/light_diag.py watch 20  and catch")
        print("  the firefly inside the window to see whether the registers EVER move.")
    elif mb - ma >= 4:
        print("  The game IS brightening its vertex colours. Both renderers discard that, so")
        print("  the fault is in shared decode/colour handling in gpu.c - not the GTE, not codegen.")
    elif b["prims"] - a["prims"] >= max(20, a["prims"] // 10):
        print("  The light extends DRAW DISTANCE (more geometry submitted), not brightness.")
        print("  Test at 4:3: the widescreen cull hooks rescale depth bounds at 16:9.")


def watch(seconds: float) -> int:
    """Poll depth-cue registers + frame brightness through the pickup moment,
    so a transient write (set, then clobbered a frame later) is not missed."""
    import time
    print(f"watching {seconds:.0f}s - catch the firefly now.")
    print("   t     frame    DQA     DQB          FC             mean_bright")
    t0 = time.time()
    last = None
    s16 = lambda v: v - 0x10000 if v & 0x8000 else v          # noqa: E731
    s32 = lambda v: v - (1 << 32) if v & 0x80000000 else v    # noqa: E731
    while time.time() - t0 < seconds:
        gte = ask("gte_state")
        ctrl = [int(x, 16) if isinstance(x, str) else int(x) for x in gte.get("gte_ctrl", [])]
        dqa, dqb = s16(ctrl[27] & 0xFFFF), s32(ctrl[28])
        fc = tuple(s32(ctrl[i]) for i in (21, 22, 23))
        frame = ask("gpu_ring_stats")["newest_frame"]
        dump = ask("gpu_frame_dump", frame=frame, count=4096)
        br = []
        for e in dump.get("entries", []):
            op = int(e["op"], 16)
            if is_poly(op):
                br.extend(brightness(c) for c in
                          vertex_colours(op, [int(w, 16) for w in e.get("w", [])]))
        mean = sum(br) / len(br) if br else 0.0
        row = (dqa, dqb, fc)
        mark = "  <-- CHANGED" if last is not None and row != last else ""
        print(f"  {time.time()-t0:5.1f}s  f{frame:<7d} {dqa:<7d} {dqb:<12d} {str(fc):<14s} {mean:5.1f}{mark}")
        last = row
        time.sleep(0.25)
    return 0


def funcs_mode() -> int:
    """Which game functions submit the frame's polygons, and how bright each
    one's vertices are. The level-mesh renderer is the function with the most
    shaded/textured polygons; that is where per-vertex light is computed."""
    frame = ask("gpu_ring_stats")["newest_frame"]
    dump = ask("gpu_frame_dump", frame=frame, count=65536)
    per: dict[str, dict] = {}
    for e in dump.get("entries", []):
        op = int(e["op"], 16)
        if not is_poly(op):
            continue
        f = e.get("func", "?")
        d = per.setdefault(f, {"n": 0, "shaded": 0, "tex": 0, "semi": 0, "br": [], "pcs": Counter()})
        d["n"] += 1
        d["shaded"] += 1 if op & 0x10 else 0
        d["tex"] += 1 if op & 0x04 else 0
        d["semi"] += 1 if op & 0x02 else 0
        d["pcs"][e.get("pc", "?")] += 1
        src = int(e.get("src", "0x0"), 16)
        d["src_lo"] = min(d.get("src_lo", src), src)
        d["src_hi"] = max(d.get("src_hi", src), src)
        d["br"].extend(brightness(c) for c in vertex_colours(op, [int(w, 16) for w in e.get("w", [])]))
    print(f"frame {frame}: {sum(d['n'] for d in per.values())} polygons from {len(per)} functions\n")
    print(f"  {'func':12} {'polys':>6} {'shaded':>6} {'tex':>5} {'semi':>5}  {'mean':>6} {'max':>5}  {'>=0x80':>6}   top submit pc   prim buffer (RAM)")
    for f, d in sorted(per.items(), key=lambda kv: -kv[1]["n"]):
        b = d["br"]
        mean = sum(b) / len(b) if b else 0.0
        mx = max(b) if b else 0.0
        lit = (sum(1 for x in b if x >= 0x80) / len(b) * 100) if b else 0.0
        pc, _ = d["pcs"].most_common(1)[0]
        print(f"  {f:12} {d['n']:6d} {d['shaded']:6d} {d['tex']:5d} {d['semi']:5d}  {mean:6.1f} {mx:5.0f}  {lit:5.1f}%   {pc}   "
              f"0x{d['src_lo']:08X}..0x{d['src_hi']:08X}")
    print("\nThe level mesh is the row with the most shaded textured polygons. Its 'func' is")
    print("the recompiled function to read; 'top submit pc' is the store that hands the")
    print("primitive to the GPU. Run once dark and once right after a catch and compare.")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "funcs":
        try:
            ask("gpu_ring_stats")
        except Exception as e:  # noqa: BLE001
            print(f"cannot reach debug server on {PORT}: {e}")
            return 1
        return funcs_mode()
    if len(sys.argv) > 1 and sys.argv[1] == "watch":
        try:
            ask("gpu_ring_stats")
        except Exception as e:  # noqa: BLE001
            print(f"cannot reach debug server on {PORT}: {e}")
            return 1
        return watch(float(sys.argv[2]) if len(sys.argv) > 2 else 20.0)
    try:
        ask("gpu_ring_stats")
    except Exception as e:  # noqa: BLE001
        print(f"cannot reach debug server on {PORT}: {e}")
        print("Launcher -> Advanced -> Debug server port = 4370, then Play.")
        return 1
    print("Stand somewhere DARK in Night Fight (no firefly). Press Enter for snapshot A...")
    input()
    a = snapshot("A: dark, no firefly")
    describe(a)
    print("\nNow catch the firefly, stand still, press Enter for snapshot B...")
    input()
    b = snapshot("B: firefly caught")
    describe(b)
    diff(a, b)
    return 0


if __name__ == "__main__":
    sys.exit(main())
