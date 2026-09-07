"""light_fog.py - Night Fight light: what the renderer uses, and who supplies it.

Night Fight's render family (flags bit 2) does not use fog at all: it lights
each vertex from up to two LIGHT SOURCES whose positions func_80020A24 derives
every frame from two object pointers, *0x8006CD50 (A) and *0x8006CC48 (B), and
writes to params+64..76. Null pointer -> "no light" sentinel -> black. The two
pointers have NO writer anywhere in the EXE by constant address, so they are
script (GOOL) globals; this tool traces the whole globals block they sit in and
names whatever writes it, plus the words that change at the catch.

The older fog machinery below is kept because it still documents the frame
timeline (params pointer, brightness, setup re-runs).

Nothing here is sampled asynchronously (light_scan.py's `fog` mode was, and the
scratchpad is shared scratch - it read other code's locals). The world renderer
func_8003DB94 copies its render-params struct into the scratchpad at the start
of every call:

    0x8003DBDC   sw $a2, 96($v1)    params pointer      -> scratch[96]
    0x8003DBF8   sw $t8,  8($v1)    params[160] SHIFT   -> scratch[8]
    0x8003DBFC   sw $t9, 16($v1)    params[164] START   -> scratch[16]

The debug server's write trace logs each store with its PC, the value written
and the register file. Filtering to those three PCs gives fog shift / fog start
/ params pointer per frame, exactly as used. Once the pointer is known (a couple
of seconds in), the trace is also armed on the struct's fog fields, so the code
that changes them at the catch is named by store PC.

Per vertex the renderer computes  IR0 = (SZ - START) << SHIFT  and depth-cues
the baked colour toward black by IR0 (see tuning/NOTES.md). START is where the
darkness begins; the firefly can only light the level by moving START out.

Usage (Night Fight, standing in the dark, launcher Advanced -> debug port 4370):
    python _build/light_fog.py [seconds=30]     then catch the firefly
"""

from __future__ import annotations

import json
import socket
import sys
import time
from collections import defaultdict

PORT = 4370
SCR = 0x1F800000
PC_PARAMS, PC_SHIFT, PC_START = 0x8003DBDC, 0x8003DBF8, 0x8003DBFC
OFF_FARCOL, OFF_SHIFT, OFF_START = 156, 160, 164

# The level render setup, func_8002131C, reads a flags word, looks the fog record
# (item type 0x1DE) up in the current zone entry, writes the fog fields, and
# installs a 5-entry renderer function-pointer table. Every run of it rewrites
# that table, so stores to the table timestamp "setup re-ran" exactly.
FLAGS = 0x80062AD8                       # level flags; bit 5 = fog mode
ZONE_LO, ZONE_HI = 0x800608D8, 0x800608E4  # camera-table / zone-entry pointers
FNTAB_LO, FNTAB_HI = 0x80062A28, 0x80062A3C
FAMILY = {0x80044980: "default", 0x8004299C: "bit3", 0x8004399C: "FOG per-vertex",
          0x8004599C: "bit21 fog-flat", 0x80043D54: "bit6 fog-B", 0x80045C18: "bit24",
          0x80044A70: "DARK-LEVEL light (bit2)"}

