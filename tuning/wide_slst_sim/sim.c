/* Model check for crash2_wide_slst.h (the widescreen scenery range).
 *
 * 1. Every SLST entry on the disc (export.py writes them, with a hash of each
 *    node's list from the validated Python reference) is laid out in guest RAM
 *    the way the game holds it, rebuilt by the header's C port of the game's
 *    forward delta, and every node list compared with the reference.
 * 2. The merge is checked on real lists: the game's list kept intact and in
 *    order, each added polygon once, from a neighbour within k, the union
 *    complete when not capped, never over the cap.
 * 3. The hook end to end: entry captured at 0x8002037C, the render call
 *    redirected only when the game's list is this node's list, the frame's
 *    primitive buffer has room, the view is widened and the code words match.
 *
 *   sh tuning/wide_slst_sim/build.sh
 *   python tuning/wide_slst_sim/export.py slst.bin
 *   tuning/wide_slst_sim/sim.exe slst.bin path/to/SCUS_941.54
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---- guest memory: 2 MB RAM, and the runtime's mod memory at 0x9F000000 -- */
static uint8_t g_ram[2 * 1024 * 1024];
static uint8_t g_mod[64 * 1024];
static uint32_t g_mod_used;
static uint8_t *at(uint32_t a, uint32_t n)
{
    if (a >= 0x9F000000u && a - 0x9F000000u + n <= g_mod_used) return g_mod + (a - 0x9F000000u);
    if ((a & 0xE0000000u) == 0x80000000u && (a & 0x1FFFFFFFu) + n <= sizeof g_ram)
        return g_ram + (a & 0x1FFFFFu);
    return NULL;
}
static uint32_t r32(uint32_t a) { uint8_t *p = at(a, 4); uint32_t v = 0xFFFFFFFFu; if (p) memcpy(&v, p, 4); return v; }
static uint16_t r16(uint32_t a) { uint8_t *p = at(a, 2); uint16_t v = 0xFFFFu; if (p) memcpy(&v, p, 2); return v; }
static void w32(uint32_t a, uint32_t v) { uint8_t *p = at(a, 4); if (p) memcpy(p, &v, 4); }
static void w16(uint32_t a, uint16_t v) { uint8_t *p = at(a, 2); if (p) memcpy(p, &v, 2); }
static uint32_t mod_alloc(uint32_t n)
{
    if (g_mod_used + n > sizeof g_mod) return 0;
    uint32_t a = 0x9F000000u + g_mod_used;
    g_mod_used += (n + 3u) & ~3u;
    return a;
}
#define C2SL_R32(a)    r32(a)
#define C2SL_R16(a)    r16(a)
#define C2WG_R32(a)    r32(a)       /* crash2_wide_geom.h */
#define C2WG_R16(a)    r16(a)
#define C2WG_R8(a)     ((uint8_t)(r16((a) & ~1u) >> (((a) & 1u) * 8u)))
#define C2WG_RAMPTR()  g_ram
#define C2SL_W16(a, v) w16((a), (v))
#define C2SL_ALLOC(n)  mod_alloc(n)

/* ---- the runtime pieces the header touches ------------------------------ */
typedef struct { uint32_t gpr[32]; uint32_t gte_data[32]; uint32_t gte_ctrl[32]; } CPUState;
typedef struct { int mode, active, present_native_43, x_margin, nw_extra; } GpuWsDebug;
static GpuWsDebug g_ws = { 2, 0, 0, 85, 170 };
static void gpu_ws_get_debug(GpuWsDebug *o) { *o = g_ws; }

#include "crash2_wide_slst.h"

static int FAILS;
#define CHECK(c, ...) do { if (!(c)) { if (FAILS++ < 25) { printf("  FAIL "); printf(__VA_ARGS__); printf("\n"); } } } while (0)

static uint64_t fnv(const uint16_t *ids, int n)
{
    uint64_t h = 0xCBF29CE484222325ull;
    for (int i = 0; i < n; ++i) {
        h ^= ids[i] & 0xFFu;  h *= 0x100000001B3ull;
        h ^= ids[i] >> 8;     h *= 0x100000001B3ull;
    }
    return h;
}

