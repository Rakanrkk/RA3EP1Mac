#ifndef RA3_FIXED_VIEWPORT_MATH_H
#define RA3_FIXED_VIEWPORT_MATH_H

#include <math.h>
#include <stdbool.h>

/* Coordinates use a top-left origin, in one caller-selected unit (points or pixels). */
typedef struct {
    double x, y, width, height;
} Ra3ViewportRect;

typedef struct {
    Ra3ViewportRect source;
    Ra3ViewportRect destination;
} Ra3ViewportTransform;

static inline bool ra3_viewport_rect_valid(Ra3ViewportRect rect) {
    return isfinite(rect.x) && isfinite(rect.y) && isfinite(rect.width) &&
           isfinite(rect.height) && rect.width > 0 && rect.height > 0;
}

/* Preserve the requested output extent; do not silently shrink on another display. */
static inline bool ra3_viewport_bottom_aligned(Ra3ViewportRect screen,
                                              double safe_top,
                                              double width, double height,
                                              Ra3ViewportRect *result) {
    if (!result || !ra3_viewport_rect_valid(screen) || !isfinite(safe_top) ||
        safe_top < 0 || !isfinite(width) || !isfinite(height) || width <= 0 ||
        height <= 0 || width > screen.width || height > screen.height - safe_top)
        return false;
    *result = (Ra3ViewportRect){screen.x + (screen.width - width) / 2,
                               screen.y + screen.height - height, width, height};
    return true;
}

static inline bool ra3_viewport_transform_valid(Ra3ViewportTransform transform) {
    return ra3_viewport_rect_valid(transform.source) &&
           ra3_viewport_rect_valid(transform.destination);
}

/* Continuous coordinates: inverse(forward(p)) == p, including rectangle edges. */
static inline bool ra3_viewport_map(Ra3ViewportTransform transform,
                                    double x, double y,
                                    double *mapped_x, double *mapped_y) {
    if (!mapped_x || !mapped_y || !ra3_viewport_transform_valid(transform) ||
        !isfinite(x) || !isfinite(y)) return false;
    *mapped_x = transform.destination.x +
        (x - transform.source.x) * transform.destination.width / transform.source.width;
    *mapped_y = transform.destination.y +
        (y - transform.source.y) * transform.destination.height / transform.source.height;
    return true;
}

static inline bool ra3_viewport_inverse(Ra3ViewportTransform transform,
                                        double x, double y,
                                        double *mapped_x, double *mapped_y) {
    Ra3ViewportTransform inverse = {transform.destination, transform.source};
    return ra3_viewport_map(inverse, x, y, mapped_x, mapped_y);
}

/* A captured absolute pointer must reach the last game pixel at the viewport edge. */
static inline bool ra3_viewport_inverse_pixel(Ra3ViewportTransform transform,
                                              double x, double y,
                                              double *mapped_x, double *mapped_y) {
    if (transform.source.width < 1 || transform.source.height < 1 ||
        !ra3_viewport_inverse(transform, x, y, mapped_x, mapped_y)) return false;
    *mapped_x = fmin(transform.source.x + transform.source.width - 1,
                     fmax(transform.source.x, floor(*mapped_x)));
    *mapped_y = fmin(transform.source.y + transform.source.height - 1,
                     fmax(transform.source.y, floor(*mapped_y)));
    return true;
}

#endif
