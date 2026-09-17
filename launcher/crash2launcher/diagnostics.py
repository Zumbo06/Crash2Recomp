"""Privacy-conscious, release-build diagnostic export.

The heartbeat is written by the normal runtime, without the debug TCP server.
It contains guest memory and machine state, so never attach it verbatim.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .version import full_version


SETTING_KEYS = (
    "renderer", "aspect", "output_aspect", "scaling_mode", "fullscreen_mode",
    "window_width", "window_height", "supersampling", "vsync",
    "frame_interpolation", "frame_interpolation_fps", "frame_blend",
    "audio_latency_ms", "audio_hq", "developer_mode", "fast_loading",
    "native_60fps", "native_60fps_cpu_percent",
)
HEARTBEAT_KEYS = ("backend", "frame_count", "total_checks", "dispatch_count",
                  "exception_entries", "exception_reentry_blocks", "fatal",
                  "vblank_raise_count", "game_loop_count",
                  "display_flip_count", "host_swap_count",
                  # native_60fps is the setting; native_60fps_gate_open is
                  # whether the runtime is actually holding 60 right now, and
                  # game_frame_ticks (17 or 34) is the engine's own answer.
                  # A report with the first but not the other two cannot tell
                  # "on and working" from "on and fallen back".
                  "native_60fps", "native_60fps_gate_open",
                  "game_frame_ticks")
SAMPLE_KEYS = ("wall", "frame", "exc_re", "in_exc", "tcp_ms")
RATE_KEYS = ("vblank_raise_count", "game_loop_count",
             "display_flip_count", "host_swap_count")


def cadence_sample(heartbeat_path: Path, host_time: float) -> dict | None:
    """Read only monotonic cadence counters from the atomic heartbeat file."""
    try:
        source = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        if not isinstance(source, dict) or not isinstance(source.get("frame_count"), int):
            return None
        return {"host_time": host_time, "frame_count": source["frame_count"],
                **{key: source[key] for key in RATE_KEYS
                   if isinstance(source.get(key), int)}}
    except (OSError, ValueError, UnicodeError):
        return None


def make_report(settings: Settings, heartbeat_path: Path,
                recent_samples: list[dict] | None = None) -> dict:
    """Return an allowlisted report. No disc path, RAM, cards or home paths."""
    report = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "launcher_version": full_version(),
        "settings": {key: getattr(settings, key) for key in SETTING_KEYS},
        "heartbeat": None,
    }
    if recent_samples:
        samples = recent_samples[-31:]
        report["cadence_samples"] = samples
        first, last = samples[0], samples[-1]
        seconds = last["host_time"] - first["host_time"]
        if seconds > 0 and last["frame_count"] >= first["frame_count"]:
            report["cadence_window_seconds"] = round(seconds, 3)
            report["cadence_per_second"] = {
                key: round((last[key] - first[key]) / seconds, 2)
                for key in RATE_KEYS if key in first and key in last
                and last[key] >= first[key]
            }
    try:
        source = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        if not isinstance(source, dict):
            raise ValueError("heartbeat is not an object")
        heartbeat = {key: source[key] for key in HEARTBEAT_KEYS if key in source}
        samples = source.get("ring", [])
        if isinstance(samples, list):
            heartbeat["samples"] = [
                {key: item[key] for key in SAMPLE_KEYS if key in item}
                for item in samples[-300:] if isinstance(item, dict)
            ]
        report["heartbeat"] = heartbeat
    except (OSError, ValueError, UnicodeError):
        report["heartbeat_status"] = "unavailable"
    return report


def save_report(destination: Path, settings: Settings, heartbeat_path: Path,
                recent_samples: list[dict] | None = None) -> None:
    destination.write_text(
        json.dumps(make_report(settings, heartbeat_path, recent_samples),
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
