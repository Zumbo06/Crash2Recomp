"""psxexe.py - pull SCUS_941.54 out of the disc image and read it as a whole.

The recompiled C only carries the instructions the recompiler DISCOVERED; the
`.ranges` manifest is its function list. Code it never found (reached through a
table it could not follow) runs in the runtime's interpreter and is invisible
to every grep over generated/*.c - including a store to the light-source
pointer. The disc has the real thing, so read that.

    python _build/psxexe.py extract              # -> scratch dir /SCUS_941.54  (game code stays out of the repo)
    python _build/psxexe.py coverage             # text words not inside any recompiled function
    python _build/psxexe.py findptr 8006CD50 ... # data words holding these addresses (pointer tables)
    python _build/psxexe.py refs 8006CD50 ...    # code (recompiled OR NOT) addressing these words via lui/lo
    python _build/psxexe.py dis LO HI            # disassembly straight from the EXE, branches included

The .bin has 2352-byte raw sectors (Mode 2 Form 1 on a PS1 disc: 24 bytes of
sync/header/subheader, then 2048 data bytes). The EXE is one contiguous
ISO9660 extent starting at the sector whose data begins with "PS-X EXE".
"""
from __future__ import annotations

import bisect
import glob
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mipsdis as M  # noqa: E402

CUE = (r"c:/Users/yhgoz/Desktop/Crash2_Rcomp/Crash Bandicoot 2 - Cortex Strikes Back (USA)/"
       r"Crash Bandicoot 2 - Cortex Strikes Back (USA).cue")
SCRATCH = (r"C:\Users\yhgoz\AppData\Local\Temp\claude\c--Users-yhgoz-Desktop-Crash2-Rcomp"
           r"\5426624f-116c-4ebb-8386-2aebca2f6c08\scratchpad")
EXE = os.path.join(SCRATCH, "SCUS_941.54")
RAW = 2352


def bin_path() -> str:
    with open(CUE, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r'\s*FILE\s+"(.+?)"', line)
            if m:
                return os.path.join(os.path.dirname(CUE), m.group(1))
    raise SystemExit("no FILE line in cue")


def sector_data(buf: bytes, n: int) -> bytes:
    s = buf[n * RAW:(n + 1) * RAW]
    mode = s[15]
    off = 24 if mode == 2 else 16
    return s[off:off + 2048]


def extract() -> str:
    path = bin_path()
    with open(path, "rb") as f:
        buf = f.read()
    nsec = len(buf) // RAW
    start = None
    for n in range(nsec):
        if sector_data(buf, n)[:8] == b"PS-X EXE":
            start = n
            break
    if start is None:
        raise SystemExit("no PS-X EXE header in any sector")
    hdr = sector_data(buf, start)
    pc0, gp, t_addr, t_size = struct.unpack_from("<IIII", hdr, 0x10)
    total = 2048 + t_size
    out = bytearray()
    n = start
    while len(out) < total:
        out += sector_data(buf, n)
        n += 1
    os.makedirs(SCRATCH, exist_ok=True)
    with open(EXE, "wb") as f:
        f.write(out[:total])
    print(f"{os.path.basename(path)}: PS-X EXE at sector {start}, pc0 0x{pc0:08X}, gp 0x{gp:08X}, "
          f"text 0x{t_addr:08X}..0x{t_addr + t_size:08X} ({t_size} bytes)")
    print(f"written {EXE}")
    return EXE


def load() -> tuple[int, bytes]:
    if not os.path.exists(EXE):
        extract()
    with open(EXE, "rb") as f:
        data = f.read()
    t_addr, t_size = struct.unpack_from("<II", data, 0x18)
    return t_addr, data[2048:2048 + t_size]


def word(text: bytes, base: int, addr: int) -> int:
    return struct.unpack_from("<I", text, addr - base)[0]


def recompiled_pcs() -> set[int]:
    pat = re.compile(r"/\* 0x([0-9A-F]{8}): 0x([0-9A-F]{8}) \*/")
    pcs: set[int] = set()
    for path in glob.glob(os.path.join(M.PROJ, "generated", "*.c")):
        text = open(path, encoding="utf-8", errors="replace").read()
        for m in pat.finditer(text):
            pcs.add(int(m.group(1), 16))
    return pcs


