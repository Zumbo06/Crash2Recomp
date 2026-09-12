"""Phase 3 check: is native-wide (mode 2) actually compositing on Crash 2?

ATTACH-ONLY. This never starts the game -- unlike wsdiag.py / wsquery.py, which
spawn their own instance. Start the game yourself, get into a level, then run
this against the running process.

    python _build/ws_phase3.py [port]        # port defaults to 4370

Needs the debugtools build (the TCP debug server is compiled out of the release
tree) and Developer mode -> Debug server port in the launcher.

It reports the four things Phase 3's completion condition asks for:

    mode == 2            native-wide selected, not the GTE squash
    squash == [1,1]      projection left at identity (mode 2 must not squash)
    nw_extra > 0         a genuinely wider surface was allocated
    present_native_43==0 the wide surface is what actually reaches the screen

Blank margins with all four green are EXPECTED at this stage: the compositor is
working and the game is still culling at 4:3. That is missing-content work
(Phase 4), not a compositor failure -- which is exactly the distinction this
script exists to make.
"""
import json
import socket
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4370


def ask(cmd, **kw):
    """One command per connection.

    The server is accept -> recv_line -> respond -> close (debug_server.c
    io_thread_main), so a socket is good for exactly one command; reusing it
    gets WinError 10053 on the second. Reconnect each time.
    """
    msg = {"id": 1, "cmd": cmd}
    msg.update(kw)
    with socket.create_connection(("127.0.0.1", PORT), timeout=15) as sock:
        sock.settimeout(15)
        sock.sendall(json.dumps(msg).encode() + b"\n")
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(1 << 20)
            if not chunk:
                break
            buf += chunk
    if not buf.strip():
        raise SystemExit("no reply to %r (server closed without responding)" % cmd)
    return json.loads(buf.split(b"\n")[0].decode())


def dig(resp, key):
    """The server nests some payloads under 'result'; accept either shape."""
    if key in resp:
        return resp[key]
    return (resp.get("result") or {}).get(key)


try:
    state = ask("gpu_state")
except OSError as exc:
    raise SystemExit(
        "could not reach the debug server on port %d (%s).\n"
        "  - is the game running?\n"
        "  - is it the build-debugtools exe? the release tree has no server\n"
        "  - launcher: Settings -> Performance -> Developer mode, then\n"
        "    Advanced -> Debug server port = %d" % (PORT, exc, PORT))

ws = dig(state, "ws") or {}
if not ws:
    raise SystemExit("gpu_state returned no 'ws' block:\n" + json.dumps(state, indent=2))

# Secondary, and non-fatal: gpu_state already carries mode/nw_extra. This only
# adds the native_wide FLAG, which separates "config never reached the runtime"
# from "mode 2 asked for and refused". No 'on' key => read-only, no live change.
try:
    nw = ask("ws_nw")
except Exception as exc:                                  # noqa: BLE001
    nw = {"native_wide": "(ws_nw failed: %s)" % exc}

mode     = ws.get("mode")
squash   = ws.get("squash")
extra    = ws.get("nw_extra")
native43 = ws.get("present_native_43")
margin   = ws.get("x_margin")
act      = ws.get("activation_margin")

print("raw ws state:")
print(json.dumps(ws, indent=2))
print()

checks = [
    ("mode == 2 (native-wide selected)",      mode == 2,             "mode = %r" % (mode,)),
    ("squash == [1,1] (identity projection)", list(squash or []) == [1, 1],
                                                                     "squash = %r" % (squash,)),
    ("nw_extra > 0 (wider surface)",          isinstance(extra, int) and extra > 0,
                                                                     "nw_extra = %r" % (extra,)),
    ("present_native_43 == 0 (wide present)", native43 == 0,         "present_native_43 = %r" % (native43,)),
]

width = max(len(name) for name, _, _ in checks)
ok = True
for name, passed, detail in checks:
    ok &= passed
    print("  [%s] %-*s   %s" % ("PASS" if passed else "FAIL", width, name, detail))

print()
print("  native_wide flag = %r, mode = %r, nw_extra = %r"
      % (nw.get("native_wide"), nw.get("mode"), nw.get("nw_extra")))
print("  x_margin = %r   activation_margin = %r" % (margin, act))

if ok:
    print("\nPHASE 3 PASS - the compositor is live.")
    print("Expected next: the revealed side columns are EMPTY, because the game")
    print("still culls at 4:3. That is Phase 4 (missing content), not a bug here.")
else:
    print("\nPHASE 3 FAIL - read the failing rows above.")
    if mode != 2:
        print("  mode != 2: native_wide did not reach the runtime. Set it in the")
        print("  launcher BEFORE launching (not via 'ws_nw on=1' -- toggling mode")
        print("  live re-writes emitted cull constants mid-frame and is the known")
        print("  crash path). Confirm game.toml [widescreen] native_wide = true.")
    elif native43 != 0:
        print("  present_native_43 != 0: the frame was classified as FMV or full-2D,")
        print("  so widescreen is deliberately suppressed. Were you in a level, or")
        print("  in a menu/cutscene? Re-run during ordinary gameplay.")
    elif not extra:
        print("  nw_extra == 0 with mode 2: surface allocation was refused. Check")
        print("  the game's stdout for 'native-wide ... allocation failed' and")
        print("  lower [video] supersampling (5 needs ~280 MB of GL surfaces).")
