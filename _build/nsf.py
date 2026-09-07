"""nsf.py - level files off the disc, and the GOOL scripts inside them.

    python _build/nsf.py extract            # every *.NSF -> scratch dir /nsf/
    python _build/nsf.py gools              # GOOL entries per level, EIDs unique to few levels
    python _build/nsf.py code EID [level]   # disassemble a GOOL's code: opcode, operand A, operand B
    python _build/nsf.py ext [level ...]    # external-pool refs (0x400-0x7FF) used by each GOOL

NSF = 64 KiB chunks. Normal chunk: u16 magic 0x1234, u16 type, u32 id, u32 entry
count, entry offsets at +0x10. Entry: u32 0x100FFFF, u32 EID, u32 type, u32
item count, item offsets. GOOL entry type 11: item 0 header, item 1 code
(32-bit words: opcode in bits 24..31, operand A bits 12..23, operand B bits
0..11 - exactly how func_80037448 splits them), item 2 data pool.
"""
from __future__ import annotations

import glob
import os
import struct
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psxexe as X  # noqa: E402

OUT = os.path.join(X.SCRATCH, "nsf")
CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_!"


def eid_name(e: int) -> str:
    return "".join(CHARS[(e >> (1 + 6 * (4 - i))) & 0x3F] for i in range(5))


