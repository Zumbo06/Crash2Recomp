"""Compare a GL and a D3D12 parity run: VRAM exactly, presents by pixel."""
import sys

import numpy as np
from PIL import Image

gl, dx = sys.argv[1], sys.argv[2]
a = np.fromfile(f"{gl}_vram.bin", dtype=np.uint16).reshape(512, 1024)
b = np.fromfile(f"{dx}_vram.bin", dtype=np.uint16).reshape(512, 1024)
d = a != b
print(f"VRAM: {int(d.sum())} of {a.size} pixels differ")
ys, xs = np.nonzero(d)
for y, x in list(zip(ys, xs))[:8]:
    print(f"   ({x},{y}) gl={a[y, x]:#06x} d3d12={b[y, x]:#06x}")
n = 0
while True:
    try:
        g = np.asarray(Image.open(f"{gl}_{n:04d}.ppm")).astype(int)
        h = np.asarray(Image.open(f"{dx}_{n:04d}.ppm")).astype(int)
    except FileNotFoundError:
        break
    dd = np.abs(g - h).max(axis=2)
    print(f"present {n}: {int((dd > 0).sum())} px differ, {int((dd > 2).sum())} by more than 2/255, max {int(dd.max())}")
    n += 1
if n == 0:
    print("no presents captured")