def func_extents() -> list[tuple[int, int]]:
    """[lo, hi) per recompiled function = min..max instruction PC seen in its C
    (branch PCs are not commented, but they sit between commented ones)."""
    F = M.funcs()
    pcs = sorted(recompiled_pcs())
    ext: dict[int, list[int]] = {}
    for pc in pcs:
        k = bisect.bisect_right(F, pc) - 1
        if k < 0:
            continue
        e = ext.setdefault(F[k], [pc, pc])
        e[0] = min(e[0], pc)
        e[1] = max(e[1], pc)
    return sorted((lo, hi + 4) for lo, hi in ext.values())


def coverage() -> None:
    base, text = load()
    ext = func_extents()
    covered = 0
    gaps: list[tuple[int, int]] = []
    cur = base
    for lo, hi in ext:
        if lo > cur:
            gaps.append((cur, lo))
        covered += hi - max(lo, cur)
        cur = max(cur, hi)
    end = base + len(text)
    if cur < end:
        gaps.append((cur, end))
    print(f"text 0x{base:08X}..0x{end:08X}: {len(ext)} recompiled functions cover {covered} of {len(text)} bytes")
    # classify each gap: code-like (mostly valid-looking instructions with a jr $ra) vs data
    print(f"{len(gaps)} uncovered ranges; those that look like CODE (contain jr $ra and sane opcodes):")
    for lo, hi in gaps:
        if hi - lo < 16:
            continue
        ws = [word(text, base, a) for a in range(lo, hi, 4)]
        has_ret = any(w == 0x03E00008 for w in ws)
        sane = sum(1 for w in ws if w == 0 or M.dis(0, w) not in ("??",) and not M.dis(0, w).startswith(("spec", "regimm", "cop"))) / len(ws)
        tag = "CODE?" if has_ret and sane > 0.9 else "data "
        if tag == "CODE?" or hi - lo >= 0x400:
            print(f"  0x{lo:08X}..0x{hi:08X}  {hi - lo:6d} bytes  {tag}  sane {sane:.2f}")


def findptr(addrs: list[int]) -> None:
    base, text = load()
    for a in addrs:
        needle = struct.pack("<I", a)
        hits = [i for i in range(0, len(text) - 3, 4) if text[i:i + 4] == needle]
        print(f"0x{a:08X}: {len(hits)} data word(s) hold it")
        for i in hits:
            at = base + i
            ctx = [word(text, base, at + d) for d in range(-16, 20, 4)]
            print(f"  at 0x{at:08X}   context " + " ".join(f"{w:08X}" for w in ctx))


def refs(addrs: list[int], lo: int | None = None, hi: int | None = None) -> None:
    """Every lui/lo pair in the WHOLE text (not only recompiled code) that forms
    one of these addresses, with the memory op that uses it."""
    base, text = load()
    lo = lo or base
    hi = hi or base + len(text)
    F = M.funcs()
    rec = recompiled_pcs()
    want = {a: [] for a in addrs}
    n = (hi - lo) // 4
    ws = [word(text, base, a) for a in range(lo, hi, 4)]
    for i, w in enumerate(ws):
        if (w >> 26) != 15:
            continue
        rt = (w >> 16) & 31
        hi16 = (w & 0xFFFF) << 16
        regs = {rt: hi16}
        for j in range(i + 1, min(i + 24, n)):
            w2 = ws[j]
            op = w2 >> 26
            rs = (w2 >> 21) & 31
            rt2 = (w2 >> 16) & 31
            rd = (w2 >> 11) & 31
            imm = M.s16(w2 & 0xFFFF)
            if op == 9 and rs in regs:
                regs[rt2] = regs[rs] + imm
                if regs[rt2] in want:
                    want[regs[rt2]].append((lo + 4 * j, w2, "addr"))
                continue
            if op in M.MEM and rs in regs:
                tgt = regs[rs] + imm
                if tgt in want:
                    want[tgt].append((lo + 4 * j, w2, "mem"))
                if op < 40 and rt2 in regs:
                    del regs[rt2]
                continue
            if (op in (8, 9, 10, 11, 12, 13, 14, 15) and rt2 in regs) or (op == 0 and rd in regs):
                del regs[rd if op == 0 else rt2]
            if op in (2, 3) or (op == 0 and (w2 & 63) in (8, 9)):
                break
            if not regs:
                break
    for a in addrs:
        print(f"0x{a:08X}: {len(want[a])} reference(s) in the EXE text")
        for pc, w2, kind in want[a]:
            k = bisect.bisect_right(F, pc) - 1
            fn = f"func_{F[k]:08X}" if k >= 0 else "?"
            tag = "recompiled" if pc in rec else "NOT RECOMPILED"
            print(f"  {pc:08X}  {M.dis(pc, w2):28} {kind:4} {fn:15} {tag}")


