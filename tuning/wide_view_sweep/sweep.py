"""Every view of the game, with the game's own camera: what the widescreen
scenery hook draws in the extra columns, and what it leaves black.

    sh tuning/wide_view_sweep/build.sh            (raster.dll)
    python tuning/wide_view_sweep/sweep.py [--levels S0000020.NSF,...]
        [--step 4] [--nodes all|part13] [--jobs N] [--variants hook,0054]
        [--json FILE] [--png N] [--verify N]

For every camera path (wide_frustum_sim/export.py level_paths) at every STEP-th
node and the last, the game's camera at rest there (crash2_wide_geom.h: the path point, the
node's angles from the item after the path, H from 0x130) with the 16:9
margin, 85 px a side. Three pictures are rasterised by raster.c, painter's
order far to near by each polygon's mean depth (the ordering table), the
GPU's 1023 x 511 limit applied and polygons with a corner behind the eye left
out:

  game   the game's own list L(n);
  drawn  what crash2_wide_slst.h draws: the merged list (the same rules as
         frustum_ref.draw, with the reasons kept), and on a side-on path the
         void cover over it (frustum_ref.void_cover);
  ref    every polygon of the zone's worlds and of its neighbour zones'.

Per view, in pixels:

  in43_changed  of the 4:3 frame, where drawn differs from game - the hook's
                promise is to leave it alone;
  in the extra columns -
    covered     under the void cover;
    gap         ref has geometry there, nothing is drawn;
    void        no geometry anywhere;
    wrong       drawn shows a polygon farther than ref's nearest there: see
                through something;
    ok          the rest;
  wedges        added polygons with a corner the GTE cannot place.

Every gap pixel is put down to why its polygon is not drawn: in the game's
list (culled here), a candidate refused (outside the wide frame, inside the
4:3 one, by the side-on rules, or by the budget), listed only by another path
of the zone or of a neighbour zone, in no list at all, or in a neighbour's
world the zone does not have (which the renderer cannot draw).

--variants picks the rules for forward paths (kind other than 3 and 8), one
pass for all: "hook" is crash2_wide_slst.h as it is, "0054" the rules before
widescreen part 15, the others candidates (VARIANTS). --nodes part13 samples
the nodes parts 13 and 14 measured. --verify N checks the first N views' hook
lists against frustum_ref.draw. JSON and PNGs are game data: out/ is
gitignored.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import struct
import sys
import time
import zlib
from concurrent.futures import ProcessPoolExecutor

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tuning", "wide_frustum_sim"))
sys.path.insert(0, os.path.join(ROOT, "tuning", "wide_slst_sim"))
sys.path.insert(0, os.path.join(ROOT, "_build"))

import frustum_ref as F  # noqa: E402


def _load(name, path):
    """wide_slst_sim has an export.py too: load wide_frustum_sim's by path."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


E = _load("frustum_export", os.path.join(ROOT, "tuning", "wide_frustum_sim", "export.py"))

MARGIN = 85
W = 512 + 2 * MARGIN
H = F.FRAME_H
GUARD = F.GUARD
OUT, IN43, WIDE = F.OUT, F.IN43, F.WIDE
FOREIGN = 1 << 16                 # ids past the 16-bit keys: unaddressable

CATS = ("in_list", "kept_culled", "refused_out", "refused_in43", "refused_side",
        "refused_wedge", "refused_band", "refused_budget", "other_path", "neighbour_path",
        "no_list", "unaddressable")

# Forward-path rules (kind other than 3 and 8), read by draw_explained.
#   hook   crash2_wide_slst.h as it is (frustum_ref.draw): an addition needs
#          every corner placeable, as on side-on paths;
#   0054   before widescreen part 15: no such rule on forward paths;
#   pool   the hook, and the side-on pool on forward paths too, under its
#          rule (wholly in the extra columns of one side, every corner
#          placeable);
#   pool_band  the pool, but not on a side whose band of the 4:3 frame is a
#          dark, open scene for what is drawn without it (the void cover's
#          test, frustum_ref.band_open).
VARIANTS = {
    "hook": {},
    "0054": {"legacy": True},
    "pool": {"pool": True},
    "pool_band": {"pool": True, "band": True},
}


# --- level geometry, vectorised ---------------------------------------------

def s16(a):
    return ((a & 0xFFFF) ^ 0x8000) - 0x8000


