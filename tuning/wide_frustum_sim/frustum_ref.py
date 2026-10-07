"""Reference for crash2_wide_slst.h's camera test, written apart from the C.

What the hook draws at node n when it can judge the camera:
  - candidates: every polygon the node lists within FRUSTUM_K either side hold
    (past the path's ends, the linked path's, world slots mapped by world
    EID) that the game's list L(n) does not, nearest node first, each with the
    position in L(n) of the last polygon before it in its own list that L(n)
    holds; on a side-on path (kind 3, 8) then the pool's - every polygon the
    other paths of the zone and of its neighbour zones list - after L(n)'s
    last polygon;
  - the model check: most of L(n) must land in the 4:3 frame;
  - kept: candidates reaching into the extra columns (outside 512 wide, inside
    it plus the margin), at most len(L(n)) + 32, in candidate order; never
    one with a corner the GTE cannot place (on forward paths too since
    widescreen part 15), and a pool polygon only wholly in the extra columns
    of one side;
  - the list: L(n), each kept candidate right after its anchor.
On a side-on path, the void cover: per side, how far in from the wide
frame's edge the void enclosed in the extra columns reaches (void_cover).
The projection is the GTE's RTPS in integers, no translation, no squash.
"""
from __future__ import annotations

import math
import struct

import numpy as np

FRUSTUM_K = 20
MAX_LIST = 1520
MAX_CAND = 12288
POOL_MAX = 8192
GUARD = 24
FRAME_H = 216
VC_CELL = 2
VC_STRIP = 16
VC_BAND = 48
VC_OPEN = 50
VC_COLS = 80
VC_ROWS = FRAME_H // VC_CELL
VC_GROW = 1.5
VC_MIN = 4


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


def placeable(cam, x, y, z):
    """In front of the eye and far enough that the GTE's division does not
    saturate (H < 2 SZ)."""
    r = cam["r"]
    m3 = (r[6] * x + r[7] * y + r[8] * z) >> 12
    return m3 > 0 and cam["h"] < 2 * min(m3, 0xFFFF)


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


def side_keep(cam, corners, margin, from_pool):
    """A side-on path's verdict: WIDE with every corner placeable; a pool
    polygon only wholly left or wholly right of the 4:3 columns."""
    lo, hi = -margin - GUARD, 512 + margin + GUARD
    all_l = all_r = all_t = all_b = True
    left = right = 0
    for x, y, z in corners:
        if not placeable(cam, x, y, z):
            return False
        sx, sy = project(cam, x, y, z)
        all_l &= sx < lo
        all_r &= sx >= hi
        all_t &= sy < -GUARD
        all_b &= sy >= FRAME_H + GUARD
        if sx < 0:
            left += 1
        elif sx >= 512:
            right += 1
    if all_l or all_r or all_t or all_b:
        return False
    if not left and not right:
        return False
    return not from_pool or left == len(corners) or right == len(corners)


def fwd_keep(cam, corners, margin, from_pool):
    """A forward path's verdict: WIDE, and - as on side-on paths - every
    corner placeable: behind the eye the GTE's saturated division lands a
    corner near twice its camera offset from the centre, and such a polygon
    was drawn as a wedge up to the whole screen. A pool polygon by the
    side-on pool's rule."""
    if from_pool:
        return side_keep(cam, corners, margin, True)
    return all(placeable(cam, *c) for c in corners) and classify(cam, corners, margin) == WIDE


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


def pool(zone_worlds, paths):
    """A side-on path's pool. paths: [(world EIDs of that path's zone, its node
    lists)] in the order the hook reads them - the zone's other paths, then
    each neighbour zone's (by slot, each zone once), paths in item order.
    Every polygon whose world this zone has, mapped to this zone's slot by
    world EID, first one seen of each, at most POOL_MAX."""
    out, seen = [], set()
    for wl, lists in paths:
        wmap = [zone_worlds.index(e) if e in zone_worlds else 0xFF for e in wl[:8]]
        wmap += [0xFF] * (8 - len(wmap))
        for lst in lists:
            for v in lst:
                if len(out) >= POOL_MAX:
                    return out
                w = wmap[v >> 13]
                if w == 0xFF:
                    continue
                m = (w << 13) | (v & 0x1FFF)
                if key(m) in seen:
                    continue
                seen.add(key(m))
                out.append(m)
    return out


