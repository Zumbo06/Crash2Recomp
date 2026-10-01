/* Model check for crash2_wide_spawn.h (the widescreen object window).
 *
 * Drives the real header against a model of the game: guest RAM holding the
 * real SCUS-94154 executable (so the hook's code-word checks run against the
 * actual bytes), a camera entity written in the engine's own layout, and C
 * copies of what func_8001A13C / func_8001A054 / func_8001A014 /
 * func_8001A23C do to the queue at 0x8006302C. The expected object set at
 * every node is computed independently from the entities' intervals.
 *
 *   sh tuning/wide_spawn_sim/build.sh
 *   tuning/wide_spawn_sim/sim.exe [path/to/SCUS_941.54]
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---- guest RAM -------------------------------------------------------- */
static uint8_t g_ram[2 * 1024 * 1024];
static uint32_t ram_r32(uint32_t a) { uint32_t v; memcpy(&v, g_ram + (a & 0x1FFFFF), 4); return v; }
static uint16_t ram_r16(uint32_t a) { uint16_t v; memcpy(&v, g_ram + (a & 0x1FFFFF), 2); return v; }
static uint8_t  ram_r8(uint32_t a)  { return g_ram[a & 0x1FFFFF]; }
static void     ram_w32(uint32_t a, uint32_t v) { memcpy(g_ram + (a & 0x1FFFFF), &v, 4); }
static void     ram_w16(uint32_t a, uint16_t v) { memcpy(g_ram + (a & 0x1FFFFF), &v, 2); }
#define C2WS_R32(a)    ram_r32(a)
#define C2WS_R16(a)    ram_r16(a)
#define C2WS_R8(a)     ram_r8(a)
#define C2WG_R32(a)    ram_r32(a)   /* crash2_wide_geom.h */
#define C2WG_R16(a)    ram_r16(a)
#define C2WG_R8(a)     ram_r8(a)
#define C2WS_W32(a, v) ram_w32((a), (v))

/* ---- the runtime pieces the header touches ------------------------------ */
typedef struct { uint32_t gpr[32]; uint32_t (*read_word)(uint32_t); } CPUState;
typedef struct { int mode, active, present_native_43, x_margin, nw_extra; uint64_t cur_frame; } GpuWsDebug;
static GpuWsDebug g_ws = { 1, 1, 0, 85, 0, 0 };
static void gpu_ws_get_debug(GpuWsDebug *o) { *o = g_ws; }

#include "crash2_wide_spawn.h"

static int FAILS;
#define CHECK(c, ...) do { if (!(c)) { FAILS++; printf("  FAIL "); printf(__VA_ARGS__); printf("\n"); } } while (0)

/* ---- a level: one camera path of L nodes ------------------------------ */
#define L 40
#define NENT 12
#define CAM 0x80100000u
typedef struct { int s, e; } Iv;               /* present on [s, e]; e = 99 open */
static Iv  g_iv[NENT][3];
static int g_niv[NENT];
static int g_carried[NENT];                    /* only an end row: present before it */
static int g_load_a[L], g_load_b[L];           /* non-empty LA / LB rows */
static int g_spawn_fail_once[NENT];

static uint32_t val_of(int e) { return ((uint32_t)(e & 0xFF) << 24) | ((uint32_t)(100 + e) << 8) | 0u; }
static int ent_of(uint32_t v) { return (int)((v >> 8) & 0xFFFF) - 100; }

/* Rows of the four properties, derived from the intervals. */
typedef struct { int n; int node[64]; int cnt[64]; uint32_t v[64][NENT]; } Rows;
static void add_row(Rows *r, int node, uint32_t v)
{
    for (int i = 0; i < r->n; i++)
        if (r->node[i] == node) { r->v[i][r->cnt[i]++] = v; return; }
    int i = r->n++;
    while (i > 0 && r->node[i - 1] > node) {   /* keep sorted */
        r->node[i] = r->node[i - 1]; r->cnt[i] = r->cnt[i - 1];
        memcpy(r->v[i], r->v[i - 1], sizeof r->v[i]); i--;
    }
    r->node[i] = node; r->cnt[i] = 1; r->v[i][0] = v;
}
static Rows g_A, g_B, g_LA, g_LB;

