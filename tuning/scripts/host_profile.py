"""Read psx_host_profile.json (runtime/src/host_profiler.c) and say where the
emulation thread's time went.

    python tuning/scripts/host_profile.py
    python tuning/scripts/host_profile.py --profile path/to/psx_host_profile.json
                                          --exe path/to/the.exe [--top 40]

Addresses in the executable are named from its own symbol table (llvm-nm),
then grouped: recompiled game code (func_8...), the runtime by function, the
graphics driver and OS modules by module. Each line shows the share of all
samples and how it splits between emulating, presenting and pacing.
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE = ROOT / "_build" / "Crash2Recomp" / "build-clang" / "psx_host_profile.json"
DEFAULT_EXE = ROOT / "_build" / "Crash2Recomp" / "build-clang" / "Crash_Bandicoot_2_Recompiled.exe"
TOOLCHAIN = Path(os.environ.get(
    "RETCOMM_TOOLCHAIN",
    Path.home() / ".local" / "share" / "retcomm" / "toolchains" / "cmake-clang-v1" / "latest"))
PHASES = ("emulate", "present", "pace", "other")


def tool(name: str) -> str:
    exe = TOOLCHAIN / "bin" / (name + ".exe")
    return str(exe) if exe.is_file() else name


def image_base(exe: Path) -> int:
    out = subprocess.run([tool("llvm-objdump"), "-p", str(exe)], capture_output=True,
                         text=True).stdout
    m = re.search(r"ImageBase\s+([0-9a-fA-F]+)", out)
    return int(m.group(1), 16) if m else 0x140000000


def symbols(exe: Path) -> tuple[list[int], list[str]]:
    out = subprocess.run([tool("llvm-nm"), "--defined-only", "-n", str(exe)],
                         capture_output=True, text=True).stdout
    addrs, names = [], []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) != 3 or parts[1].lower() not in ("t", "w"):
            continue
        addrs.append(int(parts[0], 16))
        names.append(parts[2])
    return addrs, names


def group_of(module: str, name: str | None) -> str:
    if name is None:
        low = module.lower()
        if low.startswith(("nv", "amd", "ig", "d3d12", "dxgi", "d3dcompiler")):
            return f"graphics driver ({module})"
        if low in ("ntdll.dll", "kernelbase.dll", "kernel32.dll", "win32u.dll"):
            return f"OS ({module})"
        return module
    if name.startswith("func_8") or name.startswith("func_0") or name.startswith("func_1F"):
        return "guest code (recompiled)"
    return name


def load(profile: Path, exe: Path) -> dict:
    data = json.loads(profile.read_text(encoding="utf-8"))
    base = image_base(exe)
    addrs, names = symbols(exe)
    exe_name = exe.name.lower()
    by_func: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    by_group: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    by_module: dict[str, int] = defaultdict(int)
    for leaf in data["leaf"]:
        module, rva, counts = leaf["m"], int(leaf["rva"], 16), leaf["n"]
        name = None
        if module.lower() == exe_name:
            i = bisect.bisect_right(addrs, base + rva) - 1
            name = names[i] if i >= 0 else f"?+0x{rva:X}"
            key = name
        else:
            key = module
        for p in range(4):
            by_func[key][p] += counts[p]
            by_group[group_of(module, name)][p] += counts[p]
        by_module[module] += sum(counts)
    return {"data": data, "by_func": by_func, "by_group": by_group, "by_module": by_module}


def show(result: dict, top: int) -> None:
    data = result["data"]
    total = data["samples"] or 1
    ph = data["phases"]
    print(f"{data['tag']}: {data['samples']} samples over {data['active_ms'] / 1000:.1f} s "
          f"({data['distinct']} addresses, {data['dropped']} dropped)")
    print("phases: " + ", ".join(f"{p} {100.0 * ph[p] / total:.1f}%" for p in PHASES))

    def rows(table: dict, limit: int, title: str) -> None:
        print(f"\n{title}")
        items = sorted(table.items(), key=lambda kv: -sum(kv[1]))[:limit]
        for key, counts in items:
            n = sum(counts)
            split = " ".join(f"{PHASES[p][:4]} {100.0 * counts[p] / n:3.0f}%" for p in range(3))
            print(f"  {100.0 * n / total:5.1f}%  {key[:60]:<60} {split}")

    rows(result["by_group"], top, "by group")
    rows(result["by_func"], top, "by function")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    ap.add_argument("--exe", type=Path, default=DEFAULT_EXE)
    ap.add_argument("--top", type=int, default=35)
    args = ap.parse_args()
    show(load(args.profile, args.exe), args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
