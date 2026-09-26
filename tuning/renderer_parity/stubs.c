/* Stubs for the runtime pieces the renderer references that the harness does
 * not need (OSD, menus, rewind, widescreen debug, timing). */
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <windows.h>

#include "gpu.h"
#include "latency_ring.h"

uint64_t s_frame_count = 0;
int g_ws_tex_edge_pct = 0;

int  psx_netplay_active(void) { return 0; }
void latency_ring_mark(LatencyStage stage) { (void)stage; }
int  host_osd_image(const uint32_t **p, int *w, int *h) { (void)p; (void)w; (void)h; return 0; }
int  host_osd_volume_image(const uint32_t **p, int *w, int *h) { (void)p; (void)w; (void)h; return 0; }
int  psx_rewind_overlay_image(const uint32_t **p, int *w, int *h) { (void)p; (void)w; (void)h; return 0; }
float psx_rewind_slide(void) { return 0.0f; }
int  psx_savestate_menu_overlay_image(const uint32_t **p, int *w, int *h) { (void)p; (void)w; (void)h; return 0; }
int  psx_pause_menu_overlay_image(const uint32_t **p, int *w, int *h) { (void)p; (void)w; (void)h; return 0; }
void host_osd_present_done(void) {}
int  gpu_display_is_depth24(void) { return 0; }
void gpu_get_display_info(GpuDisplayInfo *out) { if (out) memset(out, 0, sizeof(*out)); }
int  host_osd_needs_present(void) { return 0; }
void psx_ws_dbg_gate_frame_snapshot(void) {}
void psx_host_sleep_ms(unsigned ms) { Sleep(ms); }
int  gpu_ws_nw_flat_backdrop_enabled(void) { return 0; }
int  psx_ws_prim_in_backdrop(void) { return 0; }
int  psx_ws_prim_is_tagged(void) { return 0; }
void gpu_depth24_upload_span_reset(void) {}