static void build_rows(void)
{
    memset(&g_A, 0, sizeof g_A); memset(&g_B, 0, sizeof g_B);
    memset(&g_LA, 0, sizeof g_LA); memset(&g_LB, 0, sizeof g_LB);
    for (int e = 0; e < NENT; e++)
        for (int i = 0; i < g_niv[e]; i++) {
            if (!(g_carried[e] && i == 0)) add_row(&g_B, g_iv[e][i].s, val_of(e));
            if (g_iv[e][i].e < 99) add_row(&g_A, g_iv[e][i].e, val_of(e));
        }
    for (int j = 0; j < L; j++) {
        if (g_load_a[j]) add_row(&g_LA, j, 0x00001234u);
        if (g_load_b[j]) add_row(&g_LB, j, 0x00005678u);
    }
}

/* Write a camera entity in the engine layout 0x80031AE8 reads. */
static void write_entity(void)
{
    memset(g_ram + (CAM & 0x1FFFFF), 0, 0x8000);
    const Rows *props[4] = { &g_A, &g_B, &g_LA, &g_LB };
    const int ids[4] = { 0x13B, 0x13C, 0x208, 0x209 };
    ram_w16(CAM + 12, 4);
    uint32_t data = CAM + 0x100;
    for (int p = 0; p < 4; p++) {
        const Rows *r = props[p];
        const uint32_t h = CAM + 16 + 8u * (uint32_t)p;
        ram_w16(h, (uint16_t)ids[p]);
        ram_w16(h + 2, (uint16_t)(data - CAM - 12));
        g_ram[(h + 4) & 0x1FFFFF] = 0x40 | 0x20 | 4;   /* sparse, metavalues, type */
        g_ram[(h + 5) & 0x1FFFFF] = 4;
        ram_w16(h + 6, (uint16_t)r->n);
        uint32_t a = data;
        for (int i = 0; i < r->n; i++) ram_w16(a + 2u * (uint32_t)i, (uint16_t)r->cnt[i]);
        a += 2u * (uint32_t)r->n;
        for (int i = 0; i < r->n; i++) ram_w16(a + 2u * (uint32_t)i, (uint16_t)r->node[i]);
        a = (a + 2u * (uint32_t)r->n + 3u) & ~3u;
        for (int i = 0; i < r->n; i++)
            for (int j = 0; j < r->cnt[i]; j++) { ram_w32(a, r->v[i][j]); a += 4; }
        data = (a + 15u) & ~15u;
    }
}

/* ---- the game: queue, steps, flush ----------------------------------- */
static int g_alive[NENT];
static uint32_t q_n(void) { return ram_r32(C2WS_Q_COUNT); }
static uint32_t q_at(uint32_t i, int f) { return ram_r32(C2WS_Q_BASE + i * 12u + 4u * (uint32_t)f); }

static void game_queue(const Rows *r, int node, uint32_t action)   /* func_8001A054 */
{
    for (int i = 0; i < r->n; i++) {
        if (r->node[i] != node) continue;
        for (int j = r->cnt[i] - 1; j >= 0; j--) {
            const uint32_t v = r->v[i][j];
            uint32_t n = q_n(), k;
            for (k = 0; k < n; k++)
                if ((q_at(k, 0) & 0x00FFFF00u) == (v & 0x00FFFF00u)) {
                    ram_w32(C2WS_Q_BASE + k * 12u, v);
                    ram_w32(C2WS_Q_BASE + k * 12u + 8u, action);
                    break;
                }
            if (k == n) {
                ram_w32(C2WS_Q_BASE + n * 12u, v);
                ram_w32(C2WS_Q_BASE + n * 12u + 4u, 0);
                ram_w32(C2WS_Q_BASE + n * 12u + 8u, action);
                ram_w32(C2WS_Q_COUNT, n + 1);
            }
        }
    }
}

