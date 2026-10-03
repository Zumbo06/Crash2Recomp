"""Export camera-test cases for sim.c, with the reference's expected result.

    python tuning/wide_frustum_sim/export.py OUT.bin [--nsf-dir DIR] [--per-level N]

Each case: a camera path at one node, near an end or in the middle, up to N
per level on side-on paths (kind 3, 8) and N on the others, and one more
side-on case per level whose extra columns hold void to cover; the game's own
camera at rest there (crash2_wide_geom.h: the path point, turned by the
node's angles from the item after the path, H from the path's 0x130); the
entries the hook reads (the zone, its worlds, the path's SLST, the zones and
SLSTs of the paths linked at its ends, and on a side-on path the zones and
SLSTs its pool reads: every other path of the zone and of its neighbour
zones); and what frustum_ref.py says the hook draws. OUT.bin is game data:
keep it out of the repository.

Format, little-endian: u32 'C2FS', u32 cases; per case: u32 entries, per
entry u32 eid, type, items, per item u32 length + bytes padded to 4; then
u32 zone eid, path item, node; i32 camera x, y, z; i32 r[9], h, ofx, ofy,
margin; u32 model seen, total, merged length, kept, joins; u64 FNV-1a of the
merged list (0 when the model fails); i32 the void cover's width left, right
(px; 0 off side-on paths); u32 objects, per object u32 draw-list
value, i32 the object hook's verdict (1 in the extra columns, 0 not, -1
cannot tell). Ends with u32 0xFFFFFFFF.
"""
from __future__ import annotations

import argparse
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tuning", "wide_slst_sim"))
sys.path.insert(0, os.path.join(ROOT, "_build"))

import frustum_ref as F  # noqa: E402
import slst_ref as S  # noqa: E402

MARGIN = 85
POOL_PATHS = 256                 # crash2_wide_slst.h's C2SL_POOL_PATHS


def table(it):
    if len(it) < 24:
        return None
    cnt = struct.unpack_from("<H", it, 12)[0]
    if not 1 <= cnt <= 96 or 16 + 8 * cnt > len(it):
        return None
    recs = [struct.unpack_from("<HHBBH", it, 16 + 8 * j) for j in range(cnt)]
    if any(recs[j][0] >= recs[j + 1][0] for j in range(cnt - 1)):
        return None
    return {r[0]: r for r in recs}


def rows(it, rec):
    _, off, fl, es, rc = rec
    p = 12 + off
    if fl & 0x40:
        counts = list(struct.unpack_from("<%dH" % rc, it, p))
        p += 2 * rc
    else:
        counts = [struct.unpack_from("<H", it, p)[0]] * rc
        p += 2
    metas = [None] * rc
    if fl & 0x20:
        metas = list(struct.unpack_from("<%dh" % rc, it, p))
        p += 2 * rc
    p = (p + 3) & ~3
    out = []
    for r in range(rc):
        vals = []
        for _ in range(counts[r]):
            if es == 4:
                vals.append(struct.unpack_from("<I", it, p)[0])
            elif es == 1:
                vals.append(it[p])
            elif es == 6:
                vals.append(struct.unpack_from("<hhh", it, p))
            p += es
        out.append((metas[r], vals))
    return out


def object_pos(ents, zone_items, value, placed):
    """Where a draw-list value's object stands (world units), as
    crash2_wide_geom.h's c2wg_object_pos finds it; None if it cannot."""
    hdr = zone_items[0]
    nn = struct.unpack_from("<I", hdr, 0x190)[0]
    slot = value & 0xFF
    if slot >= nn or nn > 16:
        return None
    zeid = struct.unpack_from("<I", hdr, 0x194 + 4 * slot)[0]
    if zeid not in placed or ents.get(zeid, (0,))[0] != 7:
        return None
    z = ents[zeid][1]
    c0, cn = struct.unpack_from("<2I", z[0], 0x184)
    item = (value >> 24) + c0 + cn
    if item >= len(z):
        return None
    tb = table(z[item])
    if not tb or 0x09F not in tb or 0x04B not in tb:
        return None
    if rows(z[item], tb[0x09F])[0][1][0] != (value >> 8) & 0xFFFF:
        return None
    x, y, zz = rows(z[item], tb[0x04B])[0][1][0]
    ox, oy, oz = struct.unpack_from("<3i", z[1], 0)
    return (ox + 4 * x, oy + 4 * y, oz + 4 * zz)