# --- ISO9660 --------------------------------------------------------------------
def iso_walk(buf: bytes):
    pvd = X.sector_data(buf, 16)
    assert pvd[1:6] == b"CD001", "no primary volume descriptor"
    root = pvd[156:190]
    lba, size = struct.unpack_from("<I", root, 2)[0], struct.unpack_from("<I", root, 10)[0]
    todo = [("", lba, size)]
    while todo:
        path, lba, size = todo.pop()
        data = b"".join(X.sector_data(buf, lba + k) for k in range((size + 2047) // 2048))
        pos = 0
        while pos < len(data):
            ln = data[pos]
            if ln == 0:
                pos = (pos // 2048 + 1) * 2048
                continue
            rec = data[pos:pos + ln]
            pos += ln
            ext, sz, flags, nl = (struct.unpack_from("<I", rec, 2)[0], struct.unpack_from("<I", rec, 10)[0],
                                  rec[25], rec[32])
            name = rec[33:33 + nl].decode("latin-1")
            if name in ("\x00", "\x01"):
                continue
            if flags & 2:
                todo.append((path + "/" + name, ext, sz))
            else:
                yield path + "/" + name.split(";")[0], ext, sz


def extract() -> None:
    with open(X.bin_path(), "rb") as f:
        buf = f.read()
    os.makedirs(OUT, exist_ok=True)
    n = 0
    for path, lba, size in iso_walk(buf):
        if not path.upper().endswith(".NSF"):
            continue
        data = b"".join(X.sector_data(buf, lba + k) for k in range((size + 2047) // 2048))[:size]
        with open(os.path.join(OUT, os.path.basename(path)), "wb") as f:
            f.write(data)
        n += 1
    print(f"{n} NSF files -> {OUT}")


# --- NSF ---------------------------------------------------------------------------
def entries(nsf: bytes):
    for c in range(0, len(nsf), 0x10000):
        ch = nsf[c:c + 0x10000]
        if struct.unpack_from("<H", ch, 0)[0] != 0x1234:
            continue
        ctype = struct.unpack_from("<H", ch, 2)[0]
        if ctype != 0:
            continue
        cnt = struct.unpack_from("<I", ch, 8)[0]
        offs = struct.unpack_from("<%dI" % (cnt + 1), ch, 0x10)
        for k in range(cnt):
            e = ch[offs[k]:offs[k + 1]]
            if len(e) < 16 or struct.unpack_from("<I", e, 0)[0] != 0x100FFFF:
                continue
            eid, etype, icnt = struct.unpack_from("<III", e, 4)
            ioffs = struct.unpack_from("<%dI" % (icnt + 1), e, 0x10)
            items = [e[ioffs[i]:ioffs[i + 1]] for i in range(icnt)]
            yield eid, etype, items


def levels() -> dict[str, bytes]:
    if not glob.glob(os.path.join(OUT, "*.NSF")):
        extract()
    return {os.path.basename(p): open(p, "rb").read() for p in sorted(glob.glob(os.path.join(OUT, "*.NSF")))}


def gools() -> None:
    where: dict[str, list[str]] = defaultdict(list)
    per: dict[str, list[str]] = {}
    for lv, data in levels().items():
        names = [eid_name(eid) for eid, t, items in entries(data) if t == 11]
        per[lv] = names
        for nm in names:
            where[nm].append(lv)
    for lv, names in per.items():
        print(f"{lv}: {len(names)} GOOLs  " + " ".join(names))
    print("\nGOOLs present in 2..3 levels only (level-specific mechanics):")
    for nm, lvs in sorted(where.items(), key=lambda kv: (len(kv[1]), kv[0])):
        if 2 <= len(lvs) <= 3:
            print(f"  {nm}: " + ", ".join(lvs))


def find_gool(name: str, level: str | None = None):
    for lv, data in levels().items():
        if level and lv != level:
            continue
        for eid, t, items in entries(data):
            if t == 11 and eid_name(eid) == name:
                return lv, items
    raise SystemExit(f"GOOL {name} not found")


def ref_str(r: int) -> str:
    if r < 0x400:
        return f"pool[{r}]"
    if r < 0x800:
        return f"EXT[{r - 0x400}]"
    if r & 0x400:                       # 0xC00..0xFFF: linked object field / own field
        if r >= 0xE00:
            return "pop" if r == 0xE1F else f"self.f{r & 0x1FF}"
        return f"link{(r >> 6) & 7}.f{r & 0x3F}"
    if r & 0x200:
        if r & 0x100:
            if r & 0x80:
                return {0xBE0: "null", 0xBF0: "true"}.get(r, f"sp{r & 0x7F}")
            return f"frame[{r & 0x7F}]"
        v = ((r & 0xFF) ^ 0x80) - 0x80
        return f"#{v * 16}"
    v = ((r & 0x1FF) ^ 0x100) - 0x100
    return f"#{v * 256}"


def code(name: str, level: str | None = None) -> None:
    lv, items = find_gool(name, level)
    hdr, codei, pool = items[0], items[1], items[2]
    print(f"{name} in {lv}: header {' '.join('%08X' % w for w in struct.unpack_from('<%dI' % (len(hdr)//4), hdr))}")
    print(f"code {len(codei)//4} instrs, pool {len(pool)//4} words")
    for i in range(0, len(codei), 4):
        w = struct.unpack_from("<I", codei, i)[0]
        op, a, b = w >> 24, (w >> 12) & 0xFFF, w & 0xFFF
        print(f"  {i//4:4d}  {w:08X}  op {op:02X}  A {ref_str(a):14}  B {ref_str(b)}")


def ext(levels_: list[str]) -> None:
    lvs = levels()
    for lv in (levels_ or list(lvs)):
        data = lvs[lv]
        print(f"\n== {lv} ==")
        for eid, t, items in entries(data):
            if t != 11 or len(items) < 2:
                continue
            codei = items[1]
            used: dict[int, list[tuple[int, int]]] = defaultdict(list)
            for i in range(0, len(codei), 4):
                w = struct.unpack_from("<I", codei, i)[0]
                op, a, b = w >> 24, (w >> 12) & 0xFFF, w & 0xFFF
                for r in (a, b):
                    if 0x400 <= r < 0x800:
                        used[r - 0x400].append((i // 4, op))
            if used:
                print(f"  {eid_name(eid)}: EXT " + "  ".join(f"[{k}]x{len(v)}" for k, v in sorted(used.items())))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "extract":
        extract()
    elif cmd == "gools":
        gools()
    elif cmd == "code":
        code(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    elif cmd == "ext":
        ext(sys.argv[2:])


# --- camera-path record tables ---------------------------------------------------
# func_80031AE8 does a binary search over 8-byte records at item+16 (count at item+12):
#   u16 type, u16 offset, u8 flags, u8 elem_size, u16 count ; data at item+12+offset.
# Record 0x185 word[0] is the level render-flags word (bit 2 = light objects, bit 5 = fog).
def records(level: str, rtype: int, limit: int = 12) -> None:
    data = levels()[level]
    shown = 0
    for eid, t, items in entries(data):
        if t != 7:
            continue
        for k, it in enumerate(items):
            if len(it) < 24:
                continue
            cnt = struct.unpack_from("<H", it, 12)[0]
            if not 1 <= cnt <= 64 or 16 + 8 * cnt > len(it):
                continue
            recs = [struct.unpack_from("<HHBBH", it, 16 + 8 * j) for j in range(cnt)]
            if any(recs[j][0] >= recs[j + 1][0] for j in range(cnt - 1)):
                continue
            for rt, off, fl, es, rc in recs:
                if rt != rtype:
                    continue
                base = 12 + off
                n = max(1, (rc * es + 3) // 4) if es else 4
                ws = struct.unpack_from("<%dI" % min(n, (len(it) - base) // 4), it, base)
                print(f"{eid_name(eid)} item {k}: rec 0x{rt:03X} flags 0x{fl:02X} elem {es} count {rc}  data " + " ".join(f"{w:08X}" for w in ws[:8]))
                shown += 1
                if shown >= limit:
                    return
    if not shown:
        print(f"no record 0x{rtype:03X} in {level}")


if __name__ == "__main__" and sys.argv[1] == "records":
    records(sys.argv[2], int(sys.argv[3], 16), int(sys.argv[4]) if len(sys.argv) > 4 else 12)