static void game_step(int n, int dir)                               /* func_8001A13C */
{
    if (dir == 2) { game_queue(&g_A, n - 1, C2WS_KILL); game_queue(&g_B, n, C2WS_SPAWN); }
    else          { game_queue(&g_B, n + 1, C2WS_KILL); game_queue(&g_A, n, C2WS_SPAWN); }
}

static void game_mark_all(void)                                     /* func_8001A014 */
{
    for (uint32_t i = 0; i < q_n(); i++) ram_w32(C2WS_Q_BASE + i * 12u + 8u, C2WS_KILL);
}

static void q_remove(uint32_t i)
{
    const uint32_t n = q_n();
    for (uint32_t k = i + 1; k < n; k++)
        for (int f = 0; f < 3; f++)
            ram_w32(C2WS_Q_BASE + (k - 1) * 12u + 4u * (uint32_t)f, q_at(k, f));
    ram_w32(C2WS_Q_COUNT, n - 1);
}

static void game_flush(void)                                        /* func_8001A23C(-1) */
{
    for (int i = (int)q_n() - 1; i >= 0; i--) {
        const uint32_t v = q_at((uint32_t)i, 0), act = q_at((uint32_t)i, 2);
        const int e = ent_of(v);
        if (act == C2WS_KILL) {
            if (e >= 0 && e < NENT) g_alive[e] = 0;
            q_remove((uint32_t)i);
        } else if (act == C2WS_SPAWN) {
            if (e >= 0 && e < NENT && !g_alive[e]) {
                if (g_spawn_fail_once[e]) { g_spawn_fail_once[e] = 0; q_remove((uint32_t)i); continue; }
                g_alive[e] = 1;
                ram_w32(C2WS_Q_BASE + (uint32_t)i * 12u + 4u, 0x80123456u);
            }
            ram_w32(C2WS_Q_BASE + (uint32_t)i * 12u + 8u, 0);
        }
    }
}

/* One call of func_8001A13C with the hook around it, as the camera does. */
static CPUState g_cpu;
static void call_step(int n, int dir, uint32_t ra)
{
    g_cpu.gpr[31] = ra; g_cpu.gpr[4] = CAM; g_cpu.gpr[5] = (uint32_t)(n << 8);
    g_cpu.gpr[6] = (uint32_t)dir; g_cpu.gpr[21] = CAM;
    crash2_wide_spawn(&g_cpu, C2WS_FN);
    game_step(n, dir);
    g_cpu.gpr[4] = 0xDEADBEEFu;                 /* clobbered by the callee */
    crash2_wide_spawn(&g_cpu, ra);
}

static int g_node;
/* Path entry (or jump): mark all, replay from the nearer end, flush. */
static void enter(int target)
{
    game_mark_all();
    if (target < L / 2) {
        call_step(0, 2, C2WS_RA_ENTRY_0);
        for (int n = 1; n <= target; n++) call_step(n, 2, C2WS_RA_STEP);
    } else {
        call_step(L - 1, 1, C2WS_RA_ENTRY_L);
        for (int n = L - 2; n >= target; n--) call_step(n, 1, C2WS_RA_STEP);
    }
    game_flush();
    g_node = target;
}
/* The malformed entities, recorded along a camera route with the hook off,
 * must read the same along the same route with it on. */
static int g_trace[512][2], g_ntrace, g_tracing;
static void note_malformed(void)
{
    if (g_ntrace >= 512) return;
    if (g_tracing == 1) { g_trace[g_ntrace][0] = g_alive[8]; g_trace[g_ntrace][1] = g_alive[9]; }
    else CHECK(g_trace[g_ntrace][0] == g_alive[8] && g_trace[g_ntrace][1] == g_alive[9],
               "malformed entities changed at trace step %d: %d%d vs %d%d", g_ntrace,
               g_alive[8], g_alive[9], g_trace[g_ntrace][0], g_trace[g_ntrace][1]);
    g_ntrace++;
}

