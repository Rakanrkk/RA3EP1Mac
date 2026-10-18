#ifndef U60_SCHEDULE_H
#define U60_SCHEDULE_H
/* Fixed 4:1 display/logic ratio. Each display executes 1 or 2 native stages.
 * ponytail: no lag compensation; fewer than 60 displays/s slows simulation.
 */
static unsigned end_stage(unsigned first) {
    if (first <= 2) return 2;
    if (first == 3) return 3;
    if (first <= 5) return 5;
    return 6;
}
static unsigned next_milliseconds(unsigned *remainder) {
    *remainder += 1000;
    unsigned ms = *remainder / 60;
    *remainder %= 60;
    return ms;
}
static unsigned interpolation_bits(unsigned stage) {
    if (stage <= 2) return 0x3e800000; /* 1/4 */
    if (stage == 3) return 0x3f000000; /* 2/4 */
    if (stage <= 5) return 0x3f400000; /* 3/4 */
    return 0x3f800000;                /* 4/4 */
}
#endif
