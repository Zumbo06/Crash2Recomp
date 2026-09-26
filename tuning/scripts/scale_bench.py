"""Where a frame goes at a given internal resolution, from the release build.

Reads the heartbeat the runtime writes every 100 ms (psx_freeze_heartbeat.json,
next to the game exe) twice, N seconds apart, and turns the monotonic totals of
its "gl" object (gpu_gl_renderer.c, GlCost) into per-second and per-frame
rates. No debug port, no debug-tools build: this is the build the player runs.

Use it once per internal resolution, standing in the same spot of the same
level with 60 FPS on, e.g. Turtle Woods' first crates:

    python tuning/scripts/scale_bench.py --label 1x
    python tuning/scripts/scale_bench.py --label 3x
    python tuning/scripts/scale_bench.py --label 5x
    python tuning/scripts/scale_bench.py --show

Each run appends one row to tuning/runs/scale_bench.jsonl; --show prints them
side by side. For GPU timings start the game with PSX_GPU_PERF=1 (the
launcher's Advanced > "Runtime profiler" sets it); everything else is always
counted.

Reading the table:
  swaps/vbl   presented frames per emulated VBlank. Below ~0.99 the host is
              not keeping up; that is the 5x symptom.
  swap ms/s   time the emulation thread spent inside SwapWindow. Large with
              vsync off means the driver's queue was full: GPU-bound.
  sync ms/s   time spent waiting for the GPU to answer a guest VRAM read.
              Every millisecond here is GPU time turned into emulation time.
  stencil Mpx/s, hold Mpx/s
              full-surface work that scales with S*S.
  gpu scene / present
              GPU-timeline milliseconds per frame (needs PSX_GPU_PERF=1).
              Scene includes idle gaps while the GPU waited for the emulator.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD_DIRS = (ROOT / "_build" / "Crash2Recomp" / "build-clang",
              ROOT / "_build" / "Crash2Recomp" / "build-debugtools")
RUNS = ROOT / "tuning" / "runs" / "scale_bench.jsonl"

GL_TOTALS = ("presents", "swap_us", "batches", "semi_isolated",
             "break_semi", "break_blend", "break_mask", "break_filter",
             "break_gate", "break_twin", "break_full",
             "stencil_rebuilds", "stencil_px", "sync_reads", "sync_skips",
             "sync_us", "hold_copies", "hold_px", "pack_px", "mirror_passes",
             "gpu_frames", "gpu_scene_us", "gpu_present_us")
TOP_TOTALS = ("vblank_raise_count", "host_swap_count", "game_loop_count")


def default_heartbeat() -> Path:
    """The newest heartbeat of the two build trees the launcher can run."""
    found = [d / "psx_freeze_heartbeat.json" for d in BUILD_DIRS
             if (d / "psx_freeze_heartbeat.json").is_file()]
    if not found:
        raise SystemExit("no psx_freeze_heartbeat.json - is the game running?")
    return max(found, key=lambda p: p.stat().st_mtime)


def read_sample(path: Path, retries: int = 20) -> dict:
    """One heartbeat, or an error. The file is replaced atomically, but a read
    can still land between two writes on Windows, so retry briefly."""
    last_error: Exception | None = None
    for _ in range(retries):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError) as error:
            last_error = error
        time.sleep(0.05)
    raise SystemExit(f"cannot read {path}: {last_error}")


def rates(first: dict, last: dict, seconds: float) -> dict:
    """Per-second and per-frame rates between two heartbeats."""
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    gl0, gl1 = first.get("gl") or {}, last.get("gl") or {}
    if not gl1:
        raise ValueError("this build has no \"gl\" heartbeat object (patch 0038)")

    def delta(a: dict, b: dict, key: str) -> float:
        value = b.get(key, 0) - a.get(key, 0)
        return float(value) if value >= 0 else 0.0

    vbl = delta(first, last, "vblank_raise_count")
    swaps = delta(first, last, "host_swap_count")
    loops = delta(first, last, "game_loop_count")
    presents = delta(gl0, gl1, "presents")
    frames = presents or 1.0
    gpu_frames = delta(gl0, gl1, "gpu_frames")
    out = {
        "scale": gl1.get("scale"),
        "wide": gl1.get("wide"),
        "seconds": round(seconds, 2),
        "vblank_hz": round(vbl / seconds, 2),
        "swap_hz": round(swaps / seconds, 2),
        "loop_hz": round(loops / seconds, 2),
        "swaps_per_vblank": round(swaps / vbl, 3) if vbl else None,
        "one_field_pct": last.get("native_60fps_one_field_pct"),
        "swap_ms_s": round(delta(gl0, gl1, "swap_us") / 1000.0 / seconds, 1),
        "sync_reads_s": round(delta(gl0, gl1, "sync_reads") / seconds, 1),
        "sync_skips_s": round(delta(gl0, gl1, "sync_skips") / seconds, 1),
        "sync_ms_s": round(delta(gl0, gl1, "sync_us") / 1000.0 / seconds, 1),
        "stencil_rebuilds_s": round(delta(gl0, gl1, "stencil_rebuilds") / seconds, 1),
        "stencil_mpx_s": round(delta(gl0, gl1, "stencil_px") / 1e6 / seconds, 1),
        "hold_mpx_s": round(delta(gl0, gl1, "hold_px") / 1e6 / seconds, 1),
        "pack_kpx_s": round(delta(gl0, gl1, "pack_px") / 1e3 / seconds, 1),
        "batches_frame": round(delta(gl0, gl1, "batches") / frames, 1),
        "semi_isolated_frame": round(delta(gl0, gl1, "semi_isolated") / frames, 1),
        "mirror_frame": round(delta(gl0, gl1, "mirror_passes") / frames, 1),
        "breaks_frame": {
            key[len("break_"):]: round(delta(gl0, gl1, key) / frames, 1)
            for key in GL_TOTALS if key.startswith("break_")
        },
        "gpu_scene_ms": (round(delta(gl0, gl1, "gpu_scene_us") / 1000.0 / gpu_frames, 2)
                         if gpu_frames else None),
        "gpu_present_ms": (round(delta(gl0, gl1, "gpu_present_us") / 1000.0 / gpu_frames, 2)
                           if gpu_frames else None),
        "gpu_scene_max_ms": (round(gl1.get("gpu_scene_max_us", 0) / 1000.0, 2)
                             if gpu_frames else None),
    }
    return out


def verdict(row: dict) -> str:
    """One sentence: keeping up, or the largest measured cost."""
    ratio = row.get("swaps_per_vblank")
    if ratio is None:
        return "no VBlanks in the window - is the game paused?"
    if ratio >= 0.99:
        return "host keeps up"
    costs = {
        "waiting on SwapWindow (GPU queue full, or vsync)": row["swap_ms_s"],
        "waiting on guest VRAM reads (GPU sync)": row["sync_ms_s"],
    }
    worst = max(costs, key=costs.get)
    if costs[worst] >= 50.0:
        return f"behind ({ratio:.2f} swaps/VBlank): {worst}, {costs[worst]:.0f} ms/s"
    return (f"behind ({ratio:.2f} swaps/VBlank) without a large GPU wait - "
            "the emulation thread itself is busy (CPU clock, overlays)")


def show(rows: list[dict]) -> str:
    columns = (("label", "label"), ("scale", "S"), ("swaps_per_vblank", "swaps/vbl"),
               ("loop_hz", "loops/s"), ("one_field_pct", "1-field%"),
               ("swap_ms_s", "swap ms/s"), ("sync_ms_s", "sync ms/s"),
               ("sync_reads_s", "reads/s"), ("stencil_mpx_s", "stencil Mpx/s"),
               ("hold_mpx_s", "hold Mpx/s"), ("batches_frame", "batches/f"),
               ("semi_isolated_frame", "semi/f"), ("mirror_frame", "mirror/f"),
               ("gpu_scene_ms", "gpu scene"), ("gpu_present_ms", "gpu present"))
    lines = ["  ".join(f"{title:>13}" for _, title in columns)]
    for row in rows:
        lines.append("  ".join(f"{str(row.get(key, '')):>13}" for key, _ in columns))
    return "\n".join(lines)


def load_runs() -> list[dict]:
    if not RUNS.is_file():
        return []
    return [json.loads(line) for line in RUNS.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--heartbeat", type=Path, help="psx_freeze_heartbeat.json path")
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--label", default="", help="row name, e.g. 5x-turtle-woods")
    parser.add_argument("--show", action="store_true", help="print the saved rows and exit")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    if args.show:
        rows = load_runs()
        print(show(rows) if rows else f"no runs in {RUNS}")
        return 0

    path = args.heartbeat or default_heartbeat()
    print(f"sampling {path} for {args.seconds:.0f} s - keep playing the same spot")
    t0 = time.monotonic()
    first = read_sample(path)
    time.sleep(args.seconds)
    last = read_sample(path)
    row = rates(first, last, time.monotonic() - t0)
    row["label"] = args.label or f"{row['scale']}x"
    row["verdict"] = verdict(row)
    print(json.dumps(row, indent=2))
    print(row["verdict"])
    if not args.no_save:
        RUNS.parent.mkdir(parents=True, exist_ok=True)
        with RUNS.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