static void walk_to(int target)
{
    while (g_node != target) {
        const int d = target > g_node ? 1 : -1;
        g_node += d;
        call_step(g_node, d > 0 ? 2 : 1, C2WS_RA_STEP);
        game_flush();
        if (g_tracing) note_malformed();
    }
}

/* ---- the oracle -------------------------------------------------------- */
static int present(int e, int x)
{
    for (int i = 0; i < g_niv[e]; i++) {
        const int s = (g_carried[e] && i == 0) ? -1000 : g_iv[e][i].s;
        if (x >= s && x <= g_iv[e][i].e) return 1;
    }
    return 0;
}
static int widened(int e, int n, int k)
{
    for (int x = n - k; x <= n + k; x++) if (present(e, x)) return 1;
    return 0;
}
static void expect_set(const char *what, int n, int k)
{
    for (int e = 0; e < 8; e++)
        CHECK(g_alive[e] == widened(e, n, k), "%s node %d: entity %d alive=%d want=%d",
              what, n, e, g_alive[e], widened(e, n, k));
}

/* One fixed camera route over the whole path: both directions, reversals,
 * jumps and entries from both ends. */
static void route(int k, int check)
{
    enter(0);
    if (g_tracing) note_malformed();
    if (check) expect_set("entry", 0, k);
    for (int n = 1; n < L; n++) { walk_to(n); if (check) expect_set("fwd", n, k); }
    for (int n = L - 2; n >= 0; n--) { walk_to(n); if (check) expect_set("bwd", n, k); }
    walk_to(20); if (check) expect_set("fwd again", 20, k);
    walk_to(11); if (check) expect_set("reverse", 11, k);
    walk_to(26); if (check) expect_set("reverse again", 26, k);
    enter(33); if (g_tracing) note_malformed();
    if (check) expect_set("jump to the far end", 33, k);
    walk_to(36);
    enter(12); if (g_tracing) note_malformed();
    if (check) expect_set("jump back", 12, k);
    walk_to(4);
    enter(L - 1); if (g_tracing) note_malformed();
    if (check) expect_set("entry at the last node", L - 1, k);
    for (int n = L - 2; n >= 20; n--) { walk_to(n); if (check) expect_set("bwd from the end", n, k); }
}

static void reset_level(void)
{
    memset(g_iv, 0, sizeof g_iv); memset(g_niv, 0, sizeof g_niv);
    memset(g_carried, 0, sizeof g_carried); memset(g_alive, 0, sizeof g_alive);
    memset(g_load_a, 0, sizeof g_load_a); memset(g_load_b, 0, sizeof g_load_b);
    memset(g_spawn_fail_once, 0, sizeof g_spawn_fail_once);
    ram_w32(C2WS_Q_COUNT, 0);
    crash2_wide_spawn_note_restore();
}
static void iv(int e, int s, int en) { g_iv[e][g_niv[e]].s = s; g_iv[e][g_niv[e]].e = en; g_niv[e]++; }

static void standard_level(void)
{
    reset_level();
    iv(0, 10, 20);
    iv(1, 15, 15);
    iv(2, 5, 8); iv(2, 12, 18);          /* two intervals */
    iv(3, 0, 6);                         /* visible from the first node */
    iv(4, 30, L - 1);                    /* visible to the last node */
    iv(5, 22, 23);
    iv(6, 2, 3); iv(6, 7, 9);            /* gap of 3 */
    iv(7, 25, 35);
    /* Malformed on purpose: an end with no start here, and a start that
     * never ends here. Their state depends on which end the path was entered
     * from; the hook must leave them exactly as the game has them. */
    iv(8, 0, 6); g_carried[8] = 1;
    iv(9, 28, 99);
    build_rows();
    write_entity();
}

