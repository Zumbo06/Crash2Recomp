"""Ask the runtime what the widescreen path is actually doing."""
import json, os, socket, subprocess, sys, time

PROJ = r"c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\Crash2Recomp"
BUILD = PROJ + r"\build-debugtools"
EXE = BUILD + r"\Crash_Bandicoot_2_Recompiled.exe"
PORT = 28711
WAIT = float(sys.argv[1]) if len(sys.argv) > 1 else 50

env = dict(os.environ)
env.update({"PSX_DEV_INPUT": "1", "PSX_VSYNC": "0", "PSX_FPS_TELEMETRY": "1"})
p = subprocess.Popen(
    [EXE, "--no-launcher", "--game", PROJ + r"\game.toml",
     "--renderer", "opengl", "--debug-port", str(PORT)],
    cwd=BUILD, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    env=env, text=True, errors="replace")

def ask(sock, cmd, **kw):
    msg = {"id": 1, "cmd": cmd}
    msg.update(kw)
    sock.sendall(json.dumps(msg).encode() + b"\n")
    buf = b""
    while b"\n" not in buf:
        c = sock.recv(1 << 20)
        if not c:
            break
        buf += c
    return json.loads(buf.split(b"\n")[0].decode())

try:
    time.sleep(WAIT)
    s = socket.create_connection(("127.0.0.1", PORT), timeout=15)
    s.settimeout(15)

    st = ask(s, "gpu_state")
    body = st.get("result", st)
    ws = body.get("ws")
    print("=== ws state ===")
    print(json.dumps(ws, indent=2) if ws else "(no ws key)")
    for k in ("display_w", "display_h", "win_w", "win_h", "aspect_num", "aspect_den"):
        if k in body:
            print(f"  {k} = {body[k]}")
    s.close()
finally:
    p.terminate()
    try:
        out, _ = p.communicate(timeout=10)
    except Exception:
        p.kill()
        out = ""
    print("\n=== runtime widescreen/aspect log ===")
    for line in (out or "").splitlines():
        low = line.lower()
        if any(k in low for k in ("widescreen", "aspect", "clamp", "native-wide", "internal scale")):
            print("  " + line.strip())
