/* Model check for the camera test in crash2_wide_slst.h.
 *
 * Real level data (export.py: a camera path's zone, its worlds, its SLST and
 * the paths linked at its ends) is laid out in guest RAM the way the game
 * holds it - entry headers with item pointers, entities pointing at their
 * zone, the zone's world records filled for the frame (offsets from the
 * camera, the world's items), an EID record table where 0x80014B90 looks -
 * and the hook is driven at the render call with a camera in the GTE. What it
 * draws must equal what frustum_ref.py says, case by case. Then the ways it
 * must refuse: worlds it cannot read, a camera that does not put the game's
 * list on screen, a linked path that is not resident.
 *
 *   sh tuning/wide_frustum_sim/build.sh
 *   python tuning/wide_frustum_sim/export.py cases.bin   (game data: keep it out of git)
 *   tuning/wide_frustum_sim/sim.exe cases.bin path/to/SCUS_941.54
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

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
#define C2SL_W16(a, v) w16((a), (v))
#define C2SL_ALLOC(n)  mod_alloc(n)
#define C2WG_R32(a)    r32(a)
#define C2WG_R16(a)    r16(a)
#define C2WG_R8(a)     ((uint8_t)(r16((a) & ~1u) >> (((a) & 1u) * 8u)))
#define C2WG_RAMPTR()  g_ram

#define C2WS_R32(a)    r32(a)
#define C2WS_R16(a)    r16(a)
#define C2WS_R8(a)     ((uint8_t)(r16((a) & ~1u) >> (((a) & 1u) * 8u)))
#define C2WS_W32(a, v) w32((a), (v))

typedef struct {
    uint32_t gpr[32]; uint32_t gte_data[32]; uint32_t gte_ctrl[32];
    uint32_t (*read_word)(uint32_t);
} CPUState;
typedef struct { int mode, active, present_native_43, x_margin, nw_extra; } GpuWsDebug;
static GpuWsDebug g_ws = { 2, 0, 0, 85, 170 };
static void gpu_ws_get_debug(GpuWsDebug *o) { *o = g_ws; }

#include "crash2_wide_spawn.h"   /* as interrupts.c: both share crash2_wide_geom.h */
#include "crash2_wide_slst.h"

static int FAILS;
#define CHECK(c, ...) do { if (!(c)) { if (FAILS++ < 30) { printf("  FAIL "); printf(__VA_ARGS__); printf("\n"); } } } while (0)

static uint64_t fnv(uint32_t list, int n)
{
    uint64_t h = 0xCBF29CE484222325ull;
    for (int i = 0; i < n; ++i) {
        const uint16_t v = r16(list + 4u + 2u * (uint32_t)i);
        h ^= v & 0xFFu;  h *= 0x100000001B3ull;
        h ^= v >> 8;     h *= 0x100000001B3ull;
    }
    return h;
}

/* ---- the cases ----------------------------------------------------------- */
typedef struct { uint32_t eid, type, items; uint32_t *ilen; uint8_t **ib; uint32_t addr; } Ent;
typedef struct {
    int nent; Ent *ent;
    uint32_t zone_eid, item, node;
    int32_t cam[3], r[9], h, ofx, ofy, margin;
    uint32_t seen, total, merged, kept, joins;
    uint64_t hash;
    uint32_t nobj, *obj_value;
    int32_t *obj_verdict;
} Case;
static Case *g_cases;
static int g_ncases;

static int rd(FILE *f, void *p, size_t n) { return fread(p, 1, n, f) == n; }

