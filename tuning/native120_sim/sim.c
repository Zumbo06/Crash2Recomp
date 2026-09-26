/* Offline check of native 120 FPS (crash2_60fps.h) against a model of the
 * machine it drives.
 *
 * crash2_60fps.h is included exactly as interrupts.c includes it, with the
 * runtime services it calls stubbed here. Guest RAM holds the real
 * SCUS-94154 executable, so every signature check reads the real words. The
 * model around it is the part of Crash 2 the mode depends on:
 *
 *   - guest time in CPU cycles; root counter 2 ticking every 32768 cycles
 *     (1033.6 Hz) into 0x8003BEA4; VBlank every 564480 / divisor cycles, the
 *     edge hook called at each edge and then libsnd's chained tick
 *     ([0x8005EFE0]) counted when it is the real tick;
 *   - the frame end at 0x80016810..0x80016988: buffer rotation, VSync(0), the
 *     30 Hz gate (a second VSync when under 25 ticks and the word is the
 *     game's own), the stamp at [DB_NEXT]+12, and func_80016F04's quantized
 *     value at [DB_CUR]+20;
 *   - a loop body of W guest cycles at 100% CPU, divided by the clock the
 *     mode set, that "simulates" [DB_CUR]+20 ticks of physics and calls the
 *     GoolObjectUpdate entry hook once.
 *
 * What it asserts is listed per scenario below. It cannot say anything about
 * how the game LOOKS at 120 - only that the numbers the engine is fed are
 * the ones the design says.
 *
 *   sh tuning/native120_sim/build.sh && tuning/native120_sim/sim.exe
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "mod_plugins.h"

/* Only what crash2_60fps.h touches; the runtime's cpu_state.h brings the
 * whole cycle-accounting layer with it. */
typedef struct CPUState {
    uint32_t gpr[32];
    uint32_t pc;
    uint32_t hi, lo;
    uint32_t cop0[32];
    uint32_t (*read_word)(uint32_t addr);
    void (*write_word)(uint32_t addr, uint32_t value);
} CPUState;

/* --- what crash2_60fps.h takes from interrupts.c ---------------------------- */
#define COP0_SR    12
#define COP0_CAUSE 13
uint32_t i_stat, i_mask;
static int in_exception;

static unsigned s_div = 1;
static void (*s_edge_hook)(void);
void interrupts_set_vblank_divisor(unsigned d) { s_div = d < 1 ? 1 : d > 4 ? 4 : d; }
unsigned interrupts_vblank_divisor(void) { return s_div; }
void psx_set_vblank_edge_hook(void (*fn)(void)) { s_edge_hook = fn; }

/* --- runtime services ---------------------------------------------------------- */
static uint8_t ram[2u << 20];
static uint8_t scratch[1024];
uint8_t *memory_get_ram_ptr(void) { return ram; }
uint8_t *memory_get_scratchpad_ptr(void) { return scratch; }
uint32_t psx_read_word(uint32_t a)
{
    uint32_t v;
    memcpy(&v, ram + (a & 0x1FFFFCu), 4);
    return v;
}
void psx_write_word(uint32_t a, uint32_t v) { memcpy(ram + (a & 0x1FFFFCu), &v, 4); }
static unsigned s_code_writes;
void psx_mod_write_code_word(uint32_t a, uint32_t v) { psx_write_word(a, v); s_code_writes++; }
static unsigned s_cpu_pct = 100;
void psx_crash2_cpu_clock_set(uint32_t p) { s_cpu_pct = p; }
static uint64_t s_host_ms;
uint64_t psx_host_mono_ms(void) { return s_host_ms; }
uint64_t g_vblank_raise_count;
uint64_t psx_cycle_count;
uint32_t psx_cycles_to_next_idle_event(void) { return 0; }
void psx_advance_cycles(uint32_t c) { (void)c; }
int psx_get_in_exception(void) { return 0; }
int g_psx_call_bail, g_precise_mode, g_ls_mode, g_ls_replay_active;
static int s_netplay, s_interp;
int psx_netplay_active(void) { return s_netplay; }
int psx_selfcheck_enabled(void) { return 0; }
int psx_frame_interpolation_active(void) { return s_interp; }
static PSXModFunctionEntryCallback s_gool_entry;
int psx_mod_register_function_entry_plugin(const char *id, uint32_t address,
                                           PSXModFunctionEntryCallback cb)
{
    (void)id; (void)address;
    s_gool_entry = cb;
    return 1;
}

