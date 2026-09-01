"""Query the runtime's TCP debug server for widescreen state."""
import json, socket, subprocess, sys, time, os

PROJ = r"c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\Crash2Recomp"
EXE  = PROJ + r"\build-clang\Crash_Bandicoot_2_Recompiled.exe"
PORT = 28000

env = dict(os.environ)
env.update({"PSX_DEV_INPUT": "1", "PSX_VSYNC": "0", "PSX_FPS_TELEMETRY": "1"})
p = subprocess.Popen(
    [EXE, "--no-launcher", "--game", PROJ + r"\game.toml",
     "--renderer", "opengl", "--debug-port", str(PORT)],
    cwd=PROJ + r"\build-clang",
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
try:
    time.sleep(float(sys.argv[1]) if len(sys.argv) > 1 else 45)
    s = socket.create_connection(("127.0.0.1", PORT), timeout=10)
    s.settimeout(10)
    s.sendall(json.dumps({"id": 1, "cmd": "gpu_state"}).encode() + b"\n")
    buf = b""
    while b"\n" not in buf:
        chunk = s.recv(65536)
        if not chunk: break
        buf += chunk
    resp = json.loads(buf.split(b"\n")[0].decode())
    ws = resp.get("ws") or resp.get("result", {}).get("ws")
    print("ws state:", json.dumps(ws, indent=2) if ws else "(no ws key)")
    for k in ("display_w","display_h","width","height","aspect_num","aspect_den"):
        v = resp.get(k) or resp.get("result", {}).get(k)
        if v is not None: print(f"  {k} = {v}")
    s.close()
finally:
    p.terminate()
    try: p.wait(10)
    except Exception: p.kill()