# Night Fight uses flags bit 2 -> family F4 (renderer[0]=0x80044A70). That family
# ignores the fog fields: it forces the far colour black and lights each vertex
# from a LIGHT whose parameters live at params+64..+76:
#   +64/+68  radius / intensity terms (computed at 0x800210B0..E8)
#   +72      light XY relative to camera, packed (dy<<16 | dx), or 0x80008000 = none
#   +76      light Z relative to camera, or -32768 = none
# The position comes from the object at *0x8006CD50 - the light-source object.
LIGHT_LO, LIGHT_HI = 0x80062A54, 0x80062A68
LIGHT_OBJ = 0x8006CD50      # light A object -> params+72/76
LIGHT_OBJ_B = 0x8006CC48    # light B object -> params+64/68
LIGHT_NONE_XY, LIGHT_NONE_Z = 0x80008000, -32768
# No instruction in the whole EXE stores to either pointer by any constant
# address form (psxexe.py refs/argstores/findptr), so they are written through a
# computed address - script (GOOL) globals. Trace the whole block they sit in:
# whatever writes ANY word of it names the mechanism, and the words that change
# at the catch show what the script actually did.
GLOB_LO, GLOB_HI = 0x8006CC00, 0x8006CE00
# The script VM's global table: base 0x8006CB70 (scratchpad 0x1F800058), GLOBAL[i] at base+4i.
# The firefly script FflOC (Night Fight = S000000C.NSF) registers itself as a light source in
# NATIVE MIPS embedded in its code item (opcode 0x49 block at word 121):
#   if GLOBAL[54] == 0: GLOBAL[54] = self  else if GLOBAL[120] == 0: GLOBAL[120] = self
#   GLOBAL[55] += 1.0 ; self.f72 = 1
GLOBALS_BASE = 0x8006CB70
GLOBAL_NAMES = {54: "light B object", 55: "light count (8.8)", 120: "light A object", 126: "firefly state word",
                119: "level flags copy", 88: "render param", 121: "tracked obj x", 122: "tracked obj y", 123: "tracked obj z"}


def gname(addr: int) -> str:
    i = (addr - GLOBALS_BASE) // 4
    return f"GLOBAL[{i}]" + (f" {GLOBAL_NAMES[i]}" if i in GLOBAL_NAMES else "")


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


def s32(v: int) -> int:
    return v - (1 << 32) if v & 0x80000000 else v


def hx(s: str) -> int:
    return int(s, 16)


def arm(lo: int, hi: int) -> None:
    ask("wtrace_add", lo="0x%08X" % (lo & 0x1FFFFFFF), hi="0x%08X" % (hi & 0x1FFFFFFF))


FRAME_LO = 0   # dumps page over [FRAME_LO, FRAME_HI]; set in main()
FRAME_HI = 0


