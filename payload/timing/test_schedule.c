#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "schedule.h"
int main(void) {
    unsigned stage = 6, steps = 0, remainder = 0, ms = 0;
    const unsigned ends[4] = {2, 3, 5, 6};
    for (unsigned frame = 1; frame <= 60000; ++frame) {
        unsigned first = stage == 6 ? 1 : stage + 1;
        stage = end_stage(first);
        assert(stage == ends[(frame - 1) % 4]);
        assert(stage >= first && stage <= 6);
        steps += stage - first + 1;
        unsigned delta = next_milliseconds(&remainder);
        assert(delta == 16 || delta == 17);
        ms += delta;
        unsigned bits = interpolation_bits(stage);
        float alpha;
        memcpy(&alpha, &bits, 4);
        assert(alpha == (float)((frame - 1) % 4 + 1) / 4);
        if (frame % 60 == 0) {
            assert(steps == frame / 60 * 90);
            assert(ms == frame / 60 * 1000);
        }
    }
    puts("PASS: 60000 displays / 90000 native stages / 15000 logic cycles / 1000000 ms; no zero-stage displays.");
}
