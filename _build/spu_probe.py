"""Find the largest spu_events batch the debug server can actually deliver."""
import json, os, socket, subprocess, sys, time

PROJ = r"c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\Crash2Recomp"
BUILD = PROJ + r"\build-debugtools"
EXE = BUILD + r"\Crash_Bandicoot_2_Recompiled.exe"
PORT = 4370

def ask(cmd, **kw):
    """One request per connection - the server drops the socket on big writes."""
    s = socket.create_connection(("127.0.0.1", PORT), timeout=15)
    s.settimeout(15)
    m = {"id": 1, "cmd": cmd}; m.update(kw)
    s.sendall(json.dumps(m).encode() + b"\n")
    buf = b""
    try:
        while b"\n" not in buf:
            c = s.recv(1 << 20)
            if not c: break
            buf += c
    finally:
        s.close()
    if not buf: raise RuntimeError("empty response")
    return json.loads(buf.split(b"\n", 1)[0].decode())

env = dict(os.environ); env.update({"PSX_DEV_INPUT": "1", "PSX_VSYNC": "0"})
p = subprocess.Popen([EXE, "--no-launcher", "--game", PROJ + r"\game.toml",
                      "--renderer", "opengl", "--debug-port", str(PORT)],
                     cwd=BUILD, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, env=env)
try:
    time.sleep(float(sys.argv[1]) if len(sys.argv) > 1 else 55)
    for n in (4, 16, 32, 64, 128, 256):
        try:
            r = ask("spu_events", count=n)
            evs = r.get("events", [])
            print(f"  count={n:<4} OK   got={len(evs):<4} total_ring={r.get('total')}")
        except Exception as ex:
            print(f"  count={n:<4} FAIL {type(ex).__name__}")
            break
finally:
    p.terminate()
    try: p.wait(10)
    except Exception: p.kill()