def _vc_tri(cells, s, marg, cols, x, y):
    """Mark side s's cells whose centres the triangle grown by VC_GROW
    covers - every step in float32, in the C's order."""
    f = np.float32
    x = [f(v) for v in x]
    y = [f(v) for v in y]
    area = f(f(x[1] - x[0]) * f(y[2] - y[0])) - f(f(y[1] - y[0]) * f(x[2] - x[0]))
    if f(-0.5) < area < f(0.5):
        return
    nx, ny, nd = [], [], []
    for e in range(3):
        a, b = e, (e + 1) % 3
        ex, ey = f(x[b] - x[a]), f(y[b] - y[a])
        ln = f(math.sqrt(float(f(f(ex * ex) + f(ey * ey)))))
        if not ln > 0:
            return
        k = f(f(1.0 if area > 0 else -1.0) / ln)
        nxe, nye = f(f(-ey) * k), f(ex * k)
        nx.append(nxe)
        ny.append(nye)
        nd.append(f(-f(f(nxe * x[a]) + f(nye * y[a]))))
    x0, x1, y0, y1 = min(x), max(x), min(y), max(y)
    g, cell = f(VC_GROW), f(VC_CELL)
    edge = f(-marg) if s == 0 else f(512 + marg)
    if s == 0:
        c0 = math.floor(f(f(f(x0 - g) - edge) / cell))
        c1 = math.floor(f(f(f(x1 + g) - edge) / cell))
    else:
        c0 = math.floor(f(f(f(edge - x1) - g) / cell))
        c1 = math.floor(f(f(f(edge - x0) + g) / cell))
    r0, r1 = math.floor(f(f(y0 - g) / cell)), math.floor(f(f(y1 + g) / cell))
    c0, c1 = max(c0, 0), min(c1, cols - 1)
    r0, r1 = max(r0, 0), min(r1, VC_ROWS - 1)
    half = f(cell * f(0.5))
    for r in range(r0, r1 + 1):
        py = f(f(f(r) * cell) + half)
        for c in range(c0, c1 + 1):
            if cells[r][c]:
                continue
            if s == 0:
                px = f(f(edge + f(f(c) * cell)) + half)
            else:
                px = f(f(edge - f(f(c) * cell)) - half)
            if all(f(f(f(nx[e] * px) + f(ny[e] * py)) + nd[e]) >= -g for e in range(3)):
                cells[r][c] = 1