#include "crash2_60fps.h"

/* --- the machine ------------------------------------------------------------ */
#define CYC_PER_TICK   32768ull
#define VBLANK_NTSC    564480ull
#define CPU_HZ         33868800.0
#define TICKS          0x8003BEA4u
#define DB_A           0x80100000u
#define DB_B           0x80110000u
#define GATE_PC        0x8001685Cu

static CPUState cpu;
static uint64_t t;             /* guest cycles */
static uint64_t last_vb;       /* guest cycle of the last VBlank edge */
static double host_speed = 1.0;  /* wall seconds per guest second */
static unsigned long long music_ticks, vblanks, loops, phys_ticks;
static unsigned last_db20;
static int fails;

static void check(int cond, const char *what)
{
    printf("  %s %s\n", cond ? "ok  " : "FAIL", what);
    if (!cond) fails++;
}

static void sync_clock(void)
{
    const uint64_t ticks = t / CYC_PER_TICK;
    psx_write_word(TICKS, (uint32_t)ticks);
    psx_cycle_count = t;
    s_host_ms = (uint64_t)((double)t / CPU_HZ * 1000.0 * host_speed);
}

static uint64_t period(void) { return VBLANK_NTSC / s_div; }

/* Advance guest time to `until`, firing every VBlank edge on the way. */
static void run_to(uint64_t until)
{
    while (last_vb + period() <= until) {
        last_vb += period();
        t = last_vb;
        sync_clock();
        g_vblank_raise_count++;
        vblanks++;
        if (s_edge_hook) s_edge_hook();
        /* libsnd's chain (0x80054E74): the displaced handler, then the tick. */
        if (psx_read_word(0x8005EFE0u) == 0x80054F04u) music_ticks++;
    }
    t = until;
    sync_clock();
}

static void vsync0(void) { run_to(last_vb + period()); }

static uint32_t quantize(int32_t raw)
{
    if (raw < 0) return 34;
    if (raw < 19) return 17;
    if (raw < 36) return 34;
    if (raw < 53) return 51;
    return (uint32_t)raw;
}

/* One trip round the game loop: body, frame end, LOOP_PC. */
static void game_loop(uint64_t work_at_100)
{
    /* Body: physics reads [DB_CUR]+20, scripts run through the entry hook. */
    const uint32_t cur = psx_read_word(0x80063450u);
    last_db20 = psx_read_word(cur + 20u);
    phys_ticks += last_db20;
    if (s_gool_entry) s_gool_entry(&cpu, 0x8001C718u);
    run_to(t + work_at_100 * 100u / s_cpu_pct);

    /* Frame end. */
    const uint32_t a = psx_read_word(0x80063454u), b = psx_read_word(0x80063458u);
    psx_write_word(0x80063450u, a);
    psx_write_word(0x80063454u, b);
    psx_write_word(0x80063458u, a);
    vsync0();
    const uint32_t dbc = psx_read_word(0x80063450u), dbn = psx_read_word(0x80063454u);
    if (psx_read_word(GATE_PC) == 0x2C630019u &&
        psx_read_word(TICKS) - psx_read_word(dbc + 12u) < 25u)
        vsync0();
    psx_write_word(dbn + 12u, psx_read_word(TICKS));
    psx_write_word(dbc + 20u, quantize((int32_t)(psx_read_word(dbn + 12u)
                                                 - psx_read_word(dbc + 12u))));
    loops++;
    crash2_60fps(&cpu, C2_60_LOOP_PC);
}

