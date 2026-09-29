/* Model check for crash2_wide_reject.h (the native-wide polygon test).
 *
 * Runs the ACTUAL instructions of each of the seven renderer sites, read from
 * the real SCUS-94154 executable, through a small MIPS interpreter (just the
 * opcodes those sequences use), with the real header hooked in exactly where
 * the generated code calls it: on entering the X-branch block, after the Y
 * branch fell through. Random vertices are weighted toward every edge that
 * matters (0, 512, and the native-wide edges at 14:9 and 16:9).
 *
 *   sh tuning/wide_reject_sim/build.sh
 *   tuning/wide_reject_sim/sim.exe [path/to/SCUS_941.54]
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---- guest memory: 2 MB RAM holding the executable, 1 KB scratchpad ---- */
static uint8_t g_ram[2 * 1024 * 1024];
static uint8_t g_spad[1024];
static uint8_t *mem_at(uint32_t a)
{
    uint32_t p = a & 0x1FFFFFFFu;
    if (p >= 0x1F800000u && p < 0x1F800400u) return g_spad + (p - 0x1F800000u);
    if (p < 0x00200000u) return g_ram + p;
    fprintf(stderr, "bad guest address %08X\n", a);
    exit(2);
}
static uint32_t guest_r32(uint32_t a) { uint32_t v; memcpy(&v, mem_at(a), 4); return v; }
static void guest_w32(uint32_t a, uint32_t v) { memcpy(mem_at(a), &v, 4); }

/* ---- the runtime pieces the header touches ------------------------------ */
typedef struct {
    uint32_t gpr[32];
    uint32_t gte_data[32];
    uint32_t (*read_word)(uint32_t);
} CPUState;
static int g_nw_extra;
static int ws_nw_extra(void) { return g_nw_extra; }

#include "crash2_wide_reject.h"

static int FAILS;
#define CHECK(c, ...) do { if (!(c)) { if (FAILS++ < 20) { printf("  FAIL "); printf(__VA_ARGS__); printf("\n"); } } } while (0)

/* ---- the sites ----------------------------------------------------------- */
typedef struct { const char *name; uint32_t start, xbranch; int kind, n; } Site;
static const Site SITES[] = {
    { "model triangle",       0x80041BDCu, 0x80041C18u, C2WR_TRI,  3 },
    { "world triangle",       0x800424A8u, 0x800424E0u, C2WR_TRI,  3 },
    { "world quad",           0x80042728u, 0x800427A0u, C2WR_QUAD, 4 },
    { "line",                 0x80045154u, 0x8004518Cu, C2WR_LINE, 2 },
    { "dot (right edge only)", 0x800453DCu, 0x80045404u, C2WR_DOT, 1 },
    { "world triangle, loop 2", 0x80045E8Cu, 0x80045EC4u, C2WR_TRI, 3 },
    { "world quad, loop 2",   0x80046048u, 0x800460D0u, C2WR_QUAD, 4 },
};
#define NSITES ((int)(sizeof SITES / sizeof SITES[0]))

static uint32_t pack(int x, int y) { return ((uint32_t)(uint16_t)y << 16) | (uint16_t)x; }

/* ---- a MIPS interpreter for exactly what these sequences use ------------ */
static uint32_t g_next_vertex;          /* what an RTPS in the sequence projects */