class Geo:
    """A WGEO entry as arrays (frustum_ref.World, all at once)."""

    def __init__(self, items):
        info = items[0]
        self.origin = struct.unpack_from("<3i", info, 0)
        nv, nt, nq = struct.unpack_from("<3i", info, 16)
        v, t, q = items[1], items[2], items[3]
        xy = np.frombuffer(v, "<u4", nv)[::-1].astype(np.int64)
        z = np.frombuffer(v, "<u2", nv, 4 * nv).astype(np.int64)
        self.fx, self.fy, self.fz = xy & 0xFFF0, (xy >> 16) & 0xFFF0, z & 0xFFF0
        tw = np.frombuffer(t, "<u4", nt)[::-1].astype(np.int64)
        th = np.frombuffer(t, "<u2", nt, 4 * nt).astype(np.int64)
        self.tri = np.stack([tw >> 20, (tw >> 8) & 0xFFF, (th >> 4) & 0xFFF], 1) \
            if nt else np.zeros((0, 3), np.int64)
        qw = np.frombuffer(q, "<u4", 2 * nq).astype(np.int64).reshape(nq, 2)
        self.quad = np.stack([qw[:, 0] >> 20, (qw[:, 0] >> 8) & 0xFFF,
                              qw[:, 1] >> 20, (qw[:, 1] >> 8) & 0xFFF], 1) \
            if nq else np.zeros((0, 4), np.int64)
        self.nv, self.nt, self.nq = nv, nt, nq
        self.tri_ok = (self.tri < nv).all(1)
        self.quad_ok = (self.quad < nv).all(1)


def geo(cache, ents, eid):
    if eid not in cache:
        try:
            cache[eid] = Geo(ents[eid][1]) if ents.get(eid, (0,))[0] == 3 else None
        except (ValueError, IndexError, struct.error):
            cache[eid] = None
    return cache[eid]


def offsets_for(g, cam_pos):
    cx, cy, cz = cam_pos
    return ((g.origin[0] - cx) & 0xFFFFFFFF, ((g.origin[1] - cy) & 0xFFFFFFFF) & 0xFFFE,
            (g.origin[2] - cz) & 0xFFFFFFFF)


