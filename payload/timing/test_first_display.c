#include <assert.h>
#include <stdio.h>
#include "schedule.h"

#ifndef FIRST_DISPLAY_THRESHOLD
#define FIRST_DISPLAY_THRESHOLD 8
#endif

/* Mirrors the supported binary's 60B5F0 branch, including integer truncation. */
static unsigned native_first_display(unsigned stage, unsigned ratio, unsigned threshold) {
    return ratio < 6 ? stage == threshold / ratio : stage == 1;
}

int main(void) {
    const unsigned threshold = FIRST_DISPLAY_THRESHOLD;
    const unsigned ratio = 4;
    unsigned stage = 6, first_displays = 0;
    (void)next_milliseconds;
    (void)interpolation_bits;
    for (unsigned cycle = 0; cycle < 15000; ++cycle) {
        for (unsigned frame = 0; frame < 4; ++frame) {
            unsigned first = stage == 6 ? 1 : stage + 1;
            stage = end_stage(first);
            if (native_first_display(stage, ratio, threshold)) {
                assert(stage == 2);
                ++first_displays;
            }
        }
    }
    if (first_displays != 15000) {
        printf("FAIL: native predicate hit %u/15000 cycles at threshold %u\n", first_displays, threshold);
        return 1;
    }
    printf("PASS: native predicate hits stage 2 once per cycle at threshold %u\n", threshold);
    return 0;
}