int main(int argc, char **argv)
{
    const char *exe = argc > 1 ? argv[1] : "_build/Crash2Recomp/input/SCUS_941.54";
    FILE *f = fopen(exe, "rb");
    if (!f) { printf("cannot open %s\n", exe); return 2; }
    static uint8_t buf[512 * 1024];
    const size_t got = fread(buf, 1, sizeof buf, f);
    fclose(f);
    uint32_t t_addr, t_size;
    memcpy(&t_addr, buf + 0x18, 4); memcpy(&t_size, buf + 0x1C, 4);
    if (got < 2048 + t_size) { printf("short executable\n"); return 2; }
    memcpy(g_ram + (t_addr & 0x1FFFFF), buf + 2048, t_size);
    g_cpu.read_word = ram_r32;

    printf("1. the code words the hook depends on are the real ones\n");
    CHECK(c2ws_code_ok(&g_cpu), "signature check failed against %s", exe);

    printf("2. k = 0 (not widened this frame) changes nothing\n");
    standard_level();
    crash2_wide_spawn_control(2, 1);
    g_ws.active = 0;
    enter(0);
    for (int n = 0; n < L; n++) { walk_to(n); expect_set("k=0 fwd", n, 0); }
    for (int n = L - 1; n >= 0; n--) { walk_to(n); expect_set("k=0 bwd", n, 0); }
    enter(L - 1); expect_set("k=0 entry at the end", L - 1, 0);
    for (int n = L - 2; n >= 0; n--) { walk_to(n); expect_set("k=0 bwd from the end", n, 0); }
    unsigned long long s[8]; int bk, kk, sig;
    crash2_wide_spawn_stats(s, &bk, &kk, &sig);
    CHECK(s[1] == 0 && s[2] == 0 && s[3] == 0, "k=0 acted: early %llu kept %llu retired %llu",
          s[1], s[2], s[3]);
    g_ws.active = 1;

    /* The route with the hook off records what the game does to the
     * malformed entities; with it on they must read the same. */
    standard_level();
    crash2_wide_spawn_control(0, 1);
    g_tracing = 1; g_ntrace = 0;
    route(0, 0);
    g_tracing = 0;
    for (int k = 1; k <= 3; k++) {
        printf("3.%d window k=%d: forward, backward, reversals, jumps\n", k, k);
        standard_level();
        crash2_wide_spawn_control(k, 1);
        g_tracing = 2; g_ntrace = 0;
        route(k, 1);
        g_tracing = 0;
        crash2_wide_spawn_stats(s, &bk, &kk, &sig);
        CHECK(s[1] > 0 && s[2] > 0 && s[3] > 0 && s[7] == 0,
              "k=%d counters: early %llu kept %llu retired %llu bad %llu", k, s[1], s[2], s[3], s[7]);
    }

    printf("4. no early spawn across a load row\n");
    standard_level();
    g_load_a[9] = 1;                     /* forward arrival 9 loads something */
    build_rows(); write_entity();
    crash2_wide_spawn_control(2, 1);
    enter(0);
    walk_to(7); CHECK(!g_alive[0], "entity 0 spawned at node 7 across the load at node 9");
    walk_to(8); CHECK(!g_alive[0], "entity 0 spawned at node 8 across the load at node 9");
    walk_to(9); CHECK(g_alive[0], "entity 0 not spawned early at node 9 (nothing loads at 10)");
    crash2_wide_spawn_stats(s, &bk, &kk, &sig);
    CHECK(s[4] > 0, "unsafe_load not counted");

    printf("5. no late keep across an unload row\n");
    standard_level();
    g_load_b[21] = 1;                    /* forward arrival 22 unloads something */
    build_rows(); write_entity();
    crash2_wide_spawn_control(2, 1);
    enter(0);
    walk_to(21); CHECK(g_alive[0], "entity 0 not kept at node 21 (the original killed it at 21)");
    walk_to(22); CHECK(!g_alive[0], "entity 0 still alive at node 22, after the unload");
    crash2_wide_spawn_stats(s, &bk, &kk, &sig);
    CHECK(s[5] > 0, "unsafe_unload not counted");
    walk_to(10); walk_to(21);
    CHECK(g_alive[0], "entity 0 not kept at 21 on a second pass");

    printf("6. backward travel uses the other lists for loads and unloads\n");
    standard_level();
    g_load_b[23] = 1;                    /* backward arrival 23 loads something */
    build_rows(); write_entity();
    crash2_wide_spawn_control(2, 1);
    enter(L - 1);
    walk_to(24); CHECK(!g_alive[0], "entity 0 spawned early at 24 across the backward load at 23");
    walk_to(22); CHECK(g_alive[0], "entity 0 not spawned early at 22 going backward");

    printf("7. the queue's end is respected\n");
    standard_level();
    crash2_wide_spawn_control(2, 1);
    enter(0);
    walk_to(4);
    /* Fill the queue with unrelated live entries up to the hook's limit
     * (C2WS_Q_CAP - C2WS_Q_HEADROOM = 45), so its next spawn is refused. */
    for (uint32_t i = q_n(); i < 45; i++) {
        ram_w32(C2WS_Q_BASE + i * 12u, (i << 24) | ((2000u + i) << 8));
        ram_w32(C2WS_Q_BASE + i * 12u + 4u, 0x80111111u);
        ram_w32(C2WS_Q_BASE + i * 12u + 8u, 0);
    }
    ram_w32(C2WS_Q_COUNT, 45);
    crash2_wide_spawn_control(-1, 1);
    walk_to(9);
    crash2_wide_spawn_stats(s, &bk, &kk, &sig);
    CHECK(q_n() <= C2WS_Q_CAP - C2WS_Q_HEADROOM + 2, "queue grew to %u", q_n());
    CHECK(s[6] > 0, "full not counted with the queue at 45");

    printf("8. a failed spawn is retried at the next node\n");
    standard_level();
    crash2_wide_spawn_control(2, 1);
    g_spawn_fail_once[0] = 1;
    enter(0);
    walk_to(8); CHECK(!g_alive[0], "entity 0 alive at 8 although its first spawn failed");
    walk_to(9); CHECK(g_alive[0], "entity 0 not retried at node 9");

    printf("9. after a state load, a kept object far from the rows is retired\n");
    standard_level();
    crash2_wide_spawn_control(2, 1);
    enter(0);
    walk_to(21);
    CHECK(g_alive[0], "entity 0 not kept at 21");
    /* The camera skips ahead without the hook seeing the nodes (a state load). */
    g_node = 32;
    crash2_wide_spawn_note_restore();
    walk_to(33);
    CHECK(!g_alive[0], "entity 0 left alive after the load, far from its rows");

    printf("10. a narrower view (14:9) narrows the window\n");
    standard_level();
    crash2_wide_spawn_control(2, 1);
    g_ws.x_margin = 43;
    enter(0);
    for (int n = 1; n < L; n++) { walk_to(n); expect_set("14:9", n, 1); }
    g_ws.x_margin = 85;
    walk_to(20); expect_set("back to 16:9", 20, 2);

    printf("11. a changed executable leaves the game alone\n");
    {
        const uint32_t save = ram_r32(0x8001A090u);
        ram_w32(0x8001A090u, 0x25083030u);
        c2ws_sig = -1;
        CHECK(!c2ws_code_ok(&g_cpu), "a modified queue address still passed");
        ram_w32(0x8001A090u, save);
        c2ws_sig = -1;
        CHECK(c2ws_code_ok(&g_cpu), "the restored word does not pass");
    }

    printf("\n%s\n", FAILS ? "CHECKS FAILED" : "ALL CHECKS PASSED");
    return FAILS ? 1 : 0;
}