static void exec(CPUState *c, uint32_t pc, uint32_t in)
{
    const uint32_t op = in >> 26, rs = (in >> 21) & 31, rt = (in >> 16) & 31,
                   rd = (in >> 11) & 31, sa = (in >> 6) & 31, fn = in & 63;
    const uint32_t imm = in & 0xFFFFu;
    const uint32_t simm = (uint32_t)(int32_t)(int16_t)imm;
    uint32_t *r = c->gpr;
    switch (op) {
    case 0x00:
        switch (fn) {
        case 0x00: if (rd) r[rd] = r[rt] << sa; break;          /* sll (and nop) */
        case 0x02: r[rd] = r[rt] >> sa; break;                  /* srl */
        case 0x21: r[rd] = r[rs] + r[rt]; break;                /* addu */
        case 0x23: r[rd] = r[rs] - r[rt]; break;                /* subu */
        case 0x24: r[rd] = r[rs] & r[rt]; break;                /* and */
        case 0x25: r[rd] = r[rs] | r[rt]; break;                /* or */
        case 0x27: r[rd] = ~(r[rs] | r[rt]); break;             /* nor */
        default: goto bad;
        }
        break;
    case 0x08: case 0x09: r[rt] = r[rs] + simm; break;          /* addi/addiu */
    case 0x0C: r[rt] = r[rs] & imm; break;                      /* andi */
    case 0x0D: r[rt] = r[rs] | imm; break;                      /* ori */
    case 0x0F: r[rt] = imm << 16; break;                        /* lui */
    case 0x23: r[rt] = guest_r32(r[rs] + simm); break;          /* lw */
    case 0x2B: guest_w32(r[rs] + simm, r[rt]); break;           /* sw */
    case 0x3A: guest_w32(r[rs] + simm, c->gte_data[rt]); break; /* swc2 */
    case 0x12:
        if (in & (1u << 25)) {                                  /* GTE command */
            if ((in & 63) != 0x01) goto bad;                    /* only RTPS here */
            c->gte_data[12] = c->gte_data[13];
            c->gte_data[13] = c->gte_data[14];
            c->gte_data[14] = g_next_vertex;
        } else if (rs == 0) {
            r[rt] = c->gte_data[rd];                            /* mfc2 */
        } else if (rs == 2) {
            r[rt] = 0;                                          /* cfc2: FLAG clear */
        } else if (rs == 4) {
            c->gte_data[rd] = r[rt];                            /* mtc2 */
        } else goto bad;
        break;
    default: goto bad;
    }
    r[0] = 0;
    return;
bad:
    fprintf(stderr, "unmodelled instruction %08X at %08X\n", in, pc);
    exit(2);
}

enum { KEPT = 0, REJECT_EARLY = 1, REJECT_X = 2 };

/* Run a site from its first instruction through the X branch. The hook is
 * called where the generated code calls psx_check_interrupts_at: on entering
 * the X-branch block, after the preceding branch fell through. */
static int run_site(const Site *s, const int *vx, const int *vy, CPUState *c, int hook)
{
    memset(c, 0, sizeof *c);
    c->read_word = guest_r32;
    c->gpr[3] = 0x1F800000u;                 /* $v1: scratchpad base */
    c->gpr[14] = 0x80100000u;                /* $t6: some table pointer */
    c->gpr[15] = 0x80100040u;                /* $t7 */
    c->gpr[23] = 0x80110000u;                /* $s7: primitive being built */
    guest_w32(0x1F80021Cu, 0x00D90200u);     /* [v1+540], stored by func_80041E5C */
    guest_w32(0x1F800178u, 0xDEADBEEFu);     /* stale vertex 0: must be overwritten */
    if (s->kind == C2WR_DOT) {
        c->gte_data[14] = pack(vx[0], vy[0]);
    } else {
        for (int i = 0; i < s->n && i < 3; ++i) c->gte_data[12 + i] = pack(vx[i], vy[i]);
    }
    g_next_vertex = s->n == 4 ? pack(vx[3], vy[3]) : 0;
    for (uint32_t pc = s->start;;) {
        if (pc == s->xbranch && hook) crash2_wide_reject(c, pc);
        const uint32_t in = guest_r32(pc);
        if ((in >> 26) == 1 && ((in >> 16) & 31) == 0) {       /* bltz */
            const int taken = (int32_t)c->gpr[(in >> 21) & 31] < 0;
            exec(c, pc + 4, guest_r32(pc + 4));                 /* delay slot */
            if (taken) return pc == s->xbranch ? REJECT_X : REJECT_EARLY;
            if (pc == s->xbranch) return KEPT;
            pc += 8;
            continue;
        }
        exec(c, pc, in);
        pc += 4;
        if (pc > s->xbranch) { fprintf(stderr, "ran past %08X\n", s->xbranch); exit(2); }
    }
}

