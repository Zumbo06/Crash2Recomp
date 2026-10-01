"""Export every SLST entry on the disc for sim.c, with the reference lists.

    python tuning/wide_slst_sim/export.py OUT.bin [--nsf-dir DIR]

The level files come from your own disc image (_build/nsf.py extracts them on
first use). OUT.bin is game-derived; keep it out of the repository - the test
writes it to a temporary folder.

Format, little-endian, per entry:
  u32 eid, u32 item count, then per item: u32 byte length, bytes (padded to 4)
  u32 node count, then per node: u32 list length, u64 FNV-1a hash of the ids,
      u32 position of node n-1's first polygon missing from node n (or ~0)
  u32 status: 0 = the reference decoded it and start + deltas == end
Ends with u32 0xFFFFFFFF.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "_build"))

import slst_ref  # noqa: E402


def fnv(ids: list[int]) -> int:
    h = 0xCBF29CE484222325
    for v in ids:
        for b in (v & 0xFF, v >> 8):
            h ^= b
            h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--nsf-dir", default=os.path.join(ROOT, "_build", "nsf_cache"))
    args = ap.parse_args()

    import psxexe as X
    import nsf as N
    N.OUT = args.nsf_dir
    levels = N.levels()
    if not levels:
        print("no level files")
        return 2

    entries = bad = 0
    with open(args.out, "wb") as f:
        for name, data in levels.items():
            for eid, etype, items in N.entries(data):
                if etype != 4 or len(items) < 2:
                    continue
                status = 0
                try:
                    lists = slst_ref.node_lists(items)
                    if lists[-1] != slst_ref.source(items[-1]):
                        status = 1
                except Exception:
                    lists, status = [], 2
                f.write(struct.pack("<II", eid, len(items)))
                for it in items:
                    f.write(struct.pack("<I", len(it)))
                    f.write(it + b"\0" * ((4 - len(it) % 4) % 4))
                f.write(struct.pack("<I", len(lists)))
                for n, l in enumerate(lists):
                    missing = 0xFFFFFFFF
                    if n:
                        have = set(l)
                        missing = next((i for i, v in enumerate(lists[n - 1]) if v not in have),
                                       0xFFFFFFFF)
                    f.write(struct.pack("<IQI", len(l), fnv(l), missing))
                f.write(struct.pack("<I", status))
                entries += 1
                bad += status != 0
        f.write(struct.pack("<I", 0xFFFFFFFF))
    print("exported %d SLST entries (%d the reference does not close exactly)" % (entries, bad))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
