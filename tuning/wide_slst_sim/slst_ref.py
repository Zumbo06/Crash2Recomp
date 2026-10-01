"""Reference model of Crash 2's SLST (world polygon list) items.

A port of the game's forward delta (0x8003AE54), register by register. It was
checked on every SLST entry of every level: the start list plus all deltas
gives the end list, exactly for all but four entries, which differ only in the
variant bits of one quad - a data quirk, not a decoding error.

Item layouts (16-bit little-endian):
  source  count, 0, id[count]
  delta   count, 1, addidx, swapidx, removes..., adds..., swaps...
"""
from __future__ import annotations

import struct

SENT = 0xFFFF00


def fwd(item: bytes, src: list[int]) -> list[int]:
    rd = lambda o: struct.unpack_from("<H", item, o)[0] if o + 2 <= len(item) else 0xFFFF
    count, typ, addidx, swapidx = struct.unpack_from("<hhhh", item, 0)
    assert typ == 1
    sp = 4 + 2 * addidx          # end of the remove section (bytes)
    fp = 4 + 2 * swapidx         # end of the add section
    end = 4 + 2 * count
    gp = len(src)
    out: list[int] = []
    v1 = t5 = t6 = 0
    pos = 8

    def dec(p, lim):
        at = rd(p)
        p += 2
        n = 0
        t = SENT
        nxt = None
        if p < lim and at != 0xFFFF:
            t = at & 0xFFF
            n = ((at & 0xF000) >> 12) + 1
        if n > 0:
            nxt = rd(p)
            p += 2
        return p, t, n, nxt

    pos, t1, t3, t8 = dec(pos, sp)
    t7, t0, t2, s7 = dec(sp, fp)
    guard = 0
    while v1 < gp or t2 > 0 or t3 > 0:
        guard += 1
        if guard > 100000:
            raise RuntimeError("no progress")
        if v1 < gp and t3 > 0 and t1 - 1 - v1 == 0:
            t3 -= 1
            t5 += 1
            v1 += 1
            t6 += 1
            if t3 > 0:
                t8 = rd(pos)
                pos += 2
            else:
                pos, t1, t3, t8 = dec(pos, sp)
            continue
        if t2 > 0 and t0 + t5 - v1 == 0:
            out.append(s7 & 0xFFFF)
            t2 -= 1
            if t2 > 0:
                s7 = rd(t7)
                t7 += 2
            else:
                t7, t0, t2, s7 = dec(t7, fp)
            continue
        if v1 < gp:
            at = t1 - v1 - 1
            a3 = t0 + t5 - v1
            if not at < a3:
                at = a3
            a3 = gp - v1 - 1
            if not at < a3:
                at = a3
            if at < 2:
                out.append(src[t6])
                t6 += 1
                v1 += 1
            else:
                out.extend(src[t6:t6 + at])
                t6 += at
                v1 += at
            continue
        raise RuntimeError("stuck: add pending past the source end")
    q = fp
    s4 = 0
    while q < end:
        at = rd(q)
        q += 2
        if at == 0xFFFF:
            break
        if at & 0x8000:
            s3 = (at & 0x7800) >> 11
            s4 = at & 0x7FF
        elif at & 0x4000:
            s3 = (at & 0x1F) + 16
            s4 += (at & 0x3FE0) >> 5
        elif at & 0x2000:
            s4 = at & 0x7FF
            out[s4] ^= (at & 0x1800)
            continue
        else:
            s4 = at & 0xFFF
            at = rd(q)
            q += 2
            s3 = at & 0xFFF
        j = s4 + s3 + 1
        out[s4], out[j] = out[j], out[s4]
    return out


def source(item: bytes) -> list[int]:
    count, typ = struct.unpack_from("<hh", item, 0)
    assert typ == 0
    return list(struct.unpack_from("<%dH" % count, item, 4))


def node_lists(items: list[bytes]) -> list[list[int]]:
    """One list per camera node: the start list, then each delta applied."""
    lists = [source(items[0])]
    for d in items[1:-1]:
        lists.append(fwd(d, lists[-1]))
    return lists