def dis(lo: int, hi: int) -> None:
    base, text = load()
    for a in range(lo, hi, 4):
        w = word(text, base, a)
        d = M.dis(a, w)
        mark = ""
        if "mtc2" in d and "IR0" in d:
            mark = "   <== IR0"
        elif d.startswith("gte "):
            mark = "   <-- gte"
        print(f"{a:08X}  {w:08X}  {d}{mark}")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "extract":
        extract()
    elif cmd == "coverage":
        coverage()
    elif cmd == "findptr":
        findptr([int(x, 16) for x in sys.argv[2:]])
    elif cmd == "refs":
        refs([int(x, 16) for x in sys.argv[2:]])
    elif cmd == "dis":
        dis(int(sys.argv[2], 16), int(sys.argv[3], 16))


# ---------------------------------------------------------------------------
# Interprocedural: stores through a struct pointer passed as an argument.
#   caller:  addiu $a1, $v0, -13048   (a1 = 0x8006CD08) ; jal callee
#   callee:  sw $v0, 72($a1)          -> writes 0x8006CD50
# Neither half names the target on its own, so both scans above miss it.
# ---------------------------------------------------------------------------
def _decode(w):
    return (w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31, w & 63, M.s16(w & 0xFFFF))


def argstores(targets: list[int]) -> None:
    base, text = load()
    F = M.funcs()
    n = len(text) // 4
    ws = [struct.unpack_from("<I", text, 4 * i)[0] for i in range(n)]
    ext = {F[i]: (F[i], F[i + 1] if i + 1 < len(F) else base + len(text)) for i in range(len(F))}
    STORES = (40, 41, 42, 43, 46, 58)
    CALLER_SAVED = (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 24, 25, 31)
    arg_stores: dict[int, set] = {}      # func -> {(argn, off)}
    passes: dict[int, list] = {}         # func -> [(callee, m, argn, k)]
    const_calls: list = []               # (caller_pc, callee, m, const)
    for f, (lo, hi) in ext.items():
        regs = {r: ("arg", r - 4, 0) for r in (4, 5, 6, 7)}
        st, ps = set(), []
        i0 = (lo - base) // 4
        for i in range(i0, min((hi - base) // 4, n)):
            w = ws[i]; pc = base + 4 * i
            op, rs, rt, rd, f6, imm = _decode(w)
            if op == 15 and rt:
                regs[rt] = ("k", 0, (w & 0xFFFF) << 16); continue
            if op == 9 and rs in regs:
                kind, a, k = regs[rs]; regs[rt] = (kind, a, k + imm); continue
            if op == 0 and f6 in (32, 33, 37) and (rt == 0 or rs == 0) and (rs or rt) in regs:
                regs[rd] = regs[rs or rt]; continue
            if op in M.MEM and rs in regs:
                kind, a, k = regs[rs]
                if op in STORES and kind == "arg":
                    st.add((a, k + imm))
                if op not in STORES and rt in regs:
                    del regs[rt]
                continue
            if op in (2, 3) or (op == 0 and f6 == 9):
                callee = ((pc + 4) & 0xF0000000) | ((w & 0x3FFFFFF) << 2) if op == 3 else None
                # arguments as they stand at the call (include the delay slot's own setup)
                snap = dict(regs)
                if i + 1 < n:
                    w2 = ws[i + 1]; op2, rs2, rt2, rd2, f62, imm2 = _decode(w2)
                    if op2 == 15 and rt2: snap[rt2] = ("k", 0, (w2 & 0xFFFF) << 16)
                    elif op2 == 9 and rs2 in snap: kind, a, k = snap[rs2]; snap[rt2] = (kind, a, k + imm2)
                    elif op2 == 0 and f62 in (32, 33, 37) and (rt2 == 0 or rs2 == 0) and (rs2 or rt2) in snap: snap[rd2] = snap[rs2 or rt2]
                if callee is not None:
                    for m in range(4):
                        v = snap.get(4 + m)
                        if v is None: continue
                        if v[0] == "arg": ps.append((callee, m, v[1], v[2]))
                        else: const_calls.append((pc, callee, m, v[2]))
                for r in CALLER_SAVED: regs.pop(r, None)
                continue
            if op in (8, 9, 10, 11, 12, 13, 14, 15) and rt in regs: del regs[rt]
            elif op == 0 and rd in regs and f6 != 8: del regs[rd]
        arg_stores[f] = st; passes[f] = ps
    # propagate stores through argument passing (3 rounds is plenty)
    for _ in range(3):
        for f, ps in passes.items():
            for callee, m, argn, k in ps:
                for a2, off in list(arg_stores.get(callee, ())):
                    if a2 == m: arg_stores[f].add((argn, off + k))
    hits = 0
    for pc, callee, m, const in const_calls:
        for a2, off in arg_stores.get(callee, ()):
            if a2 == m and const + off in targets:
                k = bisect.bisect_right(F, pc) - 1
                print(f"  0x{const + off:08X} <- func_{callee:08X} stores a{m}+{off} ; called from func_{F[k]:08X}@{pc:08X} with a{m} = 0x{const:08X}")
                hits += 1
    print(f"{hits} argument-relative store path(s) reach {', '.join('0x%08X' % t for t in targets)}")


def regmap(lo: int, hi: int) -> None:
    """Static R/W map of a window from the EXE words (lui/lo forms)."""
    base, text = load()
    F = M.funcs()
    n = len(text) // 4
    ws = [struct.unpack_from("<I", text, 4 * i)[0] for i in range(n)]
    hits: dict[int, list] = {}
    for i, w in enumerate(ws):
        if (w >> 26) != 15 or not ((w >> 16) & 31): continue
        regs = {(w >> 16) & 31: (w & 0xFFFF) << 16}
        for j in range(i + 1, min(i + 32, n)):
            w2 = ws[j]; op, rs, rt, rd, f6, imm = _decode(w2)
            if op == 9 and rs in regs: regs[rt] = regs[rs] + imm; continue
            if op in M.MEM and rs in regs:
                tgt = regs[rs] + imm
                if lo <= tgt < hi: hits.setdefault(tgt, []).append((base + 4 * j, M.dis(base + 4 * j, w2)))
                if op < 40 and rt in regs: del regs[rt]
                continue
            if (op in (8, 9, 10, 11, 12, 13, 14, 15) and rt in regs) or (op == 0 and rd in regs): del regs[rd if op == 0 else rt]
            if op in (2, 3) or (op == 0 and f6 in (8, 9)) or not regs: break
    def fn(pc):
        k = bisect.bisect_right(F, pc) - 1
        return "func_%08X" % F[k] if k >= 0 else "?"
    print(f"{len(hits)} words referenced in 0x{lo:08X}..0x{hi:08X}")
    for t in sorted(hits):
        kinds = hits[t]
        wr = [x for x in kinds if x[1].split()[0] in ("sw", "sh", "sb", "swl", "swr", "swc2")]
        print(f"0x{t:08X}  R:{len(kinds) - len(wr):2d} W:{len(wr):2d}  " + "  ".join(f"{fn(p)}@{p:08X}:{d.split()[0]}" for p, d in sorted(kinds)[:5]))


if __name__ == "__main__" and sys.argv[1] == "argstores":
    argstores([int(x, 16) for x in sys.argv[2:]])
if __name__ == "__main__" and sys.argv[1] == "map":
    regmap(int(sys.argv[2], 16), int(sys.argv[3], 16))

