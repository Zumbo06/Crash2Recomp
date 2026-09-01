"""Diagnose presentation artifacts from the runtime's present ring.

Every SwapWindow records which path it took and the rects it used. For an edge
artifact the interesting questions are:

  * is the path STABLE, or flipping between frames? (wide vs canonical
    alternating would make the edge shimmer)
  * does the wide path fall back? (`wide_fellback`)
  * does present_w disagree with disp_w?

Ring entry layout, from handle_present_ring in debug_server.c:
  [seq, frame, path, present_w, disp_w, disp_h, game_mode, native_43,
   wide_fellback, tag_delta, nw_extra, gte_verts, ovh_prims]

Usage:
    python present_diag.py [warmup] [fullscreen 0|1]
    python present_diag.py attach [port]
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

PROJ = r"c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\Crash2Recomp"
BUILD = PROJ + r"\build-debugtools"
EXE = BUILD + r"\Crash_Bandicoot_2_Recompiled.exe"
PORT = 4370

FIELDS = ["seq", "frame", "path", "present_w", "disp_w", "disp_h", "game_mode",
          "native_43", "wide_fellback", "tag_delta", "nw_extra", "gte_verts",
          "ovh_prims"]


def ask(cmd: str, **kw) -> dict:
    """One request per connection - the server closes the socket after each."""
    sock = socket.create_connection(("127.0.0.1", PORT), timeout=15)
    sock.settimeout(15)
    msg = {"id": 1, "cmd": cmd}
    msg.update(kw)
    sock.sendall(json.dumps(msg).encode() + b"\n")
    buf = b""
    try:
        while b"\n" not in buf:
            chunk = sock.recv(1 << 20)
            if not chunk:
                break
            buf += chunk
    finally:
        sock.close()
    if not buf:
        raise RuntimeError("empty response to " + cmd)
    return json.loads(buf.split(b"\n", 1)[0].decode())


def row(e: list) -> dict:
    return dict(zip(FIELDS, e))


def analyse(events: list[list]) -> None:
    rows = [row(e) for e in events if isinstance(e, list) and len(e) >= 13]
    if not rows:
        print("no usable ring entries")
        return

    print("present paths:", dict(Counter(r["path"] for r in rows)))

    fell = [r for r in rows if r["wide_fellback"]]
    print("wide_fellback set on %d of %d presents" % (len(fell), len(rows)))

    # Path flipping between consecutive presents is the classic cause of an
    # unstable edge: two different source rects alternating frame to frame.
    flips = sum(1 for a, b in zip(rows, rows[1:]) if a["path"] != b["path"])
    print("path changes between consecutive presents: %d" % flips)
    if flips:
        seen = []
        for a, b in zip(rows, rows[1:]):
            if a["path"] != b["path"] and len(seen) < 6:
                seen.append("%s->%s @frame %s" % (a["path"], b["path"], b["frame"]))
        for s in seen:
            print("   " + s)

    # present_w vs disp_w: a mismatch means the presented width is not the
    # width the game is drawing, which shows up at the edges.
    widths = Counter((r["present_w"], r["disp_w"], r["disp_h"]) for r in rows)
    print("\n(present_w, disp_w, disp_h) seen:")
    for (pw, dw, dh), n in widths.most_common(8):
        flag = "   <-- present_w != disp_w" if pw != dw else ""
        print("   %5s %5s %5s   x%d%s" % (pw, dw, dh, n, flag))

    nw = Counter(r["nw_extra"] for r in rows)
    print("nw_extra values:", dict(nw))
    print("native_43 values:", dict(Counter(r["native_43"] for r in rows)))


def grab() -> int:
    resp = ask("present_ring", n=300)
    events = resp.get("events", [])
    print("present ring: %d entries (total %s)\n"
          % (len(events), resp.get("total")))
    if not events:
        print("empty ring")
        return 1
    analyse(events)

    st = ask("gpu_state")
    ws = st.get("ws", {})
    print("\ngpu_state: display %sx%s at (%s,%s)"
          % (st.get("width"), st.get("height"),
             st.get("display_x"), st.get("display_y")))
    print("ws: active=%s mode=%s squash=%s nw_extra=%s present_native_43=%s"
          % (ws.get("active"), ws.get("mode"), ws.get("squash"),
             ws.get("nw_extra"), ws.get("present_native_43")))
    return 0


def set_fullscreen(on: bool) -> None:
    """Point the debugtools tree's settings.toml at the mode under test."""
    p = Path(BUILD) / "settings.toml"
    text = p.read_text(encoding="utf-8") if p.is_file() else "[video]\n"
    if re.search(r"^fullscreen\s*=", text, re.M):
        text = re.sub(r"^fullscreen\s*=.*$", "fullscreen        = %d" % (2 if on else 0),
                      text, flags=re.M)
    else:
        text = text.rstrip() + "\nfullscreen        = %d\n" % (2 if on else 0)
    p.write_text(text, encoding="utf-8")


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "attach":
        if len(sys.argv) > 2:
            global PORT
            PORT = int(sys.argv[2])
        return grab()

    warmup = float(sys.argv[1]) if len(sys.argv) > 1 else 55
    fullscreen = len(sys.argv) > 2 and sys.argv[2] == "1"
    set_fullscreen(fullscreen)
    print("mode: %s" % ("exclusive fullscreen" if fullscreen else "windowed"))

    env = dict(os.environ)
    env.update({"PSX_DEV_INPUT": "1", "PSX_VSYNC": "0"})
    proc = subprocess.Popen(
        [EXE, "--no-launcher", "--game", PROJ + r"\game.toml",
         "--renderer", "opengl", "--debug-port", str(PORT)],
        cwd=BUILD, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    try:
        time.sleep(warmup)
        return grab()
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