def dump(lo: int, hi: int) -> list[dict]:
    """Every write in [lo,hi) between FRAME_LO and FRAME_HI, oldest-first.
    wtrace_dump returns at most 2048 entries per call, so page over frame
    windows and halve any window that comes back full."""
    seen: dict[int, dict] = {}
    span, f = 64, FRAME_LO
    while f <= FRAME_HI:
        g = min(FRAME_HI, f + span - 1)
        d = ask("wtrace_dump", addr_lo="0x%08X" % (lo & 0x1FFFFFFF),
                addr_hi="0x%08X" % (hi & 0x1FFFFFFF),
                frame_lo=f, frame_hi=g, count=2048, newest=0)
        ents = d.get("entries", [])
        if len(ents) >= 2048 and span > 1:
            span = max(1, span // 2)      # too dense: retry a narrower window
            continue
        for e in ents:
            seen[e["seq"]] = e
        f = g + 1
        if len(ents) < 512:
            span = min(512, span * 2)
    return [seen[k] for k in sorted(seen)]


def frame_brightness(frame: int) -> float:
    d = ask("gpu_frame_dump", frame=frame, count=4096)
    tot = n = 0
    for e in d.get("entries", []):
        op = hx(e["op"])
        if not (0x20 <= op <= 0x3F):
            continue
        w = [hx(x) for x in e.get("w", [])]
        shaded, quad, tex = op & 0x10, op & 0x08, op & 0x04
        nv = 4 if quad else 3
        cols = ([w[0] & 0xFFFFFF] * nv if w else []) if not shaded else \
               [w[v * (3 if tex else 2)] & 0xFFFFFF for v in range(nv) if v * (3 if tex else 2) < len(w)]
        for c in cols:
            tot += ((c & 0xFF) + ((c >> 8) & 0xFF) + ((c >> 16) & 0xFF)) / 3.0
            n += 1
    return tot / n if n else 0.0


def renderer_writes(lo: int, hi: int, pc: int) -> list[dict]:
    return [e for e in dump(lo, hi) if hx(e["pc"]) == pc and e.get("dma_ch", -1) == -1]


def resolve_objects(table: list[int]) -> list[int]:
    """Object list entries (0x8006CDFC[i]) are read by the engine as *(entry+4) -> object.
    Accept either shape, validated by the object's GOOL entry magic at (obj+0x10)->+0."""
    out: list[int] = []

    def w32(a: int) -> int:
        return hx(ask("mem_words", addr="0x%08X" % a, count=1)["words"][0])
    for x in table:
        if not (0x80010000 <= x < 0x80200000):
            continue
        for cand in (w32(x + 4), x):
            if 0x80010000 <= cand < 0x80200000:
                ent = w32(cand + 0x10)
                if 0x80010000 <= ent < 0x80200000 and w32(ent) == 0x100FFFF:
                    out.append(cand)
                    break
    return out


def main() -> int:
    global FRAME_LO, FRAME_HI
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 180.0
    try:
        ask("wtrace_stats")
    except Exception as e:  # noqa: BLE001
        print(f"cannot reach debug server on {PORT}: {e}")
        print("Launcher -> Advanced -> Debug server port = 4370, then Play.")
        return 1

    try:
        ask("wtrace_disarm_all")
    except Exception:  # noqa: BLE001
        pass
    ask("wtrace_clear")
    FRAME_LO = max(0, ask("gpu_ring_stats")["newest_frame"] - 5)
    # Function-entry log position, so the window's entries of the render setup
    # (func_8002131C, reached only through a pointer) can be pulled with their
    # return addresses - i.e. WHO triggers a fog reload.
    seq0 = int(ask("fn_entry_tail", count=1).get("total", 0))
    # Exactly the three words the renderer writes; a span would fill the ring
    # with other scratchpad users' stores within seconds.
    arm(SCR + 96, SCR + 100)
    arm(SCR + 8, SCR + 12)
    arm(SCR + 16, SCR + 20)
    arm(FLAGS, FLAGS + 4)
    arm(ZONE_LO, ZONE_HI)
    arm(FNTAB_LO, FNTAB_HI)              # all five renderer entries
    arm(LIGHT_LO, LIGHT_HI)              # the dark-level family's light params
    arm(GLOB_LO, GLOB_HI)                # the script-owned globals block, both light pointers included

    def w32(a: int) -> int:
        return hx(ask("mem_words", addr="0x%08X" % a, count=1)["words"][0])
    flags = w32(FLAGS)
    print(f"level flags 0x{flags:08X}  fog mode (bit5) {'ON' if flags & 0x20 else 'OFF'}  "
          f"renderer[0] 0x{w32(FNTAB_LO):08X} = {FAMILY.get(w32(FNTAB_LO), '?')}")
    print("zone ptrs   " + "  ".join(f"0x{w32(a):08X}" for a in range(ZONE_LO, ZONE_HI, 4)))
    la, lb = w32(LIGHT_OBJ), w32(LIGHT_OBJ_B)
    print(f"light objects  A *0x{LIGHT_OBJ:08X} = 0x{la:08X} ({'NONE' if not la else 'set'})"
          f"   B *0x{LIGHT_OBJ_B:08X} = 0x{lb:08X} ({'NONE' if not lb else 'set'})")
    print("script globals now: " + "  ".join(f"[{i}] {GLOBAL_NAMES.get(i, '')}=0x{w32(GLOBALS_BASE + 4 * i):08X}"
                                           for i in (54, 55, 120, 126)))
    gp_ = w32(0x1F800058)
    print(f"scratchpad globals base 0x1F800058 = 0x{gp_:08X} ({'OK' if gp_ == GLOBALS_BASE else 'UNEXPECTED, expected 0x8006CB70'})")
    lw_ = [w32(a) for a in range(LIGHT_LO + 4, LIGHT_HI, 4)]
    print(f"light params   B xy 0x{lw_[0]:08X} z {s32(lw_[1])}   A xy 0x{lw_[2]:08X} z {s32(lw_[3])}"
          f"   -> {'A none' if lw_[2] == LIGHT_NONE_XY else 'A ACTIVE'}, {'B none' if lw_[0] == LIGHT_NONE_XY else 'B ACTIVE'}")
    # GOOL operand classes (func_80036D0C / 0x80039E80): own pool, EXTERNAL pool
    # = *( *(obj+0x14) + 24 ) + idx*4, immediates, frame locals, link fields, own
    # fields, stack. Scripts have no other way to reach a fixed address, so if
    # the light pointers are script-written they are external-pool words. Probe
    # the chain on a live object: object list at 0x8006CDFC, count at 0x8006CB6C.
    try:
        nobj = w32(0x8006CB6C)
        objs = [hx(x) for x in ask("mem_words", addr="0x8006CDFC", count=min(max(nobj, 1), 64))["words"]]
        pools: dict[int, int] = defaultdict(int)
        for o in resolve_objects(objs):
            q = w32(o + 0x14)
            if 0x80010000 <= q < 0x80200000:
                pools[w32(q + 24)] += 1
        print(f"objects {nobj}; external pool base(s) via obj+0x14 -> +24: "
              + ", ".join(f"0x{b:08X} (x{c})" for b, c in sorted(pools.items(), key=lambda kv: -kv[1])[:4]))
        for b in pools:
            for name, a in (("light A ptr", LIGHT_OBJ), ("light B ptr", LIGHT_OBJ_B)):
                idx = (a - b) // 4
                if 0 <= idx < 0x400 and (a - b) % 4 == 0:
                    print(f"  {name} 0x{a:08X} = external pool 0x{b:08X} index {idx} (0x{idx:03X})  <-- script-visible")
                else:
                    print(f"  {name} 0x{a:08X} is NOT inside the pool at 0x{b:08X} (idx {idx})")
    except Exception as ex:  # noqa: BLE001
        print(f"  (pool probe failed: {ex})")
    # ---- find the firefly objects (GOOL entry EID "FflOC") and watch their state ----
    # obj+0x10 -> GOOL entry, entry+4 = EID. obj+0xB8 = script field 30 (state), obj+0x160 = field 72
    # ("registered as light", set by the native block that stores the light pointers).
    CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_!"

    def eid_name(e: int) -> str:
        return "".join(CHARS[(e >> (1 + 6 * (4 - i))) & 0x3F] for i in range(5))
    ffl_objs: list[int] = []
    try:
        nobj = w32(0x8006CB6C)
        objs = [hx(x) for x in ask("mem_words", addr="0x8006CDFC", count=min(max(nobj, 1), 64))["words"]]
        kinds: dict[str, int] = defaultdict(int)
        for o in resolve_objects(objs):
            ent = w32(o + 0x10)
            nm = eid_name(w32(ent + 4))
            kinds[nm] += 1
            if nm == "FflOC":
                ffl_objs.append(o)
        print(f"objects {nobj}: " + " ".join(f"{k}x{v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])))
        for o in ffl_objs[:6]:
            arm(o + 0xB8, o + 0xBC)      # state field
            arm(o + 0x160, o + 0x164)    # registered-as-light flag
            print(f"  firefly object 0x{o:08X}: state f30=0x{w32(o + 0xB8):08X} registered f72={w32(o + 0x160)}"
                  f" pos ({s32(w32(o + 0x60)) >> 8}, {s32(w32(o + 0x64)) >> 8}, {s32(w32(o + 0x68)) >> 8})")
        if not ffl_objs:
            print("  no FflOC (firefly) object is alive right now - stand near the fireflies before starting")
    except Exception as ex:  # noqa: BLE001
        print(f"  (object scan failed: {ex})")
    print("write trace armed on the renderer's scratchpad copies. Learning the params struct...")

    # ---- phase 1: learn the params pointer(s) from the renderer's own store ----
    time.sleep(2.5)
    FRAME_HI = ask("gpu_ring_stats")["newest_frame"]
    params_seen: dict[int, int] = defaultdict(int)
    for e in renderer_writes(SCR + 96, SCR + 100, PC_PARAMS):
        params_seen[hx(e["new"])] += 1
    if not params_seen:
        print("no params-pointer stores from func_8003DB94 seen in 2.5 s. Either the world")
        print("renderer is not running (menu / load screen) or scratchpad stores are not")
        print("traced on this build. Re-run during gameplay.")
        return 1
    ptrs = sorted(params_seen, key=lambda p: -params_seen[p])[:4]
    print(f"params struct(s) the renderer used in 2.5 s: "
          + ", ".join(f"0x{p:08X} x{params_seen[p]}" for p in ptrs))
    for p in ptrs:
        arm(p + OFF_FARCOL, p + OFF_START + 4)
        w = ask("mem_words", addr="0x%08X" % (p + OFF_FARCOL), count=3)["words"]
        fc, sh, st = hx(w[0]), s32(hx(w[1])), s32(hx(w[2]))
        black_at = (0x1000 >> sh) if 0 <= sh < 12 else None
        print(f"  0x{p:08X}: far colour 0x{fc:06X}  shift {sh}  start {st}"
              + (f"  -> black at depth {st + black_at}" if black_at is not None else ""))
    print("armed on those structs' fog fields.\n")

    # ---- phase 2: the user catches the firefly; sample brightness for context ----
    import threading
    caught, done = threading.Event(), threading.Event()

    def keys() -> None:
        input()
        caught.set()
        input()
        done.set()
    threading.Thread(target=keys, daemon=True).start()
    print("Press Enter THE MOMENT you catch the firefly, then Enter again about 10 s later")
    print(f"(sampling until the second Enter, or {seconds:.0f}s at most) ...")
    t0 = time.time()
    samples: list[tuple[float, int, float]] = []
    catch_frame: int | None = None
    while not done.is_set() and time.time() - t0 < seconds:
        fr = ask("gpu_ring_stats")["newest_frame"]
        if caught.is_set() and catch_frame is None:
            catch_frame = fr
            print(f"  catch marked at f{fr} ({time.time() - t0:.1f}s)")
        samples.append((time.time() - t0, fr, frame_brightness(fr)))
        time.sleep(0.25)
    if catch_frame is None:
        catch_frame = samples[-1][1] if samples else 0

    FRAME_HI = ask("gpu_ring_stats")["newest_frame"]
    # ---- fog per frame, as used ----
    shift_w = renderer_writes(SCR + 8, SCR + 12, PC_SHIFT)
    start_w = renderer_writes(SCR + 16, SCR + 20, PC_START)
    param_w = renderer_writes(SCR + 96, SCR + 100, PC_PARAMS)
    by_frame: dict[int, dict] = defaultdict(dict)
    for e in shift_w:
        by_frame[e["frame"]]["shift"] = s32(hx(e["new"]))
    for e in start_w:
        by_frame[e["frame"]]["start"] = s32(hx(e["new"]))
    for e in param_w:
        by_frame[e["frame"]].setdefault("params", set()).add(hx(e["new"]))

    print(f"\n=== fog as the renderer used it: {len(shift_w)} shift / {len(start_w)} start stores "
          f"over frames {min(by_frame) if by_frame else '?'}..{max(by_frame) if by_frame else '?'} ===")
    print("   t     frame   bright    shift  start     params      (rows on change, or every 5 s)")
    last = None
    last_br = None
    last_t = -99.0
    frames_sorted = sorted(by_frame)
    for t, fr, br in samples:
        cand = [f for f in frames_sorted if f <= fr]
        if not cand:
            continue
        d = by_frame[cand[-1]]
        row = (d.get("shift"), d.get("start"), tuple(sorted(d.get("params", ()))))
        changed = last is not None and row != last
        bright_jump = last_br is not None and abs(br - last_br) >= 12
        if not (last is None or changed or bright_jump or t - last_t >= 5.0):
            continue
        ps = ",".join(f"0x{p:08X}" for p in row[2])
        mark = "  <-- FOG CHANGED" if changed else ("  <-- brightness" if bright_jump else "")
        print(f"  {t:5.1f}s  f{fr:<7d} {br:6.1f}   {str(row[0]):>5}  {str(row[1]):>6}    {ps}{mark}")
        last, last_br, last_t = row, br, t

    # distinct (shift,start) states over the whole window, with first frame seen
    states: dict[tuple, int] = {}
    for f in frames_sorted:
        d = by_frame[f]
        if "shift" in d and "start" in d:
            states.setdefault((d["shift"], d["start"]), f)
    print("\n  distinct fog states (shift, start) -> first frame:")
    for (sh, st), f in sorted(states.items(), key=lambda kv: kv[1]):
        black_at = (0x1000 >> sh) if 0 <= sh < 12 else None
        print(f"    shift {sh:>3}  start {st:>6}   first f{f}"
              + (f"   (black from depth {st + black_at})" if black_at is not None else ""))

    # ---- who wrote the struct's fog fields ----
    print("\n=== writers of the params struct fog fields during the window ===")
    any_w = False
    for p in ptrs:
        ents = [e for e in dump(p + OFF_FARCOL, p + OFF_START + 4) if e.get("dma_ch", -1) == -1]
        dma = [e for e in dump(p + OFF_FARCOL, p + OFF_START + 4) if e.get("dma_ch", -1) != -1]
        if not ents and not dma:
            print(f"  0x{p:08X}: no writes")
            continue
        any_w = True
        groups: dict[tuple, list] = defaultdict(list)
        for e in ents:
            off = (hx(e["addr"]) & 0x1FFFFFFF) - (p & 0x1FFFFFFF)
            groups[(off, e["pc"], e["func"])].append((e["frame"], s32(hx(e["new"]))))
        for (off, pc, func), ws in sorted(groups.items(), key=lambda kv: (kv[0][0], -len(kv[1]))):
            field = {OFF_FARCOL: "far colour", OFF_SHIFT: "fog shift", OFF_START: "fog start"}.get(off, f"+{off}")
            vals = sorted({v for _, v in ws})
            print(f"  0x{p:08X} {field:10} store pc {pc}  func {func}  x{len(ws)}  frames {ws[0][0]}..{ws[-1][0]}"
                  f"  values {vals[:10]}")
        if dma:
            print(f"  0x{p:08X}: {len(dma)} DMA writes into the struct (block copy from level data) "
                  f"first f{dma[0]['frame']} ch{dma[0]['dma_ch']}")
    if not any_w:
        print("  The struct was never written while the window ran. The renderer keeps reading")
        print("  the same fog values, so the firefly logic never reaches them - or the light")
        print("  lives in a DIFFERENT struct the renderer switched to (see the params column).")

    # ---- when did the render setup re-run, and what moved around it? ----
    # Grouped, not per frame: the table is rewritten every game frame, and a
    # per-frame list ran to hundreds of lines and truncated the sections after it.
    print("\n=== renderer fn-ptr table 0x80062A28..3C: writers and values ===")
    tab = [e for e in dump(FNTAB_LO, FNTAB_HI) if e.get("dma_ch", -1) == -1]
    groups: dict[tuple, list] = defaultdict(list)
    for e in tab:
        idx = ((hx(e["addr"]) & 0x1FFFFFFF) - (FNTAB_LO & 0x1FFFFFFF)) // 4
        groups[(idx, e["pc"], hx(e["new"]))].append(e)
    for (idx, pc, v), es in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[1][0]["frame"])):
        print(f"  [{idx}] = 0x{v:08X} {FAMILY.get(v, ''):14} x{len(es):<5} f{es[0]['frame']}..f{es[-1]['frame']}"
              f"   store pc {pc}  ra {es[0]['ra']}")
    if not tab:
        print("  no writes")
    print("\n=== entries of the render setup func_8002131C in the window (caller = ra) ===")
    try:
        seq1 = int(ask("fn_entry_tail", count=1).get("total", 0))
        fe = ask("fn_entry_dump", seq_lo=str(seq0), seq_hi=str(seq1),
                 addr_lo="0x8002131C", addr_hi="0x80021320", count=64)
        ents = fe.get("entries", [])
        for e in ents:
            print(f"  f{e['frame']:<7d} func {e['func']}  called from ra {e['ra']}  "
                  f"a0 {e['a0']} a1 {e['a1']} a2 {e['a2']}")
        if not ents:
            print("  none - the render setup was not entered in this window")
    except Exception as ex:  # noqa: BLE001
        print(f"  (entry log unavailable: {ex})")
    print("\n=== LIGHT: source-object pointers and params+64..76 (the dark-level family's real inputs) ===")
    for name, addr in (("A *0x8006CD50", LIGHT_OBJ), ("B *0x8006CC48", LIGHT_OBJ_B)):
        ws = [x for x in dump(addr, addr + 4) if x.get("dma_ch", -1) == -1]
        for e in ws[:12]:
            print(f"  f{e['frame']:<7d} light obj {name}: {e['old']} -> {e['new']}   store pc {e['pc']}  func {e['func']}  ra {e['ra']}")
        if len(ws) > 12:
            print(f"  ... {len(ws) - 12} more")
        if not ws:
            print(f"  light obj {name}: never written in the window (value 0x{w32(addr):08X})")
    lp = [x for x in dump(LIGHT_LO, LIGHT_HI) if x.get("dma_ch", -1) == -1]
    lgroups: dict[tuple, list] = defaultdict(list)
    for e in lp:
        off = (hx(e["addr"]) & 0x1FFFFFFF) - (0x80062A18 & 0x1FFFFFFF)
        v = hx(e["new"])
        none = v == LIGHT_NONE_XY or s32(v) == LIGHT_NONE_Z
        lgroups[(off, none)].append(e)
    for (off, none), es in sorted(lgroups.items()):
        vals = sorted({hx(e["new"]) for e in es})
        print(f"  params+{off:<3d} {'NONE sentinel' if none else 'REAL position'} x{len(es):<5} "
              f"f{es[0]['frame']}..f{es[-1]['frame']}   e.g. {', '.join('0x%08X' % v for v in vals[:4])}")
    if not lp:
        print("  light params: no writes")

    # ---- the whole script-owned globals block: what moved, and who moved it ----
    print(f"\n=== globals block 0x{GLOB_LO:08X}..0x{GLOB_HI:08X}: every word written in the window"
          f" (catch marked at f{catch_frame}) ===")
    gw = dump(GLOB_LO, GLOB_HI)
    by_word: dict[int, list] = defaultdict(list)
    for e in gw:
        by_word[hx(e["addr"]) | 0x80000000].append(e)

    def ptrlike(v: int) -> bool:
        return 0x80010000 <= v < 0x80200000
    near: list[int] = []
    for a, es in sorted(by_word.items()):
        pcs = sorted({(e["pc"], e["func"]) for e in es})
        vals: list[int] = []
        for e in es:
            v = hx(e["new"])
            if v not in vals:
                vals.append(v)
        first, last = es[0]["frame"], es[-1]["frame"]
        dma = sum(1 for e in es if e.get("dma_ch", -1) != -1)
        tag = "  " + gname(a)
        if any(ptrlike(v) for v in vals):
            tag += "  (object pointers)"
        print(f"  0x{a:08X} x{len(es):<5} f{first}..f{last}  by "
              + ", ".join(f"{p}/{f}" for p, f in pcs[:3]) + (f" +{len(pcs) - 3}" if len(pcs) > 3 else "")
              + (f"  dma:{dma}" if dma else "") + "  values "
              + " ".join(f"0x{v:08X}" for v in vals[:4]) + (" ..." if len(vals) > 4 else "") + tag)
        if any(abs(e["frame"] - catch_frame) <= 90 and e["new"] != e["old"] for e in es):
            near.append(a)
    if not gw:
        print("  nothing in the block was written - not even the setup's own words. The trace is")
        print("  not seeing RAM stores on this build; nothing below can be trusted.")
    print("\n  words that CHANGED within 3 s of the catch: " + (", ".join(f"0x{a:08X}" for a in near) or "none"))
    for a in near:
        es = [e for e in by_word[a] if abs(e["frame"] - catch_frame) <= 90 and e["new"] != e["old"]][:6]
        for e in es:
            print(f"    f{e['frame']:<7d} 0x{a:08X}: {e['old']} -> {e['new']}   pc {e['pc']} {e['func']} ra {e['ra']}")
    la2, lb2 = w32(LIGHT_OBJ), w32(LIGHT_OBJ_B)
    print(f"\n  light pointers now: A 0x{la2:08X}  B 0x{lb2:08X}   light count GLOBAL[55] = 0x{w32(GLOBALS_BASE + 220):08X}"
          + ("   -> STILL NULL after the catch: the firefly's native register block never stored itself"
             if not la2 and not lb2 else "   -> a light IS registered; the fault is downstream (params+64..76 / vertex lighting)"))
    for e in [x for x in gw if hx(x["addr"]) | 0x80000000 in (LIGHT_OBJ, LIGHT_OBJ_B, GLOBALS_BASE + 220)][:12]:
        print(f"    f{e['frame']:<7d} {gname(hx(e['addr']) | 0x80000000):28} {e['old']} -> {e['new']}  pc {e['pc']} {e['func']} ra {e['ra']}")

    print("\n=== firefly objects: state (f30) and registered-flag (f72) writes in the window ===")
    for o in ffl_objs[:6]:
        for off, nm in ((0xB8, "state f30"), (0x160, "registered f72")):
            es = [x for x in dump(o + off, o + off + 4) if x["new"] != x["old"]]
            print(f"  0x{o:08X} {nm:15} x{len(es):<4} " + "  ".join(f"f{e['frame']}:{e['old']}->{e['new']}@{e['pc']}" for e in es[:8])
                  + (" ..." if len(es) > 8 else "") + (f"   now 0x{w32(o + off):08X}" if not es else ""))
    if not ffl_objs:
        print("  (no firefly objects were found at start)")

    print("\n=== level flags 0x80062AD8: writers and values (written every frame by func_80020A24) ===")
    fl = [x for x in dump(FLAGS, FLAGS + 4) if x.get("dma_ch", -1) == -1]
    fgroups: dict[tuple, list] = defaultdict(list)
    for e in fl:
        fgroups[(e["pc"], hx(e["new"]))].append(e)
    for (pc, v), es in sorted(fgroups.items(), key=lambda kv: kv[1][0]["frame"]):
        print(f"  flags = 0x{v:08X}  x{len(es):<5} f{es[0]['frame']}..f{es[-1]['frame']}   store pc {pc}  ra {es[0]['ra']}")
    if not fl:
        print("  no writes")
    print("\n=== zone / camera pointers 0x800608D8..E0 ===")
    zn = [x for x in dump(ZONE_LO, ZONE_HI) if x.get("dma_ch", -1) == -1]
    for e in zn[:40]:
        off = (hx(e["addr"]) & 0x1FFFFFFF) - (ZONE_LO & 0x1FFFFFFF)
        print(f"  f{e['frame']:<7d} zone+{off:<2d}  {e['old']} -> {e['new']}   store pc {e['pc']}  func {e['func']}")
    if len(zn) > 40:
        print(f"  ... {len(zn) - 40} more")
    if not zn:
        print("  no writes")

    peak = max((b for _, _, b in samples), default=0.0)
    print()
    if peak < 40:
        print(f"NO CATCH IN THIS WINDOW: peak vertex brightness {peak:.1f} never left the dark band.")
        print("The fog values above are the steady dark state only. Re-run and catch one.")
    else:
        print(f"catch seen: peak vertex brightness {peak:.1f}")
    for p in ptrs:
        w = ask("mem_words", addr="0x%08X" % (p + OFF_FARCOL), count=3)["words"]
        print(f"params 0x{p:08X} now: far colour 0x{hx(w[0]):06X}  shift {s32(hx(w[1]))}  start {s32(hx(w[2]))}")
    print("\nRead a writer with:  python _build/mipsdis.py func <func>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
