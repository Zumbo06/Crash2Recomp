"""Export camera-test cases for sim.c, with the reference's expected result.

    python tuning/wide_frustum_sim/export.py OUT.bin [--nsf-dir DIR] [--per-level N]

Each case: a camera path at one node, near an end or in the middle, on
side-on (kind 3) and other paths; a camera at the node's path point whose
rotation is fitted to put most of the node's own list in the 4:3 frame (the
game's real camera is not modelled here - the hook reads it at run time);
the entries the hook reads (the zone, its worlds, the path's SLST, and the
zones and SLSTs of the paths linked at its ends); and what frustum_ref.py
says the hook draws. OUT.bin is game data: keep it out of the repository.

Format, little-endian: u32 'C2FS', u32 cases; per case: u32 entries, per
entry u32 eid, type, items, per item u32 length + bytes padded to 4; then
u32 zone eid, path item, node; i32 camera x, y, z; i32 r[9], h, ofx, ofy,
margin; u32 model seen, total, merged length, kept, joins; u64 FNV-1a of the
merged list (0 when the model fails); u32 objects, per object u32 draw-list
value, i32 the object hook's verdict (1 in the extra columns, 0 not, -1
cannot tell). Ends with u32 0xFFFFFFFF.
"""
from __future__ import annotations

import argparse
import math
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tuning", "wide_slst_sim"))
sys.path.insert(0, os.path.join(ROOT, "_build"))

import frustum_ref as F  # noqa: E402
import slst_ref as S  # noqa: E402

MARGIN = 85


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


def rotation(yaw, pitch):
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    fwd = np.array([sy * cp, sp, cy * cp])
    right = np.array([cy, 0.0, -sy])
    down = np.cross(fwd, right)
    return np.stack([right, down, fwd])


def fit_camera(corner_sets):
    """Rows of a rotation putting most corner sets in the 4:3 frame (H 256)."""
    pts = np.array([c + [c[-1]] * (4 - len(c)) for c in corner_sets], dtype=float)

    def score(R):
        v = pts @ R.T
        z = v[..., 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            sx = 256 + 256 * v[..., 0] / z
            sy = 108 + 256 * v[..., 1] / z
        inside = (z > 1) & (sx >= 0) & (sx < 512) & (sy >= 0) & (sy < 216)
        return inside.any(axis=1).mean()

    best = (-1.0, 0.0, 0.0)
    for yd in range(0, 360, 6):
        for pd in range(-60, 61, 6):
            s = score(rotation(math.radians(yd), math.radians(pd)))
            if s > best[0]:
                best = (s, yd, pd)
    s0, yd0, pd0 = best
    for yd in np.arange(yd0 - 6, yd0 + 6.01, 1.0):
        for pd in np.arange(pd0 - 6, pd0 + 6.01, 1.0):
            s = score(rotation(math.radians(yd), math.radians(pd)))
            if s > best[0]:
                best = (s, float(yd), float(pd))
    R = rotation(math.radians(best[1]), math.radians(best[2]))
    return best[0], [int(round(max(-32768, min(32767, x * 4096)))) for x in R.flatten()]


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
        got = 0
        for zeid, (t, items) in ents.items():
            if t != 7 or got >= args.per_level:
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
                if got >= args.per_level or k >= len(items):
                    break
                it = items[k]
                tb = table(it)
                if not tb or 0x103 not in tb or 0x04B not in tb:
                    continue
                kind = rows(it, tb[0x029])[0][1][0] if 0x029 in tb else -1
                se = rows(it, tb[0x103])[0][1][0]
                if ents.get(se, (0,))[0] != 4:
                    continue
                try:
                    lists = S.node_lists(ents[se][1])
                except Exception:
                    continue
                pos = [(ox + x, oy + y, oz + z) for x, y, z in rows(it, tb[0x04B])[0][1]]
                if len(pos) != len(lists) or len(pos) < 6:
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
                    try:
                        jl = S.node_lists(ents[jse][1])
                    except Exception:
                        continue
                    jnw = struct.unpack_from("<I", jz[0], 0)[0]
                    jw = [struct.unpack_from("<I", jz[0], 4 + 48 * i)[0] for i in range(min(jnw, 8))]
                    wmap = [wl_eids.index(e) if e in wl_eids else 0xFF for e in jw] + [0xFF] * (8 - len(jw))
                    joins[end] = (jl, at_start, wmap)
                    extra += [neigh[slot], jse]
                for n in (len(pos) - 3, len(pos) // 2):
                    if got >= args.per_level:
                        break
                    cx, cy, cz = pos[n]
                    offsets = [((w.origin[0] - cx) & 0xFFFFFFFF, ((w.origin[1] - cy) & 0xFFFFFFFF) & 0xFFFE,
                                (w.origin[2] - cz) & 0xFFFFFFFF) for w in worlds]
                    cs = []
                    for v in lists[n]:
                        w = v >> 13
                        c = worlds[w].corners(v, offsets[w]) if w < len(worlds) else None
                        if c:
                            cs.append(c)
                    if len(cs) < 20:
                        continue
                    frac, r = fit_camera(cs)
                    if frac < 0.6:
                        continue
                    cam = {"r": r, "h": 256, "ofx": 256 << 16, "ofy": 108 << 16}
                    seen, total, merged, kept = F.draw(cam, worlds, offsets, lists, n, joins, MARGIN)
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
                        "zone": zeid, "item": k, "node": n, "cam": (cx, cy, cz), "r": r,
                        "seen": seen, "total": total, "merged": merged, "kept": kept,
                        "joins": sum(j is not None for j in joins),
                        "kind": kind, "level": name, "objs": objs,
                    })
                    got += 1
                    print("%s %s item %d kind %d node %d/%d: fit %.0f%%, model %d/%d, kept %d, joins %d"
                          % (name, N.eid_name(zeid), k, kind, n, len(pos), 100 * frac, seen, total,
                             kept, cases[-1]["joins"]))
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
            f.write(struct.pack("<4i", 256, 256 << 16, 108 << 16, MARGIN))
            merged = c["merged"] or []
            f.write(struct.pack("<5I", c["seen"], c["total"], len(merged), c["kept"], c["joins"]))
            f.write(struct.pack("<Q", fnv(merged) if c["merged"] else 0))
            f.write(struct.pack("<I", len(c["objs"])))
            for v, verdict in c["objs"]:
                f.write(struct.pack("<Ii", v, verdict))
        f.write(struct.pack("<I", 0xFFFFFFFF))
    print("exported %d cases" % len(cases))
    return 0 if cases else 1


if __name__ == "__main__":
    raise SystemExit(main())