typedef struct {
    double seconds, loop_hz, vblank_hz, music_hz, phys_per_s, script_hz;
    unsigned ticks_min, ticks_max;
} Window;

static Window measure(double seconds, uint64_t work_at_100)
{
    Window w;
    const uint64_t t0 = t;
    const unsigned long long l0 = loops, v0 = vblanks, m0 = music_ticks,
                             p0 = phys_ticks, s0 = crash2_60fps_script_steps();
    w.ticks_min = 1000u;
    w.ticks_max = 0u;
    while ((double)(t - t0) < seconds * CPU_HZ) {
        game_loop(work_at_100);
        if (last_db20 < w.ticks_min) w.ticks_min = last_db20;
        if (last_db20 > w.ticks_max) w.ticks_max = last_db20;
    }
    w.seconds = (double)(t - t0) / CPU_HZ;
    w.loop_hz = (double)(loops - l0) / w.seconds;
    w.vblank_hz = (double)(vblanks - v0) / w.seconds;
    w.music_hz = (double)(music_ticks - m0) / w.seconds;
    w.phys_per_s = (double)(phys_ticks - p0) / w.seconds;
    w.script_hz = (double)(crash2_60fps_script_steps() - s0) / w.seconds;
    printf("  [%5.1fs] loops %6.2f/s  vblank %6.2f/s  music %5.2f/s  physics "
           "%7.1f ticks/s  scripts %5.2f/s  db+20 %u..%u  div %u  cpu %u%%\n",
           w.seconds, w.loop_hz, w.vblank_hz, w.music_hz, w.phys_per_s,
           w.script_hz, w.ticks_min, w.ticks_max, s_div, s_cpu_pct);
    return w;
}

static int near(double v, double want, double tol) { return fabs(v - want) <= tol; }

static void load_exe(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); exit(2); }
    static uint8_t exe[4u << 20];
    const size_t n = fread(exe, 1, sizeof exe, f);
    fclose(f);
    uint32_t addr, size;
    memcpy(&addr, exe + 0x18, 4);
    memcpy(&size, exe + 0x1C, 4);
    if (n < 0x800u + size) { fprintf(stderr, "short exe\n"); exit(2); }
    memcpy(ram + (addr & 0x1FFFFFu), exe + 0x800, size);
}

static void boot(void)
{
    memset(&cpu, 0, sizeof cpu);
    cpu.read_word = psx_read_word;
    cpu.write_word = psx_write_word;
    cpu.gpr[29] = 0x801FFF00u;
    /* libsnd as SsSetTickMode(1) + SsStart(1) leave it on NTSC. */
    psx_write_word(0x8005EFD8u, 5u);
    psx_write_word(0x8005EFE0u, 0x80054F04u);
    psx_write_word(0x8005EFE4u, 0x8004AE40u);
    psx_write_word(0x80063450u, DB_A);
    psx_write_word(0x80063454u, DB_B);
    psx_write_word(0x80063458u, DB_A);
    psx_write_word(0x8006CD14u, 0u);   /* live play */
    t = last_vb = 0;
    sync_clock();
}

