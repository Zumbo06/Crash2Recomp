"""Capture and analyse the SPU event ring from a running game.

The runtime records KEYON / KEYOFF / END_STOP / END_LOOP / IRQ per voice, with
the pitch, ADSR words and L/R volumes latched at the event. That is enough to
tell a voice that was never keyed from one that was keyed silently - the first
fork in diagnosing a dropped sound effect.

Two protocol details that are easy to get wrong:
  * the server closes the socket after each response, so every command needs a
    fresh connection (reusing one looks exactly like a response-size limit and
    is not one)
  * `kind` arrives as a name string, the voice field is "v", and the numeric
    fields are hex strings like "0x1234"

Usage:
    python spu_capture.py [warmup_seconds] [capture_seconds]
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections import Counter, defaultdict

PROJ = r"c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\Crash2Recomp"
BUILD = PROJ + r"\build-debugtools"
EXE = BUILD + r"\Crash_Bandicoot_2_Recompiled.exe"
PORT = 4370


def ask(cmd: str, **kw) -> dict:
    """Send one command on its own connection and return the parsed reply."""
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


def kind_of(e: dict) -> str:
    return str(e.get("kind", "?"))


def voice_of(e: dict) -> int:
    return int(e.get("v", e.get("voice", -1)))


def hexval(e: dict, key: str) -> int:
    v = e.get(key, 0)
    if isinstance(v, str):
        try:
            return int(v, 16)
        except ValueError:
            return 0
    return int(v or 0)


def analyse(events: list[dict]) -> None:
    print("event mix:", dict(Counter(kind_of(e) for e in events)))

    per_voice: dict[int, Counter] = defaultdict(Counter)
    for e in events:
        per_voice[voice_of(e)][kind_of(e)] += 1

    print("\n voice  KEYON  KEYOFF  END_LOOP  END_STOP")
    for v in sorted(per_voice):
        c = per_voice[v]
        print("  v%-4d %5d %7d %9d %9d" % (
            v, c["KEYON"], c["KEYOFF"], c["END_LOOP"], c["END_STOP"]))

    # A voice keyed on with both volumes zero can never be heard: the game asked
    # for the sound but nothing reached the mix.
    keyons = [e for e in events if kind_of(e) == "KEYON"]
    silent = [e for e in keyons
              if not hexval(e, "vol_l") and not hexval(e, "vol_r")]
    print("\nKEYON total: %d   with BOTH volumes zero: %d"
          % (len(keyons), len(silent)))
    for e in silent[:8]:
        print("   v%d addr=0x%06X adsr=%04X%04X"
              % (voice_of(e), hexval(e, "addr"),
                 hexval(e, "adsr_hi"), hexval(e, "adsr_lo")))

    by_voice: dict[int, list[dict]] = defaultdict(list)
    for e in sorted(events, key=lambda x: x.get("seq", 0)):
        by_voice[voice_of(e)].append(e)

    # KEYON whose very next event on the same voice is END_STOP in the same or
    # next frame: the first decoded block already carried the END flag - a bad
    # start address, or sample data that is not where the game thinks it is.
    instant = []
    steals = 0
    for v, seq in by_voice.items():
        for a, b in zip(seq, seq[1:]):
            ka, kb = kind_of(a), kind_of(b)
            if ka == "KEYON" and kb == "END_STOP" and \
                    b.get("frame", 0) - a.get("frame", 0) <= 1:
                instant.append((v, a))
            if ka == "KEYON" and kb == "KEYON":
                steals += 1

    print("KEYON -> END_STOP within 1 frame (ends instantly): %d" % len(instant))
    for v, e in instant[:8]:
        print("   v%d addr=0x%06X pitch=0x%04X"
              % (v, hexval(e, "addr"), hexval(e, "pitch")))
    print("KEYON -> KEYON with no end between (voice re-keyed while busy): %d"
          % steals)


def attach() -> int:
    """Analyse a game that is ALREADY running with --debug-port 4370.

    Launch the game yourself with the debug server on (launcher -> Settings ->
    Debug server port; whatever value you set is the port), play to the spot
    that fails, then run:

        python spu_capture.py attach [port]

    It clears the ring, waits for you to trigger the failing sound a few times,
    then reports. Attract-mode audio is not a substitute for this: the failure
    is level dependent, so the capture has to come from the level that fails.

    NOTE: the debug server only exists in the build-debugtools tree - the
    release build compiles it out (PSX_NO_DEBUG_TOOLS), so a port set there
    silently listens on nothing.
    """
    try:
        ask("spu_status")
    except Exception:
        print("Nothing answering on port %d." % PORT)
        print("  * launch from build-debugtools (the release build strips the")
        print("    debug server entirely), and")
        print("  * set the launcher's Debug server port to %d," % PORT)
        print("    or pass the port: python spu_capture.py attach <port>")
        return 1

    ask("spu_events_reset")
    print("Ring cleared. NOW trigger the failing sound 5-10 times "
          "(smash crates), then press Enter here.")
    try:
        input()
    except EOFError:
        time.sleep(20)

    resp = ask("spu_events", count=256)
    events = resp.get("events", [])
    print("captured %d events (ring total %s)\n"
          % (len(events), resp.get("total")))
    if not events:
        print("NO EVENTS - nothing reached the SPU at all.")
        return 1
    analyse(events)

    voice_report(ask("spu_voices"))
    return 0


PHASE = {0: "ATTACK", 1: "DECAY", 2: "SUSTAIN", 3: "RELEASE"}


def voice_report(resp: dict) -> None:
    """Why the game cannot find a free voice.

    Crash 2 picks a free voice by polling each voice's live envelope level and
    treating env == 0 as available (see the CURVOL comment in spu.c). So a voice
    parked in RELEASE with a small but NON-ZERO envelope is invisible as free -
    it occupies the pool without making sound. Enough of those and the game has
    to steal a voice that is still playing, which truncates that sound.
    """
    vs = resp.get("voices", [])
    live = [v for v in vs if v.get("active")]
    print("\nlive voices right now: %d of %d" % (len(live), len(vs)))
    print("endx=%s active_mask=%s" % (resp.get("endx"), resp.get("active_mask")))

    stuck = []
    print("\n  v  act  phase     env     Rr  exp  (Rr = release rate the GAME asked for,")
    print("                                       0 = fastest, 31 = slowest)")
    for v in vs:
        if not v.get("active"):
            continue
        env = hexval(v, "env")
        ph = v.get("env_phase", -1)
        # ADSR is one 32-bit word split across two registers. Release rate is
        # bits 16..20, its exponential flag bit 21 - same decode as adsr_run().
        raw = hexval(v, "adsr_lo") | (hexval(v, "adsr_hi") << 16)
        rr = (raw >> 16) & 0x1F
        rexp = (raw >> 21) & 1
        print("  %-3d %-4d %-8s 0x%04X  %2d   %d"
              % (v.get("v"), v.get("active"), PHASE.get(ph, str(ph)),
                 env, rr, rexp))
        if ph == 3 and env != 0:
            stuck.append((v.get("v"), env, rr))

    print("\nvoices parked in RELEASE with a non-zero envelope: %d" % len(stuck))
    if stuck:
        print("  ^^ these look busy to the game but make no sound.")
        print("     %s" % ", ".join("v%d=0x%04X(Rr=%d)" % (n, e, r)
                                    for n, e, r in stuck))
        # Did the envelope MOVE? 0x7FF7 is the sustain clamp, i.e. exactly where
        # a voice sits the instant it is keyed off. Anything below it has
        # already decayed some distance, which is proof the envelope is running.
        # Rr is 0..31 (0 fastest); only a genuinely quick rate pinned at the
        # clamp would indicate a broken envelope, so judge on movement, not on
        # an arbitrary rate threshold.
        SUSTAIN_CLAMP = 0x7FF7
        moved = [n for n, e, _ in stuck if e < SUSTAIN_CLAMP]
        pinned_fast = [n for n, e, r in stuck
                       if e >= SUSTAIN_CLAMP and r <= 7]
        print("  >> %d of %d have decayed below the key-off level (0x%04X)"
              % (len(moved), len(stuck), SUSTAIN_CLAMP))
        if moved:
            print("     %s - the envelope IS running for these."
                  % ", ".join("v%d" % n for n in moved))
        if pinned_fast:
            print("  >> %s asked for a FAST release (Rr<=7) yet sit at the"
                  % ", ".join("v%d" % n for n in pinned_fast))
            print("     key-off level. That would be an envelope bug.")
        else:
            print("  >> nothing with a fast rate is pinned. Voices held high all")
            print("     asked for slow releases (high Rr), which is what the")
            print("     game requested - so the release rate is NOT the bug.")
        print("  If this number is high while sounds are dropping, the release")
        print("  decay is too slow and the free-voice pool is being starved.")


def release_watch(samples: int = 8, gap: float = 0.25) -> int:
    """Is a RELEASE voice actually decaying, or is it stuck?

    A single snapshot cannot tell those apart: a voice one sample after key-off
    legitimately sits at full envelope. So sample the same voices repeatedly and
    watch the envelope move. A voice in RELEASE whose env never falls is
    occupying the free-voice pool forever, and that is what forces the game to
    steal a voice that is still sounding.
    """
    try:
        ask("spu_status")
    except Exception:
        print("Nothing answering on port %d - launch the game first." % PORT)
        return 1

    hist: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for _ in range(samples):
        for v in ask("spu_voices").get("voices", []):
            if v.get("active"):
                hist[int(v["v"])].append((v.get("env_phase", -1), hexval(v, "env")))
        time.sleep(gap)

    span = samples * gap
    print("\nenvelope movement over %.1fs (%d samples)\n" % (span, samples))
    print("  v   phases seen        env first -> last     verdict")
    stuck = []
    for v in sorted(hist):
        seq = hist[v]
        phases = {PHASE.get(p, str(p)) for p, _ in seq}
        first, last = seq[0][1], seq[-1][1]
        rel = [e for p, e in seq if p == 3]
        # Only judge voices that were in RELEASE for the whole window.
        if len(rel) == len(seq) and len(seq) > 2:
            if last >= first:
                verdict = "STUCK - env never fell"
                stuck.append(v)
            elif last > 0x2000:
                verdict = "decaying, still high"
            else:
                verdict = "decaying ok"
        else:
            verdict = "-"
        print("  %-3d %-18s 0x%04X -> 0x%04X   %s"
              % (v, ",".join(sorted(phases)), first, last, verdict))

    print("\nvoices in RELEASE the whole window with no envelope decay: %d"
          % len(stuck))
    if stuck:
        print("  >> %s" % ", ".join("v%d" % v for v in stuck))
        print("  These never reach env == 0, so the game never sees them free.")
        print("  That is a release-rate bug in the SPU envelope, not the game.")
    else:
        print("  Release decay is working. Voice pressure is the game's own")
        print("  allocation under load, not an envelope bug - the fix would")
        print("  have to be elsewhere.")
    return 0


def audio_report(seconds: float = 20.0) -> int:
    """Is the audio OUTPUT pipeline dropping sound, independently of the SPU?

    The SPU can mix a sound perfectly and still have it never reach the speakers
    if the output ring underruns or the pump skips. That failure is inherently
    intermittent - which fits "works, but not always" far better than any
    deterministic voice-logic bug does.

    Samples the counters twice so the numbers are a RATE over the window, not a
    total since boot (which says nothing about what just happened).
    """
    try:
        before = ask("audio_stats")
    except Exception:
        print("Nothing answering on port %d - launch the game first." % PORT)
        return 1

    print("Baseline taken. Play for ~%.0fs, ideally triggering the dropouts,"
          % seconds)
    print("then press Enter (or wait).")
    try:
        input()
    except EOFError:
        time.sleep(seconds)
    after = ask("audio_stats")

    def delta(key: str) -> int:
        return int(after.get(key, 0) or 0) - int(before.get(key, 0) or 0)

    print("\n=== audio pipeline over the window ===")
    rows = [
        ("pump calls", "pump_calls"),
        ("pump SKIPS", "pump_skips"),
        ("UNDERRUNS", "underruns"),
        ("mutes", "mutes"),
        ("unmutes", "unmutes"),
    ]
    for label, key in rows:
        print("  %-12s %d" % (label, delta(key)))

    out = after.get("out") or {}
    out0 = before.get("out") or {}
    print("\n=== output device ===")
    for label, key in (("underruns", "underruns"),
                       ("overflow drops", "overflow_drops"),
                       ("corrections", "correction")):
        d = int(out.get(key, 0) or 0) - int(out0.get(key, 0) or 0)
        print("  %-15s %s" % (label, d))
    for key in ("mode", "host_rate", "fill_ms", "target_ms", "active"):
        if key in out:
            print("  %-15s %s" % (key, out[key]))

    print("\n=== per-tap audio (frames that actually carried sound) ===")
    for tap in after.get("taps", []):
        name = tap.get("name", "?")
        prev = next((t for t in before.get("taps", [])
                     if t.get("name") == name), {})
        fr = int(tap.get("frames", 0)) - int(prev.get("frames", 0) or 0)
        nz = int(tap.get("nonzero", 0)) - int(prev.get("nonzero", 0) or 0)
        au = int(tap.get("audible", 0)) - int(prev.get("audible", 0) or 0)
        print("  %-12s frames=%-7d nonzero=%-7d audible=%-7d peak=%s"
              % (name, fr, nz, au, tap.get("peak")))

    bad = delta("underruns") + delta("pump_skips")
    print()
    if bad:
        print(">> %d underruns/skips in this window. The mix was produced but"
              % bad)
        print("   did not reach the device - that drops sounds intermittently")
        print("   no matter how correct the SPU is. This is the lead to pull.")
    else:
        print(">> No underruns or pump skips: the output pipeline delivered")
        print("   everything the SPU produced. Nothing was lost on the way to")
        print("   the speakers during this window.")
        print()
        print("   If sound is now correct, that was the whole problem.")
        print("   If sounds STILL drop, the cause is upstream of the output -")
        print("   voice allocation or the game itself - and 'attach' is the")
        print("   next capture to run.")
    return 0


def main() -> int:
    global PORT

    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode in ("watch", "attach", "audio"):
        if len(sys.argv) > 2:
            PORT = int(sys.argv[2])
        if mode == "watch":
            return release_watch()
        if mode == "audio":
            return audio_report()
        return attach()

    warmup = float(sys.argv[1]) if len(sys.argv) > 1 else 55
    capture = float(sys.argv[2]) if len(sys.argv) > 2 else 25

    env = dict(os.environ)
    env.update({"PSX_DEV_INPUT": "1", "PSX_VSYNC": "0"})
    proc = subprocess.Popen(
        [EXE, "--no-launcher", "--game", PROJ + r"\game.toml",
         "--renderer", "opengl", "--debug-port", str(PORT)],
        cwd=BUILD, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    try:
        # Read the ring as it stands rather than clearing and waiting: it holds
        # the most recent N events anyway, and a long second wait risks the
        # attract demo ending (which drops the debug server with it).
        time.sleep(warmup)
        if capture > 0:
            print("warmed up; letting %.0fs more audio accumulate..." % capture)
            time.sleep(capture)

        resp = ask("spu_events", count=256)
        events = resp.get("events", [])
        print("captured %d events (ring total %s)\n"
              % (len(events), resp.get("total")))
        if not events:
            print("NO EVENTS - no SPU activity in that window.")
            return 1
        analyse(events)

        vs = ask("spu_voices").get("voices", [])
        live = [v for v in vs if v.get("active")]
        print("\nlive voices at end of capture: %d of %d" % (len(live), len(vs)))
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except Exception:
            proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