/* ---- the exported entries ------------------------------------------------ */
typedef struct { uint32_t len; uint64_t hash; uint32_t missing; } RefNode;
typedef struct {
    uint32_t eid, items, nodes, status;
    uint32_t *ilen; uint8_t **ibytes;
    RefNode *ref;
} RefEntry;
static RefEntry *g_entries;
static int g_nentries;

static int load_export(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    int cap = 4096;
    g_entries = calloc((size_t)cap, sizeof(RefEntry));
    for (;;) {
        uint32_t eid;
        if (fread(&eid, 4, 1, f) != 1 || eid == 0xFFFFFFFFu) break;
        RefEntry *e = &g_entries[g_nentries++];
        e->eid = eid;
        if (fread(&e->items, 4, 1, f) != 1) return 0;
        e->ilen = calloc(e->items, sizeof(uint32_t));
        e->ibytes = calloc(e->items, sizeof(uint8_t *));
        for (uint32_t i = 0; i < e->items; ++i) {
            if (fread(&e->ilen[i], 4, 1, f) != 1) return 0;
            uint32_t padded = (e->ilen[i] + 3u) & ~3u;
            e->ibytes[i] = malloc(padded ? padded : 4);
            if (fread(e->ibytes[i], 1, padded, f) != padded) return 0;
        }
        if (fread(&e->nodes, 4, 1, f) != 1) return 0;
        e->ref = calloc(e->nodes ? e->nodes : 1, sizeof(RefNode));
        for (uint32_t n = 0; n < e->nodes; ++n) {
            if (fread(&e->ref[n].len, 4, 1, f) != 1 || fread(&e->ref[n].hash, 8, 1, f) != 1 ||
                fread(&e->ref[n].missing, 4, 1, f) != 1) return 0;
        }
        if (fread(&e->status, 4, 1, f) != 1) return 0;
        if (g_nentries == cap) return 0;
    }
    fclose(f);
    return g_nentries > 0;
}

#define ENTRY_AT 0x80100000u
/* Lay an entry out the way the game holds it: header, absolute item pointers. */
static void place_entry(const RefEntry *e)
{
    memset(g_ram + (ENTRY_AT & 0x1FFFFFu), 0, 0x60000);
    w32(ENTRY_AT, C2SL_MAGIC);
    w32(ENTRY_AT + 4, e->eid);
    w32(ENTRY_AT + 8, C2SL_TYPE);
    w32(ENTRY_AT + 12, e->items);
    uint32_t p = ENTRY_AT + 16 + 4 * (e->items + 1);
    for (uint32_t i = 0; i < e->items; ++i) {
        w32(ENTRY_AT + 16 + 4 * i, p);
        memcpy(g_ram + (p & 0x1FFFFFu), e->ibytes[i], e->ilen[i]);
        p += (e->ilen[i] + 3u) & ~3u;
    }
    w32(ENTRY_AT + 16 + 4 * e->items, p);
}

/* ---- merge properties ----------------------------------------------------- */
static int check_merge(const C2slEntry *ce, int node, int k, int cap, int verbose_fail)
{
    const uint16_t *game = ce->ids + ce->off[node];
    const int count = ce->len[node];
    memcpy(c2sl_merged, game, sizeof(uint16_t) * (size_t)count);
    C2slJoin no_joins[2];
    int joined = 0;
    memset(no_joins, 0, sizeof no_joins);          /* inside one path */
    const int m = c2sl_merge(ce, node, k, count, cap, no_joins, &joined);
    int bad = 0;
    /* P1: the game's list is a subsequence, in order, starting at element 0 */
    int gi = 0;
    for (int i = 0; i < m && gi < count; ++i)
        if (c2sl_merged[i] == game[gi]) gi++;
    if (gi != count || (count && c2sl_merged[0] != game[0])) bad |= 1;
    /* P2: added keys are new, unique, and come from a node within k */
    static uint8_t seen[65536];
    memset(seen, 0, sizeof seen);
    for (int i = 0; i < count; ++i) seen[c2sl_key(game[i])] = 1;
    static uint8_t near[65536];
    memset(near, 0, sizeof near);
    for (int j = -k; j <= k; ++j) {
        const int nb = node + j;
        if (nb < 0 || nb >= ce->nodes) continue;
        for (int i = 0; i < ce->len[nb]; ++i) near[c2sl_key(ce->ids[ce->off[nb] + i])] = 1;
    }
    int added = 0;
    gi = 0;
    for (int i = 0; i < m; ++i) {
        if (gi < count && c2sl_merged[i] == game[gi]) { gi++; continue; }
        const uint16_t key = c2sl_key(c2sl_merged[i]);
        if (seen[key] == 1 || seen[key] == 2) bad |= 2;
        if (!near[key]) bad |= 4;
        seen[key] = 2;
        added++;
    }
    /* P3: complete when not capped; never over the cap */
    int want = 0;
    for (int key = 0; key < 65536; ++key) if (near[key] && seen[key] == 0) want++;
    if (m > cap) bad |= 8;
    if (m < cap && want != 0) bad |= 16;
    if (bad && verbose_fail)
        printf("  merge node %d k %d: count %d merged %d added %d missing %d bad %d\n",
               node, k, count, m, added, want, bad);
    return bad ? -1 : added;
}