int main(int argc, char **argv)
{
    const char *exe = argc > 1 ? argv[1]
        : "_build/Crash2Recomp/input/SCUS_941.54";
    load_exe(exe);
    boot();
    _putenv("PSX_CRASH2_60FPS=1");
    _putenv("PSX_CRASH2_FPS=120");
    _putenv("PSX_CRASH2_60FPS_CPU_PCT=200");
    _putenv("PSX_CRASH2_120FPS_CPU_PCT=400");

    /* A busy Turtle Woods frame: 700k cycles of work at the stock clock -
     * one 60 Hz field at 200%, one 120 Hz field at 400%. */
    const uint64_t busy = 700000u;

    printf("1. the gate opens, 120 engages, and the engine is fed 8.5 ticks a field\n");
    measure(2.0, busy);
    Window w = measure(10.0, busy);
    check(crash2_60fps_field_hz() == 120 && s_div == 2, "engaged: VBlank at twice NTSC");
    check(near(w.vblank_hz, 119.88, 0.2), "119.88 VBlanks a second");
    check(near(w.loop_hz, 119.88, 0.5), "a new frame every field");
    check(w.ticks_min == 8 && w.ticks_max == 9, "one-field frames carry 8 or 9 ticks");
    check(near(w.phys_per_s, 17.0 * 59.94, 3.0),
          "physics advances 17 ticks per 60 Hz field - the stock timeline");
    check(near(w.music_hz, 59.94, 0.2), "music ticks at 59.94 Hz - tempo unchanged");
    check(near(w.script_hz, 29.97, 0.3), "scripts step at 30 Hz");
    check(s_cpu_pct == 400, "400% clock while engaged");
    check(psx_read_word(C2_60_VS_ARGN_PC) == C2_60_VS_ARGN_WIDE4 &&
          psx_read_word(C2_60_VS_ARG1_PC) == C2_60_VS_ARG1_WIDE4 &&
          crash2_60fps_vsync_wide() == 2, "VSync's timeout widened four times");
    check(crash2_60fps_hold_pct() >= 99, "the hold judge sees one-field frames");

    printf("2. a loop-less stretch (a load) drops to NTSC, quick loops bring 120 back\n");
    {
        const unsigned long long m0 = music_ticks, v0 = vblanks;
        const uint64_t t0 = t;
        game_loop(busy + 20u * 564480u * 4u);   /* ~330 ms of loading at 400% */
        const double secs = (double)(t - t0) / CPU_HZ;
        /* 12 fields at 120 Hz before the drop, then NTSC for the rest. */
        const double want_vbl = C2_120_STALL_FIELDS + (secs - C2_120_STALL_FIELDS / 120.0) * 60.0;
        check(crash2_60fps_fast_stalls() == 1, "one stall counted");
        check(near((double)(music_ticks - m0) / secs, 59.94, 4.0),
              "music keeps its tempo through the drop");
        check(near((double)(vblanks - v0), want_vbl, 2.0),
              "VBlank fell back to NTSC after 12 loop-less fields");
        check(psx_read_word(0x8005EFE0u) == 0x80054F04u, "the tick pointer is the tick");
    }
    game_loop(busy);
    check(s_div == 1, "still NTSC right after the load");
    for (int i = 0; i < 6; i++) game_loop(busy);
    check(s_div == 2 && crash2_60fps_field_hz() == 120, "re-armed after quick loops");
    w = measure(3.0, busy);
    check(near(w.music_hz, 59.94, 0.3), "tempo still right after re-arming");

    printf("3. a scene that cannot fit a 120 Hz field steps down to 60, gate open\n");
    /* 1.5M at the stock clock is 375k at 400%: two 120 Hz fields, one NTSC
     * field - but only if the step down keeps the fast clock. At 200% it
     * would be 750k, over a whole NTSC field, and fall through to 30. */
    const uint64_t heavy = 1500000u;
    w = measure(4.0, heavy);
    check(crash2_60fps_field_hz() == 60 && s_div == 1, "stepped down to 60");
    check(crash2_60fps_gate_open(), "the gate stays open");
    check(crash2_60fps_fast_fallbacks() == 1, "one fallback counted");
    w = measure(4.0, heavy);
    check(near(w.loop_hz, 59.94, 0.5) && w.ticks_min == 17 && w.ticks_max == 17,
          "holds 60 on the quantizer's own 17");
    check(s_cpu_pct == 400 && crash2_60fps_vsync_wide() == 2,
          "keeps the fast clock and its x4 timeout at 60");
    check(near(w.music_hz, 59.94, 0.2), "music at 59.94 Hz at 60 too");
    check(near(w.phys_per_s, 17.0 * 59.94, 3.0), "same physics timeline at 60");
    check(near(w.script_hz, 29.97, 0.3), "scripts at 30 Hz at 60");
    measure(14.0, heavy);   /* the retry after 10 s fails again */
    check(crash2_60fps_fast_fallbacks() == 2, "retried 120 after 10 s and fell back again");
    check(crash2_60fps_gate_open() && crash2_60fps_backoff_count() == 0,
          "and never closed the gate");

    printf("4. a quiet window forgives, and the scene back in budget holds 120\n");
    for (int i = 0; i < 12; i++) game_loop(busy + 30u * 564480u);  /* a load screen */
    measure(2.0, busy);
    w = measure(4.0, busy);
    check(crash2_60fps_field_hz() == 120, "120 again after the quiet window");
    check(near(w.loop_hz, 119.88, 0.5), "a new frame every field again");
    check(crash2_60fps_fast_fallbacks() == 2, "no new fallback");

    printf("5. frames of three fields get 25/26 ticks, not the quantizer's 34\n");
    {
        c2_60_win_ms = 0;             /* no verdict inside the next 0.5 s */
        double sum = 0.0;
        unsigned n = 0, seen = 0;
        game_loop(2700000u);          /* its physics read the last 1-field frame */
        for (int i = 0; i < 19; i++) {
            /* 675k at 400%: 2.4 fields of work, ending on the third */
            game_loop(2700000u);
            sum += last_db20;
            n++;
            if (last_db20 == 25 || last_db20 == 26) seen++;
        }
        check(seen == n, "every three-field frame reads 25 or 26");
        check(near(sum / n, 25.5, 0.1), "8.5 ticks a field on average");
        check(crash2_60fps_field_hz() == 120, "still engaged (no window judged)");
    }

    printf("6. frame interpolation and netplay never engage 120\n");
    crash2_60fps_set_target_fps(60);
    check(s_div == 1 && crash2_60fps_field_hz() == 60, "switching to 60 drops at once");
    s_interp = 1;
    crash2_60fps_set_target_fps(120);
    measure(3.0, busy);
    check(s_div == 1, "interpolation on: stays at 60");
    s_interp = 0;
    s_netplay = 1;
    measure(2.0, busy);
    check(s_div == 1, "netplay: stays at 60");
    s_netplay = 0;
    measure(2.0, busy);
    check(s_div == 2, "neither: 120");

    printf("7. \"Prefer 60\" still steps 120 down, and never closes the gate\n");
    c2_60_force_gate = 1;
    measure(5.0, heavy);
    check(crash2_60fps_field_hz() == 60 && crash2_60fps_gate_open(),
          "forced gate: 120 -> 60, gate open");
    c2_60_force_gate = 0;

    printf("8. a savestate carrying the stand-in is repaired; mode off undoes it all\n");
    crash2_60fps_set_target_fps(60);
    psx_write_word(0x8005EFE0u, C2_120_SND_NOP);
    crash2_60fps_note_restore();
    game_loop(busy);
    check(psx_read_word(0x8005EFE0u) == 0x80054F04u, "restored RAM gets the tick back");
    crash2_60fps_set_target_fps(120);
    measure(3.0, busy);
    check(s_div == 2, "engaged before switching the mode off");
    crash2_60fps_set_mode(0);
    game_loop(busy);
    check(s_div == 1, "NTSC VBlank");
    check(psx_read_word(0x8005EFE0u) == 0x80054F04u, "tick pointer restored");
    check(psx_read_word(GATE_PC) == C2_60_GATE_ORIG, "30 Hz gate word restored");
    check(psx_read_word(C2_60_VS_ARGN_PC) == C2_60_VS_ARGN_ORIG &&
          psx_read_word(C2_60_VS_ARG1_PC) == C2_60_VS_ARG1_ORIG, "VSync words restored");
    check(s_cpu_pct == 100, "stock clock");
    w = measure(3.0, busy);
    check(near(w.loop_hz, 29.97, 0.3) && w.ticks_min == 34, "stock 30 Hz, 34 ticks");
    check(near(w.music_hz, 59.94, 0.2), "music at 59.94 Hz at stock");

    printf("\n%s\n", fails ? "FAILED" : "ALL CHECKS PASSED");
    return fails ? 1 : 0;
}
