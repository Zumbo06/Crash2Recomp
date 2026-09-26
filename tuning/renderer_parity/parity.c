/* Renderer parity harness: the same PS1 GPU command stream through the OpenGL
 * and the Direct3D 12 compilation of gpu_gl_renderer.c, VRAM read back and
 * every presented frame captured, so the two can be diffed.
 *
 *   parity.exe gl|d3d12 <scale> <out_prefix>
 *
 * Writes <out_prefix>_vram.bin (1024x512 raw 1555) and <out_prefix>_NNNN.ppm
 * per present (GL via the SDL_GL_SwapWindow hook below; D3D12 via
 * PSX_GL12_DUMP, which the harness sets itself). */
#include "psx_sdl.h"
#include <SDL3/SDL_opengl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "gpu_render.h"
#include "gpu_gl_renderer.h"

extern const GpuRenderBackend *gl_backend_get(void);

static uint16_t g_vram[1024 * 512];
static uint16_t g_out[1024 * 512];
static const char *g_prefix = "out";
static unsigned g_frames = 0;

/* The OpenGL copy is compiled with -DSDL_GL_SwapWindow=parity_gl_swap. */
bool parity_gl_swap(SDL_Window *w) {
    int ww = 0, wh = 0;
    SDL_GetWindowSizeInPixels(w, &ww, &wh);
    if (ww > 0 && wh > 0) {
        unsigned char *px = (unsigned char *)malloc((size_t)ww * wh * 4);
        glPixelStorei(GL_PACK_ALIGNMENT, 4);
        glReadPixels(0, 0, ww, wh, GL_RGBA, GL_UNSIGNED_BYTE, px);
        char path[512];
        snprintf(path, sizeof(path), "%s_%04u.ppm", g_prefix, g_frames++);
        FILE *f = fopen(path, "wb");
        if (f) {
            fprintf(f, "P6\n%d %d\n255\n", ww, wh);
            for (int y = wh - 1; y >= 0; y--)
                for (int x = 0; x < ww; x++) fwrite(px + ((size_t)y * ww + x) * 4, 1, 3, f);
            fclose(f);
        }
        free(px);
    }
    return SDL_GL_SwapWindow(w);
}

/* ---- the command stream ------------------------------------------------ */
static uint16_t rgb15(int r, int g, int b, int stp) {
    return (uint16_t)((r & 31) | ((g & 31) << 5) | ((b & 31) << 10) | (stp ? 0x8000 : 0));
}

static uint16_t texpage(int x64, int y256, int depth, int semi) {
    return (uint16_t)((x64 & 15) | ((y256 & 1) << 4) | ((semi & 3) << 5) | ((depth & 3) << 7));
}