/* ---- vertex generator, weighted to the edges ----------------------------- */
static uint32_t g_rng = 0x2545F491u;
static uint32_t rnd(void) { g_rng ^= g_rng << 13; g_rng ^= g_rng >> 17; g_rng ^= g_rng << 5; return g_rng; }
static int clampi(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }
static int rand_x(void)
{
    static const int edges[] = { 0, 512, -43, 555, -85, 597, -1024, 1023 };
    switch (rnd() % 4) {
    case 0: return (int)(rnd() % 2048) - 1024;
    default: return clampi(edges[rnd() % 8] + (int)(rnd() % 13) - 6, -1024, 1023);
    }
}
static int rand_y(void)
{
    if (rnd() % 8 == 0) return (int)(rnd() % 2048) - 1024;      /* sometimes off */
    return (int)(rnd() % 217);
}

/* The rule the hook should apply, written independently of the header. */
static int expect_outside(const Site *s, const int *x, int lo, int hi)
{
    int all_left = s->kind != C2WR_DOT, all_right = 1;
    for (int i = 0; i < s->n; ++i) {
        if (x[i] >= lo) all_left = 0;
        if (x[i] < hi) all_right = 0;
    }
    return all_left || all_right;
}

static int same_except_t8_bit31(const CPUState *a, const CPUState *b)
{
    for (int i = 0; i < 32; ++i) {
        uint32_t m = i == 24 ? 0x7FFFFFFFu : 0xFFFFFFFFu;
        if ((a->gpr[i] & m) != (b->gpr[i] & m)) return 0;
        if (a->gte_data[i] != b->gte_data[i]) return 0;
    }
    return 1;
}

