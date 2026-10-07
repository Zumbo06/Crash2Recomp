/* raster.c - painter's-order triangle fill for wide_view_sweep (sweep.py).
 *
 * Draws n screen-space triangles into an id/depth buffer in the order given:
 * the caller sorts far to near, as the game's ordering table draws, so each
 * pixel ends up holding the nearest polygon that covers its centre. A pixel is
 * covered when its centre lies inside or on an edge; touching neighbours then
 * leave no cracks, and coverage statistics do not depend on a fill rule.
 *
 *   tris   n x 6 floats: x0 y0 x1 y1 x2 y2, in buffer pixels
 *   depth  n ints, id n ints
 *   out_id / out_depth  w x h, filled by the caller (id -1 = nothing)
 */
#include <math.h>
#include <stdint.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

EXPORT void raster(int n, const float *tris, const int32_t *depth, const int32_t *id,
                   int w, int h, int32_t *out_id, int32_t *out_depth)
{
    for (int t = 0; t < n; t++) {
        const float *v = tris + 6 * t;
        float x0 = v[0], y0 = v[1], x1 = v[2], y1 = v[3], x2 = v[4], y2 = v[5];
        float area = (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0);
        if (area == 0.0f)
            continue;
        if (area < 0.0f) {                       /* make it counter-clockwise */
            float tx = x1, ty = y1;
            x1 = x2; y1 = y2; x2 = tx; y2 = ty;
        }
        float fx0 = fminf(x0, fminf(x1, x2)), fx1 = fmaxf(x0, fmaxf(x1, x2));
        float fy0 = fminf(y0, fminf(y1, y2)), fy1 = fmaxf(y0, fmaxf(y1, y2));
        int bx0 = (int)floorf(fx0 - 0.5f), bx1 = (int)ceilf(fx1 - 0.5f);
        int by0 = (int)floorf(fy0 - 0.5f), by1 = (int)ceilf(fy1 - 0.5f);
        if (bx0 < 0) bx0 = 0;
        if (by0 < 0) by0 = 0;
        if (bx1 > w - 1) bx1 = w - 1;
        if (by1 > h - 1) by1 = h - 1;
        for (int y = by0; y <= by1; y++) {
            float py = (float)y + 0.5f;
            for (int x = bx0; x <= bx1; x++) {
                float px = (float)x + 0.5f;
                float e0 = (x1 - x0) * (py - y0) - (y1 - y0) * (px - x0);
                float e1 = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1);
                float e2 = (x0 - x2) * (py - y2) - (y0 - y2) * (px - x2);
                if (e0 >= 0.0f && e1 >= 0.0f && e2 >= 0.0f) {
                    out_id[y * w + x] = id[t];
                    out_depth[y * w + x] = depth[t];
                }
            }
        }
    }
}