int main(int argc, char **argv)
{
    if (argc < 3) { printf("usage: sim slst.bin SCUS_941.54\n"); return 2; }
    if (!load_export(argv[1])) { printf("cannot read %s\n", argv[1]); return 2; }
    {
        FILE *f = fopen(argv[2], "rb");
        if (!f) { printf("cannot open %s\n", argv[2]); return 2; }
        static uint8_t exe[2 * 1024 * 1024];
        size_t got = fread(exe, 1, sizeof exe, f);
        fclose(f);
        uint32_t taddr, tsize;
        memcpy(&taddr, exe + 0x18, 4);
        memcpy(&tsize, exe + 0x1C, 4);
        if (got < 2048 + tsize) { printf("short executable\n"); return 2; }
        memcpy(g_ram + (taddr & 0x1FFFFF), exe + 2048, tsize);
    }

    printf("1. the C port rebuilds every node list of every SLST entry\n");
    int entries_ok = 0, entries_ref_bad = 0, node_total = 0, node_bad = 0, c_refused = 0;
    static C2slEntry ce;
    for (int i = 0; i < g_nentries; ++i) {
        const RefEntry *re = &g_entries[i];
        place_entry(re);
        c2sl_build_entry(&ce, ENTRY_AT);
        if (re->status == 2) { entries_ref_bad++; continue; }
        if (!ce.ok) { c_refused++; continue; }
        entries_ok++;
        if ((uint32_t)ce.nodes != re->nodes) { node_bad++; continue; }
        for (int n = 0; n < ce.nodes; ++n) {
            node_total++;
            if (ce.len[n] != re->ref[n].len ||
                fnv(ce.ids + ce.off[n], ce.len[n]) != re->ref[n].hash) node_bad++;
        }
    }
    printf("   %d entries rebuilt, %d node lists, %d differ from the reference; "
           "%d refused by C, %d the reference cannot decode\n",
           entries_ok, node_total, node_bad, c_refused, entries_ref_bad);
    CHECK(entries_ok > 1000, "most entries rebuild (%d)", entries_ok);
    CHECK(node_bad == 0, "%d node lists differ from the reference", node_bad);
    CHECK(c_refused <= 4, "C refused %d entries (the reference does not close 4)", c_refused);

    printf("2. merging the neighbours' lists\n");
    long merges = 0, merge_bad = 0, added_total = 0, list_total = 0, capped = 0;
    for (int i = 0; i < g_nentries; ++i) {
        const RefEntry *re = &g_entries[i];
        if (re->status == 2) continue;
        place_entry(re);
        c2sl_build_entry(&ce, ENTRY_AT);
        if (!ce.ok) continue;
        for (int n = 0; n < ce.nodes; n += 3) {
            for (int k = 1; k <= 3; ++k) {
                const int count = ce.len[n];
                int cap = count + count / 2 + 32;
                if (cap > C2SL_MAX_LIST) cap = C2SL_MAX_LIST;
                const int added = check_merge(&ce, n, k, cap, merge_bad < 5);
                merges++;
                if (added < 0) { merge_bad++; continue; }
                if (count + added >= cap) capped++;
                if (k == 2) { added_total += added; list_total += count; }
                /* spot check: node n-1's first polygon missing at n is drawn */
                if (n > 0 && k >= 1 && re->ref[n].missing != 0xFFFFFFFFu && count + added < cap) {
                    const uint16_t want = c2sl_key(ce.ids[ce.off[n - 1] + re->ref[n].missing]);
                    int found = 0;
                    for (int j = 0; j < count + added; ++j)
                        if (c2sl_key(c2sl_merged[j]) == want) { found = 1; break; }
                    if (!found) merge_bad++;
                }
            }
        }
    }
    printf("   %ld merges, %ld wrong, %ld hit the cap; at k=2 the lists grow by %.1f%%\n",
           merges, merge_bad, capped, list_total ? 100.0 * added_total / list_total : 0.0);
    CHECK(merges > 10000 && merge_bad == 0, "merge properties (%ld wrong)", merge_bad);

    printf("3. the hook, end to end\n");
    {
        /* An entry with a node whose neighbours add polygons. */
        int pick = -1, node = -1;
        for (int i = 0; i < g_nentries && pick < 0; ++i) {
            if (g_entries[i].status != 0 || g_entries[i].nodes < 9) continue;
            place_entry(&g_entries[i]);
            c2sl_build_entry(&ce, ENTRY_AT);
            if (!ce.ok) continue;
            for (int n = 3; n < ce.nodes - 3; ++n)
                if (check_merge(&ce, n, 2, C2SL_MAX_LIST, 0) > 20) { pick = i; node = n; break; }
        }
        CHECK(pick >= 0, "found an entry to drive the hook");
        if (pick >= 0) {
            const RefEntry *re = &g_entries[pick];
            place_entry(re);
            c2sl_build_entry(&ce, ENTRY_AT);
            const uint32_t LIST = 0x80180000u, CAM = 0x80170000u, FRAME = C2SL_FRAME0,
                           PRIM = 0x80190000u;
            const uint16_t *mine = ce.ids + ce.off[node];
            const int count = ce.len[node];
            w16(LIST, (uint16_t)count); w16(LIST + 2, 0);
            for (int i = 0; i < count; ++i) w16(LIST + 4 + 2 * i, mine[i]);
            w32(C2SL_LIST_PTR, LIST);
            w32(C2SL_NODE, (uint32_t)node);
            w32(C2SL_CAM, CAM);
            w32(FRAME + 4, PRIM);
            w32(FRAME + 8, PRIM);

            crash2_wide_slst_control(2, 1);
            CPUState cpu;
            memset(&cpu, 0, sizeof cpu);
            cpu.gpr[2] = ENTRY_AT; cpu.gpr[21] = CAM;
            crash2_wide_slst(&cpu, C2SL_ENTRY_RA);              /* capture */
            CHECK(c2sl_code == 1, "the real executable passes the guard");

#define RENDER(ra_) do { memset(&cpu, 0, sizeof cpu); cpu.gpr[31] = (ra_); \
                         cpu.gpr[4] = LIST; cpu.gpr[5] = FRAME; \
                         crash2_wide_slst(&cpu, C2SL_RENDER_FN); } while (0)
            RENDER(C2SL_RA_SITE1);
            CHECK(cpu.gpr[4] == c2sl_buf && c2sl_buf, "site 1 draws the merged list");
            const int m = r16(c2sl_buf);
            CHECK(m > count, "it adds polygons (%d -> %d)", count, m);
            int gi = 0;
            for (int i = 0; i < m && gi < count; ++i)
                if (r16(c2sl_buf + 4 + 2 * i) == mine[gi]) gi++;
            CHECK(gi == count, "the game's list is in it, intact and in order");
            RENDER(C2SL_RA_SITE2);
            CHECK(cpu.gpr[4] == c2sl_buf, "site 2 too");
            RENDER(0x80012345u);
            CHECK(cpu.gpr[4] == LIST, "any other caller is left alone");

            /* the game's list is not this node's: left alone */
            w16(LIST + 4 + 2 * (count / 2), (uint16_t)(mine[count / 2] ^ 0x0400u));
            unsigned long long st[6]; int bk, kk, ok, ad;
            crash2_wide_slst_stats(st, &bk, &kk, &ok, &ad);
            const unsigned long long mism = st[2];
            RENDER(C2SL_RA_SITE1);
            crash2_wide_slst_stats(st, &bk, &kk, &ok, &ad);
            CHECK(cpu.gpr[4] == LIST && st[2] == mism + 1,
                  "a list that is not this node's is drawn as the game built it");
            w16(LIST + 4 + 2 * (count / 2), mine[count / 2]);
            RENDER(C2SL_RA_SITE1);
            CHECK(cpu.gpr[4] == c2sl_buf, "and the merge comes back with the right list");

            /* another node in the node variable than the list is for */
            w32(C2SL_NODE, (uint32_t)(node + 1));
            RENDER(C2SL_RA_SITE1);
            CHECK(cpu.gpr[4] == LIST || ce.len[node + 1] == count,
                  "a node that does not match the list changes nothing");
            w32(C2SL_NODE, (uint32_t)node);

            /* a stale capture: another camera path */
            w32(C2SL_CAM, CAM + 0x100);
            RENDER(C2SL_RA_SITE1);
            CHECK(cpu.gpr[4] == LIST, "another camera path: left alone");
            w32(C2SL_CAM, CAM);

            /* no room in the primitive buffer */
            w32(FRAME + 8, PRIM + C2SL_PRIM_SIZE - 0x2000u);
            RENDER(C2SL_RA_SITE1);
            CHECK(cpu.gpr[4] == LIST, "a nearly full primitive buffer: the game's list");
            w32(FRAME + 8, PRIM);

            /* 4:3, and squash */
            g_ws.mode = 0; g_ws.nw_extra = 0;
            RENDER(C2SL_RA_SITE1);
            CHECK(cpu.gpr[4] == LIST, "4:3: identity");
            g_ws.mode = 1; g_ws.active = 1;
            RENDER(C2SL_RA_SITE1);
            CHECK(cpu.gpr[4] == c2sl_buf, "squash widescreen: widened too");
            g_ws.mode = 2; g_ws.active = 0; g_ws.nw_extra = 170;
            g_ws.present_native_43 = 1;
            RENDER(C2SL_RA_SITE1);
            CHECK(cpu.gpr[4] == LIST, "a frame presented 4:3: identity");
            g_ws.present_native_43 = 0;

            /* switched off, and a changed code word */
            crash2_wide_slst_control(0, 0);
            RENDER(C2SL_RA_SITE1);
            CHECK(cpu.gpr[4] == LIST, "k = 0: identity");
            crash2_wide_slst_control(2, 0);
            const uint32_t w = r32(0x80041EA8u);
            w32(0x80041EA8u, 0x84990002u);
            crash2_wide_slst_note_restore();
            cpu.gpr[2] = ENTRY_AT; cpu.gpr[21] = CAM;
            crash2_wide_slst(&cpu, C2SL_ENTRY_RA);
            RENDER(C2SL_RA_SITE1);
            CHECK(cpu.gpr[4] == LIST && c2sl_code == 0, "a changed code word: disabled");
            w32(0x80041EA8u, w);
            crash2_wide_slst_note_restore();
            memset(&cpu, 0, sizeof cpu);
            cpu.gpr[2] = ENTRY_AT; cpu.gpr[21] = CAM;
            crash2_wide_slst(&cpu, C2SL_ENTRY_RA);
            RENDER(C2SL_RA_SITE1);
            CHECK(cpu.gpr[4] == c2sl_buf, "restored: back on");

            /* 14:9 halves the window */
            g_ws.x_margin = 43; g_ws.nw_extra = 86;
            RENDER(C2SL_RA_SITE1);
            crash2_wide_slst_stats(st, &bk, &kk, &ok, &ad);
            CHECK(kk == 1, "14:9 reaches one node (k %d)", kk);
        }
    }

    printf("\n%s (%d failure%s)\n", FAILS ? "FAILED" : "ALL CHECKS PASSED", FAILS,
           FAILS == 1 ? "" : "s");
    return FAILS ? 1 : 0;
}