static int load(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    char magic[4];
    uint32_t n;
    if (!rd(f, magic, 4) || memcmp(magic, "C2FS", 4) || !rd(f, &n, 4)) return 0;
    g_cases = calloc(n ? n : 1, sizeof(Case));
    for (uint32_t c = 0; c < n; ++c) {
        Case *k = &g_cases[g_ncases++];
        uint32_t ne;
        if (!rd(f, &ne, 4)) return 0;
        k->nent = (int)ne;
        k->ent = calloc(ne, sizeof(Ent));
        for (uint32_t e = 0; e < ne; ++e) {
            Ent *x = &k->ent[e];
            if (!rd(f, &x->eid, 4) || !rd(f, &x->type, 4) || !rd(f, &x->items, 4)) return 0;
            x->ilen = calloc(x->items + 1, 4);
            x->ib = calloc(x->items + 1, sizeof(uint8_t *));
            for (uint32_t i = 0; i < x->items; ++i) {
                if (!rd(f, &x->ilen[i], 4)) return 0;
                const uint32_t padded = (x->ilen[i] + 3u) & ~3u;
                x->ib[i] = malloc(padded ? padded : 4);
                if (padded && !rd(f, x->ib[i], padded)) return 0;
            }
        }
        if (!rd(f, &k->zone_eid, 4) || !rd(f, &k->item, 4) || !rd(f, &k->node, 4) ||
            !rd(f, k->cam, 12) || !rd(f, k->r, 36) || !rd(f, &k->h, 4) || !rd(f, &k->ofx, 4) ||
            !rd(f, &k->ofy, 4) || !rd(f, &k->margin, 4) || !rd(f, &k->seen, 4) ||
            !rd(f, &k->total, 4) || !rd(f, &k->merged, 4) || !rd(f, &k->kept, 4) ||
            !rd(f, &k->joins, 4) || !rd(f, &k->hash, 8) || !rd(f, &k->nobj, 4))
            return 0;
        k->obj_value = calloc(k->nobj + 1, 4);
        k->obj_verdict = calloc(k->nobj + 1, 4);
        for (uint32_t i = 0; i < k->nobj; ++i)
            if (!rd(f, &k->obj_value[i], 4) || !rd(f, &k->obj_verdict[i], 4)) return 0;
    }
    fclose(f);
    return g_ncases > 0;
}

/* ---- laying a case out in guest RAM --------------------------------------- */
#define PLACE_LO   0x80100000u
#define PLACE_HI   0x801E0000u
#define HASH_AT    0x801E0000u   /* bucket heads, 256 words              */
#define RECS_AT    0x801E0400u   /* records, 8 bytes                       */
#define NSD_AT     0x801EF000u   /* record count at +1028                  */
#define LIST_AT    0x801F0000u
#define PRIM_AT    0x80090000u
#define ZONE_PTR   0x800608CCu

static Ent *find(Case *k, uint32_t eid)
{
    for (int i = 0; i < k->nent; ++i) if (k->ent[i].eid == eid) return &k->ent[i];
    return NULL;
}

static uint32_t item_addr(const Ent *e, uint32_t i) { return r32(e->addr + 16u + 4u * i); }

static int place(Case *k)
{
    memset(g_ram + (PLACE_LO & 0x1FFFFFu), 0, 0x200000u - (PLACE_LO & 0x1FFFFFu));
    uint32_t p = PLACE_LO;
    for (int i = 0; i < k->nent; ++i) {
        Ent *e = &k->ent[i];
        uint32_t need = 16u + 4u * (e->items + 1u);
        for (uint32_t j = 0; j < e->items; ++j) need += (e->ilen[j] + 3u) & ~3u;
        if (p + need > PLACE_HI) return 0;
        e->addr = p;
        w32(p, C2SL_MAGIC); w32(p + 4, e->eid); w32(p + 8, e->type); w32(p + 12, e->items);
        uint32_t q = p + 16u + 4u * (e->items + 1u);
        for (uint32_t j = 0; j < e->items; ++j) {
            w32(p + 16u + 4u * j, q);
            memcpy(g_ram + (q & 0x1FFFFFu), e->ib[j], e->ilen[j]);
            q += (e->ilen[j] + 3u) & ~3u;
        }
        w32(p + 16u + 4u * e->items, q);
        p = (q + 15u) & ~15u;
    }
    /* the record table, by bucket (0x80014B90: bucket = (eid >> 15) & 0xFF) */
    int order[512], n = 0;
    for (int b = 0; b < 256; ++b) {
        w32(HASH_AT + 4u * (uint32_t)b, RECS_AT + 8u * (uint32_t)n);
        for (int i = 0; i < k->nent && n < 512; ++i)
            if (((k->ent[i].eid >> 15) & 0xFFu) == (uint32_t)b) order[n++] = i;
    }
    for (int i = 0; i < n; ++i) {
        w32(RECS_AT + 8u * (uint32_t)i, k->ent[order[i]].addr);
        w32(RECS_AT + 8u * (uint32_t)i + 4u, k->ent[order[i]].eid);
    }
    w32(0x80057590u, HASH_AT);
    w32(0x80057594u, RECS_AT);
    w32(0x800575A0u, NSD_AT);
    w32(NSD_AT + 1028u, (uint32_t)n);
    /* every zone's entities know their zone (the game fills +4 at load) */
    for (int i = 0; i < k->nent; ++i) {
        Ent *e = &k->ent[i];
        if (e->type != 7) continue;
        for (uint32_t j = 2; j < e->items; ++j) w32(item_addr(e, j) + 4u, e->addr);
    }
    return 1;
}