static void scenario(const GpuRenderBackend *b) {
    static uint16_t buf[256 * 256];
    /* Texture data at x=640.. (4-bit), 704.. (8-bit), 768.. (15-bit), and
     * CLUTs at y=480.. - uploaded as the game would, through transfer_in. */
    for (int y = 0; y < 256; y++)
        for (int x = 0; x < 64; x++) {          /* 4-bit: 4 texels per halfword */
            int t0 = ((x * 4 + 0) / 8 + y / 8) & 15, t1 = ((x * 4 + 1) / 8 + y / 8) & 15;
            int t2 = ((x * 4 + 2) / 8 + y / 8) & 15, t3 = ((x * 4 + 3) / 8 + y / 8) & 15;
            if ((x * 4 + y) % 29 == 0) t0 = 0;   /* some transparent texels */
            buf[y * 64 + x] = (uint16_t)(t0 | (t1 << 4) | (t2 << 8) | (t3 << 12));
        }
    b->vram_transfer_in(640, 0, 64, 256, buf);
    for (int y = 0; y < 256; y++)
        for (int x = 0; x < 64; x++) {          /* 8-bit: 2 texels per halfword */
            int t0 = (x * 2 + y * 3) & 255, t1 = (x * 2 + 1 + y * 3) & 255;
            buf[y * 64 + x] = (uint16_t)(t0 | (t1 << 8));
        }
    b->vram_transfer_in(704, 0, 64, 256, buf);
    for (int y = 0; y < 256; y++)
        for (int x = 0; x < 64; x++) {          /* 15-bit, STP on a checker */
            buf[y * 64 + x] = rgb15(x / 2, y / 8, (x + y) / 4, ((x / 8 + y / 8) & 1));
            if ((x + y * 7) % 37 == 0) buf[y * 64 + x] = 0;
        }
    b->vram_transfer_in(768, 0, 64, 256, buf);
    for (int i = 0; i < 256; i++)
        buf[i] = (i == 0) ? 0 : rgb15(i & 31, (i * 3) & 31, 31 - (i & 31), (i & 16) ? 1 : 0);
    b->vram_transfer_in(0, 480, 256, 1, buf);  /* CLUT for 8-bit and 4-bit */

    b->set_draw_area(0, 0, 319, 239);
    b->set_draw_offset(0, 0);
    b->set_mask_bits(0, 0);
    b->set_semi_transparency(0, 0);
    b->set_texture_window(0);
    b->fill_rect(0, 0, 320, 240, rgb15(4, 6, 10, 0));

    /* Flat and gouraud, opaque. */
    b->draw_flat_triangle(10, 10, 150, 20, 40, 120, rgb15(31, 10, 0, 0));
    b->draw_gouraud_triangle(160, 10, rgb15(31, 0, 0, 0), 310, 30, rgb15(0, 31, 0, 0),
                             200, 130, rgb15(0, 0, 31, 0));
    /* Semi-transparent flat in every mode. */
    for (int m = 0; m < 4; m++) {
        b->set_semi_transparency(1, m);
        b->draw_flat_rect(20 + m * 70, 60, 60, 40, rgb15(20, 20, 8, 0));
    }
    b->set_semi_transparency(0, 0);

    /* Textured: 4-bit, 8-bit, 15-bit, modulated and raw. */
    b->set_color_modulation(128, 128, 128, 0);
    b->draw_textured_rect(10, 130, 64, 64, 0, 0, 0, 480, texpage(10, 0, 0, 0));
    b->draw_textured_rect(80, 130, 64, 64, 16, 32, 0, 480, texpage(11, 0, 1, 0));
    b->draw_textured_rect(150, 130, 64, 64, 8, 8, 0, 0, texpage(12, 0, 2, 0));
    b->set_color_modulation(200, 90, 60, 0);
    b->draw_textured_triangle(220, 130, 0, 0, 310, 140, 63, 0, 240, 230, 0, 63,
                              0, 480, texpage(10, 0, 0, 0));
    b->set_color_modulation(128, 128, 128, 1);
    b->draw_shaded_textured_triangle(250, 200, 0, 0, 0x00FF8040,
                                     315, 235, 60, 60, 0x0040FF80,
                                     230, 238, 5, 60, 0x008040FF,
                                     0, 0, texpage(12, 0, 2, 0), 0);
    /* Textured semi-transparency, every mode, on 15-bit STP texels. */
    b->set_color_modulation(128, 128, 128, 0);
    for (int m = 0; m < 4; m++) {
        b->set_semi_transparency(1, m);
        b->draw_textured_rect(10 + m * 40, 200, 32, 32, m * 8, 16, 0, 0, texpage(12, 0, 2, m));
    }
    b->set_semi_transparency(0, 0);

    /* Texture window: repeat an 8x8 corner. */
    b->set_texture_window((1u) | (1u << 5));
    b->draw_textured_rect(180, 60, 48, 32, 0, 0, 0, 480, texpage(10, 0, 0, 0));
    b->set_texture_window(0);

    /* Mask: set on one rect, then draw with check over it. */
    b->set_mask_bits(1, 0);
    b->draw_flat_rect(240, 60, 60, 50, rgb15(0, 20, 20, 0));
    b->set_mask_bits(0, 1);
    b->draw_flat_rect(260, 80, 50, 50, rgb15(31, 31, 0, 0));
    b->draw_textured_rect(250, 100, 40, 40, 0, 0, 0, 0, texpage(12, 0, 2, 0));
    b->set_mask_bits(0, 0);

    /* Lines, and a VRAM-to-VRAM copy of the top-left corner. */
    b->draw_line(5, 235, 315, 180, rgb15(31, 31, 31, 0));
    b->draw_shaded_line(5, 5, rgb15(31, 0, 31, 0), 315, 120, rgb15(0, 31, 31, 0));
    b->copy_rect(0, 0, 400, 0, 160, 120);

    /* Draw offset and a clipped draw area. */
    b->set_draw_area(400, 130, 559, 249);
    b->set_draw_offset(400, 130);
    b->draw_gouraud_triangle(-20, -10, rgb15(31, 31, 0, 0), 200, 20, rgb15(0, 31, 31, 0),
                             60, 150, rgb15(31, 0, 31, 0));
    b->set_draw_area(0, 0, 319, 239);
    b->set_draw_offset(0, 0);

    /* A CPU poke and a small readback in the middle, like a game might. */
    b->vram_write(300, 230, rgb15(31, 0, 0, 1));
    uint16_t probe[16];
    b->vram_transfer_out(10, 10, 4, 4, probe);
}