def fnv(ids):
    h = 0xCBF29CE484222325
    for v in ids:
        for b in (v & 0xFF, v >> 8):
            h ^= b
            h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def _sin(a):
    return int(round(4096 * math.sin(2 * math.pi * (a & 0xFFF) / 4096)))


def _mul(a, b):
    """MulMatrix (0x8004ECB8): a x b, each element >> 12 as MVMVA leaves it."""
    return [[max(-0x8000, min(0x7FFF, sum(a[i][k] * b[k][j] for k in range(3)) >> 12))
             for j in range(3)] for i in range(3)]


def camera_rotation(ax, ay, az):
    """The GTE rotation for a node's angles (x, y, z; 4096 a turn), as
    0x80017BC4 builds it - Rz(-z) Rx(-x) Ry(-y) - and 0x80017AF8 hands it to
    the world draw: row 1 times -5/8, row 2 negated. Sines from 4096 sin(),
    the game's table to within one."""
    s, c = _sin(-az), _sin(1024 - az)
    m = [[c, -s, 0], [s, c, 0], [0, 0, 4096]]
    s, c = _sin(-ax), _sin(1024 - ax)
    m = _mul(m, [[4096, 0, 0], [0, c, -s], [0, s, c]])
    s, c = _sin(-ay), _sin(1024 - ay)
    m = _mul(m, [[c, 0, s], [0, 4096, 0], [-s, 0, c]])
    return m[0] + [(-(5 * v)) >> 3 for v in m[1]] + [-v for v in m[2]]


def path_h(it, tb, node):
    """H at a node: the path's 0x130, rows by node, linear between them; 288
    without it (0x80023D7C)."""
    if 0x130 not in tb:
        return 288
    rs = sorted((m, v[0]) for m, v in rows(it, tb[0x130]) if v)
    if not rs:
        return 288
    if node <= rs[0][0]:
        return rs[0][1]
    for (m0, v0), (m1, v1) in zip(rs, rs[1:]):
        if m0 <= node <= m1:
            return int(round(v0 + (v1 - v0) * (node - m0) / max(1, m1 - m0)))
    return rs[-1][1]


def node_lists(items, lax):
    """The node lists as crash2_wide_slst.h rebuilds them, None where it
    refuses. A rebuild whose last list is not the entry's own end list (four
    entries in the game) serves side-on paths only (lax)."""
    try:
        ll = S.node_lists(items)
        if not lax and [F.key(v) for v in S.source(items[-1])] != [F.key(v) for v in ll[-1]]:
            return None
    except Exception:
        return None
    return ll


def zone_worlds(zone_items):
    """A zone's world EIDs, as c2wg_world reads them (none past 8)."""
    hdr = zone_items[0]
    nw = struct.unpack_from("<I", hdr, 0)[0]
    if nw > 8:
        return []
    return [struct.unpack_from("<I", hdr, 4 + 48 * i)[0] for i in range(nw)]