/* The current zone's world records, as 0x80018344 fills them for a camera at
 * (cx, cy, cz): resolved reference, origin - camera, the world's items. */
static void fill_worlds(Case *k, const Ent *zone)
{
    const uint32_t h = item_addr(zone, 0);
    const uint32_t nw = r32(h);
    for (uint32_t i = 0; i < nw && i < 8; ++i) {
        const uint32_t rec = h + 4u + 48u * i;
        const uint32_t eid = r32(rec);
        Ent *w = find(k, (eid & 1u) ? eid : r32(eid + 4u));
        if (!w) continue;
        for (uint32_t j = 0; j < 512; ++j)          /* resolve in place, as the game does */
            if (r32(RECS_AT + 8u * j + 4u) == w->eid) { w32(rec, RECS_AT + 8u * j); break; }
        const uint32_t info = item_addr(w, 0);
        w32(rec + 4u, (uint32_t)((int32_t)r32(info) - k->cam[0]));
        w32(rec + 8u, (uint32_t)((int32_t)r32(info + 4u) - k->cam[1]));
        w32(rec + 12u, (uint32_t)((int32_t)r32(info + 8u) - k->cam[2]));
        w32(rec + 16u, info);
        w32(rec + 20u, item_addr(w, 2));
        w32(rec + 24u, item_addr(w, 3));
        w32(rec + 28u, item_addr(w, 1));
    }
}

static void set_gte(CPUState *cpu, const int32_t *r, int32_t h, int32_t ofx, int32_t ofy)
{
    for (int i = 0; i < 4; ++i)
        cpu->gte_ctrl[i] = (uint32_t)(uint16_t)r[2 * i] | ((uint32_t)(uint16_t)r[2 * i + 1] << 16);
    cpu->gte_ctrl[4] = (uint32_t)(uint16_t)r[8];
    cpu->gte_ctrl[24] = (uint32_t)ofx;
    cpu->gte_ctrl[25] = (uint32_t)ofy;
    cpu->gte_ctrl[26] = (uint32_t)h;
}

/* Capture the path's SLST at 0x8002037C, set the node's list, render. */
static uint32_t drive(Case *k, const Ent *zone, const Ent *slst, uint32_t path, const int32_t *r,
                      int *count_out)
{
    static C2slEntry ce;
    c2sl_build_entry(&ce, slst->addr);
    if (!ce.ok || (int)k->node >= ce.nodes) return 0;
    const uint16_t *mine = ce.ids + ce.off[k->node];
    const int count = ce.len[k->node];
    w16(LIST_AT, (uint16_t)count); w16(LIST_AT + 2, 0);
    for (int i = 0; i < count; ++i) w16(LIST_AT + 4u + 2u * (uint32_t)i, mine[i]);
    w32(C2SL_LIST_PTR, LIST_AT);
    w32(C2SL_NODE, k->node);
    w32(C2SL_CAM, path);
    w32(ZONE_PTR, zone->addr);
    w32(C2SL_FRAME0 + 4, PRIM_AT);
    w32(C2SL_FRAME0 + 8, PRIM_AT);
    w32(0x800607F4u, (uint32_t)(k->cam[0] * 256));
    w32(0x800607F8u, (uint32_t)(k->cam[1] * 256));
    w32(0x800607FCu, (uint32_t)(k->cam[2] * 256));
    CPUState cpu;
    memset(&cpu, 0, sizeof cpu);
    cpu.gpr[2] = slst->addr;
    cpu.gpr[21] = path;
    crash2_wide_slst(&cpu, C2SL_ENTRY_RA);
    memset(&cpu, 0, sizeof cpu);
    set_gte(&cpu, r, k->h, k->ofx, k->ofy);
    cpu.gpr[31] = C2SL_RA_SITE1;
    cpu.gpr[4] = LIST_AT;
    cpu.gpr[5] = C2SL_FRAME0;
    crash2_wide_slst(&cpu, C2SL_RENDER_FN);
    *count_out = count;
    return cpu.gpr[4];
}

