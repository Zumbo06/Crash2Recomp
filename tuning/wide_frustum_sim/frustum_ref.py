"""Reference for crash2_wide_slst.h's camera test, written apart from the C.

What the hook draws at node n when it can judge the camera:
  - candidates: every polygon the node lists within FRUSTUM_K either side hold
    (past the path's ends, the linked path's, world slots mapped by world
    EID) that the game's list L(n) does not, nearest node first, each with the
    position in L(n) of the last polygon before it in its own list that L(n)
    holds;
  - the model check: most of L(n) must land in the 4:3 frame;
  - kept: candidates reaching into the extra columns (outside 512 wide, inside
    it plus the margin), at most len(L(n)) + 32, nearest first;
  - the list: L(n), each kept candidate right after its anchor.
The projection is the GTE's RTPS in integers, no translation, no squash.
"""
from __future__ import annotations

import struct

FRUSTUM_K = 20
MAX_LIST = 1520
GUARD = 24
FRAME_H = 216


def key(v):
    return (v | 0x1800) if (v & 0x1800) else v


def s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


class World:
    """A WGEO entry: info (origin, counts), vertices, triangles, quads."""

    def __init__(self, items):
        info = items[0]
        self.origin = struct.unpack_from("<3i", info, 0)
        self.nv, self.nt, self.nq = struct.unpack_from("<3i", info, 16)
        self.v, self.t, self.q = items[1], items[2], items[3]

    def corners(self, pid, off):
        """Camera-relative 16-bit corners, as the renderer feeds the GTE."""
        idx = pid & 0x7FF
        if pid & 0x1800:
            if idx >= self.nq:
                return None
            w0, w1 = struct.unpack_from("<2I", self.q, 8 * idx)
            ids = (w0 >> 20, (w0 >> 8) & 0xFFF, w1 >> 20, (w1 >> 8) & 0xFFF)
        else:
            if idx >= self.nt:
                return None
            w0 = struct.unpack_from("<I", self.t, 4 * (self.nt - 1 - idx))[0]
            h = struct.unpack_from("<H", self.t, 4 * self.nt + 2 * idx)[0]
            ids = (w0 >> 20, (w0 >> 8) & 0xFFF, (h >> 4) & 0xFFF)
        out = []
        for i in ids:
            if i >= self.nv:
                return None
            xy = struct.unpack_from("<I", self.v, 4 * (self.nv - 1 - i))[0]
            z = struct.unpack_from("<H", self.v, 4 * self.nv + 2 * i)[0]
            out.append((s16((xy & 0xFFF0) + off[0]), s16(((xy >> 16) & 0xFFF0) + off[1]),
                        s16((z & 0xFFF0) + off[2])))
        return out


def project(cam, x, y, z):
    r, h, ofx, ofy = cam["r"], cam["h"], cam["ofx"], cam["ofy"]
    m3 = (r[6] * x + r[7] * y + r[8] * z) >> 12
    if m3 <= 0:
        return None
    m1 = max(-0x8000, min(0x7FFF, (r[0] * x + r[1] * y + r[2] * z) >> 12))
    m2 = max(-0x8000, min(0x7FFF, (r[3] * x + r[4] * y + r[5] * z) >> 12))
    sz = min(m3, 0xFFFF)
    q = 0x1FFFF if h >= sz * 2 else min(0x1FFFF, ((h << 16) + sz // 2) // sz)
    px = max(-1024, min(1023, (ofx + m1 * q) >> 16))
    py = max(-1024, min(1023, (ofy + m2 * q) >> 16))
    return px, py


OUT, IN43, WIDE = 0, 1, 2


def classify(cam, corners, margin):
    lo, hi = -margin - GUARD, 512 + margin + GUARD
    front = behind = aside = 0
    all_l = all_r = all_t = all_b = True
    for x, y, z in corners:
        p = project(cam, x, y, z)
        if p is None:
            behind += 1
            continue
        front += 1
        sx, sy = p
        all_l &= sx < lo
        all_r &= sx >= hi
        all_t &= sy < -GUARD
        all_b &= sy >= FRAME_H + GUARD
        aside |= sx < 0 or sx >= 512
    if not front:
        return OUT
    if behind:
        return WIDE if aside else IN43
    if all_l or all_r or all_t or all_b:
        return OUT
    return WIDE if aside else IN43


BODY = 384


def object_verdict(cam, margin, cam_pos, obj_pos):
    """crash2_wide_spawn.h's c2ws_in_margin for an object at obj_pos (world
    units) and a camera at cam_pos: in front, its body overlapping the wide
    frame, its middle outside the 4:3 one."""
    x, y, z = (obj_pos[i] - cam_pos[i] for i in range(3))
    r = cam["r"]
    depth = (r[6] * x + r[7] * y + r[8] * z) >> 12
    p = project(cam, x, y, z)
    if depth <= 0 or p is None:
        return 0
    body = min(256, (cam["h"] * BODY) // depth)
    lo, hi = -margin - GUARD - body, 512 + margin + GUARD + body
    sx, sy = p
    return int(lo <= sx < hi and -GUARD - body <= sy < FRAME_H + GUARD + body and (sx < 0 or sx >= 512))


def candidates(lists, n, joins, k=FRUSTUM_K):
    """joins[0] / joins[1]: (lists, at_start, map) of the path linked at the
    start / end, or None."""
    game = lists[n]
    pos = {}
    for i, v in enumerate(game):
        pos[key(v)] = i
    seen = set(pos)
    out = []
    for j in range(1, k + 1):
        for side in (-1, 1):
            nb = n + side * j
            if 0 <= nb < len(lists):
                lst = lists[nb]
            else:
                jn = joins[0 if nb < 0 else 1]
                if not jn:
                    continue
                jl, at_start, wmap = jn
                idx = -nb if nb < 0 else nb - (len(lists) - 1)
                at = idx if at_start else len(jl) - 1 - idx
                if not 0 <= at < len(jl):
                    continue
                lst = [(wmap[v >> 13] << 13) | (v & 0x1FFF) for v in jl[at] if wmap[v >> 13] < 8]
            anchor = 0
            for v in lst:
                kk = key(v)
                if kk in seen:
                    if kk in pos:
                        anchor = pos[kk]
                    continue
                seen.add(kk)
                out.append((v, anchor))
    return out


def draw(cam, worlds, offsets, lists, n, joins, margin):
    """(model_seen, model_total, merged list, kept count)."""
    game = lists[n]

    def corners(pid):
        w = pid >> 13
        if w >= len(worlds) or worlds[w] is None:
            return None
        return worlds[w].corners(pid, offsets[w])

    seen = total = 0
    for v in game:
        c = corners(v)
        if c is None:
            continue
        total += 1
        seen += classify(cam, c, 0) != OUT
    model = total >= 8 and seen * 2 >= total
    if not model:
        return seen, total, None, 0
    cands = candidates(lists, n, joins)
    budget = min(len(game) + 32, MAX_LIST - len(game))
    keep = []
    for v, anchor in cands:
        ok = False
        if budget > 0:
            c = corners(v)
            if c is not None and classify(cam, c, margin) == WIDE:
                ok = True
                budget -= 1
        keep.append(ok)
    by_anchor = {}
    for (v, anchor), ok in zip(cands, keep):
        if ok:
            by_anchor.setdefault(anchor, []).append(v)
    merged = []
    for p, v in enumerate(game):
        merged.append(v)
        merged.extend(by_anchor.get(p, []))
    return seen, total, merged, sum(keep)