int main(int argc, char **argv)
{
    const char *path = argc > 1 ? argv[1] : "_build/Crash2Recomp/input/SCUS_941.54";
    FILE *f = fopen(path, "rb");
    if (!f) { printf("cannot open %s\n", path); return 2; }
    static uint8_t exe[2 * 1024 * 1024];
    size_t got = fread(exe, 1, sizeof exe, f);
    fclose(f);
    uint32_t taddr, tsize;
    memcpy(&taddr, exe + 0x18, 4);
    memcpy(&tsize, exe + 0x1C, 4);
    if (got < 2048 + tsize) { printf("short executable\n"); return 2; }
    memcpy(g_ram + (taddr & 0x1FFFFF), exe + 2048, tsize);

    printf("1. code words\n");
    crash2_wide_reject_control(1, 1);
    g_nw_extra = 170;
    {
        CPUState c;
        int vx[4] = { -50, -60, -70, -80 }, vy[4] = { 10, 20, 30, 40 };
        run_site(&SITES[1], vx, vy, &c, 1);
        unsigned long long s[3]; int on, ok, off;
        crash2_wide_reject_stats(s, &on, &ok, &off);
        CHECK(ok == 1, "the real executable passes the guard (code_ok %d)", ok);
        CHECK(off == 85, "16:9 native-wide reveals 85 per side (%d)", off);
    }

    printf("2. identity whenever native-wide is not live (4:3, squash, 4:3 frames)\n");
    g_nw_extra = 0;
    for (int si = 0; si < NSITES; ++si) {
        const Site *s = &SITES[si];
        int diffs = 0;
        for (int t = 0; t < 20000; ++t) {
            int vx[4], vy[4];
            for (int i = 0; i < 4; ++i) { vx[i] = rand_x(); vy[i] = rand_y(); }
            CPUState a, b;
            int ra = run_site(s, vx, vy, &a, 0), rb = run_site(s, vx, vy, &b, 1);
            if (ra != rb || memcmp(a.gpr, b.gpr, sizeof a.gpr)) diffs++;
        }
        CHECK(diffs == 0, "%s: %d runs changed at margin 0", s->name, diffs);
    }

    printf("3. widened at 14:9 and 16:9: game verdict reproduced, margins drawn\n");
    static const int OFFS[] = { 43, 85 };
    for (int oi = 0; oi < 2; ++oi) {
        const int off = OFFS[oi];
        g_nw_extra = 2 * off;
        for (int si = 0; si < NSITES; ++si) {
            const Site *s = &SITES[si];
            crash2_wide_reject_control(1, 1);
            int wrong = 0, touched = 0, orig_x = 0, now_kept = 0;
            for (int t = 0; t < 40000; ++t) {
                int vx[4], vy[4];
                for (int i = 0; i < 4; ++i) { vx[i] = rand_x(); vy[i] = rand_y(); }
                CPUState a, b;
                const int ra = run_site(s, vx, vy, &a, 0);
                const int rb = run_site(s, vx, vy, &b, 1);
                if (ra == REJECT_EARLY) {          /* the Y test decides; untouched */
                    if (rb != REJECT_EARLY || memcmp(a.gpr, b.gpr, sizeof a.gpr)) wrong++;
                    continue;
                }
                /* the game's own X verdict is the independent rule at [0,512) */
                if ((ra == REJECT_X) != expect_outside(s, vx, 0, 512)) wrong++;
                orig_x += ra == REJECT_X;
                const int want = expect_outside(s, vx, -off, 512 + off) ? REJECT_X : KEPT;
                if (rb != want) wrong++;
                if (ra == REJECT_X && rb == KEPT) now_kept++;
                if (ra == KEPT && rb != KEPT) wrong++;          /* never adds a reject */
                if (!same_except_t8_bit31(&a, &b)) touched++;
            }
            unsigned long long st[3]; int on, ok, o;
            crash2_wide_reject_stats(st, &on, &ok, &o);
            printf("   off %2d  %-24s game dropped %5d, now drawn %5d (hook: checked %llu kept %llu mismatch %llu)\n",
                   off, s->name, orig_x, now_kept, st[0], st[1], st[2]);
            CHECK(wrong == 0, "%s off %d: %d wrong verdicts", s->name, off, wrong);
            CHECK(touched == 0, "%s off %d: %d runs changed other state", s->name, off, touched);
            CHECK(st[2] == 0, "%s off %d: %llu mismatches (vertices not the ones tested)",
                  s->name, off, st[2]);
            CHECK(now_kept > 0 && st[1] == (unsigned long long)now_kept,
                  "%s off %d: margin polygons drawn (%d, hook says %llu)",
                  s->name, off, now_kept, st[1]);
        }
    }

    printf("4. a quad keeps the vertex the projection pushed out of the GTE\n");
    {
        /* Vertex 0 alone reaches into the left margin; 1-3 are far left. */
        g_nw_extra = 170;
        int vx[4] = { -40, -300, -310, -320 }, vy[4] = { 50, 50, 60, 60 };
        CPUState c;
        CHECK(run_site(&SITES[2], vx, vy, &c, 1) == KEPT, "world quad, vertex 0 in the margin");
        CHECK(run_site(&SITES[6], vx, vy, &c, 1) == KEPT, "world quad 2, vertex 0 in the margin");
        vx[0] = -200;
        CHECK(run_site(&SITES[2], vx, vy, &c, 1) == REJECT_X, "world quad, all past the margin");
    }

    printf("5. switched off, and a changed code word, leave the game alone\n");
    {
        g_nw_extra = 170;
        int vx[4] = { -10, -20, -30, -40 }, vy[4] = { 10, 20, 30, 40 };
        CPUState c;
        crash2_wide_reject_control(0, 1);
        CHECK(run_site(&SITES[1], vx, vy, &c, 1) == REJECT_X, "c2_reject on:0 is the game's test");
        crash2_wide_reject_control(1, 1);
        CHECK(run_site(&SITES[1], vx, vy, &c, 1) == KEPT, "on:1 draws it");
        const uint32_t a = 0x80041FA4u, w = guest_r32(a);
        guest_w32(a, 0x37180201u);            /* a different bound */
        crash2_wide_reject_note_restore();
        CHECK(run_site(&SITES[1], vx, vy, &c, 1) == REJECT_X, "changed word: disabled");
        unsigned long long st[3]; int on, ok, o;
        crash2_wide_reject_stats(st, &on, &ok, &o);
        CHECK(ok == 0, "code_ok reports it (%d)", ok);
        guest_w32(a, w);
        crash2_wide_reject_note_restore();
        CHECK(run_site(&SITES[1], vx, vy, &c, 1) == KEPT, "restored word: back on");
    }

    printf("\n%s (%d failure%s)\n", FAILS ? "FAILED" : "ALL CHECKS PASSED", FAILS,
           FAILS == 1 ? "" : "s");
    return FAILS ? 1 : 0;
}