def pool_sources(ents, zeid, item):
    """The paths a side-on path's pool reads, in the hook's order: the zone's
    other paths, then each neighbour zone's (by slot, each zone once), items
    in order. [(world EIDs, node lists, SLST eid)] for those whose SLST
    rebuilds; and every zone read."""
    hdr = ents[zeid][1][0]
    nn = struct.unpack_from("<I", hdr, 0x190)[0]
    neigh = list(struct.unpack_from("<%dI" % nn, hdr, 0x194)) if nn <= 16 else []
    zones = [zeid]
    for z in neigh:
        if ents.get(z, (0,))[0] == 7 and z not in zones:
            zones.append(z)
    out, counted = [], 0
    for z in zones:
        zi = ents[z][1]
        c0, cn = struct.unpack_from("<2I", zi[0], 0x184)
        if c0 > 4096 or cn > 3 * POOL_PATHS:
            continue
        for k in range(c0, c0 + cn, 3):
            if k >= len(zi) or counted >= POOL_PATHS:
                break
            if z == zeid and k == item:
                continue
            counted += 1
            tb = table(zi[k])
            if not tb or 0x103 not in tb:
                continue
            se = rows(zi[k], tb[0x103])[0][1][0]
            if ents.get(se, (0,))[0] != 4:
                continue
            ll = node_lists(ents[se][1], True)
            if ll is None:
                continue
            out.append((zone_worlds(zi), ll, se))
    return out, zones


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--nsf-dir", default=os.path.join(ROOT, "_build", "nsf_cache"))
    ap.add_argument("--per-level", type=int, default=2)
    ap.add_argument("--levels", default="")
    args = ap.parse_args()

    import nsf as N
    N.OUT = args.nsf_dir
    levels = N.levels()
    if not levels:
        print("no level files")
        return 2
    want_levels = set(args.levels.split(",")) if args.levels else None
    cases = []
    for name, data in levels.items():
        if want_levels and name not in want_levels:
            continue
        ents = {eid: (t, items) for eid, t, items in N.entries(data)}
        got = {True: 0, False: 0}                  # side-on, other
        void_case = False                          # one side-on case with a cover
        for zeid, (t, items) in ents.items():
            if t != 7 or (min(got.values()) >= args.per_level and void_case):
                continue
            hdr = items[0]
            nw = struct.unpack_from("<I", hdr, 0)[0]
            if not 1 <= nw <= 8:
                continue
            wl_eids = [struct.unpack_from("<I", hdr, 4 + 48 * i)[0] for i in range(nw)]
            if any(ents.get(e, (0,))[0] != 3 for e in wl_eids):
                continue
            worlds = [F.World(ents[e][1]) for e in wl_eids]
            cam0, ncam_items = struct.unpack_from("<2I", hdr, 0x184)
            nn = struct.unpack_from("<I", hdr, 0x190)[0]
            neigh = list(struct.unpack_from("<%dI" % min(nn, 16), hdr, 0x194))
            ox, oy, oz = struct.unpack_from("<3i", items[1], 0)
            for k in range(cam0, cam0 + ncam_items, 3):
                if k + 1 >= len(items):
                    break
                it = items[k]
                tb, ta = table(it), table(items[k + 1])
                if not tb or 0x103 not in tb or 0x04B not in tb or not ta or 0x04B not in ta:
                    continue
                kind = rows(it, tb[0x029])[0][1][0] if 0x029 in tb else -1
                side = kind in (3, 8)
                if got[side] >= args.per_level and (not side or void_case):
                    continue
                se = rows(it, tb[0x103])[0][1][0]
                if ents.get(se, (0,))[0] != 4:
                    continue
                lists = node_lists(ents[se][1], side)
                if lists is None:
                    continue
                pos = [(ox + x, oy + y, oz + z) for x, y, z in rows(it, tb[0x04B])[0][1]]
                angles = rows(items[k + 1], ta[0x04B])[0][1]
                if len(pos) != len(lists) or len(pos) < 6 or len(angles) < 2 * len(pos):
                    continue
                # the paths linked at each end
                joins, extra = [None, None], []
                for node, vals in (rows(it, tb[0x109]) if 0x109 in tb else []):
                    end = 0 if node == 0 else (1 if node == len(pos) - 1 else None)
                    if end is None or len(vals) != 1:
                        continue
                    rec = vals[0]
                    slot, pidx, at_start = (rec >> 16) & 0xFF, (rec >> 8) & 0xFF, (rec & 0xFF) == 1
                    if slot >= len(neigh) or ents.get(neigh[slot], (0,))[0] != 7:
                        continue
                    jz = ents[neigh[slot]][1]
                    jc0 = struct.unpack_from("<I", jz[0], 0x184)[0]
                    jit = jz[pidx * 3 + jc0] if pidx * 3 + jc0 < len(jz) else None
                    jtb = table(jit) if jit else None
                    if not jtb or 0x103 not in jtb:
                        continue
                    jse = rows(jit, jtb[0x103])[0][1][0]
                    if ents.get(jse, (0,))[0] != 4:
                        continue
                    jl = node_lists(ents[jse][1], side)
                    if jl is None:
                        continue
                    jnw = struct.unpack_from("<I", jz[0], 0)[0]
                    jw = [struct.unpack_from("<I", jz[0], 4 + 48 * i)[0] for i in range(min(jnw, 8))]
                    wmap = [wl_eids.index(e) if e in wl_eids else 0xFF for e in jw] + [0xFF] * (8 - len(jw))
                    joins[end] = (jl, at_start, wmap)
                    extra += [neigh[slot], jse]
                pool_ids = []
                if side:
                    srcs, pool_zones = pool_sources(ents, zeid, k)
                    pool_ids = F.pool(wl_eids, [(w, ll) for w, ll, _ in srcs])
                    extra += pool_zones + [s for _, _, s in srcs]
                for n in (len(pos) - 3, len(pos) // 2, 1):
                    regular = got[side] < args.per_level and n != 1
                    if not regular and (not side or void_case):
                        continue
                    cx, cy, cz = pos[n]
                    offsets = [((w.origin[0] - cx) & 0xFFFFFFFF, ((w.origin[1] - cy) & 0xFFFFFFFF) & 0xFFFE,
                                (w.origin[2] - cz) & 0xFFFFFFFF) for w in worlds]
                    if sum(1 for v in lists[n] if (v >> 13) < len(worlds)) < 20:
                        continue
                    r = camera_rotation(*angles[2 * n])
                    h = path_h(it, tb, n)
                    cam = {"r": r, "h": h, "ofx": 256 << 16, "ofy": 108 << 16}
                    seen, total, merged, kept = F.draw(cam, worlds, offsets, lists, n, joins, MARGIN,
                                                       side, pool_ids)
                    cover = F.void_cover(cam, worlds, offsets, merged, MARGIN) \
                        if side and merged is not None else [0, 0]
                    if not regular:
                        if not any(cover):
                            continue
                        void_case = True
                    entry_eids = [zeid] + wl_eids + [se] + extra
                    placed = set(entry_eids)
                    objs = []
                    for prop in (0x13B, 0x13C):
                        for _, vals in (rows(it, tb[prop]) if prop in tb else []):
                            for v in vals:
                                if v not in [o[0] for o in objs] and len(objs) < 200:
                                    p = object_pos(ents, items, v, placed)
                                    verdict = -1 if merged is None or p is None else \
                                        F.object_verdict(cam, MARGIN, (cx, cy, cz), p)
                                    objs.append((v, verdict))
                    cases.append({
                        "entries": [(e, ents[e][0], ents[e][1]) for e in dict.fromkeys(entry_eids)],
                        "zone": zeid, "item": k, "node": n, "cam": (cx, cy, cz), "r": r, "h": h,
                        "seen": seen, "total": total, "merged": merged, "kept": kept,
                        "joins": sum(j is not None for j in joins),
                        "kind": kind, "level": name, "objs": objs, "cover": cover,
                    })
                    if regular:
                        got[side] += 1
                    print("%s %s item %d kind %d node %d/%d: H %d, model %d/%d, kept %d, joins %d, pool %d, "
                          "cover %d/%d" % (name, N.eid_name(zeid), k, kind, n, len(pos), h, seen, total, kept,
                                           cases[-1]["joins"], len(pool_ids), cover[0], cover[1]))
    with open(args.out, "wb") as f:
        f.write(b"C2FS")
        f.write(struct.pack("<I", len(cases)))
        for c in cases:
            f.write(struct.pack("<I", len(c["entries"])))
            for eid, typ, its in c["entries"]:
                f.write(struct.pack("<III", eid, typ, len(its)))
                for b in its:
                    f.write(struct.pack("<I", len(b)))
                    f.write(b + b"\0" * ((4 - len(b) % 4) % 4))
            f.write(struct.pack("<III", c["zone"], c["item"], c["node"]))
            f.write(struct.pack("<3i", *c["cam"]))
            f.write(struct.pack("<9i", *c["r"]))
            f.write(struct.pack("<4i", c["h"], 256 << 16, 108 << 16, MARGIN))
            merged = c["merged"] or []
            f.write(struct.pack("<5I", c["seen"], c["total"], len(merged), c["kept"], c["joins"]))
            f.write(struct.pack("<Q", fnv(merged) if c["merged"] else 0))
            f.write(struct.pack("<2i", *c["cover"]))
            f.write(struct.pack("<I", len(c["objs"])))
            for v, verdict in c["objs"]:
                f.write(struct.pack("<Ii", v, verdict))
        f.write(struct.pack("<I", 0xFFFFFFFF))
    print("exported %d cases" % len(cases))
    return 0 if cases else 1


if __name__ == "__main__":
    raise SystemExit(main())