def project(g, off, cam):
    """Every vertex as the GTE's RTPS leaves it: px, py, in front, placeable,
    SZ. In front of the eye this is frustum_ref.project. Behind it SZ clamps
    to 0, the division saturates and the corner lands at the screen's edge -
    and the world renderer has no near test (0x80042420 RTPT straight into
    the screen test at 0x800424D8 and the ordering-table slot, 1904 - (SZ1 +
    SZ2 + SZ3) / 32), so such a polygon is drawn, as a wedge, unless the GPU's
    size limit drops it."""
    x, y, z = s16(g.fx + off[0]), s16(g.fy + off[1]), s16(g.fz + off[2])
    r, h = cam["r"], cam["h"]
    m3 = (r[6] * x + r[7] * y + r[8] * z) >> 12
    m1 = np.clip((r[0] * x + r[1] * y + r[2] * z) >> 12, -0x8000, 0x7FFF)
    m2 = np.clip((r[3] * x + r[4] * y + r[5] * z) >> 12, -0x8000, 0x7FFF)
    sz = np.clip(m3, 0, 0xFFFF)
    safe = np.where(sz > 0, sz, 1)
    q = np.where(h >= 2 * sz, 0x1FFFF, np.minimum(0x1FFFF, ((h << 16) + safe // 2) // safe))
    px = np.clip((cam["ofx"] + m1 * q) >> 16, -1024, 1023)
    py = np.clip((cam["ofy"] + m2 * q) >> 16, -1024, 1023)
    front = m3 > 0
    return px, py, front, front & (h < 2 * sz), sz


class Corners:
    """Per polygon, four corners (a triangle repeats its third): screen x, y,
    in front, placeable, sz; q4 for quads; ok where the polygon decodes."""

    def __init__(self, n):
        self.x = np.zeros((n, 4), np.int64)
        self.y = np.zeros((n, 4), np.int64)
        self.fr = np.zeros((n, 4), bool)
        self.pl = np.zeros((n, 4), bool)
        self.sz = np.zeros((n, 4), np.int64)
        self.q4 = np.zeros(n, bool)
        self.ok = np.zeros(n, bool)

    def put(self, sel, proj, vid, quad):
        px, py, fr, pl, sz = proj
        if not quad:
            vid = np.concatenate([vid, vid[:, 2:3]], 1)
        self.x[sel], self.y[sel] = px[vid], py[vid]
        self.fr[sel], self.pl[sel], self.sz[sel] = fr[vid], pl[vid], sz[vid]
        self.q4[sel] = quad
        self.ok[sel] = True


class View:
    def __init__(self, geos, cam_pos, cam):
        self.geos = geos
        self.cam = cam
        self.proj = [project(g, offsets_for(g, cam_pos), cam) if g else None for g in geos]

    def corners(self, vals):
        vals = np.asarray(vals, np.int64)
        c = Corners(len(vals))
        if not len(vals):
            return c
        slot, quad, idx = vals >> 13, (vals & 0x1800) != 0, vals & 0x7FF
        for s in np.unique(slot):
            if s >= len(self.geos) or self.geos[s] is None:
                continue
            g, p = self.geos[s], self.proj[s]
            for isq in (False, True):
                sel = np.nonzero((slot == s) & (quad == isq))[0]
                if not len(sel):
                    continue
                ii = idx[sel]
                lim, table, okt = (g.nq, g.quad, g.quad_ok) if isq else (g.nt, g.tri, g.tri_ok)
                keep = ii < lim
                sel, ii = sel[keep], ii[keep]
                keep = okt[ii]
                sel, ii = sel[keep], ii[keep]
                if len(sel):
                    c.put(sel, p, table[ii], isq)
        return c


def key_arr(vals):
    vals = np.asarray(vals, np.int64)
    return np.where(vals & 0x1800, vals | 0x1800, vals)


# --- the hook's rules, vectorised (frustum_ref.classify / side_keep / draw) ---

def classify(c, margin):
    lo, hi = -margin - GUARD, 512 + margin + GUARD
    fr = c.fr

    def allf(cond):
        return np.where(fr, cond, True).all(1)

    all_any = allf(c.x < lo) | allf(c.x >= hi) | allf(c.y < -GUARD) | allf(c.y >= H + GUARD)
    aside = (fr & ((c.x < 0) | (c.x >= 512))).any(1)
    res = np.where(aside, WIDE, IN43)
    res = np.where(fr.all(1) & all_any, OUT, res)
    return np.where(fr.any(1), res, OUT)


def side_keep(c, margin, from_pool):
    lo, hi = -margin - GUARD, 512 + margin + GUARD
    real = np.ones(c.x.shape, bool)
    real[~c.q4, 3] = False
    n = 3 + c.q4
    all_any = (c.x < lo).all(1) | (c.x >= hi).all(1) | (c.y < -GUARD).all(1) | \
        (c.y >= H + GUARD).all(1)
    left = ((c.x < 0) & real).sum(1)
    right = ((c.x >= 512) & real).sum(1)
    return c.pl.all(1) & ~all_any & ((left > 0) | (right > 0)) & \
        (~from_pool | (left == n) | (right == n))


def draw_explained(view, lists, n, joins, margin, side, pool_ids, opts, band_fn=None):
    """frustum_ref.draw, with each candidate's fate: (model ok, merged list or
    None, kept count, {candidate key: reason}, added values). band_fn(polys)
    -> [left, right] open: for the "band" rule."""
    game = lists[n]
    gc = view.corners(game)
    total = int(gc.ok.sum())
    seen = int((gc.ok & (classify(gc, 0) != OUT)).sum())
    if not (total >= 8 and seen * 2 >= total):
        return False, None, 0, {}, []
    use_pool = side or opts.get("pool")
    cands = F.candidates(lists, n, joins, pool_ids=pool_ids if use_pool else ())
    vals = np.array([v for v, _, _ in cands], np.int64)
    from_pool = np.array([p for _, _, p in cands], bool)
    cc = view.corners(vals)
    if side:
        ok = cc.ok & side_keep(cc, margin, from_pool)
        why = np.where(cc.ok, "refused_side", "refused_out")
    else:
        cls = classify(cc, margin)
        ok = cc.ok & (cls == WIDE)
        why = np.where(cls == IN43, "refused_in43", "refused_out")
        why = np.where(cc.ok, why, "refused_out")
        if not opts.get("legacy"):
            wedge = ok & ~cc.pl.all(1)
            ok &= ~wedge
            why = np.where(wedge, "refused_wedge", why)
        if len(vals) and from_pool.any():
            pk = cc.ok & side_keep(cc, margin, np.ones(len(vals), bool))
            ok = np.where(from_pool, pk, ok)
            why = np.where(from_pool & ~pk, "refused_side", why)
            if opts.get("band") and band_fn is not None:
                # No pool on a side whose band of the 4:3 frame is a dark,
                # open scene for what is drawn without it: there the game
                # shows darkness on purpose (S0000009's tube).
                budget0 = max(0, min(len(game) + 32, F.MAX_LIST - len(game)))
                pre = ok & ~from_pool
                pre &= np.cumsum(pre) <= budget0
                opened = band_fn(list(game) + vals[pre].tolist())
                real = np.ones(cc.x.shape, bool)
                real[~cc.q4, 3] = False
                on_left = ((cc.x < 0) | ~real).all(1)
                shut = from_pool & ok & ((on_left & opened[0]) | (~on_left & opened[1]))
                ok &= ~shut
                why = np.where(shut, "refused_band", why)
    budget = max(0, min(len(game) + 32, F.MAX_LIST - len(game)))
    kept = ok & (np.cumsum(ok) <= budget)
    why = np.where(kept, "kept", np.where(ok, "refused_budget", why))
    reasons = {}
    for v, r in zip(key_arr(vals).tolist(), why.tolist()):
        reasons.setdefault(v, r)
    by_anchor = {}
    added = []
    for (v, anchor, _), k in zip(cands, kept.tolist()):
        if k:
            by_anchor.setdefault(anchor, []).append(v)
            added.append(v)
    merged = []
    for p, v in enumerate(game):
        merged.append(v)
        merged.extend(by_anchor.get(p, []))
    return True, merged, len(added), reasons, added


# --- rasterising ---------------------------------------------------------------

_DLL = None


def dll():
    global _DLL
    if _DLL is None:
        _DLL = ctypes.CDLL(os.path.join(HERE, "raster.dll"))
    return _DLL


def triangles(c, ids, placeable=False):
    """Painter-sorted triangles of the polygons in c (ids per polygon):
    (n x 6 float32 in buffer pixels, depth, id). placeable: only polygons
    whose every corner the GTE can place (the reference); otherwise what the
    hardware draws, wedges included."""
    use = c.ok & (c.pl.all(1) if placeable else True)
    if not use.any():
        return np.zeros((0, 6), np.float32), np.zeros(0, np.int32), np.zeros(0, np.int32)
    x, y, sz, q4 = c.x[use], c.y[use], c.sz[use], c.q4[use]
    pid = np.asarray(ids, np.int64)[use]
    nc = np.where(q4, 4, 3)
    depth = np.where(q4, sz.sum(1), sz[:, :3].sum(1)) // nc
    order = np.arange(len(x))
    t1 = np.stack([x[:, 0], y[:, 0], x[:, 1], y[:, 1], x[:, 2], y[:, 2]], 1)
    t2 = np.stack([x[:, 1], y[:, 1], x[:, 2], y[:, 2], x[:, 3], y[:, 3]], 1)[q4]
    tris = np.concatenate([t1, t2])
    dep = np.concatenate([depth, depth[q4]])
    tid = np.concatenate([pid, pid[q4]])
    seq = np.concatenate([order, order[q4]])
    xs, ys = tris[:, 0::2], tris[:, 1::2]
    fits = (xs.max(1) - xs.min(1) <= 1023) & (ys.max(1) - ys.min(1) <= 511)
    tris, dep, tid, seq = tris[fits], dep[fits], tid[fits], seq[fits]
    o = np.lexsort((seq, -dep))                 # far first; list order breaks ties
    tris = tris[o].astype(np.float32)
    tris[:, 0::2] += MARGIN
    return np.ascontiguousarray(tris), dep[o].astype(np.int32), tid[o].astype(np.int32)


def rasterise(parts):
    """parts: [(tris, depth, id)] drawn in turn. -> (id, depth) buffers."""
    oid = np.full(W * H, -1, np.int32)
    odep = np.full(W * H, 1 << 30, np.int32)
    P = lambda a: a.ctypes.data_as(ctypes.c_void_p)  # noqa: E731
    for tris, dep, tid in parts:
        if len(tris):
            dll().raster(len(tris), P(tris), P(dep), P(tid), W, H, P(oid), P(odep))
    return oid.reshape(H, W), odep.reshape(H, W)


# --- one level ---------------------------------------------------------------

def zone_path_keys(ents, path, slcache, zcache):
    """{(zone, item): keys} for every path of the zone and of its neighbour
    zones, every node, mapped to this zone's world slots - no caps. Cached
    per zone: the mapping depends only on the zone's worlds."""
    if path.zeid in zcache:
        return zcache[path.zeid]
    out = {}
    hdr = ents[path.zeid][1][0]
    nn = struct.unpack_from("<I", hdr, 0x190)[0]
    zones = [path.zeid] + [z for z in (struct.unpack_from("<%dI" % nn, hdr, 0x194) if nn <= 16 else ())
                           if ents.get(z, (0,))[0] == 7 and z != path.zeid]
    for z in dict.fromkeys(zones):
        zi = ents[z][1]
        c0, cn = struct.unpack_from("<2I", zi[0], 0x184)
        if c0 > 4096 or cn > 3 * 4096:
            continue
        wmap = [path.wl_eids.index(e) if e in path.wl_eids else 0xFF for e in E.zone_worlds(zi)[:8]]
        wmap += [0xFF] * (8 - len(wmap))
        for k in range(c0, c0 + cn, 3):
            if k >= len(zi):
                break
            tb = E.table(zi[k])
            if not tb or 0x103 not in tb:
                continue
            se = E.rows(zi[k], tb[0x103])[0][1][0]
            if ents.get(se, (0,))[0] != 4:
                continue
            if se not in slcache:
                slcache[se] = E.node_lists(ents[se][1], True)
            ll = slcache[se]
            if ll is None:
                continue
            keys = set()
            for lst in ll:
                for v in lst:
                    w = wmap[v >> 13]
                    if w != 0xFF:
                        keys.add(F.key((w << 13) | (v & 0x1FFF)))
            out[(z, k)] = keys
    zcache[path.zeid] = out
    return out


def tagged_pool_sets(ents, path, slcache, zcache):
    """Keys listed by the zone's other paths, and by its neighbour zones'
    paths: where a gap's polygon could have come from."""
    same, neigh = set(), set()
    for (z, k), keys in zone_path_keys(ents, path, slcache, zcache).items():
        if z == path.zeid:
            if k != path.k:
                same |= keys
        else:
            neigh |= keys
    return same, neigh


def ref_parts(ref_geos):
    """Every polygon of the zone's worlds (ids as list keys) and of neighbour
    worlds the zone lacks (ids past FOREIGN)."""
    parts = []
    for slot, (g, proj, zone_slot) in enumerate(ref_geos):
        for quad in (False, True):
            table, okt = (g.quad, g.quad_ok) if quad else (g.tri, g.tri_ok)
            idx = np.nonzero(okt)[0]
            if not len(idx):
                continue
            c = Corners(len(idx))
            c.put(np.arange(len(idx)), proj, table[idx], quad)
            if zone_slot is not None:
                ids = (zone_slot << 13) | (0x1800 if quad else 0) | idx
                ids = np.where(idx < 0x800, ids, FOREIGN + (slot << 13) + idx)
            else:
                ids = FOREIGN + (slot << 13) + (0x1000 if quad else 0) + idx
            parts.append((c, ids))
    return parts


def merge_parts(parts):
    """One painter's order across several polygon sets."""
    ts = [triangles(c, ids, placeable=True) for c, ids in parts]
    ts = [t for t in ts if len(t[0])]
    if not ts:
        return [(np.zeros((0, 6), np.float32), np.zeros(0, np.int32), np.zeros(0, np.int32))]
    tris = np.concatenate([t[0] for t in ts])
    dep = np.concatenate([t[1] for t in ts])
    tid = np.concatenate([t[2] for t in ts])
    o = np.argsort(-dep, kind="stable")
    return [(np.ascontiguousarray(tris[o]), np.ascontiguousarray(dep[o]),
             np.ascontiguousarray(tid[o]))]


def spikes(c, vals):
    """Keys of the polygons in c with a corner the GTE cannot place."""
    return np.unique(key_arr(vals)[c.ok & ~c.pl.all(1)]) if len(vals) else np.zeros(0, np.int64)


def view_pictures(ents, gcache, path, n, variants, pools):
    """One view: the game's picture and the references, shared, and per
    variant (name -> rules) what the hook draws."""
    cam_pos = path.pos[n]
    r = E.camera_rotation(*path.angles[2 * n])
    cam = {"r": r, "h": E.path_h(path.it, path.tb, n), "ofx": 256 << 16, "ofy": 108 << 16}
    geos = [geo(gcache, ents, e) for e in path.wl_eids]
    view = View(geos, cam_pos, cam)
    game = path.lists[n]
    gc = view.corners(game)
    g_id, g_dep = rasterise([triangles(gc, key_arr(game))])
    # ref: the zone's worlds; all: and the neighbours' worlds it does not have
    ref_geos = [(g, view.proj[i], i) for i, g in enumerate(geos) if g is not None]
    seen_eids = set(path.wl_eids)
    for z in path.neigh:
        if ents.get(z, (0,))[0] != 7:
            continue
        for e in E.zone_worlds(ents[z][1]):
            if e in seen_eids:
                continue
            seen_eids.add(e)
            g = geo(gcache, ents, e)
            if g is not None:
                ref_geos.append((g, project(g, offsets_for(g, cam_pos), cam), None))
    nz = sum(1 for _, _, zs in ref_geos if zs is not None)
    r_id, r_dep = rasterise(merge_parts(ref_parts(ref_geos[:nz])))
    a_id = rasterise(merge_parts(ref_parts(ref_geos)))[0] if len(ref_geos) > nz else r_id
    pic = {"cam": cam, "game": (g_id, g_dep), "game_spikes": spikes(gc, game),
           "ref": (r_id, r_dep), "all": a_id, "var": {}}
    drawn_cache = {}
    worlds = None
    fw = {}

    def band_fn(polys):
        if not fw:
            fw["w"] = [F.World(ents[e][1]) if ents.get(e, (0,))[0] == 3 else None
                       for e in path.wl_eids]
            fw["o"] = [offsets_for(g, cam_pos) if g else (0, 0, 0) for g in geos]
        return F.band_open(cam, fw["w"], fw["o"], polys, MARGIN)

    for name, opts in variants.items():
        model, merged, kept, reasons, added = draw_explained(
            view, path.lists, n, path.joins, MARGIN, path.side, pools.get("side_pool", []), opts,
            band_fn)
        sig = tuple(merged) if merged is not None else None
        if sig in drawn_cache:
            drawn, dspikes, cover = drawn_cache[sig]
        elif merged is None:
            drawn, dspikes, cover = (g_id, g_dep), pic["game_spikes"], [0, 0]
        else:
            dc = view.corners(merged)
            drawn = rasterise([triangles(dc, key_arr(merged))])
            dspikes = spikes(dc, merged)
            cover = [0, 0]
            if path.side:
                if worlds is None:
                    worlds = [F.World(ents[e][1]) for e in path.wl_eids]
                offs = [offsets_for(g, cam_pos) if g else (0, 0, 0) for g in geos]
                cover = F.void_cover(cam, worlds, offs, merged, MARGIN)
        drawn_cache[sig] = (drawn, dspikes, cover)
        wedges = 0
        if added:
            ac = view.corners(added)
            wedges = int((ac.ok & ~ac.pl.all(1)).sum())
        pic["var"][name] = {"model": model, "merged": merged, "kept": kept, "reasons": reasons,
                            "cover": cover, "wedges": wedges, "drawn": drawn,
                            "drawn_spikes": dspikes}
    return pic


def measure(pic, var, game_keys, pools):
    g_id, g_dep = pic["game"]
    d_id, d_dep = var["drawn"]
    r_id, r_dep = pic["ref"]
    extra = np.zeros((H, W), bool)
    extra[:, :MARGIN] = True
    extra[:, MARGIN + 512:] = True
    cov = np.zeros((H, W), bool)
    cl, cr = var["cover"]
    if cl:
        cov[:, :cl] = True
    if cr:
        cov[:, W - cr:] = True
    e = extra & ~cov
    none = d_id < 0
    d_spike = np.isin(d_id, var["drawn_spikes"])
    g_spike = np.isin(g_id, pic["game_spikes"])
    gap = e & none & (r_id >= 0)
    elsewhere = e & none & (r_id < 0) & (pic["all"] >= 0)
    void = e & none & (pic["all"] < 0)
    spike = e & d_spike
    wrong = e & ~none & ~d_spike & (d_id != r_id) & (d_dep > r_dep)
    in43 = ~extra
    g_wrong = in43 & (g_id >= 0) & ~g_spike & (g_id != r_id) & (g_dep > r_dep)
    over = in43 & (g_id >= 0) & (d_id != g_id)
    out = {
        "extra_px": int(extra.sum()), "in43_px": int(in43.sum()),
        "in43_changed": int((in43 & (g_id != d_id)).sum()),
        "in43_fill": int((in43 & (g_id < 0) & (d_id >= 0)).sum()),
        "in43_over": int(over.sum()),
        "in43_over_ref": int((over & (d_id == r_id)).sum()),
        "covered": int((extra & cov).sum()), "gap": int(gap.sum()), "void": int(void.sum()),
        "elsewhere": int(elsewhere.sum()),
        "spike": int(spike.sum()), "wrong": int(wrong.sum()),
        "ok": int((e & ~none & ~d_spike & ~wrong).sum()),
        "game_black": int((extra & (g_id < 0)).sum()),
        "in43_game_wrong": int(g_wrong.sum()),
        "in43_game_spike": int((in43 & g_spike).sum()),
        "in43_drawn_spike": int((in43 & d_spike).sum()),
        "model": var["model"], "kept": var["kept"], "wedges": var["wedges"], "cover": var["cover"],
    }
    attr = dict.fromkeys(CATS, 0)
    if gap.any():
        ids, counts = np.unique(r_id[gap], return_counts=True)
        same, neigh = pools.get("sets") or (set(), set())
        for i, cnt in zip(ids.tolist(), counts.tolist()):
            if i >= FOREIGN:
                cat = "unaddressable"
            elif i in game_keys:
                cat = "in_list"
            elif i in var["reasons"]:
                cat = var["reasons"][i]
                cat = "kept_culled" if cat == "kept" else cat
            elif i in same:
                cat = "other_path"
            elif i in neigh:
                cat = "neighbour_path"
            else:
                cat = "no_list"
            attr[cat] += cnt
    out["gap_by"] = attr
    return out


def view_nodes(count, step, nodes):
    """part13: 1, 1 + STEP, ... short of the last node - the selection NOTES
    parts 13 and 14 measured (3,069 side-on views). all: 0, STEP, ... and the
    last node, since a camera arriving on a path starts at an end."""
    if nodes == "part13":
        return list(range(1, count - 1, step))
    out = list(range(0, count, step))
    if out[-1] != count - 1:
        out.append(count - 1)
    return out


def sweep_level(job):
    name, nsf_dir, step, names, verify, only, nodes = job
    import nsf as N
    N.OUT = nsf_dir
    data = open(os.path.join(nsf_dir, name), "rb").read()
    ents = {eid: (t, items) for eid, t, items in N.entries(data)}
    variants = {v: VARIANTS[v] for v in names}
    any_pool = any(o.get("pool") for o in variants.values())
    gcache, slcache, zcache = {}, {}, {}
    views, checked = [], 0
    for path in E.level_paths(ents):
        pools = {}
        if path.side or any_pool:
            srcs, _ = E.pool_sources(ents, path.zeid, path.k)
            pools["side_pool"] = F.pool(path.wl_eids, [(w, ll) for w, ll, _ in srcs])
        pools["sets"] = tagged_pool_sets(ents, path, slcache, zcache)
        for n in (view_nodes(len(path.pos), step, nodes) if not only else range(len(path.pos))):
            if only and (N.eid_name(path.zeid), path.k, n) not in only:
                continue
            pic = view_pictures(ents, gcache, path, n, variants, pools)
            if checked < verify and "hook" in variants:
                checked += 1
                worlds = [F.World(ents[e][1]) if ents.get(e, (0,))[0] == 3 else None
                          for e in path.wl_eids]
                offs = [offsets_for(w, path.pos[n]) if w else (0, 0, 0) for w in worlds]
                ref = F.draw(pic["cam"], worlds, offs, path.lists, n, path.joins, MARGIN,
                             path.side, pools.get("side_pool", []))[2]
                assert ref == pic["var"]["hook"]["merged"], \
                    "draw_explained differs from frustum_ref.draw"
            game = path.lists[n]
            keys = set(key_arr(game).tolist())
            rec = {
                "level": name, "zone": N.eid_name(path.zeid), "item": path.k, "kind": path.kind,
                "side": path.side, "node": n, "nodes": len(path.pos),
                "listed": int(sum(1 for v in game if (v >> 13) < len(path.wl_eids))),
                "m": {v: measure(pic, pic["var"][v], keys, pools) for v in variants},
            }
            if only:
                rec["_pic"] = {"game": pic["game"], "ref": pic["ref"],
                               "drawn": {v: pic["var"][v]["drawn"] for v in variants}}
            views.append(rec)
    return views


# --- reporting -----------------------------------------------------------------

def pct(a, b):
    return 100.0 * a / b if b else 0.0


def summary(views, label, name):
    vs = [v["m"][name] for v in views if v["m"][name]["model"]]
    if not views:
        return
    ex = sum(v["extra_px"] for v in vs)
    i43 = sum(v["in43_px"] for v in vs)
    tot = lambda k: sum(v[k] for v in vs)  # noqa: E731
    gap = tot("gap")
    print("%s [%s]: %d views (%d where the model fails, not counted)"
          % (label, name, len(views), len(views) - len(vs)))
    black = gap + tot("elsewhere") + tot("void")
    print("  extra columns: gap %.2f%%  only in a neighbour's worlds %.2f%%  void %.2f%%  black %.2f%%  "
          "covered %.2f%%  wedge %.2f%%  wrong %.2f%%  (the game's list alone: black %.2f%%)"
          % (pct(gap, ex), pct(tot("elsewhere"), ex), pct(tot("void"), ex), pct(black, ex),
             pct(tot("covered"), ex), pct(tot("spike"), ex), pct(tot("wrong"), ex),
             pct(tot("game_black"), ex)))
    print("  4:3 frame: changed %.3f%% (holes filled %.3f%%, drawn over the game's %.3f%%, of which the "
          "nearest polygon there %.3f%%), wedges %.3f%% (the game's own %.3f%%); the game's list vs "
          "every polygon: %.2f%% farther"
          % (pct(tot("in43_changed"), i43), pct(tot("in43_fill"), i43), pct(tot("in43_over"), i43),
             pct(tot("in43_over_ref"), i43), pct(tot("in43_drawn_spike"), i43),
             pct(tot("in43_game_spike"), i43), pct(tot("in43_game_wrong"), i43)))
    print("  added polygons %d, of them wedges %d" % (tot("kept"), tot("wedges")))
    by = {c: sum(v["gap_by"][c] for v in vs) for c in CATS}
    print("  gap by cause: " + ", ".join("%s %.1f%%" % (c, pct(by[c], gap)) for c in CATS if by[c]))


def per_level(views, label, names):
    print("%s, per level: gap / wedge / wrong %% of the extra columns, 4:3 changed %%, per variant"
          % label)
    print("  level     views  " + "  ".join("%-27s" % n for n in names))
    for lv in sorted({v["level"] for v in views}):
        row = []
        cnt = 0
        for name in names:
            vs = [v["m"][name] for v in views if v["level"] == lv and v["m"][name]["model"]]
            cnt = max(cnt, len(vs))
            ex = sum(v["extra_px"] for v in vs)
            i43 = sum(v["in43_px"] for v in vs)
            row.append("%5.2f %5.2f %5.2f %6.3f   " % (
                pct(sum(v["gap"] for v in vs), ex), pct(sum(v["spike"] for v in vs), ex),
                pct(sum(v["wrong"] for v in vs), ex), pct(sum(v["in43_changed"] for v in vs), i43)))
        print("  %s  %5d  %s" % (lv[:-4], cnt, "".join(row)))


def png(path, rgb):
    h, w, _ = rgb.shape
    raw = b"".join(b"\0" + rgb[y].astype(np.uint8).tobytes() for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def colours(ids):
    h = (ids.astype(np.int64) * 2654435761) & 0xFFFFFF
    rgb = np.stack([(h >> 16) & 0xFF, (h >> 8) & 0xFF, h & 0xFF], -1) // 2 + 64
    rgb[ids < 0] = 0
    return rgb


def picture_png(path, v, names):
    """game, then drawn per variant, then ref, one above the other; the 4:3
    edges marked; in each drawn picture gaps red, see-through magenta."""
    p = v["_pic"]
    rows = []
    r = p["ref"]
    for label, buf in [("game", p["game"])] + [(n, p["drawn"][n]) for n in names] + [("ref", r)]:
        img = colours(buf[0])
        if label not in ("game", "ref"):
            gap = (buf[0] < 0) & (r[0] >= 0)
            wrong = (buf[0] >= 0) & (buf[0] != r[0]) & (buf[1] > r[1])
            img[gap] = (255, 0, 0)
            img[wrong] = (255, 0, 255)
        img[:, MARGIN - 1] = img[:, MARGIN + 512] = (255, 255, 255)
        rows += [img, np.full((4, W, 3), 40)]
    png(path, np.concatenate(rows[:-1]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nsf-dir", default=os.path.join(ROOT, "_build", "nsf_cache"))
    ap.add_argument("--levels", default="")
    ap.add_argument("--step", type=int, default=4)
    ap.add_argument("--nodes", default="all", choices=("all", "part13"),
                    help="which nodes of each path: see view_nodes")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--variants", default="hook",
                    help="comma-separated, from: " + ", ".join(sorted(VARIANTS)))
    ap.add_argument("--json", default="")
    ap.add_argument("--png", type=int, default=0, help="worst forward and side-on views to draw")
    ap.add_argument("--verify", type=int, default=0,
                    help="views per level whose hook list is checked against frustum_ref.draw")
    args = ap.parse_args()
    names = [v for v in args.variants.split(",") if v]
    bad = [v for v in names if v not in VARIANTS]
    if bad or not names:
        print("unknown variant(s): %s" % ", ".join(bad))
        return 2
    if not os.path.isfile(os.path.join(HERE, "raster.dll")):
        print("build raster.dll first: sh tuning/wide_view_sweep/build.sh")
        return 2
    levels = sorted(n for n in os.listdir(args.nsf_dir) if n.upper().endswith(".NSF"))
    if args.levels:
        want = set(args.levels.split(","))
        levels = [n for n in levels if n in want]
    if not levels:
        print("no level files")
        return 2
    t0 = time.time()
    jobs = [(n, args.nsf_dir, args.step, names, args.verify, None, args.nodes) for n in levels]
    views = []
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        for got in ex.map(sweep_level, jobs):
            views += got
    print("variants %s, nodes %s every %d, %d levels, %.0f s" % (
        ", ".join(names), args.nodes, args.step, len(levels), time.time() - t0))
    side = [v for v in views if v["side"]]
    fwd = [v for v in views if not v["side"]]
    for name in names:
        summary(side, "side-on paths (kind 3, 8)", name)
    for name in names:
        summary(fwd, "forward paths", name)
    per_level(fwd, "forward paths", names)
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w") as f:
            json.dump(views, f)
    if args.png:
        out = os.path.join(HERE, "out")
        os.makedirs(out, exist_ok=True)
        first = names[0]
        for label, vs in (("side", side), ("fwd", fwd)):
            worst = sorted((v for v in vs if v["m"][first]["model"]),
                           key=lambda v: -(v["m"][first]["gap"] + v["m"][first]["wrong"]
                                           + 20 * v["m"][first]["in43_changed"]))[:args.png]
            for i, v in enumerate(worst):
                only = {(v["zone"], v["item"], v["node"])}
                for g in sweep_level((v["level"], args.nsf_dir, 1, names, 0, only, "all")):
                    picture_png(os.path.join(out, "%s_%02d_%s_%s_%d_%d.png" % (
                        label, i, g["level"][:-4], g["zone"], g["item"], g["node"])), g, names)
        print("pictures in", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