def _vc_cells(cam, worlds, offsets, polys, marg):
    """The cells c2sl_vc_scan marks covered, per side: (cells, cols, mcols)."""
    cols = min((marg + VC_BAND + VC_CELL - 1) // VC_CELL, VC_COLS)
    mcols = (marg + VC_CELL - 1) // VC_CELL
    cells = [[[0] * cols for _ in range(VC_ROWS)] for _ in range(2)]
    lo, hi = -marg, 512 + marg
    for pid in polys:
        w = pid >> 13
        if w >= len(worlds) or worlds[w] is None:
            continue
        cs = worlds[w].corners(pid, offsets[w])
        if not cs:
            continue
        pts = [project(cam, *c) for c in cs]
        if any(p is None for p in pts):
            continue
        sx = [p[0] for p in pts]
        sy = [p[1] for p in pts]
        if all(v < lo for v in sx) or all(v >= hi for v in sx) or all(v < 0 for v in sy) or \
                all(v >= 217 for v in sy):
            continue
        near_l = any(v < VC_BAND + 2 for v in sx)
        near_r = any(v >= 512 - VC_BAND - 2 for v in sx)
        tris = [(0, 1, 2), (1, 2, 3)] if len(cs) == 4 else [(0, 1, 2)]
        for t in tris:
            tx = [sx[i] for i in t]
            ty = [sy[i] for i in t]
            if max(tx) - min(tx) > 1023 or max(ty) - min(ty) > 511:
                continue
            if near_l and min(tx) < VC_BAND + 2:
                _vc_tri(cells[0], 0, marg, cols, tx, ty)
            if near_r and max(tx) >= 512 - VC_BAND - 2:
                _vc_tri(cells[1], 1, marg, cols, tx, ty)
    return cells, cols, mcols


def _vc_open(g, cols, mcols):
    """A dark, open scene on this side: its band of the 4:3 frame shows more
    than 1/VC_OPEN void of its own."""
    band_void = sum(1 for r in range(VC_ROWS) for c in range(mcols, cols) if not g[r][c])
    return band_void * VC_OPEN > VC_ROWS * (cols - mcols)


def band_open(cam, worlds, offsets, polys, marg):
    """[left, right]: whether that side is a dark, open scene for these
    polygons - the test the void cover skips a side by (c2sl_vc_open)."""
    cells, cols, mcols = _vc_cells(cam, worlds, offsets, polys, marg)
    return [_vc_open(cells[s], cols, mcols) for s in range(2)]


def void_cover(cam, worlds, offsets, polys, marg):
    """[left, right]: how many px in from the wide frame's edge the cover
    reaches (crash2_wide_slst.h's c2sl_vc_scan)."""
    cells, cols, mcols = _vc_cells(cam, worlds, offsets, polys, marg)
    scols = min(mcols + VC_STRIP // VC_CELL, cols)
    out = []
    for s in range(2):
        g = cells[s]
        if _vc_open(g, cols, mcols):
            out.append(0)                       # a dark, open scene: no cover
            continue
        queue = [(r, c) for r in range(VC_ROWS) for c in range(mcols, scols) if not g[r][c]]
        for r, c in queue:
            g[r][c] = 2
        head = 0
        while head < len(queue):
            r, c = queue[head]
            head += 1
            for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                if 0 <= rr < VC_ROWS and 0 <= cc < scols and not g[rr][cc]:
                    g[rr][cc] = 2
                    queue.append((rr, cc))
        edge = [(r, 0) for r in range(VC_ROWS) if not g[r][0]]
        for r, c in edge:
            g[r][c] = 3
        head = 0
        while head < len(edge):
            r, c = edge[head]
            head += 1
            for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                if 0 <= rr < VC_ROWS and 0 <= cc < mcols and not g[rr][cc]:
                    g[rr][cc] = 3
                    edge.append((rr, cc))
        inner = max((c for _, c in edge), default=-1)
        want = (inner + 1) * VC_CELL + 1 if len(edge) >= VC_MIN else 0
        out.append(min(want, marg))
    return out


def candidates(lists, n, joins, k=FRUSTUM_K, pool_ids=()):
    """joins[0] / joins[1]: (lists, at_start, map) of the path linked at the
    start / end, or None. (polygon, anchor, from the pool) each."""
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
                if len(out) >= MAX_CAND:
                    break
                seen.add(kk)
                out.append((v, anchor, False))
    for v in pool_ids:
        if len(out) >= MAX_CAND:
            break
        if key(v) in seen:
            continue
        seen.add(key(v))
        out.append((v, len(game) - 1, True))
    return out


def draw(cam, worlds, offsets, lists, n, joins, margin, side=False, pool_ids=()):
    """(model_seen, model_total, merged list, kept count). side: a side-on
    path (kind 3 or 8) with its pool."""
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
    cands = candidates(lists, n, joins, pool_ids=pool_ids if side else ())
    budget = min(len(game) + 32, MAX_LIST - len(game))
    keep = []
    for v, anchor, from_pool in cands:
        ok = False
        if budget > 0:
            c = corners(v)
            if c is not None and (side_keep if side else fwd_keep)(cam, c, margin, from_pool):
                ok = True
                budget -= 1
        keep.append(ok)
    by_anchor = {}
    for (v, anchor, _), ok in zip(cands, keep):
        if ok:
            by_anchor.setdefault(anchor, []).append(v)
    merged = []
    for p, v in enumerate(game):
        merged.append(v)
        merged.extend(by_anchor.get(p, []))
    return seen, total, merged, sum(keep)