int main(int argc, char **argv)
{
    if (argc < 3) { printf("usage: sim cases.bin SCUS_941.54\n"); return 2; }
    if (!load(argv[1])) { printf("cannot read %s\n", argv[1]); return 2; }
    {
        FILE *f = fopen(argv[2], "rb");
        if (!f) { printf("cannot open %s\n", argv[2]); return 2; }
        static uint8_t exe[2 * 1024 * 1024];
        const size_t got = fread(exe, 1, sizeof exe, f);
        fclose(f);
        uint32_t taddr, tsize;
        memcpy(&taddr, exe + 0x18, 4);
        memcpy(&tsize, exe + 0x1C, 4);
        if (got < 2048 + tsize) { printf("short executable\n"); return 2; }
        memcpy(g_ram + (taddr & 0x1FFFFF), exe + 2048, tsize);
    }
    CHECK(c2wg_code_ok(), "the real executable passes crash2_wide_geom.h's code words");

    printf("1. what the camera test draws, against the reference (%d cases)\n", g_ncases);
    int ran = 0, model_ok = 0, joined = 0, placed_out = 0, obj_total = 0, obj_in = 0;
    long added = 0, listed = 0;
    double worst_ms = 0.0;
    for (int c = 0; c < g_ncases; ++c) {
        Case *k = &g_cases[c];
        if (!place(k)) { placed_out++; continue; }
        Ent *zone = find(k, k->zone_eid);
        CHECK(zone != NULL, "case %d: zone exported", c);
        if (!zone) continue;
        fill_worlds(k, zone);
        const uint32_t path = item_addr(zone, k->item);
        /* the path's SLST: the one its 0x103 names */
        const uint32_t slst_entry = c2wg_slst_of(path);
        Ent *slst = NULL;
        for (int i = 0; i < k->nent; ++i) if (k->ent[i].addr == slst_entry) slst = &k->ent[i];
        CHECK(slst != NULL, "case %d: the path's SLST resolves through the record table", c);
        if (!slst) continue;
        crash2_wide_slst_note_restore();
        crash2_wide_slst_control(2, 1);
        g_ws.mode = 2; g_ws.nw_extra = 170; g_ws.x_margin = k->margin;
        int count = 0;
        const clock_t t0 = clock();
        const uint32_t drawn = drive(k, zone, slst, path, k->r, &count);
        const double ms = 1000.0 * (double)(clock() - t0) / CLOCKS_PER_SEC;
        if (ms > worst_ms) worst_ms = ms;
        ran++;
        const int want_model = k->total >= 8 && k->seen * 2 >= k->total;
        CHECK(c2sl_model_seen == (int)k->seen && c2sl_model_total == (int)k->total,
              "case %d: model check %d/%d, reference %u/%u", c, c2sl_model_seen, c2sl_model_total,
              k->seen, k->total);
        /* the object hook, judging this path's objects with the camera just handed on */
        for (uint32_t i = 0; i < k->nobj; ++i) {
            C2wsCand cand;
            memset(&cand, 0, sizeof cand);
            cand.value = k->obj_value[i];
            const int got = c2ws_in_margin(&cand, zone->addr);
            obj_total++;
            obj_in += got == 1;
            CHECK(got == k->obj_verdict[i], "case %d object %08X: verdict %d, reference %d", c,
                  k->obj_value[i], got, k->obj_verdict[i]);
        }
        CHECK(c2sl_built.model == want_model, "case %d: model verdict", c);
        CHECK(c2wg_cam.ok == want_model, "case %d: the camera handed on is marked as judged", c);
        if (!want_model) continue;
        model_ok++;
        unsigned long long st[12];
        int bk, kk, ok, ad;
        crash2_wide_slst_stats(st, &bk, &kk, &ok, &ad);
        CHECK(st[9] == 1, "case %d: drawn with the camera test (%llu)", c, st[9]);
        const int m = drawn == c2sl_buf && c2sl_buf ? r16(c2sl_buf) : count;
        CHECK((uint32_t)m == k->merged, "case %d (%u joins): %d polygons, reference %u", c, k->joins,
              m, k->merged);
        if ((uint32_t)m == k->merged && m > count)
            CHECK(fnv(c2sl_buf, m) == k->hash, "case %d: the list differs from the reference", c);
        if (k->joins) joined++;
        added += m - count;
        listed += count;
    }
    printf("   %d cases ran (%d did not fit in RAM), %d judged by the camera, %d with a linked path;\n"
           "   %.1f%% more polygons, the slowest frame %.2f ms; %d objects judged, %d in the extra columns\n",
           ran, placed_out, model_ok, joined, listed ? 100.0 * added / listed : 0.0, worst_ms,
           obj_total, obj_in);
    CHECK(ran >= 20 && model_ok >= 20, "enough cases judged (%d)", model_ok);
    CHECK(obj_total >= 200 && obj_in > 0, "objects judged (%d, %d in the margins)", obj_total, obj_in);

    printf("2. the object hook spawning by the camera\n");
    {
        int arrivals = 0, spawned = 0, wrong = 0, by_camera = 0;
        for (int c = 0; c < g_ncases; ++c) {
            Case *k = &g_cases[c];
            if (!place(k)) continue;
            Ent *zone = find(k, k->zone_eid);
            if (!zone) continue;
            fill_worlds(k, zone);
            const uint32_t path = item_addr(zone, k->item);
            Ent *slst = NULL;
            for (int i = 0; i < k->nent; ++i) if (k->ent[i].addr == c2wg_slst_of(path)) slst = &k->ent[i];
            if (!slst) continue;
            crash2_wide_slst_note_restore();
            crash2_wide_slst_control(2, 1);
            int count = 0;
            drive(k, zone, slst, path, k->r, &count);        /* hands the camera on */
            if (!c2wg_cam.ok) continue;
            crash2_wide_spawn_control(2, 1);
            crash2_wide_spawn_note_restore();
            c2ws_geo = 1;
            const int kk = c2ws_window();
            w32(C2WS_Q_COUNT, 0);
            c2ws_apply(path, (int)k->node, 2, kk);
            arrivals++;
            const uint32_t qn = r32(C2WS_Q_COUNT);
            for (uint32_t i = 0; i < qn && i < C2WS_Q_CAP; ++i) {
                const uint32_t v = r32(C2WS_Q_BASE + 12u * i), act = r32(C2WS_Q_BASE + 12u * i + 8u);
                if (act != C2WS_SPAWN) continue;
                spawned++;
                int verdict = 9;
                for (uint32_t j = 0; j < k->nobj; ++j)
                    if ((k->obj_value[j] & 0x00FFFF00u) == (v & 0x00FFFF00u)) verdict = k->obj_verdict[j];
                if (verdict == 1) by_camera++;
                if (verdict == 0) {
                    wrong++;
                    CHECK(0, "case %d: object %08X spawned early although the camera put it off the margins",
                          c, v);
                }
            }
        }
        printf("   %d node arrivals, %d early spawns (%d by the camera), %d against the camera's verdict\n",
               arrivals, spawned, by_camera, wrong);
        CHECK(arrivals >= 20 && wrong == 0, "spawns follow the camera");
    }

    printf("3. where it must not judge\n");
    {
        Case *k = NULL;
        for (int c = 0; c < g_ncases && !k; ++c)
            if (g_cases[c].total >= 8 && g_cases[c].seen * 2 >= g_cases[c].total && g_cases[c].joins)
                k = &g_cases[c];
        CHECK(k != NULL, "a case to vary");
        if (k && place(k)) {
            Ent *zone = find(k, k->zone_eid);
            fill_worlds(k, zone);
            const uint32_t path = item_addr(zone, k->item);
            Ent *slst = NULL;
            for (int i = 0; i < k->nent; ++i) if (k->ent[i].addr == c2wg_slst_of(path)) slst = &k->ent[i];
            int count = 0;
            unsigned long long st[12];
            int bk, kk, ok, ad;

            /* a camera looking the other way: the model fails, the node window draws */
            int32_t back[9];
            for (int i = 0; i < 9; ++i) back[i] = (i == 0 || i == 1 || i == 2 || i == 6 || i == 7 || i == 8) ? -k->r[i] : k->r[i];
            crash2_wide_slst_note_restore();
            crash2_wide_slst_control(2, 1);
            drive(k, zone, slst, path, back, &count);
            crash2_wide_slst_stats(st, &bk, &kk, &ok, &ad);
            CHECK(c2sl_built.model == 0 && st[10] == 1 && st[9] == 0,
                  "a camera facing away: model refused (%d), node window drawn", c2sl_built.model);
            CHECK(c2wg_cam.ok == 0, "and the objects are told it cannot judge");

            /* worlds it cannot read: a vertex pointer outside RAM */
            const uint32_t h = item_addr(zone, 0);
            const uint32_t keep = r32(h + 4u + 28u);
            w32(h + 4u + 28u, 0x80300000u);
            crash2_wide_slst_note_restore();
            crash2_wide_slst_control(2, 1);
            drive(k, zone, slst, path, k->r, &count);
            crash2_wide_slst_stats(st, &bk, &kk, &ok, &ad);
            CHECK(st[9] == 0, "unreadable worlds: no camera test");
            w32(h + 4u + 28u, keep);

            /* the camera's zone is not the path's: no candidates, node window */
            w32(ZONE_PTR, 0);
            crash2_wide_slst_note_restore();
            crash2_wide_slst_control(2, 1);
            {
                static C2slEntry ce;
                c2sl_build_entry(&ce, slst->addr);
                const uint16_t *mine = ce.ids + ce.off[k->node];
                count = ce.len[k->node];
                w16(LIST_AT, (uint16_t)count);
                for (int i = 0; i < count; ++i) w16(LIST_AT + 4u + 2u * (uint32_t)i, mine[i]);
                CPUState cpu;
                memset(&cpu, 0, sizeof cpu);
                cpu.gpr[2] = slst->addr; cpu.gpr[21] = path;
                crash2_wide_slst(&cpu, C2SL_ENTRY_RA);
                memset(&cpu, 0, sizeof cpu);
                set_gte(&cpu, k->r, k->h, k->ofx, k->ofy);
                cpu.gpr[31] = C2SL_RA_SITE1; cpu.gpr[4] = LIST_AT; cpu.gpr[5] = C2SL_FRAME0;
                crash2_wide_slst(&cpu, C2SL_RENDER_FN);
            }
            crash2_wide_slst_stats(st, &bk, &kk, &ok, &ad);
            CHECK(st[9] == 0 && c2sl_ncand == 0, "another zone than the path's: no camera test");

            /* the linked path's SLST not resident: drawn without it, still the game's list in order */
            place(k);
            fill_worlds(k, zone);
            int dropped = 0;
            for (int i = 0; i < 512; ++i) {
                const uint32_t eid = r32(RECS_AT + 8u * (uint32_t)i + 4u);
                const Ent *e = find(k, eid);
                if (e && e->type == 4 && e->addr != c2wg_slst_of(path)) {
                    w32(RECS_AT + 8u * (uint32_t)i, eid);            /* bit 0 set: not resident */
                    dropped++;
                }
            }
            crash2_wide_slst_note_restore();
            crash2_wide_slst_control(2, 1);
            const uint32_t drawn = drive(k, zone, slst, path, k->r, &count);
            crash2_wide_slst_stats(st, &bk, &kk, &ok, &ad);
            CHECK(dropped > 0 && st[9] == 1, "linked SLSTs absent: still judged (%d dropped)", dropped);
            if (drawn == c2sl_buf && c2sl_buf) {
                const int m = r16(c2sl_buf);
                static C2slEntry ce;
                c2sl_build_entry(&ce, slst->addr);
                const uint16_t *mine = ce.ids + ce.off[k->node];
                int gi = 0;
                for (int i = 0; i < m && gi < count; ++i)
                    if (r16(c2sl_buf + 4u + 2u * (uint32_t)i) == mine[gi]) gi++;
                CHECK(gi == count && (uint32_t)m <= k->merged,
                      "without the linked paths: the game's list intact, nothing more than with them");
            }
        }
    }

    printf("\n%s (%d failure%s)\n", FAILS ? "FAILED" : "ALL CHECKS PASSED", FAILS,
           FAILS == 1 ? "" : "s");
    return FAILS ? 1 : 0;
}
