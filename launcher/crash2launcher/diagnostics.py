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
    "native_60fps",
    # Assists can change saved progression, so a report that cannot show
    # whether they were on cannot tell "the game broke my save" from "I had
    # assists enabled".
    "cheat_infinite_lives", "cheat_aku_aku",
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
                  "game_frame_ticks",
                  "native_60fps_verdict", "native_60fps_one_field_pct",
                  "native_60fps_backoffs", "native_60fps_fail_pct",
                  "native_60fps_fail_loop_hz", "native_60fps_cpu_now",
                  # Whether VSync's iteration-counted timeout was widened for
                  # the raised clock. A report with the clock up and this 0 is
                  # the one that explains a VBlank wait ending early.
                  "native_60fps_vsync_wide",
                  "cheat_lives", "cheat_aku_level", "cheat_god_active")
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


# c2_60_window_verdict's return values (crash2_60fps.h). The distinction that
# matters to a player is 1 vs 2: FAIL_HOST means the machine could not DRAW the
# frames, which lowering internal resolution fixes, while FAIL_HOLD means the
# emulated game could not SIMULATE them in one field, which it does not.
VERDICT_PENDING, VERDICT_OK = -1, 0
VERDICT_FAIL_HOST, VERDICT_FAIL_HOLD, VERDICT_QUIET = 1, 2, 3


def sixty_fps_sample(heartbeat_path: Path) -> dict | None:
    """Live 60 FPS state, for the Play page.

    These fields are computed every second whether or not the sustain guard is
    allowed to act, so they are a free always-on signal. Returns None when 60
    FPS is off or the heartbeat is not readable, so the caller shows nothing
    rather than a misleading zero.
    """
    try:
        source = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(source, dict) or not source.get("native_60fps"):
        return None
    wanted = ("native_60fps_gate_open", "native_60fps_verdict",
              "native_60fps_one_field_pct", "native_60fps_backoffs",
              "native_60fps_cpu_now", "native_60fps_vsync_wide",
              "game_frame_ticks")
    return {key: source[key] for key in wanted
            if isinstance(source.get(key), int)}


def sixty_fps_summary(sample: dict | None) -> tuple[str, str] | None:
    """Turn a sample into (tone, sentence), or None when there is nothing to say.

    Tone is a `set_status` tone: "Ok", "Warn" or "". The mode never falls back
    to 30, so every message here is about what is limiting 60 - and the two
    limits need opposite answers from the player.
    """
    if not sample:
        return None
    verdict = sample.get("native_60fps_verdict", VERDICT_PENDING)
    held = sample.get("native_60fps_one_field_pct")
    cpu = sample.get("native_60fps_cpu_now", 100)

    # VSync counts loop iterations for its timeout, so a raised clock without
    # the widened timeout can end a VBlank wait early. The runtime applies the
    # widening with the clock; seeing the clock up without it is a runtime
    # defect worth saying out loud rather than a tuning problem.
    if cpu > 100 and sample.get("native_60fps_vsync_wide") == 0:
        return ("Warn",
                "60 FPS: the CPU clock is raised but VSync's timeout was not "
                "widened to match. Please report this with the Log page's "
                "diagnostic export.")
    if verdict == VERDICT_FAIL_HOST:
        return ("Warn",
                "60 FPS: this machine is behind on DRAWING the frames. Lower "
                "Internal resolution on the Video page - the game itself is "
                "keeping up.")
    if verdict == VERDICT_FAIL_HOLD:
        note = "" if held is None else f" - {held}% of frames fitted"
        return ("Warn",
                f"60 FPS: some frames in this scene need longer than one "
                f"refresh{note}. Internal resolution will not change this.")
    if verdict == VERDICT_OK:
        note = "" if held is None else f" ({held}% of frames)"
        return ("Ok", f"60 FPS: holding{note}.")
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