/* A game-like frame: many textured triangles (batchable), semi-transparent
 * ones (one draw each), flat geometry, a clear. */
static void bench_frame(const GpuRenderBackend *b, int frame) {
    b->set_draw_area(0, 0, 319, 239);
    b->set_draw_offset(0, 0);
    b->set_mask_bits(0, 0);
    b->set_semi_transparency(0, 0);
    b->fill_rect(0, 0, 320, 240, rgb15(3, 5, 9, 0));
    b->set_color_modulation(128, 128, 128, 0);
    unsigned seed = 12345u + (unsigned)frame;
#define RND() (seed = seed * 1103515245u + 12345u, (int)((seed >> 16) & 0x7FFF))
    for (int i = 0; i < 1000; i++) {
        int x = RND() % 300, y = RND() % 220;
        b->draw_textured_triangle(x, y, 0, 0, x + 20, y + 3, 30, 0, x + 4, y + 18, 0, 30,
                                  0, 480, texpage(10, 0, (i & 1), 0));
    }
    for (int i = 0; i < 300; i++) {
        int x = RND() % 300, y = RND() % 220;
        b->set_semi_transparency(1, i & 3);
        b->draw_textured_triangle(x, y, 0, 0, x + 16, y, 30, 0, x, y + 16, 0, 30,
                                  0, 0, texpage(12, 0, 2, i & 3));
    }
    b->set_semi_transparency(0, 0);
    for (int i = 0; i < 200; i++) {
        int x = RND() % 300, y = RND() % 220;
        b->draw_gouraud_triangle(x, y, rgb15(31, 0, 0, 0), x + 12, y + 2, rgb15(0, 31, 0, 0),
                                 x + 3, y + 12, rgb15(0, 0, 31, 0));
    }
#undef RND
}

