#ifndef RA3_VIEWPORT_SPEC_H
#define RA3_VIEWPORT_SPEC_H

/*
 * Pure C contract for the RA3_SAFE_VIEWPORT environment variable
 * (adaptive-viewport research). Standalone by design: no Wine or platform
 * headers, so the same text can be vendored as include/wine/ra3_viewport.h
 * and included from macdriver, win32u, or host-side tools without pulling in
 * either build.
 *
 * Encoding (strict unsigned decimal, no whitespace, no signs):
 *   "<screenWidth>x<screenHeight>:<left>,<top>,<right>,<bottom>"
 *   e.g. RA3_SAFE_VIEWPORT=3024x1964:0,64,3024,1964
 * where 3024x1964 is the raw monitor size in Wine raw coordinate units (not
 * per-screen drawable pixels; planner rawScale 2) and 0,64,3024,1964 are the
 * safe viewport edges, EXCLUSIVE and relative to the raw monitor top-left:
 * the visible area is [left, right) x [top, bottom). The caller maps the
 * validated spec onto its exact output rect after its own physical-dims
 * validation.
 *
 * Acceptance rules:
 *   - every field is strict ASCII decimal ('0'-'9'); leading zeros are
 *     tolerated ("007" == 7); '+', '-', whitespace, hex-style input, any
 *     other character, or trailing characters are rejected
 *   - exactly six fields separated by exactly 'x' ':' ',' ',' ',' (lowercase
 *     'x' only); missing, empty, or extra fields are rejected
 *   - a field whose value would exceed 32768 is rejected, including
 *     arbitrarily long digit runs (caught before accumulation can overflow)
 *   - screen_width > 0 and screen_height > 0, both <= 32768
 *   - left >= 0 and top >= 0 (implied by unsigned parsing); right <=
 *     screen_width; bottom <= screen_height
 *   - the viewport is nonempty and usable: right - left >= 320 and
 *     bottom - top >= 200, which subsumes "nonempty"
 *   - returns 1 and fills *out only on success; returns 0 and modifies
 *     neither the input string nor *out on any failure
 */

#define RA3_VIEWPORT_ENV "RA3_SAFE_VIEWPORT"

#define RA3_VIEWPORT_MAX_DIM 32768
#define RA3_VIEWPORT_MIN_WIDTH 320
#define RA3_VIEWPORT_MIN_HEIGHT 200

typedef struct Ra3ViewportSpec {
    /* Raw monitor size in Wine raw coordinate units (not per-screen drawable
     * pixels; planner rawScale 2). */
    int screen_width, screen_height;
    /* Exclusive viewport edges, relative to the raw monitor top-left. */
    int left, top, right, bottom;
} Ra3ViewportSpec;

/* Internal strict bounded unsigned-decimal reader: 1 on success, 0 when the
 * field is empty or non-decimal, or its value would exceed max (bailing
 * before accumulation can overflow). Advances *cursor past the digits on
 * success. Private to the parser; callers use ra3_parse_viewport_spec. */
static inline int ra3vp_parse_bounded(const char **cursor, int max, int *out_value) {
    const char *p = *cursor;
    int value = 0;
    if (*p < '0' || *p > '9') return 0;
    while (*p >= '0' && *p <= '9') {
        const int digit = *p++ - '0';
        if (value > (max - digit) / 10) return 0;
        value = value * 10 + digit;
    }
    *cursor = p;
    *out_value = value;
    return 1;
}

/* Parse one RA3_SAFE_VIEWPORT value against the contract above: 1 on success
 * with *out filled, 0 on failure with the input string and *out untouched. */
static inline int ra3_parse_viewport_spec(const char *value, Ra3ViewportSpec *out) {
    Ra3ViewportSpec spec;
    const char *p = value;
    if (!value || !out) return 0;
    if (!ra3vp_parse_bounded(&p, RA3_VIEWPORT_MAX_DIM, &spec.screen_width) ||
        *p++ != 'x' ||
        !ra3vp_parse_bounded(&p, RA3_VIEWPORT_MAX_DIM, &spec.screen_height) ||
        *p++ != ':' ||
        !ra3vp_parse_bounded(&p, RA3_VIEWPORT_MAX_DIM, &spec.left) ||
        *p++ != ',' ||
        !ra3vp_parse_bounded(&p, RA3_VIEWPORT_MAX_DIM, &spec.top) ||
        *p++ != ',' ||
        !ra3vp_parse_bounded(&p, RA3_VIEWPORT_MAX_DIM, &spec.right) ||
        *p++ != ',' ||
        !ra3vp_parse_bounded(&p, RA3_VIEWPORT_MAX_DIM, &spec.bottom) ||
        *p != '\0')
        return 0;
    if (spec.screen_width <= 0 || spec.screen_height <= 0 ||
        spec.right > spec.screen_width || spec.bottom > spec.screen_height ||
        spec.right - spec.left < RA3_VIEWPORT_MIN_WIDTH ||
        spec.bottom - spec.top < RA3_VIEWPORT_MIN_HEIGHT)
        return 0;
    *out = spec;
    return 1;
}

#endif