int main(int argc, char **argv) {
    if (argc < 4) { fprintf(stderr, "usage: parity gl|d3d12 scale prefix\n"); return 2; }
    const int d3d = strcmp(argv[1], "d3d12") == 0;
    const int scale = atoi(argv[2]);
    g_prefix = argv[3];
    if (d3d) {
        char dump[600];
        snprintf(dump, sizeof(dump), "PSX_GL12_DUMP=%s", g_prefix);
        _putenv(dump);
    }
    if (SDL_Init(SDL_INIT_VIDEO) != 0) { fprintf(stderr, "SDL_Init: %s\n", SDL_GetError()); return 1; }
    Uint64 flags = SDL_WINDOW_HIDDEN;
    if (!d3d) {
        SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION, 3);
        SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION, 3);
        SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK, SDL_GL_CONTEXT_PROFILE_CORE);
        flags |= SDL_WINDOW_OPENGL;
    }
    SDL_Window *win = SDL_CreateWindow("parity", 0, 0, 640, 480, flags);
    if (!win) { fprintf(stderr, "window: %s\n", SDL_GetError()); return 1; }

    if (!gl_renderer_select_d3d12(d3d)) { fprintf(stderr, "d3d12 not compiled in\n"); return 1; }
    const GpuRenderBackend *b = gl_backend_get();
    b->init(g_vram);
    b->set_scale(scale);
    gl_renderer_set_swap_interval(0);
    if (!gl_renderer_init_context(win)) { fprintf(stderr, "renderer init failed\n"); return 1; }
    printf("renderer: %s, scale %d\n", b->name, b->scale());

    scenario(b);

    if (getenv("PARITY_BENCH")) {
        const int n = atoi(getenv("PARITY_BENCH"));
        gl_renderer_set_display_aspect(4, 3);
        for (int i = 0; i < 10; i++) {           /* warm-up: pipelines, heaps */
            bench_frame(b, i);
            gl_renderer_present_vram(0, 0, 320, 240, 1, 0);
        }
        const double f = (double)SDL_GetPerformanceFrequency();
        Uint64 t_draw = 0, t_present = 0;
        double worst_draw = 0.0;
        const Uint64 t0 = SDL_GetPerformanceCounter();
        for (int i = 0; i < n; i++) {
            const Uint64 a = SDL_GetPerformanceCounter();
            bench_frame(b, i);
            const Uint64 m = SDL_GetPerformanceCounter();
            gl_renderer_present_vram(0, 0, 320, 240, 1, 0);
            const Uint64 e = SDL_GetPerformanceCounter();
            t_draw += m - a;
            t_present += e - m;
            if ((double)(m - a) * 1000.0 / f > worst_draw) worst_draw = (double)(m - a) * 1000.0 / f;
        }
        const double ms = (double)(SDL_GetPerformanceCounter() - t0) * 1000.0 / f;
        printf("bench: %d frames, %.3f ms/frame (%s, scale %d): record %.3f ms (worst %.3f), present %.3f ms\n",
               n, ms / n, b->name, scale, (double)t_draw * 1000.0 / f / n, worst_draw,
               (double)t_present * 1000.0 / f / n);
        {
            extern int gl_renderer_cost_json(char *out, size_t cap);
            char js[1024];
            gl_renderer_cost_json(js, sizeof js);
            printf("cost: %s\n", js);
        }
        gl_renderer_shutdown();
        SDL_DestroyWindow(win);
        SDL_Quit();
        return 0;
    }

    /* 1. Plain 4:3 present (post-processing per PSX_POSTFX). The second call
     *    is skipped by the renderer's dirty test on both APIs. PARITY_INTERP=1
     *    runs frame blending instead (timing-driven: counts may differ). */
    gl_renderer_set_display_aspect(4, 3);
    if (getenv("PARITY_INTERP")) {
        gl_renderer_set_interpolation(1, 120.0, 120.0, 60.0, 0);
        for (int i = 0; i < 4; i++) {
            b->fill_rect(0, 0, 320, 240, rgb15(i * 7, 4, 31 - i * 7, 0));
            b->draw_flat_triangle(10 + i * 40, 10, 150 + i * 40, 20, 40 + i * 40, 120, rgb15(31, 10, 0, 0));
            gl_renderer_present_vram(0, 0, 320, 240, 1, 0);
        }
        gl_renderer_set_interpolation(0, 120.0, 0.0, 60.0, 0);
    } else {
        for (int i = 0; i < 2; i++)
            gl_renderer_present_vram(0, 0, 320, 240, 1, 0);
    }

    /* 2. Letterboxed 16:9 with bezel art behind the bars. */
    {
        static uint8_t bez[64 * 64 * 4];
        for (int y = 0; y < 64; y++)
            for (int x = 0; x < 64; x++) {
                uint8_t *p = &bez[(y * 64 + x) * 4];
                p[0] = (uint8_t)(x * 4); p[1] = (uint8_t)(y * 4);
                p[2] = ((x / 8 + y / 8) & 1) ? 200 : 40; p[3] = 255;
            }
        gl_renderer_set_bezel(bez, 64, 64);
        gl_renderer_set_display_aspect(16, 9);
        gl_renderer_set_scaling_mode(0);
        gl_renderer_invalidate_present();
        gl_renderer_present_vram(0, 0, 320, 240, 1, 0);
        gl_renderer_set_bezel(NULL, 0, 0);
        gl_renderer_set_display_aspect(4, 3);
    }

    /* 3. The CPU / 24-bit FMV present path. */
    {
        static uint32_t px[320 * 240];
        for (int y = 0; y < 240; y++)
            for (int x = 0; x < 320; x++)
                px[y * 320 + x] = 0xFF000000u | ((uint32_t)(x * 255 / 319) << 16) |
                                  ((uint32_t)(y * 255 / 239) << 8) | (uint32_t)((x ^ y) & 0xFF);
        gl_renderer_present(px, 320, 240, 1, 1, 0);
    }

    /* 4. Hold-last: redraw the captured drawable. */
    gl_renderer_present_hold_last();

    /* 5. Native-wide compositor: a 400-wide surface for the buffer at x=0,
     *    margins cleared, a prim reaching into both margins mirrored. */
    {
        b->wide_configure(400, 40);
        b->wide_set_target(0);
        b->wide_clear(0, 0, 240, rgb15(2, 2, 6, 0));
        b->set_draw_area(0, 0, 319, 239);
        b->set_draw_offset(0, 0);
        b->draw_gouraud_triangle(-30, 20, rgb15(31, 16, 0, 0), 350, 60, rgb15(0, 31, 16, 0),
                                 160, 220, rgb15(16, 0, 31, 0));
        b->set_color_modulation(128, 128, 128, 0);
        b->draw_textured_rect(-20, 160, 64, 64, 0, 0, 0, 480, texpage(10, 0, 0, 0));
        b->wide_disable_target();
        gl_renderer_set_display_aspect(5, 3);
        gl_renderer_invalidate_present();
        if (!gl_renderer_present_wide_fbo(0, 0, 240, 1)) printf("wide present unavailable\n");
        gl_renderer_set_display_aspect(4, 3);
    }

    /* 6. Blank. */
    gl_renderer_present_blank();

    /* 7. The asynchronous whole-VRAM download rewind uses, against the
     *    synchronous readback. */
    {
        static uint16_t async_buf[1024 * 512];
        int began = b->vram_readback_begin ? b->vram_readback_begin() : 0;
        int got = began ? b->vram_readback_poll(async_buf, 1) : 0;
        b->vram_transfer_out(0, 0, 1024, 512, g_out);
        if (began && got == 1)
            printf("async readback: %s\n",
                   memcmp(async_buf, g_out, sizeof(async_buf)) == 0 ? "matches" : "DIFFERS");
        else
            printf("async readback: not taken (began %d, poll %d)\n", began, got);
    }
    char path[512];
    snprintf(path, sizeof(path), "%s_vram.bin", g_prefix);
    FILE *f = fopen(path, "wb");
    if (f) { fwrite(g_out, 2, 1024 * 512, f); fclose(f); }
    gl_renderer_shutdown();
    SDL_DestroyWindow(win);
    SDL_Quit();
    printf("done: %s\n", path);
    return 0;
}
